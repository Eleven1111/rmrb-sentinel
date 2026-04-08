#!/usr/bin/env python3
"""
RMRB-Canary Agent — 政策信号分析管道

纯计算管道，不调用任何 LLM API。
输出结构化 JSON，由 Claude Code / OpenClaw 读取后完成推理和报告撰写。

用法（Claude Code 内部 Bash 调用）：
  python3 -m agent.agent --keyword 新能源 光伏 储能
  python3 -m agent.agent --keyword 新能源 光伏 --date 20260405
  python3 -m agent.agent --history
"""

import json
import os
import sys
import argparse

from agent.tools.fetch_rmrb import fetch_rmrb
from agent.tools.fetch_media import fetch_media_sources
from agent.tools.narrative_frame import classify_narrative
from agent.tools.discourse_level import measure_intensity
from agent.tools.ministry_signals import detect_ministries
from agent.tools.policy_clock import get_policy_clock, calculate_risk_window
from agent.tools.history_compare import compare_history, get_history
from agent.tools.silence_detector import detect_silence, rolling_trend
from agent.tools.cooccurrence import analyze_cooccurrence
from agent.store.db import save_analysis


def run_pipeline(keywords: list[str], date: str = None, skip_media: bool = False) -> dict:
    """
    执行完整分析管道，返回结构化结果 dict。

    全程纯计算，零 LLM 调用。Claude Code 拿到这个 dict 后：
    1. 读 full_texts 做语义三元组提取（LLM 擅长的事）
    2. 综合所有维度撰写报告（LLM 擅长的事）
    3. 生成战略建议（LLM 擅长的事）
    """
    result = {'keywords': keywords, 'date_requested': date}

    # ── 1. 采集人民日报 ────────────────────────────────────
    print('[1/9] 采集人民日报...', file=sys.stderr)
    summary = fetch_rmrb(keywords=keywords, date=date)
    result['rmrb'] = {
        'date': summary.get('date', ''),
        'total_articles': summary.get('total_articles', 0),
        'total_pages': summary.get('total_pages', 0),
        'agenda': summary.get('step1_agenda', {}),
        'regions': summary.get('step4_regions', {}),
        'articles': summary.get('articles', []),
    }
    # full_texts 单独放，供 Claude Code 做语义分析
    result['full_texts'] = summary.get('full_texts', [])

    # ── 2. 叙事框架分类（加权版）─────────────────────────────
    print('[2/9] 叙事框架分类（加权）...', file=sys.stderr)
    full_texts = summary.get('full_texts', [])
    result['narrative'] = classify_narrative(full_texts)

    # ── 3. 话语强度七级（加权版）─────────────────────────────
    print('[3/9] 话语强度定级（加权）...', file=sys.stderr)
    result['intensity'] = measure_intensity(full_texts)

    # ── 4. 部委协同度（加权版）──────────────────────────────
    print('[4/9] 部委协同度检测（加权）...', file=sys.stderr)
    result['ministry'] = detect_ministries(full_texts)

    # ── 5. 共现语境分析 ──────────────────────────────────
    print('[5/9] 共现语境分析...', file=sys.stderr)
    result['cooccurrence'] = analyze_cooccurrence(full_texts, keywords)

    # ── 6. 政策时钟 ───────────────────────────────────────
    print('[6/9] 政策时钟校正...', file=sys.stderr)
    analysis_date = summary.get('date', '')
    result['clock'] = get_policy_clock(analysis_date or None)

    # ── 7. 风险窗口计算 ───────────────────────────────────
    print('[7/9] 风险窗口计算...', file=sys.stderr)
    result['risk_window'] = calculate_risk_window(
        intensity_level=result['intensity']['max_level'],
        ministry_compression=result['ministry']['time_compression'],
        clock_coefficient=result['clock']['coefficient'],
        narrative_speed_modifier=result['narrative']['speed_modifier'],
    )

    # ── 7.5. 交叉验证（可选）──────────────────────────────
    if not skip_media:
        print('[7.5/9] 多源交叉验证...', file=sys.stderr)
        try:
            cross = fetch_media_sources(
                keywords=keywords,
                rmrb_summary=summary,
            )
            result['cross_validation'] = cross
        except Exception as e:
            print(f'  [跳过] 交叉验证失败: {e}', file=sys.stderr)
            result['cross_validation'] = {'error': str(e)}

    # ── 8. 历史对比 + 存储 ────────────────────────────────
    print('[8/9] 历史对比 + 存储...', file=sys.stderr)
    result['trend'] = compare_history(summary, keywords)

    # 存储本次分析
    risk_emoji = result['risk_window'].get('risk_emoji', '🟢')
    analysis_id = save_analysis(summary, risk_emoji)
    result['storage'] = {'analysis_id': analysis_id, 'saved': True}

    # ── 9. 沉默检测 + 滚动趋势 ───────────────────────────
    print('[9/9] 沉默检测 + 滚动趋势...', file=sys.stderr)
    result['silence'] = detect_silence(
        keywords=keywords,
        current_date=analysis_date,
        current_count=summary.get('total_articles', 0),
    )
    result['rolling_trend'] = rolling_trend(keywords)

    # ── 摘要（供快速阅读）────────────────────────────────
    cooc = result['cooccurrence']
    silence = result['silence']
    result['summary_line'] = (
        f"日期={summary.get('date','')} "
        f"文章={summary.get('total_articles',0)} "
        f"框架={result['narrative']['primary_frame']} "
        f"强度={result['intensity']['max_level']}级({result['intensity']['max_level_name']}) "
        f"加权强度={result['intensity']['weighted_max_level']}级 "
        f"部委={result['ministry']['coordination_level']} "
        f"语境={cooc['sentiment_label']}(正{cooc['positive_ratio']:.0%}/负{cooc['negative_ratio']:.0%}) "
        f"沉默={silence['signal']}({silence['signal_strength']:.0%}) "
        f"时钟={result['clock']['phase']}(×{result['clock']['coefficient']}) "
        f"窗口={result['risk_window']['adjusted_window_label']} "
        f"{risk_emoji}"
    )

    print(f'\n[完成] {result["summary_line"]}', file=sys.stderr)
    return result


