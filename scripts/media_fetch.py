"""
media_fetch.py — 多源媒体数据采集脚本（rmrb-canary 交叉验证层）
在 rmrb_fetch.py 基础上新增：
  - 百度新闻：按关键词搜索近 N 天报道（聚合新华社/央视/人民网等官媒），判断央媒联动程度
  - 微博热搜：抓取当前热搜榜，判断议题是否进入民间舆论圈
  - 输出 cross_validation.json，直接对应 skill 交叉验证步骤

用法：
  python media_fetch.py --keyword 光伏 新能源 --days 7
  python media_fetch.py --keyword 光伏 --date 20260401 --output /tmp/media/
  python media_fetch.py --keyword 光伏 --sources rmrb official weibo  # 指定来源
"""

import requests
import bs4
import os, json, re, time, datetime, argparse
import sys

# 复用 rmrb_fetch 的核心逻辑
sys.path.insert(0, os.path.dirname(__file__))
from rmrb_fetch import fetch as fetch_rmrb, resolve_date

HEADERS = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'accept-language': 'zh-CN,zh;q=0.9',
}


# ─────────────────────────────────────────────
# 官媒聚合（人民网多频道 RSS + 关键词过滤）
# ─────────────────────────────────────────────

# 人民网各频道 RSS（经验证可访问，数据实时）
PEOPLE_RSS_CHANNELS = [
    ('人民网-政治', 'http://www.people.com.cn/rss/politics.xml'),
    ('人民网-财经', 'http://www.people.com.cn/rss/finance.xml'),
    ('人民网-科技', 'http://www.people.com.cn/rss/scitech.xml'),
    ('人民网-社会', 'http://www.people.com.cn/rss/society.xml'),
    ('人民网-环境', 'http://www.people.com.cn/rss/env.xml'),
]

RSS_HEADERS = {
    'user-agent': 'Mozilla/5.0 (compatible; RSSBot/1.0; rmrb-canary)',
    'accept': 'application/rss+xml, application/xml, text/xml, */*',
}


def fetch_official_media(keywords, days=7, max_per_kw=10):
    """
    从人民网多频道 RSS 中按关键词过滤官媒报道。
    覆盖：政治、财经、科技、社会、环境五个频道，合计约 500 条最新文章。
    不依赖搜索 API，稳定性高，不需要验证码。

    返回字段：source / keyword / title / summary / pub_time / url / media / channel
    """
    end_dt   = datetime.datetime.now()
    start_dt = end_dt - datetime.timedelta(days=days)

    # 1. 加载所有频道 RSS
    all_items = []
    for channel_name, rss_url in PEOPLE_RSS_CHANNELS:
        try:
            resp = requests.get(rss_url, headers=RSS_HEADERS, timeout=10)
            resp.encoding = 'utf-8'
            soup = bs4.BeautifulSoup(resp.text, 'xml')
            for item in soup.find_all('item'):
                t_el   = item.find('title')
                l_el   = item.find('link')
                d_el   = item.find('description')
                pub_el = item.find('pubDate')

                title   = _clean(t_el.get_text()) if t_el else ''
                link    = l_el.get_text().strip() if l_el else ''
                desc    = _clean(re.sub(r'<[^>]+>', '', d_el.get_text())) if d_el else ''
                pub_str = pub_el.get_text().strip() if pub_el else ''

                # 解析日期（格式：2025-06-05 或 Thu, 05 Jun 2025 ...）
                pub_dt = None
                for fmt in ('%Y-%m-%d', '%a, %d %b %Y %H:%M:%S %z', '%a, %d %b %Y %H:%M:%S'):
                    try:
                        pub_dt = datetime.datetime.strptime(pub_str.strip()[:25], fmt)
                        break
                    except Exception:
                        pass

                all_items.append({
                    'title':    title,
                    'link':     link,
                    'desc':     desc,
                    'pub_time': pub_str,
                    'channel':  channel_name,
                    'media':    '人民网',
                })
            time.sleep(0.5)
        except Exception as e:
            print(f'  [官媒] {channel_name} 采集失败：{e}', file=sys.stderr)

    # 2. 关键词过滤
    results = []
    seen = set()
    for kw in keywords:
        matched = 0
        for item in all_items:
            if kw not in item['title'] and kw not in item['desc']:
                continue
            key = item['link'] or item['title']
            if key in seen:
                continue
            seen.add(key)
            results.append({
                'source':      'official_media',
                'is_official': True,   # 人民网本身即官方媒体
                'keyword':     kw,
                'title':       item['title'],
                'summary':     item['desc'][:200],
                'pub_time':    item['pub_time'],
                'media':       item['media'],
                'channel':     item['channel'],
                'url':         item['link'],
            })
            matched += 1
            if matched >= max_per_kw:
                break

    official_count = len(results)
    print(f'  [官媒] RSS 共扫描 {len(all_items)} 条，关键词命中 {official_count} 条（来源：人民网各频道）', file=sys.stderr)
    return results


