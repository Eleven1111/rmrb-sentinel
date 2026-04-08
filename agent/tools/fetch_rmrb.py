"""
Tool: 人民日报数据采集
封装 scripts/rmrb_fetch.py，返回结构化 summary dict。
"""

import sys
import os

# 让 scripts/ 下的模块可导入
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'scripts')
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import rmrb_fetch


def fetch_rmrb(keywords: list[str], date: str | None = None) -> dict:
    """
    采集人民日报最新一期（或指定日期），按关键词过滤，返回结构化 summary。

    参数：
      keywords: 关键词列表，如 ["新能源", "光伏"]
      date: 可选，格式 YYYYMMDD，默认自动获取最新一期

    返回：
      summary dict，包含 step0_narrative / step1_agenda / step6_intensity /
      ministry_signals / articles / full_texts 等字段
    """
    output_dir = os.path.expanduser('~/.rmrb_canary/data')
    summary = rmrb_fetch.fetch(
        date_str=date,
        keywords=keywords,
        output_dir=output_dir,
    )
    return summary
