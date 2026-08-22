"""
店铺/城市智能匹配数据库 — Repository（全部 SQL 操作）

短连接 + 全局锁（与 GUI 多线程兼容），每个方法独立事务。
"""

import logging
import json
import sqlite3
import threading
import uuid
from datetime import datetime

from .schema import DB_PATH, init_db, get_meta, set_meta

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_initialized = False


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_init():
    global _initialized
    if not _initialized:
        init_db()
        _initialized = True


# =========================================================
# shops
# =========================================================

def upsert_shop(canonical_name, city="", province="", source="ocr",
                confidence=None, status=None):
    """插入或更新标准店铺（canonical_name 唯一）。返回 shop_id。

    已存在时：更新城市（仅当传入且现有为空或传入来源为人工/投喂时）、
    use_count/correct_count 由调用方负责。
    """
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            # 注意：此处不过滤 source —— 人工投喂需要能"收编升级"旧库迁移的同名店铺
            row = conn.execute(
                "SELECT * FROM shops WHERE canonical_name = ?", (canonical_name,)
            ).fetchone()
            if row:
                shop_id = row["shop_id"]
                updates = ["updated_at = datetime('now','localtime')"]
                params = []
                # 人工/投喂来源可以修正城市
                if city and source in ("manual", "import", "migrate"):
                    updates.append("city = ?")
                    params.append(city)
                if province and source in ("manual", "import", "migrate"):
                    updates.append("province = ?")
                    params.append(province)
                if confidence is not None:
                    updates.append("confidence = ?")
                    params.append(confidence)
                if status:
                    updates.append("status = ?")
                    params.append(status)
                if source in ("manual", "import", "migrate"):
                    updates.append("source = ?")
                    params.append(source)
                if not params:
                    conn.execute("UPDATE shops SET updated_at = datetime('now','localtime') WHERE shop_id = ?", (shop_id,))
                else:
                    params.append(shop_id)
                    conn.execute(
                        f"UPDATE shops SET {', '.join(updates)} WHERE shop_id = ?", params
                    )
                conn.commit()
                return shop_id
            else:
                cur = conn.execute(
                    "INSERT INTO shops(canonical_name, city, province, source, confidence, status) "
                    "VALUES(?, ?, ?, ?, COALESCE(?, 0), COALESCE(?, 'active'))",
                    (canonical_name, city, province, source, confidence, status),
                )
                conn.commit()
                return cur.lastrowid
        except sqlite3.Error as e:
            logger.warning("upsert_shop error: %s", e)
            raise
        finally:
            conn.close()


