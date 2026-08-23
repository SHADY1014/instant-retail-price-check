"""Rules that identify screenshots requiring manual verification."""

from typing import Mapping


def _is_missing_price(value: object) -> bool:
    if value is None or not str(value).strip():
        return True
    try:
        return float(str(value).replace("¥", "").replace("￥", "")) <= 0
    except (TypeError, ValueError):
        return True


def find_review_issues(record: Mapping[str, object]) -> list[str]:
    """Return user-facing reasons a row needs manual verification."""
    remark = str(record.get("remark", ""))
    if "OCR失败" in remark or "OCR 失败" in remark or "OCR 未完成" in remark:
        return ["OCR 识别失败"]

    issues = []
    for field_name, message in (
        ("region", "未确认所属区域"),
        ("shop_name", "未识别店铺名称"),
        ("product_name", "未识别产品名称"),
    ):
        if not str(record.get(field_name, "")).strip():
            issues.append(message)
    if _is_missing_price(record.get("original_price")):
        issues.append("未识别产品原价")
    if _is_missing_price(record.get("final_price")):
        issues.append("未识别成交价")
    return issues
