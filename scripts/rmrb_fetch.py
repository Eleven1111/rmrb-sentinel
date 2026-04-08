"""
rmrb_fetch.py — 人民日报最新数据采集脚本
基于 caspiankexin/people-daily-crawler-date 第3版，针对 rmrb-canary skill 优化

优化点：
  1. 自动获取最新一期（今天/昨天自动回退，无需手动输入日期）
  2. 输出结构化 JSON，直接对应 Step 1 议程优先度评分维度
  3. 提取栏目名（h3）、版面位置、字数——三项打分数据一次采集到位
  4. 可按关键词过滤，只返回目标议题的相关文章

用法：
  python rmrb_fetch.py                         # 采集最新一期，输出到 ./rmrb_data/
  python rmrb_fetch.py --date 20250401         # 指定日期
  python rmrb_fetch.py --keyword 教育 房地产   # 只保留包含关键词的文章
  python rmrb_fetch.py --output /tmp/rmrb/     # 自定义输出目录
"""

import requests
import bs4
import os
import json
import datetime
import time
import argparse
import re
import sys


HEADERS = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/68.0.3440.106 Safari/537.36',
}

BASE_URL = 'http://paper.people.com.cn/rmrb/pc'

# 版面类型映射：页码 → 版面类型（用于议程优先度评分）
PAGE_TYPE_MAP = {
    1: '头版',
    2: '要闻', 3: '要闻', 4: '要闻',
}
AUTHORITY_COLUMNS = {'社论', '钟声', '人民时评', '评论员文章', '本报评论员'}


def fetch_url(url, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            r.encoding = r.apparent_encoding
            return r.text
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2)


def get_page_links(year, month, day):
    """返回当天所有版面的 URL 列表"""
    url = f'{BASE_URL}/layout/{year}{month}/{day}/node_01.html'
    html = fetch_url(url)
    soup = bs4.BeautifulSoup(html, 'html.parser')

    container = soup.find('div', attrs={'id': 'pageList'}) or \
                soup.find('div', attrs={'class': 'swiper-container'})
    if not container:
        return []

    pages = container.find_all('div', attrs={'class': ['right_title-name', 'swiper-slide']})
    links = []
    for page in pages:
        if page.a:
            link = page.a['href']
            links.append(f'{BASE_URL}/layout/{year}{month}/{day}/{link}')
    return links


def get_article_links(year, month, day, page_url):
    """返回某版面内所有文章的 URL 列表"""
    html = fetch_url(page_url)
    soup = bs4.BeautifulSoup(html, 'html.parser')

    container = soup.find('div', attrs={'id': 'titleList'}) or \
                soup.find('ul', attrs={'class': 'news-list'})
    if not container:
        return []

    links = []
    for item in container.find_all('li'):
        for a in item.find_all('a'):
            href = a['href']
            if 'content' in href:
                links.append(f'{BASE_URL}/content/{year}{month}/{day}/{href}')
    return links


def parse_article(html, url, page_no, article_no):
    """
    解析单篇文章，返回结构化数据。

    评分相关字段（对应 Step 1）：
      - page_no:        版面页码
      - page_type:      头版 / 要闻 / 专题
      - position:       头条 / 非头条
      - position_score: 版面位置分（0-4）
      - word_count:     正文字数
      - word_score:     篇幅密度分（0-3）
      - column:         栏目名（h3 文本）
      - column_score:   权威栏目分（0-3）
      - agenda_score:   议程优先度总分（position + word + column，满分 10）
    """
    soup = bs4.BeautifulSoup(html, 'html.parser')

    # 标题三段
    column = soup.h3.text.strip() if soup.h3 else ''
    title  = soup.h1.text.strip() if soup.h1 else ''
    sub    = soup.h2.text.strip() if soup.h2 else ''

    # 正文
    zoom = soup.find('div', attrs={'id': 'ozoom'})
    paragraphs = [p.text.strip() for p in zoom.find_all('p')] if zoom else []
    content = '\n'.join(paragraphs)
    word_count = len(re.sub(r'\s+', '', content))

    # 版面位置分
    page_type = PAGE_TYPE_MAP.get(page_no, '专题')
    if page_no == 1 and article_no == 1:
        position = '头条'
        position_score = 4
    elif page_no == 1:
        position = '头版非头条'
        position_score = 3
    elif page_no <= 4:
        position = '要闻版'
        position_score = 2
    else:
        position = '专题版'
        position_score = 1

    # 篇幅密度分
    if word_count > 3000:
        word_score = 3
    elif word_count > 1500:
        word_score = 2
    elif word_count > 500:
        word_score = 1
    else:
        word_score = 0

    # 权威栏目分
    col_score = 0
    if any(k in column for k in ['社论', '钟声']):
        col_score = 3
    elif '人民时评' in column or '评论员' in column:
        col_score = 2
    elif any(k in column for k in ['观察', '调查', '深度']):
        col_score = 1

    agenda_score = position_score + word_score + col_score

    return {
        'url': url,
        'page_no': page_no,
        'page_type': page_type,
        'article_no': article_no,
        'position': position,
        'column': column,
        'title': title,
        'subtitle': sub,
        'content': content,
        'word_count': word_count,
        'position_score': position_score,
        'word_score': word_score,
        'column_score': col_score,
        'agenda_score': agenda_score,
    }


