"""
店铺/城市智能匹配数据库 — 统一对外入口

用法：
    from database import get_shop_city, learn_correction, import_excel

    # 识别：OCR 店名 -> 标准店名 + 城市（L1-L5；无命中返回 None 走原规则）
    result = get_shop_city("百佳汇生活超市（玉州店）")
    # -> {"shop_name": "百佳汇生活超市（玉州店）", "city": "三亚市", "level": 2, "conflict": False}

    # 学习：人工修正结果回灌
    learn_correction("广卅天河XX超市", "广州天河XX超市", "广州市")

    # 投喂：完整 Excel 表格
    import_excel("人工修正后的巡查表.xlsx")
"""

import logging

from . import importer, learner, matcher, migrate, repository
from .schema import DB_PATH, init_db

__all__ = [
    "DB_PATH", "init_db",
    "get_shop_city", "batch_get_shop_city",
    "learn_correction", "resolve_conflict",
    "import_excel", "migrate_shop_city_db",
    "get_stats", "get_conflicts", "get_city_matches",
    "list_shops", "list_aliases",
    "list_corrections", "list_batches",
    "record_network_city_consent", "create_network_city_request",
    "record_network_city_candidates", "record_network_city_decisions",
    "list_network_city_requests",
]

logger = logging.getLogger(__name__)

# 首次导入自动初始化 + 迁移旧库（幂等）
init_db()
try:
    migrate.migrate_shop_city_db()
except Exception as e:
    logger.warning("learning DB migrate failed: %s", e)


def get_shop_city(ocr_shop_name):
    """OCR 店名 -> 标准店铺名 + 城市（L1-L5 匹配）。

    Returns:
        dict | None: 命中时 {"shop_name": canonical, "city": str, "level": int,
                             "conflict": bool, "candidates": [...]}
        L6/L7（无命中）返回 None，调用方走原有规则。
    """
    result = matcher.match(ocr_shop_name)
    if result.level >= 6 or not result.shop_id:
        return None
    return {
        "shop_name": result.canonical_name,
        "city": result.city,
        "province": result.province,
        "level": result.level,
        "conflict": result.is_conflict,
        "candidates": result.candidates,
    }


def batch_get_shop_city(shop_names, source_images=None):
    """批量匹配，返回 {店名: result_dict}（仅命中项）"""
    results = matcher.batch_match(shop_names, source_images or {})
    out = {}
    for name, r in results.items():
        if r.level < 6 and r.shop_id:
            out[name] = {
                "shop_name": r.canonical_name,
                "city": r.city,
                "province": r.province,
                "level": r.level,
                "conflict": r.is_conflict,
                "candidates": r.candidates,
            }
    return out


def learn_correction(ocr_shop_name, corrected_shop_name, city="", province="",
                     operator="gui", source="manual"):
    return learner.learn_correction(ocr_shop_name, corrected_shop_name,
                                    city, province, operator, source)


def resolve_conflict(shop_id, chosen_city, operator="gui"):
    return learner.resolve_conflict(shop_id, chosen_city, operator)


def import_excel(excel_path, operator="gui"):
    return importer.import_excel(excel_path, operator)


def migrate_shop_city_db():
    return migrate.migrate_shop_city_db()


def get_stats():
    return repository.get_stats()


def get_conflicts():
    return repository.get_conflicts()


def get_city_matches(shop_id):
    """某店铺的全部城市匹配记录（裁决冲突时展示候选城市）"""
    return repository.get_city_matches(shop_id)


def list_shops(limit=1000, offset=0):
    return repository.list_shops(limit, offset)


def list_aliases(limit=2000):
    return repository.list_aliases(limit)


def list_corrections(limit=1000):
    return repository.list_corrections(limit)


def list_batches(limit=200):
    return repository.list_batches(limit)


def record_network_city_consent():
    return repository.record_network_city_consent()


def create_network_city_request(authorized_at, allowed_cities, shop_names):
    return repository.create_network_city_request(
        authorized_at, allowed_cities, shop_names
    )


def record_network_city_candidates(request_id, candidates, source="baidu_map"):
    return repository.record_network_city_candidates(request_id, candidates, source)


def record_network_city_decisions(request_id, decisions):
    return repository.record_network_city_decisions(request_id, decisions)


def list_network_city_requests(limit=200):
    return repository.list_network_city_requests(limit)
