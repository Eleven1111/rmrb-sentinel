"""
SQLite 历史存储层 — rmrb-canary agent

三张表：
  analyses           — 每次分析的摘要（叙事框架、话语强度、部委协同、风险灯）
  intensity_snapshots — 每次分析的七级话语强度分布（用于趋势对比）
  article_records    — 每篇文章的核心字段（供回溯验证）

数据库位置：~/.rmrb_canary/history.db（跨会话持久化）
"""

import sqlite3
import json
import os
import datetime

DB_DIR = os.path.expanduser('~/.rmrb_canary')
DB_PATH = os.path.join(DB_DIR, 'history.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    keywords TEXT NOT NULL,
    primary_frame TEXT,
    secondary_frame TEXT,
    max_intensity INTEGER,
    intensity_triggers TEXT,
    ministry_level TEXT,
    ministry_list TEXT,
    risk_signal TEXT,
    total_articles INTEGER,
    report_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intensity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    count INTEGER NOT NULL,
    pct REAL NOT NULL,
    triggers TEXT,
    FOREIGN KEY (analysis_id) REFERENCES analyses(id)
);

CREATE TABLE IF NOT EXISTS article_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL,
    date TEXT,
    page_no INTEGER,
    article_no INTEGER,
    title TEXT,
    column_name TEXT,
    position TEXT,
    agenda_score INTEGER,
    word_count INTEGER,
    FOREIGN KEY (analysis_id) REFERENCES analyses(id)
);

