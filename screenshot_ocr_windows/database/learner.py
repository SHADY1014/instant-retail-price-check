"""
店铺/城市智能匹配数据库 — Learner（人工反馈学习）

核心原则：
  - 人工确认 > 自动推断
  - 数据库不能因一次投喂无条件覆盖：同店不同城市 -> CONFLICT，交人工裁决
  - 修正一次，系统以后尽可能记住
"""

import logging

from . import repository
from .matcher import match, normalize

logger = logging.getLogger(__name__)


def learn_correction(ocr_shop_name, corrected_shop_name, city="", province="",
                     operator="gui", source="manual", batch_id=None):
    """学习一次人工修正结果。

    处理流程：
      1. 用「修正后店名」匹配已有店铺（L1-L4）
      2. 已存在 -> 记录修正 + OCR 店名登记为别名 + 城市确认/冲突
      3. 不存在 -> 新建店铺（canonical=修正名）+ 别名(OCR名) + 城市确认

    Returns:
        dict: {shop_id, canonical_name, conflict: bool, created: bool, updated: bool}
    """
    ocr_name = (ocr_shop_name or "").strip()
    corrected = (corrected_shop_name or ocr_name or "").strip()
    if not corrected:
        return {"shop_id": None, "canonical_name": "", "conflict": False,
                "created": False, "updated": False}

    # 1. 用修正名找已有店铺（L1-L4，不记历史）
    result = match(corrected, record_history=False)

    created = False
    updated = False
    conflict = False

    if result.level <= 4 and result.shop_id:
        shop_id = result.shop_id
        canonical = result.canonical_name or corrected
        updated = True
    elif result.level == 5 and result.shop_id:
        # L5 候选：相似度足够高视为同一店铺，强化别名（不新建）
        shop_id = result.shop_id
        canonical = result.canonical_name or corrected
        updated = True
    else:
        # 新店铺，或收编旧库迁移的同名店铺（升级为人工确认来源）
        existing = repository.get_shop_any_source(corrected)
        if existing:
            shop_id = existing["shop_id"]
            canonical = corrected
            updated = True
        else:
            shop_id = repository.upsert_shop(corrected, city, province, source=source,
                                             confidence=1.0)
            canonical = corrected
            created = True

    # 2. OCR 店名登记为别名（变体归并）
    repository.add_alias(shop_id, ocr_name, normalize(ocr_name),
                         source=source, confidence=1.0)
    if ocr_name != corrected:
        repository.add_alias(shop_id, corrected, normalize(corrected),
                             source=source, confidence=1.0)

    # 3. 城市确认（无城市则不动；有城市则确认或标记冲突）
    if city:
        _match_id, conflict = repository.add_city_match(
            shop_id, city, province, source=source, confidence=1.0)

    # 4. 人工确认提升正确计数
    repository.increment_shop_usage(shop_id, correct=True)

    # 5. 记录修正审计
    repository.add_correction(ocr_name, canonical, city, province, shop_id,
                              batch_id=batch_id, operator=operator)

    return {"shop_id": shop_id, "canonical_name": canonical,
            "conflict": conflict, "created": created, "updated": updated}


def resolve_conflict(shop_id, chosen_city, operator="gui"):
    """人工裁决店铺城市冲突"""
    repository.resolve_conflict(shop_id, chosen_city, operator)
    return True
