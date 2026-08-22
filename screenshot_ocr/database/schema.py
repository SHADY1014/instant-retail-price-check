"""
店铺/城市智能匹配数据库 — Schema 定义与初始化

数据库独立于 OCR 引擎与解析逻辑（data/ocr_learning.db），
即使未来更换 OCR 引擎也不受影响。

表结构：
  shops          店铺标准表（canonical_name 唯一）
  shop_aliases   别名表（OCR 变体归并到同一 shop_id）
  city_matches   城市匹配表（一店可多城市，冲突保留多条 status=conflict）
  corrections    人工修正记录（审计追踪）
  import_batches 投喂批次（file_hash 唯一 = 幂等键）
  match_history  匹配历史（L1-L7 分级记录）
  meta           版本/统计信息
  network_city_requests / network_city_candidates  联网城市识别审计
"""

import logging
import os
import sqlite3

import runtime_paths

logger = logging.getLogger(__name__)

# 数据库路径：项目 data/ 目录下，与 OCR 代码同数据目录
# 可用环境变量 OCR_LEARNING_DB 覆盖（测试隔离用）
_DATA_DIR = runtime_paths.data_dir()
DB_PATH = runtime_paths.learning_db_path()

SCHEMA_VERSION = "2"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS shops (
    shop_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name     TEXT UNIQUE NOT NULL,
    city               TEXT,
    province           TEXT,
    status             TEXT NOT NULL DEFAULT 'active',   -- active / conflict / merged
    confidence         REAL NOT NULL DEFAULT 0,
    source             TEXT DEFAULT 'ocr',
    use_count          INTEGER NOT NULL DEFAULT 0,
    correct_count      INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    last_confirmed_at  TEXT
);

CREATE TABLE IF NOT EXISTS shop_aliases (
    alias_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id           INTEGER NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
    alias             TEXT UNIQUE NOT NULL,
    normalized_alias  TEXT NOT NULL,
    source            TEXT DEFAULT 'ocr',
    confidence        REAL NOT NULL DEFAULT 0,
    use_count         INTEGER NOT NULL DEFAULT 0,
    correct_count     INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_aliases_norm ON shop_aliases(normalized_alias);
CREATE INDEX IF NOT EXISTS idx_aliases_shop ON shop_aliases(shop_id);

CREATE TABLE IF NOT EXISTS city_matches (
    match_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id           INTEGER NOT NULL REFERENCES shops(shop_id) ON DELETE CASCADE,
    city              TEXT NOT NULL,
    province          TEXT,
    status            TEXT NOT NULL DEFAULT 'confirmed',  -- confirmed / conflict
    source            TEXT DEFAULT 'ocr',
    confidence        REAL NOT NULL DEFAULT 0,
    use_count         INTEGER NOT NULL DEFAULT 0,
    correct_count     INTEGER NOT NULL DEFAULT 0,
    last_confirmed_at TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_city_shop ON city_matches(shop_id);

CREATE TABLE IF NOT EXISTS corrections (
    correction_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id          INTEGER,
    ocr_shop_name     TEXT NOT NULL,
    corrected_shop_name TEXT NOT NULL,
    city              TEXT,
    province          TEXT,
    shop_id           INTEGER,
    operator          TEXT DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_corrections_ocr ON corrections(ocr_shop_name);

CREATE TABLE IF NOT EXISTS import_batches (
    batch_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    filename      TEXT NOT NULL,
    file_hash     TEXT UNIQUE NOT NULL,     -- 幂等键：同一文件重复投喂直接跳过
    import_time   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    total_rows    INTEGER NOT NULL DEFAULT 0,
    new_shops     INTEGER NOT NULL DEFAULT 0,
    new_aliases   INTEGER NOT NULL DEFAULT 0,
    updated_shops INTEGER NOT NULL DEFAULT 0,
    updated_cities INTEGER NOT NULL DEFAULT 0,
    conflicts     INTEGER NOT NULL DEFAULT 0,
    ignored_rows  INTEGER NOT NULL DEFAULT 0,
    operator      TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'ok'   -- ok / duplicate
);

CREATE TABLE IF NOT EXISTS match_history (
    match_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ocr_text      TEXT NOT NULL,
    normalized_text TEXT,
    matched_shop_id INTEGER,
    match_level   INTEGER NOT NULL,           -- 1-7
    matched_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    source_image  TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_shop ON match_history(matched_shop_id);

CREATE TABLE IF NOT EXISTS network_city_requests (
    request_id       TEXT PRIMARY KEY,
    authorized_at    TEXT NOT NULL,
    requested_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    allowed_cities   TEXT NOT NULL,
    shop_count       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS network_city_candidates (
    candidate_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id       TEXT NOT NULL REFERENCES network_city_requests(request_id),
    shop_name        TEXT NOT NULL,
    candidate_city   TEXT,
    source           TEXT NOT NULL DEFAULT 'baidu_map',
    final_city       TEXT,
    decided_at       TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_network_candidates_request
    ON network_city_candidates(request_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _get_conn():
    """建立数据库连接（短连接 + 行工厂）"""
    os.makedirs(_DATA_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """初始化数据库表与版本信息（幂等，可重复调用）"""
    runtime_paths.ensure_learning_db_seed()
    conn = _get_conn()
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (SCHEMA_VERSION,),
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.warning("learning DB init error: %s", e)
        raise
    finally:
        conn.close()


def get_meta(key, default=None):
    conn = _get_conn()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_meta(key, value):
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()