CREATE INDEX IF NOT EXISTS idx_analyses_date ON analyses(date);
CREATE INDEX IF NOT EXISTS idx_analyses_keywords ON analyses(keywords);
"""


def get_conn():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def save_analysis(summary, risk_signal='🟢'):
    """
    将一次完整分析写入数据库。
    返回 analysis_id。
    """
    conn = get_conn()
    now = datetime.datetime.now().isoformat()

    narrative = summary.get('step0_narrative', {})
    intensity = summary.get('step6_intensity', {})
    ministry = summary.get('ministry_signals', {})

    cur = conn.execute(
        """INSERT INTO analyses
           (date, keywords, primary_frame, secondary_frame,
            max_intensity, intensity_triggers,
            ministry_level, ministry_list,
            risk_signal, total_articles, report_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            summary.get('date', ''),
            json.dumps(summary.get('keywords_filter', []), ensure_ascii=False),
            narrative.get('primary_frame', ''),
            narrative.get('secondary_frame', ''),
            intensity.get('max_level', 0),
            json.dumps(intensity.get('max_level_triggers', []), ensure_ascii=False),
            ministry.get('coordination_level', ''),
            json.dumps(ministry.get('ministries_found', []), ensure_ascii=False),
            risk_signal,
            summary.get('total_articles', 0),
            json.dumps(summary, ensure_ascii=False, default=str),
            now,
        )
    )
    analysis_id = cur.lastrowid

    # 写入强度快照
    dist = intensity.get('distribution', {})
    for level_key, data in dist.items():
        level_num = int(level_key.replace('level_', ''))
        conn.execute(
            """INSERT INTO intensity_snapshots
               (analysis_id, level, count, pct, triggers)
               VALUES (?, ?, ?, ?, ?)""",
            (
                analysis_id,
                level_num,
                data.get('count', 0),
                data.get('pct', 0.0),
                json.dumps(data.get('triggers', []), ensure_ascii=False),
            )
        )

    # 写入文章记录
    for art in summary.get('articles', []):
        conn.execute(
            """INSERT INTO article_records
               (analysis_id, date, page_no, article_no, title,
                column_name, position, agenda_score, word_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                analysis_id,
                art.get('date', ''),
                art.get('page_no', 0),
                art.get('article_no', 0),
                art.get('title', ''),
                art.get('column', ''),
                art.get('position', ''),
                art.get('agenda_score', 0),
                art.get('word_count', 0),
            )
        )

    conn.commit()
    conn.close()
    return analysis_id


def get_previous_analysis(keywords, current_date=None):
    """
    查找同一关键词组的上一次分析结果。
    返回 dict 或 None。
    """
    conn = get_conn()
    kw_json = json.dumps(sorted(keywords), ensure_ascii=False)

    query = """
        SELECT * FROM analyses
        WHERE keywords = ?
    """
    params = [kw_json]
    if current_date:
        query += " AND date < ?"
        params.append(current_date)
    query += " ORDER BY date DESC LIMIT 1"

    row = conn.execute(query, params).fetchone()
    if not row:
        # 回退：尝试任意包含这些关键词的分析（参数化查询）
        like_clauses = ' AND '.join('keywords LIKE ?' for _ in keywords)
        like_params = [f'%{kw}%' for kw in keywords]
        fallback_q = f"SELECT * FROM analyses WHERE {like_clauses}"
        if current_date:
            fallback_q += " AND date < ?"
            like_params.append(current_date)
        fallback_q += " ORDER BY date DESC LIMIT 1"
        row = conn.execute(fallback_q, like_params).fetchone()

    if not row:
        conn.close()
        return None

    # 读取强度快照
    snapshots = conn.execute(
        "SELECT * FROM intensity_snapshots WHERE analysis_id = ? ORDER BY level",
        (row['id'],)
    ).fetchall()

    conn.close()
    return {
        'id': row['id'],
        'date': row['date'],
        'keywords': json.loads(row['keywords']),
        'primary_frame': row['primary_frame'],
        'secondary_frame': row['secondary_frame'],
        'max_intensity': row['max_intensity'],
        'intensity_triggers': json.loads(row['intensity_triggers'] or '[]'),
        'ministry_level': row['ministry_level'],
        'ministry_list': json.loads(row['ministry_list'] or '[]'),
        'risk_signal': row['risk_signal'],
        'total_articles': row['total_articles'],
        'intensity_distribution': {
            s['level']: {'count': s['count'], 'pct': s['pct']}
            for s in snapshots
        },
    }


def compare_with_previous(current_summary, keywords):
    """
    对比当前分析与上次分析，输出趋势变化。
    返回 dict（无上期数据时返回 None）。
    """
    prev = get_previous_analysis(keywords, current_summary.get('date'))
    if not prev:
        return None

    curr_narrative = current_summary.get('step0_narrative', {})
    curr_intensity = current_summary.get('step6_intensity', {})
    curr_ministry = current_summary.get('ministry_signals', {})

    # 话语强度变化
    curr_max = curr_intensity.get('max_level', 0)
    prev_max = prev.get('max_intensity', 0)
    intensity_delta = curr_max - prev_max

    # 叙事框架漂移
    curr_frame = curr_narrative.get('primary_frame', '')
    prev_frame = prev.get('primary_frame', '')
    frame_drift = curr_frame != prev_frame

    # 部委协同升级
    curr_ml = curr_ministry.get('coordination_level', 'L0')
    prev_ml = prev.get('ministry_level', 'L0')
    ministry_escalated = curr_ml > prev_ml

    return {
        'previous_date': prev['date'],
        'previous_id': prev['id'],
        'intensity_change': f"{'+'if intensity_delta>0 else ''}{intensity_delta}级（{prev_max}级→{curr_max}级）",
        'intensity_direction': '上升' if intensity_delta > 0 else ('下降' if intensity_delta < 0 else '持平'),
        'narrative_drift': f"从'{prev_frame}'漂移到'{curr_frame}'" if frame_drift else '框架稳定',
        'narrative_drifted': frame_drift,
        'ministry_change': f"{prev_ml}→{curr_ml}",
        'ministry_escalated': ministry_escalated,
        'previous_risk': prev['risk_signal'],
        'previous_articles': prev['total_articles'],
        'trend_warning': (
            intensity_delta >= 2 or frame_drift or ministry_escalated
        ),
    }


def list_analyses(limit=20):
    """列出最近的分析历史。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, date, keywords, primary_frame, max_intensity, "
        "ministry_level, risk_signal, total_articles, created_at "
        "FROM analyses ORDER BY date DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
