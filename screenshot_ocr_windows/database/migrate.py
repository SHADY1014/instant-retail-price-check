"""
数据迁移：shop_city.db（旧单表） -> ocr_learning.db（学习库）

幂等：meta 记录 migrated_at，重复执行跳过。
迁移后旧库保持只读兼容，新数据写入学习库。
"""

import logging
import os
import sqlite3

from . import repository
from .importer import is_invalid_shop_name
from .schema import DB_PATH, get_meta, set_meta
from .matcher import normalize

logger = logging.getLogger(__name__)

MIGRATE_MARK = "migrated_from_shop_city"


def migrate_shop_city_db(old_db_path=None):
    """把旧 shop_city.db 全部记录迁移到学习库。

    Args:
        old_db_path: 旧库路径，默认 data/shop_city.db

    Returns:
        dict: {migrated: bool, shops, aliases, city_matches, ignored_rows, source}
    """
    if old_db_path is None:
        old_db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "shop_city.db",
        )

    if get_meta(MIGRATE_MARK):
        return {"migrated": False, "shops": 0, "aliases": 0,
                "city_matches": 0, "ignored_rows": 0,
                "source": "already_migrated"}

    if not os.path.exists(old_db_path):
        return {"migrated": False, "shops": 0, "aliases": 0,
                "city_matches": 0, "ignored_rows": 0, "source": "no_old_db"}

    conn = sqlite3.connect(old_db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT shop_name, city, source FROM shop_city").fetchall()
    finally:
        conn.close()

    shops = aliases = matches = ignored_rows = 0
    for row in rows:
        shop_name = (row["shop_name"] or "").strip()
        city = (row["city"] or "").strip()
        if not shop_name or is_invalid_shop_name(shop_name):
            ignored_rows += 1
            logger.warning(
                "skip invalid legacy shop name during migration: %s", shop_name)
            continue
        # 店铺 + 别名 + 城市（迁移视为历史人工确认）
        shop_id = repository.upsert_shop(shop_name, city, source="migrate", confidence=0.9)
        repository.add_alias(shop_id, shop_name, normalize(shop_name), source="migrate", confidence=0.9)
        repository.add_city_match(shop_id, city, source="migrate", confidence=0.9)
        shops += 1
        aliases += 1
        matches += 1

    set_meta(MIGRATE_MARK, "done")
    return {"migrated": True, "shops": shops, "aliases": aliases,
            "city_matches": matches, "ignored_rows": ignored_rows,
            "source": "migrated"}
