"""Rules that identify screenshots requiring manual verification."""

from typing import Mapping


def _is_missing_price(value: object) -> bool:
    """Return whether an OCR price is empty, invalid, or non-positive."""
    if value is None or not str(value).strip():
        return True
    try:
        return float(str(value).replace("¥", "").replace("￥", "")) <= 0
    except ValueError:
        return True


def find_review_issues(record: Mapping[str, object]) -> list[str]:
    """Return user-facing reasons a record needs manual verification.

    The rules intentionally only identify missing or failed OCR results. They do
    not judge whether a promotional price is reasonable, which needs business
    context and must remain a manual decision.
    """
    remark = str(record.get("remark", ""))
    if "OCR失败" in remark or "OCR 失败" in remark:
        return ["OCR 识别失败"]

    issues = []
    # 产品规格无法从截图可靠识别（数量走默认回退），需人工确认
    if "产品规格需人工确认" in remark:
        issues.append("产品规格需人工确认")

    required_fields = (
        ("region", "未确认所属区域"),
        ("shop_name", "未识别店铺名称"),
        ("product_name", "未识别产品名称"),
    )
    for field_name, message in required_fields:
        if not str(record.get(field_name, "")).strip():
            issues.append(message)

    if _is_missing_price(record.get("original_price")):
        issues.append("未识别产品原价")
    if _is_missing_price(record.get("final_price")):
        issues.append("未识别成交价")
    return issues