def resolve_date(date_str=None):
    """自动回退：今天没发布则用昨天"""
    if date_str:
        d = datetime.datetime.strptime(date_str, '%Y%m%d')
        return d.strftime('%Y'), d.strftime('%m'), d.strftime('%d')

    for delta in [0, 1]:
        d = datetime.datetime.now() - datetime.timedelta(days=delta)
        y, m, day = d.strftime('%Y'), d.strftime('%m'), d.strftime('%d')
        url = f'{BASE_URL}/layout/{y}{m}/{day}/node_01.html'
        try:
            r = requests.head(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                return y, m, day
        except Exception:
            continue

    raise RuntimeError('无法获取最新期，请手动指定 --date YYYYMMDD')


def fetch(date_str=None, keywords=None, output_dir='./rmrb_data'):
    year, month, day = resolve_date(date_str)
    date_label = f'{year}{month}{day}'
    print(f'[rmrb_fetch] 采集日期：{year}年{month}月{day}日', file=sys.stderr)

    out_dir = os.path.join(output_dir, date_label)
    os.makedirs(out_dir, exist_ok=True)

    articles = []
    page_links = get_page_links(year, month, day)
    print(f'[rmrb_fetch] 共 {len(page_links)} 个版面', file=sys.stderr)

    for page_no, page_url in enumerate(page_links, start=1):
        try:
            art_links = get_article_links(year, month, day, page_url)
            for art_no, art_url in enumerate(art_links, start=1):
                try:
                    html = fetch_url(art_url)
                    art = parse_article(html, art_url, page_no, art_no)
                    art['date'] = date_label

                    # 关键词过滤：如果指定了关键词，只保留命中的文章
                    if keywords:
                        hit = any(
                            kw in art['title'] or kw in art['content']
                            for kw in keywords
                        )
                        if not hit:
                            continue

                    articles.append(art)

                    # 保存原文 txt（兼容原版格式）
                    txt_name = f'{date_label}-{str(page_no).zfill(2)}-{str(art_no).zfill(2)}.txt'
                    with open(os.path.join(out_dir, txt_name), 'w', encoding='utf-8') as f:
                        f.write(f'{art["column"]}\n{art["title"]}\n{art["subtitle"]}\n\n{art["content"]}')

                    time.sleep(0.5)
                except Exception as e:
                    print(f'  [跳过] 版面{page_no} 文章{art_no}：{e}', file=sys.stderr)
        except Exception as e:
            print(f'  [跳过] 版面{page_no}：{e}', file=sys.stderr)

        time.sleep(1)

    # 生成 summary.json —— 直接对应 Step 1 评分维度
    summary = build_summary(date_label, articles, keywords)
    summary_path = os.path.join(out_dir, 'summary.json')
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f'[rmrb_fetch] 完成：{len(articles)} 篇文章 → {out_dir}', file=sys.stderr)
    print(f'[rmrb_fetch] 评分摘要已写入 summary.json', file=sys.stderr)
    return summary


