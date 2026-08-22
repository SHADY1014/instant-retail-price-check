"""
商店城市数据库 - 本地缓存店铺名->城市映射，避免重复网络搜索

工作原理：
  1. detect_city() 调用时，先查本地数据库（shop_city.db）
  2. 命中 -> 直接返回城市，零网络请求
  3. 未命中 -> 走原有三层识别策略（店名提取/关键词/百度搜索）
  4. 识别成功 -> 自动写入数据库，下次直接命中

数据库结构（SQLite）：
  CREATE TABLE shop_city (
    shop_name TEXT PRIMARY KEY,   -- 店铺名（完整，含括号分店名）
    city      TEXT NOT NULL,      -- 城市名（如"南宁市"）
    source    TEXT,               -- 来源: "manual"=手动 / "name"=店名提取 / "keyword"=关键词 / "baidu"=百度搜索
    updated   TEXT                -- 更新时间（ISO格式）
  );

初始化时会从历史巡查表中自动导入已有映射。
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# =========================================================
# 数据库路径
# 打包模式（PyInstaller onefile）: %LOCALAPPDATA%\LQPriceCheck\data\shop_city.db
#   （EXE 内部 _MEIPASS 是只读临时目录，首次启动由 runtime_check.ensure_db 复制）
# 开发模式: 项目 data\shop_city.db
# =========================================================
import runtime_check
_DATA_DIR = os.path.dirname(runtime_check.get_db_path())
_DB_PATH = runtime_check.get_db_path()

# 线程锁（SQLite 连接不能跨线程共享，用锁保证线程安全）
_DB_LOCK = threading.Lock()


def _get_conn():
    """获取数据库连接（每次新建，用完即关）"""
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _init_db():
    """初始化数据库表"""
    with _DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS shop_city (
                    shop_name TEXT PRIMARY KEY,
                    city      TEXT NOT NULL,
                    source    TEXT,
                    updated   TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()


def lookup(shop_name):
    """查询店铺对应的城市

    Args:
        shop_name: 店铺名称

    Returns:
        str: 城市名（如"南宁市"），未找到返回 ""
    """
    if not shop_name:
        return ""
    _init_db()
    with _DB_LOCK:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT city FROM shop_city WHERE shop_name = ?", (shop_name,)
            ).fetchone()
            return row["city"] if row else ""
        except (sqlite3.Error, OSError) as e:
            logger.warning("DB lookup error: %s", e)
            return ""
        finally:
            conn.close()


def save(shop_name, city, source="baidu"):
    """保存店铺->城市映射到数据库

    Args:
        shop_name: 店铺名称
        city: 城市名
        source: 来源标识 ("manual"/"name"/"keyword"/"baidu")
    """
    if not shop_name or not city:
        return
    _init_db()
    now = datetime.now().isoformat(timespec="seconds")
    with _DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO shop_city (shop_name, city, source, updated)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(shop_name) DO UPDATE SET
                     city = excluded.city,
                     source = excluded.source,
                     updated = excluded.updated""",
                (shop_name, city, source, now),
            )
            conn.commit()
        except (sqlite3.Error, OSError) as e:
            logger.warning("DB error: %s", e)
        finally:
            conn.close()


def batch_save(mappings, source="manual"):
    """批量保存店铺->城市映射

    Args:
        mappings: dict {shop_name: city}
        source: 来源标识
    """
    if not mappings:
        return
    _init_db()
    now = datetime.now().isoformat(timespec="seconds")
    with _DB_LOCK:
        conn = _get_conn()
        try:
            for shop_name, city in mappings.items():
                if not shop_name or not city:
                    continue
                conn.execute(
                    """INSERT INTO shop_city (shop_name, city, source, updated)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(shop_name) DO UPDATE SET
                         city = excluded.city,
                         source = excluded.source,
                         updated = excluded.updated""",
                    (shop_name, city, source, now),
                )
            conn.commit()
        except (sqlite3.Error, OSError) as e:
            logger.warning("DB error: %s", e)
        finally:
            conn.close()


def batch_lookup(shop_names):
    """批量查询店铺城市

    Args:
        shop_names: list[str] 店铺名列表

    Returns:
        dict: {shop_name: city}（只包含命中的）
    """
    if not shop_names:
        return {}
    _init_db()
    result = {}
    with _DB_LOCK:
        conn = _get_conn()
        try:
            for name in shop_names:
                if not name:
                    continue
                row = conn.execute(
                    "SELECT city FROM shop_city WHERE shop_name = ?", (name,)
                ).fetchone()
                if row:
                    result[name] = row["city"]
        except (sqlite3.Error, OSError) as e:
            logger.warning("DB error: %s", e)
        finally:
            conn.close()
    return result


def get_all():
    """返回数据库中所有店铺->城市映射

    Returns:
        dict: {shop_name: city}
    """
    _init_db()
    result = {}
    with _DB_LOCK:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT shop_name, city FROM shop_city ORDER BY city, shop_name"
            ).fetchall()
            for row in rows:
                result[row["shop_name"]] = row["city"]
        except (sqlite3.Error, OSError) as e:
            logger.warning("DB error: %s", e)
        finally:
            conn.close()
    return result


def get_stats():
    """返回数据库统计信息

    Returns:
        dict: {total: 总数, by_city: {城市: 数量}, by_source: {来源: 数量}}
    """
    _init_db()
    stats = {"total": 0, "by_city": {}, "by_source": {}}
    with _DB_LOCK:
        conn = _get_conn()
        try:
            # 总数
            row = conn.execute("SELECT COUNT(*) as cnt FROM shop_city").fetchone()
            stats["total"] = row["cnt"] if row else 0

            # 按城市统计
            rows = conn.execute(
                "SELECT city, COUNT(*) as cnt FROM shop_city GROUP BY city ORDER BY cnt DESC"
            ).fetchall()
            for row in rows:
                stats["by_city"][row["city"]] = row["cnt"]

            # 按来源统计
            rows = conn.execute(
                "SELECT source, COUNT(*) as cnt FROM shop_city GROUP BY source ORDER BY cnt DESC"
            ).fetchall()
            for row in rows:
                src = row["source"] or "unknown"
                stats["by_source"][src] = row["cnt"]
        except (sqlite3.Error, OSError) as e:
            logger.warning("DB error: %s", e)
        finally:
            conn.close()
    return stats


def delete(shop_name):
    """删除指定店铺的记录"""
    if not shop_name:
        return
    _init_db()
    with _DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM shop_city WHERE shop_name = ?", (shop_name,))
            conn.commit()
        except (sqlite3.Error, OSError) as e:
            logger.warning("DB error: %s", e)
        finally:
            conn.close()


if __name__ == "__main__":
    # 纯数据库统计查看（数据已固化，不从文件夹读取）
    print("=== 商店城市数据库 ===\n")

    stats = get_stats()
    print(f"数据库总计: {stats['total']} 条")
    print(f"\n按城市分布:")
    for city, cnt in stats["by_city"].items():
        print(f"  {city}: {cnt} 家")
    print(f"\n按来源分布:")
    for src, cnt in stats["by_source"].items():
        print(f"  {src}: {cnt} 条")