def get_shop(shop_id):
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM shops WHERE shop_id = ?", (shop_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def get_shop_by_canonical(canonical_name):
    """L1: canonical_name 精确匹配（包含迁移的历史本地库）。"""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT * FROM shops WHERE canonical_name = ? "
                "AND source IN ('manual','import','migrate')", (canonical_name,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def get_shop_any_source(canonical_name):
    """按 canonical_name 查询（不限来源），用于投喂时判断是否收编旧库店铺"""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT * FROM shops WHERE canonical_name = ?", (canonical_name,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def list_shops(limit=1000, offset=0):
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT * FROM shops ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def list_invalid_imported_shops():
    """列出可安全清理的异常投喂或迁移店铺，供界面先预览再确认。"""
    from .importer import is_invalid_shop_name

    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT s.* FROM shops s WHERE s.source IN ('import', 'migrate') OR EXISTS ("
                "SELECT 1 FROM corrections c WHERE c.shop_id = s.shop_id "
                "AND c.batch_id IS NOT NULL) ORDER BY s.shop_id"
            ).fetchall()
            return [dict(row) for row in rows if is_invalid_shop_name(row["canonical_name"])]
        finally:
            conn.close()


def delete_invalid_imported_shops(shop_ids):
    """删除用户确认的异常投喂或迁移店铺及其关联学习记录。"""
    candidates = {row["shop_id"]: row for row in list_invalid_imported_shops()}
    valid_ids = [shop_id for shop_id in shop_ids if shop_id in candidates]
    if not valid_ids:
        return []

    placeholders = ", ".join("?" for _ in valid_ids)
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            conn.execute(f"DELETE FROM corrections WHERE shop_id IN ({placeholders})", valid_ids)
            conn.execute(f"DELETE FROM shops WHERE shop_id IN ({placeholders})", valid_ids)
            conn.commit()
            return [candidates[shop_id]["canonical_name"] for shop_id in valid_ids]
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()


def increment_shop_usage(shop_id, correct=False):
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            if correct:
                conn.execute(
                    "UPDATE shops SET use_count = use_count + 1, correct_count = correct_count + 1, "
                    "updated_at = datetime('now','localtime') WHERE shop_id = ?", (shop_id,))
            else:
                conn.execute(
                    "UPDATE shops SET use_count = use_count + 1, "
                    "updated_at = datetime('now','localtime') WHERE shop_id = ?", (shop_id,))
            conn.commit()
        finally:
            conn.close()


# =========================================================
# shop_aliases
# =========================================================

def add_alias(shop_id, alias, normalized_alias, source="ocr", confidence=0.0):
    """新增别名（alias 唯一）。已存在则仅强化 use_count 与更新时间。"""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT alias_id, shop_id FROM shop_aliases WHERE alias = ?", (alias,)
            ).fetchone()
            if row:
                if row["shop_id"] != shop_id:
                    conn.execute("UPDATE shops SET status = 'conflict', updated_at = datetime('now','localtime') WHERE shop_id IN (?, ?)", (row["shop_id"], shop_id))
                    conn.commit()
                    return row["alias_id"]
                conn.execute(
                    "UPDATE shop_aliases SET use_count = use_count + 1, "
                    "updated_at = datetime('now','localtime') WHERE alias_id = ?",
                    (row["alias_id"],),
                )
                conn.commit()
                return row["alias_id"]
            cur = conn.execute(
                "INSERT INTO shop_aliases(shop_id, alias, normalized_alias, source, confidence) "
                "VALUES(?, ?, ?, ?, ?)",
                (shop_id, alias, normalized_alias, source, confidence),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.Error as e:
            logger.warning("add_alias error: %s", e)
            raise
        finally:
            conn.close()


def get_shop_by_alias(alias):
    """L2: alias 精确匹配"""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT s.*, a.alias_id, a.normalized_alias FROM shop_aliases a "
                "JOIN shops s ON s.shop_id = a.shop_id "
                "WHERE a.alias = ? AND s.source IN ('manual','import','migrate')",
                (alias,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def get_shop_by_normalized(normalized_alias):
    """L3: normalized_alias 精确匹配"""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT s.* FROM shop_aliases a "
                "JOIN shops s ON s.shop_id = a.shop_id "
                "WHERE a.normalized_alias = ? AND s.source IN ('manual','import','migrate')",
                (normalized_alias,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def get_shop_by_alias_like(normalized_alias):
    """L5 候选：normalized_alias 相同或包含（返回多个候选）"""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT s.* FROM shop_aliases a "
                "JOIN shops s ON s.shop_id = a.shop_id "
                "WHERE s.source IN ('manual','import') AND "
                "(a.normalized_alias = ? OR a.normalized_alias LIKE ? "
                "OR ? LIKE '%' || a.normalized_alias || '%')",
                (normalized_alias, "%" + normalized_alias + "%", normalized_alias),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_shop_by_correction_ocr(ocr_shop_name):
    """L4: 历史人工修正记录（ocr_shop_name -> shop_id），命中返回店铺"""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT s.* FROM corrections c "
                "JOIN shops s ON s.shop_id = c.shop_id "
                "WHERE c.ocr_shop_name = ? AND c.shop_id IS NOT NULL "
                "AND s.source IN ('manual','import','migrate') "
                "ORDER BY c.correction_id DESC LIMIT 1",
                (ocr_shop_name,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def get_shop_aliases_flat(shop_id):
    """返回某店铺全部别名文本（含 canonical 名，用于模糊匹配评分）"""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT alias FROM shop_aliases WHERE shop_id = ?", (shop_id,)
            ).fetchall()
            return [r["alias"] for r in rows]
        finally:
            conn.close()


def get_all_alias_norm_pairs():
    """人工确认的 (shop_id, canonical_name, normalized_alias) 对，用于 L5 模糊候选。

    迁移库只允许经 L1-L4 精确级匹配使用，不能扩大为模糊候选。
    """
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT a.shop_id, s.canonical_name, a.normalized_alias "
                "FROM shop_aliases a JOIN shops s ON s.shop_id = a.shop_id "
                "WHERE s.source IN ('manual','import')"
            ).fetchall()
            return [(r["shop_id"], r["canonical_name"], r["normalized_alias"]) for r in rows]
        finally:
            conn.close()


def list_aliases(limit=2000):
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT a.*, s.canonical_name FROM shop_aliases a "
                "JOIN shops s ON s.shop_id = a.shop_id "
                "ORDER BY a.updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# =========================================================
# city_matches
# =========================================================

def add_city_match(shop_id, city, province="", source="ocr", confidence=0.0):
    """记录店铺->城市关系。同店同城市：强化计数；不同城市：标记 CONFLICT。

    Returns:
        (match_id, conflict: bool)
    """
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT * FROM city_matches WHERE shop_id = ? AND city = ?",
                (shop_id, city),
            ).fetchone()
            if row:
                # 人工来源（manual/import）确认时，将旧库迁移记录升级为人工确认
                new_source = row["source"]
                if source in ("manual", "import") and row["source"] not in ("manual", "import"):
                    new_source = source
                conn.execute(
                    "UPDATE city_matches SET use_count = use_count + 1, confidence = MAX(confidence, ?), "
                    "source = ?, last_confirmed_at = datetime('now','localtime'), "
                    "updated_at = datetime('now','localtime') WHERE match_id = ?",
                    (confidence, new_source, row["match_id"]),
                )
                conn.commit()
                return row["match_id"], False

            # 检查该店是否已有其他城市（确认过的）
            others = conn.execute(
                "SELECT match_id FROM city_matches WHERE shop_id = ? AND city != ? AND status = 'confirmed'",
                (shop_id, city),
            ).fetchone()
            conflict = others is not None
            status = "conflict" if conflict else "confirmed"
            cur = conn.execute(
                "INSERT INTO city_matches(shop_id, city, province, status, source, confidence, "
                "last_confirmed_at) VALUES(?, ?, ?, ?, ?, ?, datetime('now','localtime'))",
                (shop_id, city, province, status, source, confidence),
            )
            if conflict:
                # 店铺标记冲突
                conn.execute(
                    "UPDATE shops SET status = 'conflict', updated_at = datetime('now','localtime') "
                    "WHERE shop_id = ?", (shop_id,))
            conn.commit()
            return cur.lastrowid, conflict
        except sqlite3.Error as e:
            logger.warning("add_city_match error: %s", e)
            raise
        finally:
            conn.close()


def get_city_matches(shop_id):
    """返回店铺所有城市匹配（confirmed 优先）"""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT * FROM city_matches WHERE shop_id = ? "
                "ORDER BY status='confirmed' DESC, correct_count DESC, use_count DESC",
                (shop_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_conflicts():
    """全部冲突记录（含店铺名）"""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT cm.*, s.canonical_name FROM city_matches cm "
                "JOIN shops s ON s.shop_id = cm.shop_id "
                "WHERE cm.status = 'conflict' OR s.status = 'conflict' "
                "ORDER BY cm.updated_at DESC",
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def resolve_conflict(shop_id, chosen_city, operator="gui"):
    """人工裁决冲突：选定城市为 confirmed，其余降级（保留记录）"""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            # 选定城市 -> confirmed + 强化
            conn.execute(
                "UPDATE city_matches SET status = 'confirmed', correct_count = correct_count + 1, "
                "confidence = MIN(1.0, confidence + 0.1), last_confirmed_at = datetime('now','localtime'), "
                "updated_at = datetime('now','localtime') WHERE shop_id = ? AND city = ?",
                (shop_id, chosen_city),
            )
            # 其他城市 -> 非 conflict（历史保留）
            conn.execute(
                "UPDATE city_matches SET status = 'rejected', updated_at = datetime('now','localtime') "
                "WHERE shop_id = ? AND city != ? AND status = 'conflict'",
                (shop_id, chosen_city),
            )
            # 店铺状态与主城市
            conn.execute(
                "UPDATE shops SET status = 'active', city = ?, "
                "updated_at = datetime('now','localtime'), last_confirmed_at = datetime('now','localtime') "
                "WHERE shop_id = ?",
                (chosen_city, shop_id),
            )
            conn.commit()
        finally:
            conn.close()


# =========================================================
# corrections（人工修正记录）
# =========================================================

def add_correction(ocr_shop_name, corrected_shop_name, city="", province="",
                   shop_id=None, batch_id=None, operator=""):
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            cur = conn.execute(
                "INSERT INTO corrections(ocr_shop_name, corrected_shop_name, city, province, "
                "shop_id, batch_id, operator) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (ocr_shop_name, corrected_shop_name, city, province, shop_id, batch_id, operator),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def list_corrections(limit=1000):
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT * FROM corrections ORDER BY correction_id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# =========================================================
# import_batches（投喂批次）
# =========================================================

def get_batch_by_hash(file_hash):
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT * FROM import_batches WHERE file_hash = ?", (file_hash,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def add_batch(filename, file_hash, total_rows=0, new_shops=0, new_aliases=0,
              updated_shops=0, updated_cities=0, conflicts=0, ignored_rows=0,
              operator="", status="ok"):
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            cur = conn.execute(
                "INSERT INTO import_batches(filename, file_hash, total_rows, new_shops, new_aliases, "
                "updated_shops, updated_cities, conflicts, ignored_rows, operator, status) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (filename, file_hash, total_rows, new_shops, new_aliases,
                 updated_shops, updated_cities, conflicts, ignored_rows, operator, status),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def update_batch(batch_id, **values):
    _ensure_init()
    allowed = {"total_rows", "new_shops", "new_aliases", "updated_shops", "updated_cities", "conflicts", "ignored_rows", "operator", "status"}
    updates = [(key, value) for key, value in values.items() if key in allowed]
    if not updates:
        return
    with _LOCK:
        conn = _conn()
        try:
            clause = ", ".join(f"{key} = ?" for key, _ in updates)
            conn.execute(f"UPDATE import_batches SET {clause} WHERE batch_id = ?", [value for _, value in updates] + [batch_id])
            conn.commit()
        finally:
            conn.close()


def list_batches(limit=200):
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT * FROM import_batches ORDER BY batch_id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# =========================================================
# match_history
# =========================================================

def record_match(ocr_text, normalized_text, matched_shop_id, level, source_image=""):
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO match_history(ocr_text, normalized_text, matched_shop_id, "
                "match_level, source_image) VALUES(?, ?, ?, ?, ?)",
                (ocr_text, normalized_text, matched_shop_id, level, source_image),
            )
            conn.commit()
        finally:
            conn.close()


# =========================================================
# 联网城市识别审计
# =========================================================

def record_network_city_consent():
    """Persist the time at which the user authorized name-only map searches."""
    _ensure_init()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_meta("network_city_consent_at", timestamp)
    return timestamp


def create_network_city_request(authorized_at, allowed_cities, shop_names):
    """Create an auditable, city-whitelisted map-search request."""
    _ensure_init()
    request_id = uuid.uuid4().hex
    cities_json = json.dumps(sorted(set(allowed_cities)), ensure_ascii=False)
    with _LOCK:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO network_city_requests("
                "request_id, authorized_at, allowed_cities, shop_count) "
                "VALUES(?, ?, ?, ?)",
                (request_id, authorized_at, cities_json, len(shop_names)),
            )
            conn.commit()
            return request_id
        finally:
            conn.close()


def record_network_city_candidates(request_id, candidates, source="baidu_map"):
    """Record every requested shop and its returned candidate, including blanks."""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            conn.executemany(
                "INSERT INTO network_city_candidates("
                "request_id, shop_name, candidate_city, source) VALUES(?, ?, ?, ?)",
                [(request_id, shop, city or "", source)
                 for shop, city in candidates.items()],
            )
            conn.commit()
        finally:
            conn.close()


def record_network_city_decisions(request_id, decisions):
    """Record the operator-confirmed city (or explicit rejection) for candidates."""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            for shop_name, city in decisions.items():
                conn.execute(
                    "UPDATE network_city_candidates SET final_city = ?, "
                    "decided_at = datetime('now','localtime') "
                    "WHERE candidate_id = (SELECT candidate_id FROM "
                    "network_city_candidates WHERE request_id = ? "
                    "AND shop_name = ? ORDER BY candidate_id DESC LIMIT 1)",
                    (city or "", request_id, shop_name),
                )
            conn.commit()
        finally:
            conn.close()


def list_network_city_requests(limit=200):
    """Return requests with candidate and confirmed city decisions for audit."""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT r.request_id, r.authorized_at, r.requested_at, "
                "r.allowed_cities, r.shop_count, c.shop_name, "
                "c.candidate_city, c.final_city, c.decided_at "
                "FROM network_city_requests r JOIN network_city_candidates c "
                "ON c.request_id = r.request_id "
                "ORDER BY r.requested_at DESC, c.candidate_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


# =========================================================
# 统计
# =========================================================

def get_stats():
    """数据库统计：店铺数/别名数/城市匹配数/人工确认数/冲突数/投喂次数"""
    _ensure_init()
    with _LOCK:
        conn = _conn()
        try:
            stats = {
                "shops": conn.execute("SELECT COUNT(*) c FROM shops").fetchone()["c"],
                "aliases": conn.execute("SELECT COUNT(*) c FROM shop_aliases").fetchone()["c"],
                "city_matches": conn.execute("SELECT COUNT(*) c FROM city_matches").fetchone()["c"],
                "corrections": conn.execute("SELECT COUNT(*) c FROM corrections").fetchone()["c"],
                "conflicts": conn.execute(
                    "SELECT COUNT(DISTINCT shop_id) c FROM city_matches WHERE status='conflict'"
                ).fetchone()["c"],
                "batches": conn.execute("SELECT COUNT(*) c FROM import_batches").fetchone()["c"],
                "matches": conn.execute("SELECT COUNT(*) c FROM match_history").fetchone()["c"],
            }
            return stats
        finally:
            conn.close()