def build_summary(date_label, articles, keywords):
    """
    构建直接喂入 rmrb-canary 分析主流程的评分摘要。

    新增维度（2026版）：
      - step0_narrative:   叙事框架识别（零步）
      - step6_intensity:   话语强度七级定级（替代情感四象限）
      - ministry_signals:  部委协同度检测
    """
    # ── 地域提及 ──────────────────────────────────────────────────
    province_pattern = re.compile(
        r'(北京|上海|广东|浙江|江苏|四川|湖北|湖南|河南|河北|山东|陕西|福建|安徽|辽宁|吉林|黑龙江|云南|贵州|广西|内蒙古|新疆|西藏|青海|甘肃|宁夏|海南|重庆|天津)'
    )
    region_count = {}
    for a in articles:
        for m in province_pattern.findall(a['content'] + a['title']):
            region_count[m] = region_count.get(m, 0) + 1
    top_regions = sorted(region_count.items(), key=lambda x: -x[1])[:10]

    # ── 议程优先度 ────────────────────────────────────────────────
    high_priority = [
        {'title': a['title'], 'column': a['column'], 'page': a['page_no'],
         'position': a['position'], 'score': a['agenda_score'], 'word_count': a['word_count']}
        for a in articles if a['agenda_score'] >= 7
    ]
    authority_hits = [
        {'title': a['title'], 'column': a['column'], 'score': a['agenda_score']}
        for a in articles if a['column_score'] >= 2
    ]

    # ── 第零步：叙事框架识别 ──────────────────────────────────────
    NARRATIVE_FRAMES = {
        '国家安全框架': ['安全', '自主可控', '卡脖子', '供应链安全', '数据安全', '网络安全', '粮食安全', '能源安全'],
        '共同富裕框架': ['共同富裕', '资本无序', '垄断', '平台经济', '过度逐利', '防止资本'],
        '高质量发展框架': ['高质量发展', '绿色低碳', '转型升级', '新质生产力', '淘汰落后'],
        '自立自强框架': ['自立自强', '核心技术', '国产替代', '弯道超车', '科技创新', '关键核心'],
        '防范金融风险框架': ['系统性风险', '杠杆', '债务风险', '流动性', '房住不炒', '防范化解'],
        '社会治理框架': ['基层治理', '社会稳定', '民生保障', '群众利益', '维护稳定'],
    }
    full_corpus = ' '.join(
        a['title'] + ' ' + a['content'] for a in articles
    )
    frame_hits = {}
    for frame, keywords_f in NARRATIVE_FRAMES.items():
        hit_words = [kw for kw in keywords_f if kw in full_corpus]
        if hit_words:
            frame_hits[frame] = {'count': len(hit_words), 'matched': hit_words}
    # 按命中词数降序，取前两个主要框架
    sorted_frames = sorted(frame_hits.items(), key=lambda x: -x[1]['count'])
    primary_frame = sorted_frames[0][0] if sorted_frames else '未识别'
    secondary_frame = sorted_frames[1][0] if len(sorted_frames) > 1 else None

    # ── Step 6：话语强度七级定级 ─────────────────────────────────
    INTENSITY_LEVELS = {
        7: ['雷霆行动', '清网行动', '扫黑除恶', '集中收网', '专项打击'],
        6: ['依法查处', '追究责任', '司法追诉', '移送公安', '依法追责', '绝不姑息'],
        5: ['坚决遏制', '严格管控', '决不允许', '进一步规范', '坚决整治', '严厉打击'],
        4: ['专项整治', '集中清理', '有序规范', '重点整治', '严格执法'],
        3: ['规范发展', '健全机制', '完善监管', '规范引导', '加强监管'],
        2: ['加快推进', '大力支持', '全面部署', '积极推进', '重点推进'],
        1: ['研究制定', '鼓励试点', '积极探索', '研究探索', '鼓励发展'],
    }
    level_counts = {i: 0 for i in range(1, 8)}
    level_triggers = {i: [] for i in range(1, 8)}
    for a in articles:
        text = a['title'] + ' ' + a['content']
        for level, phrases in INTENSITY_LEVELS.items():
            for phrase in phrases:
                if phrase in text:
                    level_counts[level] += 1
                    if phrase not in level_triggers[level]:
                        level_triggers[level].append(phrase)
    max_level = max((lvl for lvl, cnt in level_counts.items() if cnt > 0), default=1)
    total_hits = sum(level_counts.values()) or 1
    level_distribution = {
        f'level_{i}': {
            'count': level_counts[i],
            'pct': round(level_counts[i] / total_hits * 100, 1),
            'triggers': level_triggers[i],
        }
        for i in range(1, 8)
    }
    # 跳级检测：最低非零等级 vs 最高等级，间隔≥2为跳级
    nonzero_levels = sorted([lvl for lvl, cnt in level_counts.items() if cnt > 0])
    jump_alert = False
    jump_detail = None
    if len(nonzero_levels) >= 2:
        low, high = nonzero_levels[0], nonzero_levels[-1]
        if high - low >= 3:  # 跨越≥2个空白等级
            jump_alert = True
            jump_detail = f'从{low}级跳至{high}级（中间等级无报道）'

    # ── 部委协同度检测 ───────────────────────────────────────────
    MINISTRY_PATTERNS = {
        '国家发展改革委': ['发改委', '国家发展改革委', '发展改革'],
        '工业和信息化部': ['工信部', '工业和信息化部'],
        '国务院': ['国务院常务会议', '国务院专题', '国务院部署'],
        '中央政治局': ['政治局会议', '政治局常委', '中央政治局'],
        '国家市场监督管理总局': ['市场监管总局', '市场监管', '反垄断'],
        '国家互联网信息办公室': ['网信办', '网络安全和信息化'],
        '公安部': ['公安部', '公安机关', '警方'],
        '最高人民检察院': ['最高检', '检察院', '检察机关'],
        '最高人民法院': ['最高法', '人民法院'],
        '财政部': ['财政部', '财政政策'],
        '中国人民银行': ['人民银行', '央行', '货币政策'],
        '国家税务总局': ['税务总局', '税务机关'],
        '生态环境部': ['生态环境部', '环保部门'],
        '教育部': ['教育部', '教育主管'],
        '国家能源局': ['国家能源局', '能源监管'],
    }
    ministry_hits = {}
    for ministry, patterns in MINISTRY_PATTERNS.items():
        for a in articles:
            text = a['title'] + ' ' + a['content']
            for pat in patterns:
                if pat in text:
                    ministry_hits[ministry] = ministry_hits.get(ministry, 0) + 1
                    break

    ministry_count = len(ministry_hits)
    has_state_council = '国务院' in ministry_hits
    has_politburo = '中央政治局' in ministry_hits
    has_judicial = any(m in ministry_hits for m in ['公安部', '最高人民检察院', '最高人民法院'])

    if has_judicial:
        coordination_level = 'L5'
        coordination_label = '司法入轨，窗口≤30天'
    elif has_politburo:
        coordination_level = 'L4'
        coordination_label = '政治局级，最高优先级'
    elif has_state_council:
        coordination_level = 'L3'
        coordination_label = '国务院级，执行意志确认'
    elif ministry_count >= 3:
        coordination_level = 'L2'
        coordination_label = '多部委协同，行动概率显著提升'
    elif ministry_count >= 1:
        coordination_level = 'L1'
        coordination_label = '单部委关注，预警信号'
    else:
        coordination_level = 'L0'
        coordination_label = '未检测到部委信号'

    return {
        'date': date_label,
        'keywords_filter': keywords or [],
        'total_articles': len(articles),
        'total_pages': max((a['page_no'] for a in articles), default=0),

        # 第零步：叙事框架
        'step0_narrative': {
            'primary_frame': primary_frame,
            'secondary_frame': secondary_frame,
            'frame_hits': {k: v for k, v in sorted_frames},
            'note': '框架识别基于关键词匹配，需结合上下文判断',
        },

        # Step 1：议程优先度
        'step1_agenda': {
            'high_priority_articles': high_priority,
            'front_page_count': sum(1 for a in articles if a['page_no'] == 1),
            'authority_column_hits': authority_hits,
        },

        # Step 4：地域映射
        'step4_regions': {
            'top_regions': [{'region': r, 'count': c} for r, c in top_regions],
        },

        # Step 6：话语强度七级
        'step6_intensity': {
            'max_level': max_level,
            'max_level_triggers': level_triggers[max_level],
            'distribution': level_distribution,
            'jump_alert': jump_alert,
            'jump_detail': jump_detail,
            'note': '等级基于关键词匹配，系数未经历史标定，仅供参考',
        },

        # 部委协同度
        'ministry_signals': {
            'coordination_level': coordination_level,
            'coordination_label': coordination_label,
            'ministry_count': ministry_count,
            'ministries_found': list(ministry_hits.keys()),
            'has_state_council': has_state_council,
            'has_politburo': has_politburo,
            'has_judicial': has_judicial,
        },

        # 原始评分列表（供逐篇核验）
        'articles': [
            {k: v for k, v in a.items() if k != 'content'}
            for a in articles
        ],

        # 全文内容（Step 5 语义三元组用）
        'full_texts': [
            {'title': a['title'], 'column': a['column'],
             'page_no': a['page_no'], 'content': a['content']}
            for a in articles
        ],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='人民日报最新数据采集（rmrb-canary 专用）')
    parser.add_argument('--date',    help='指定日期 YYYYMMDD，默认自动获取最新一期')
    parser.add_argument('--keyword', nargs='+', dest='keywords', help='关键词过滤（空格分隔多个词）')
    parser.add_argument('--output',  default='./rmrb_data', help='输出目录，默认 ./rmrb_data')
    args = parser.parse_args()

    fetch(date_str=args.date, keywords=args.keywords, output_dir=args.output)