def main():
    parser = argparse.ArgumentParser(
        description='RMRB-Canary — 政策信号分析管道（纯计算，零 LLM 调用）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
在 Claude Code 中使用：
  1. 运行管道获取结构化数据：
     python3 -m agent.agent --keyword 新能源 光伏

  2. Claude Code 读取 JSON 输出，完成语义推理和报告撰写

直接运行：
  python3 -m agent.agent --keyword 新能源 光伏 储能
  python3 -m agent.agent --keyword 教育 培训 --date 20260405
  python3 -m agent.agent --keyword 光伏 --skip-media   # 跳过交叉验证
  python3 -m agent.agent --history                      # 查看历史
        """,
    )
    parser.add_argument('--keyword', nargs='+', required=False, help='关键词列表')
    parser.add_argument('--date', help='指定日期 YYYYMMDD，默认最新一期')
    parser.add_argument('--skip-media', action='store_true', help='跳过多源交叉验证')
    parser.add_argument('--history', action='store_true', help='查看分析历史')
    parser.add_argument('--compact', action='store_true', help='输出精简 JSON（去掉 full_texts）')

    args = parser.parse_args()

    if args.history:
        records = get_history(limit=20)
        if not records['records']:
            print('暂无历史分析记录。', file=sys.stderr)
        else:
            print(json.dumps(records, ensure_ascii=False, indent=2))
        return

    if not args.keyword:
        parser.print_help()
        return

    result = run_pipeline(
        keywords=args.keyword,
        date=args.date,
        skip_media=args.skip_media,
    )

    if args.compact:
        result.pop('full_texts', None)

    # JSON 输出到 stdout，供 Claude Code 读取
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