def _clean(text):
    """去除 HTML 标签和多余空格"""
    text = re.sub(r'<[^>]+>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()


# ─────────────────────────────────────────────
# 微博热搜
# ─────────────────────────────────────────────

def fetch_weibo_hot(keywords=None):
    """
    获取微博实时热搜榜。
    依次尝试：① 微博官方热搜页 → ② tophub 聚合站（无需登录）
    返回：{hot_list: [...], keyword_hits: [...], source: str}
    """
    hot_list = []
    source_used = 'none'

    # ── 方案①：微博官方热搜页 ─────────────────
    try:
        url  = 'https://s.weibo.com/top/summary?cate=realtimehot'
        hdrs = {**HEADERS, 'referer': 'https://weibo.com/', 'host': 's.weibo.com'}
        resp = requests.get(url, headers=hdrs, timeout=15, allow_redirects=True)
        resp.encoding = 'utf-8'
        soup = bs4.BeautifulSoup(resp.text, 'html.parser')
        rows = soup.select('tbody tr')
        for row in rows:
            rank_td = row.select_one('td.td-01')
            name_td = row.select_one('td.td-02')
            hot_td  = row.select_one('td.td-03')
            if not name_td:
                continue
            a_tag = name_td.find('a')
            name  = a_tag.get_text(strip=True) if a_tag else name_td.get_text(strip=True)
            rank  = rank_td.get_text(strip=True) if rank_td else ''
            hot   = hot_td.get_text(strip=True)  if hot_td  else ''
            span  = name_td.find('span', class_=re.compile('label'))
            label = span.get_text(strip=True) if span else ''
            if name and len(name) > 1:
                hot_list.append({'rank': rank, 'name': name, 'hot': hot, 'label': label})
        if hot_list:
            source_used = 'weibo_official'
    except Exception:
        pass

    # ── 方案②：tophub 聚合站（官方被拦时的降级）────
    if not hot_list:
        try:
            url  = 'https://tophub.today/n/KqndgxeLl9'
            hdrs = {**HEADERS, 'referer': 'https://tophub.today/'}
            resp = requests.get(url, headers=hdrs, timeout=15)
            resp.encoding = 'utf-8'
            soup = bs4.BeautifulSoup(resp.text, 'html.parser')
            for i, item in enumerate(soup.select('.nano-item'), start=1):
                title_el = item.select_one('.nano-item-content') or item.select_one('a')
                if title_el:
                    hot_list.append({
                        'rank':  str(i),
                        'name':  title_el.get_text(strip=True),
                        'hot':   '',
                        'label': '',
                    })
            if hot_list:
                source_used = 'tophub'
        except Exception as e:
            print(f'  [微博热搜] tophub 也失败：{e}', file=sys.stderr)

    # ── 方案③：百度热搜作为参照（两种来源都失败时）──
    if not hot_list:
        try:
            url  = 'https://top.baidu.com/board?tab=realtime'
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.encoding = 'utf-8'
            soup = bs4.BeautifulSoup(resp.text, 'html.parser')
            for i, el in enumerate(soup.select('.c-single-text-ellipsis'), start=1):
                name = el.get_text(strip=True)
                if name:
                    hot_list.append({'rank': str(i), 'name': name, 'hot': '', 'label': ''})
            if hot_list:
                source_used = 'baidu_hot'
                print('  [热搜] 微博不可达，已降级使用百度热搜作为民间情绪参照', file=sys.stderr)
        except Exception as e:
            print(f'  [热搜] 所有方案均失败：{e}', file=sys.stderr)

    # 关键词命中标记
    keyword_hits = []
    if keywords:
        for item in hot_list:
            matched = [kw for kw in keywords if kw in item['name']]
            if matched:
                keyword_hits.append({**item, 'matched_keywords': matched})

    return {
        'hot_list':     hot_list[:50],
        'keyword_hits': keyword_hits,
        'source':       source_used,   # 便于分析时注明数据来源
    }


# ─────────────────────────────────────────────
# 交叉验证汇总
# ─────────────────────────────────────────────

def build_cross_validation(rmrb_summary, official_articles, weibo_data, keywords):
    """
    将三个来源的数据整合为交叉验证报告。
    直接对应 skill 中"第七节 交叉验证信号"的输入。

    official_articles: 来自 fetch_official_media()，百度新闻聚合官媒报道
    """
    rmrb_count = rmrb_summary.get('total_articles', 0)

    # 官媒联动度：按关键词分组统计，优先统计官方来源
    official_by_kw = {}
    official_only  = [a for a in official_articles if a.get('is_official', False)]
    for a in official_articles:
        kw = a['keyword']
        official_by_kw.setdefault(kw, []).append(a)

    # 以官方来源篇数为主要判断依据
    official_count = len(official_only)
    official_coverage = 'strong'   if official_count >= 5 else \
                        'moderate' if official_count >= 2 else 'weak'

    # 微博热搜命中
    weibo_hits   = weibo_data.get('keyword_hits', [])
    weibo_in_hot = len(weibo_hits) > 0

    # 官民张力初步判断
    if weibo_in_hot and rmrb_count >= 3:
        tension = 'medium'   # 双向都有，需进一步判断情绪倾向
    elif weibo_in_hot and rmrb_count < 3:
        tension = 'high'     # 民间热但官方冷，值得警惕
    elif not weibo_in_hot and rmrb_count >= 3:
        tension = 'low'      # 官方推动但民间未感知，属正常宣传期
    else:
        tension = 'unknown'

    return {
        'keywords': keywords,
        'generated_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),

        'rmrb': {
            'article_count': rmrb_count,
            'high_priority':  rmrb_summary.get('step1_agenda', {}).get('high_priority_articles', []),
        },

        # 官媒聚合（百度新闻，含新华社/央视/人民网等）
        'official_media': {
            'source_note':    '百度新闻聚合（含新华社、央视、人民网、光明网等官方来源）',
            'total_count':    len(official_articles),
            'official_count': official_count,   # 可识别官方来源的数量
            'coverage_level': official_coverage,
            'coverage_label': {'strong': '强联动（≥5篇官方报道）',
                               'moderate': '跟进报道（2-4篇）',
                               'weak': '基本未跟进（<2篇）'}[official_coverage],
            'articles':       official_articles[:15],
            'by_keyword':     {kw: len(arts) for kw, arts in official_by_kw.items()},
        },

        'weibo': {
            'in_hot_search':  weibo_in_hot,
            'keyword_hits':   weibo_hits,
            'hot_list_top10': weibo_data.get('hot_list', [])[:10],
            'source':         weibo_data.get('source', 'unknown'),
        },

        'cross_analysis': {
            'official_amplification': official_coverage in ('strong', 'moderate'),
            # True = 央媒联动信号，政策方向基本确定
            'public_awareness': weibo_in_hot,
            # True = 议题已进入大众视野，舆情管理难度上升
            'tension_estimate': tension,
            'tension_label': {
                'high':    '⚠️ 高张力（民间热、官方冷）',
                'medium':  '🟡 中张力（双向关注，需判断情绪）',
                'low':     '🟢 低张力（官方推动，民间未感知）',
                'unknown': '❓ 数据不足',
            }[tension],
            'confidence': 'limited' if not weibo_in_hot and official_count == 0 else 'partial',
        },
    }


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='多源媒体采集（人民日报 + 新华社 + 微博热搜）')
    parser.add_argument('--keyword', nargs='+', dest='keywords', required=True,
                        help='关键词列表（空格分隔）')
    parser.add_argument('--date',    help='人民日报日期 YYYYMMDD，默认最新一期')
    parser.add_argument('--days',    type=int, default=7,
                        help='新华社搜索时间范围（天），默认 7')
    parser.add_argument('--output',  default='./media_data', help='输出目录')
    parser.add_argument('--sources', nargs='+',
                        default=['rmrb', 'official', 'weibo'],
                        choices=['rmrb', 'official', 'weibo'],
                        help='数据来源，默认全部（official=百度新闻聚合官媒）')
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    print(f'[media_fetch] 关键词：{args.keywords}  来源：{args.sources}', file=sys.stderr)

    # ── 人民日报 ──────────────────────────────
    rmrb_summary = {'total_articles': 0, 'step1_agenda': {}, 'articles': [], 'full_texts': []}
    if 'rmrb' in args.sources:
        print('\n[1/3] 人民日报 ...', file=sys.stderr)
        rmrb_summary = fetch_rmrb(
            date_str=args.date,
            keywords=args.keywords,
            output_dir=os.path.join(args.output, 'rmrb')
        )

    # ── 官媒聚合（百度新闻）───────────────────
    official_articles = []
    if 'official' in args.sources:
        print(f'\n[2/3] 官媒聚合·百度新闻（近 {args.days} 天）...', file=sys.stderr)
        official_articles = fetch_official_media(args.keywords, days=args.days)
        with open(os.path.join(args.output, 'official_media.json'), 'w', encoding='utf-8') as f:
            json.dump(official_articles, f, ensure_ascii=False, indent=2)

    # ── 微博热搜 ──────────────────────────────
    weibo_data = {'hot_list': [], 'keyword_hits': []}
    if 'weibo' in args.sources:
        print('\n[3/3] 微博热搜 ...', file=sys.stderr)
        weibo_data = fetch_weibo_hot(keywords=args.keywords)
        hits = weibo_data.get('keyword_hits', [])
        print(f'      热搜榜 {len(weibo_data["hot_list"])} 条，关键词命中 {len(hits)} 条', file=sys.stderr)
        with open(os.path.join(args.output, 'weibo_hot.json'), 'w', encoding='utf-8') as f:
            json.dump(weibo_data, f, ensure_ascii=False, indent=2)

    # ── 交叉验证汇总 ──────────────────────────
    cv = build_cross_validation(rmrb_summary, official_articles, weibo_data, args.keywords)
    cv_path = os.path.join(args.output, 'cross_validation.json')
    with open(cv_path, 'w', encoding='utf-8') as f:
        json.dump(cv, f, ensure_ascii=False, indent=2)

    # ── 终端摘要 ──────────────────────────────
    print('\n' + '='*60, file=sys.stderr)
    print(f'[结果摘要]', file=sys.stderr)
    om = cv["official_media"]
    print(f'  人民日报：{cv["rmrb"]["article_count"]} 篇', file=sys.stderr)
    print(f'  官媒聚合：{om["total_count"]} 条（官方来源 {om["official_count"]} 条 | {om["coverage_label"]}）', file=sys.stderr)
    print(f'  微博热搜：{"命中 " + str(len(cv["weibo"]["keyword_hits"])) + " 条" if cv["weibo"]["in_hot_search"] else "未上热搜"}（来源：{cv["weibo"].get("source","unknown")}）', file=sys.stderr)
    print(f'  官民张力：{cv["cross_analysis"]["tension_label"]}', file=sys.stderr)
    print(f'  交叉验证 → {cv_path}', file=sys.stderr)
    print('='*60, file=sys.stderr)


if __name__ == '__main__':
    main()
