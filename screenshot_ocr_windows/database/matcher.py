"""
店铺/城市智能匹配数据库 — Matcher（L1-L7 分级匹配 + 字符串标准化）

匹配优先级（严格按序，数据库命中 > 模糊匹配，人工确认 > 自动推断）：
  L1 canonical_name 精确匹配
  L2 alias 精确匹配
  L3 normalized_alias 精确匹配
  L4 历史人工确认记录（corrections: ocr_shop_name -> shop_id）
  L5 高置信度模糊匹配（仅候选，不写 canonical）
  L6 无数据库命中（由外部规则推断）
  L7 无法确认
"""

import logging
import re
from difflib import SequenceMatcher

from . import repository
from .models import MatchResult

logger = logging.getLogger(__name__)

# =========================================================
# 标准化
# =========================================================

# 常见 OCR 错别字映射（中文）
_OCR_TYPO_MAP = {
    "卅": "州",   # 广卅 -> 广州
    "丿": "",     # 噪声
    "丨": "",
    "…": "",
    "⋯": "",
    "。。": "。",
}

# 全角数字/字母 -> 半角
_FULLWIDTH_TRANS = str.maketrans(
    "０１２３４５６７８９．ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789.ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)


def normalize(name):
    """标准化店铺名：
    - 去空白（含全角空格/不间断空格）
    - 全角数字字母转半角
    - 中文括号统一为英文括号
    - 常见 OCR 错别字替换
    - 去除孤立的分隔符噪声
    """
    if not name:
        return ""
    s = str(name).strip()
    # 全角括号统一为半角（保留括号，分店名信息重要）
    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("【", "(").replace("】", ")")
    # 全角数字/字母
    s = s.translate(_FULLWIDTH_TRANS)
    # 去除所有空白
    s = re.sub(r"\s+", "", s)
    # OCR 常见错别字
    for k, v in _OCR_TYPO_MAP.items():
        s = s.replace(k, v)
    # 清理尾部标点噪声
    s = re.sub(r"[·•、,，。;；:：>〉＞~～]+$", "", s)
    return s.strip()


def similarity(a, b):
    """两个标准化的店铺名相似度（0~1）"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _branch_match(input_stripped, norm_alias):
    """OCR 丢括号场景：库中 alias 形如"主名(分店名)"，
    输入形如"主名分店名"（括号丢失但内容保留）。

    条件：主名是输入前缀 且 分店名也包含在输入中 -> 同一店铺。
    """
    m = re.search(r"[()](.*?)[()]", norm_alias)
    if not m:
        return 0.0
    main = norm_alias.replace(m.group(0), "")
    branch = m.group(1)
    if not main or not branch:
        return 0.0
    if input_stripped.startswith(main) and branch in input_stripped:
        return 1.0
    return 0.0


# =========================================================
# 分级匹配
# =========================================================

def match(name, record_history=True, source_image=""):
    """对 OCR 店名执行 L1-L5 分级匹配。

    Args:
        name: OCR 识别的店铺名
        record_history: 是否记录匹配历史
        source_image: 来源图片路径（记录用）

    Returns:
        MatchResult: level 1-5 命中；level 6 表示数据库无命中（走原规则）；
        level 7 无任何结果。
    """
    raw = (name or "").strip()
    if not raw:
        return MatchResult(level=7, raw_name="")

    normalized = normalize(raw)

    # L1: canonical_name 精确匹配
    shop = repository.get_shop_by_canonical(raw)
    if shop:
        return _build_result(1, shop, raw, normalized, record_history, source_image)

    # L2: alias 精确匹配（原始写法）
    shop = repository.get_shop_by_alias(raw)
    if shop:
        return _build_result(2, shop, raw, normalized, record_history, source_image)

    # L3: normalized_alias 精确匹配（清洗后一致）
    if normalized:
        shop = repository.get_shop_by_normalized(normalized)
        if shop:
            return _build_result(3, shop, raw, normalized, record_history, source_image)

    # L4: 历史人工修正记录（ocr_shop_name -> shop_id）
    shop = repository.get_shop_by_correction_ocr(raw)
    if shop:
        return _build_result(4, shop, raw, normalized, record_history, source_image)

    # L5: 高置信度模糊匹配（仅候选，不写 canonical）
    # OCR 常丢失括号（"老王便利店(滨江路店)" -> "老王便利店滨江路店"），
    # 因此对全量别名做"去括号"形式比较，保证缺括号场景也能给出候选
    stripped = re.sub(r"[()].*?[()]", "", normalized) if normalized else ""
    seen_ids = set()
    scored = []
    for shop_id, canonical, norm_alias in repository.get_all_alias_norm_pairs():
        if shop_id in seen_ids:
            continue
        cand_stripped = re.sub(r"[()].*?[()]", "", norm_alias)
        # OCR 丢括号场景优先判断（主名前缀 + 分店名包含，不受长度差限制）
        bscore = _branch_match(stripped, norm_alias)
        if bscore == 0.0:
            # 普通相似度快速预筛
            if abs(len(stripped) - len(cand_stripped)) > 2:
                continue
            if stripped and cand_stripped and stripped[0] != cand_stripped[0]:
                continue
        score = max(bscore, similarity(normalized, norm_alias),
                    similarity(stripped, cand_stripped))
        # 与 canonical 名再比较一次
        score = max(score, similarity(stripped, re.sub(r"[()].*?[()]", "", normalize(canonical))))
        if score >= 0.95:
            seen_ids.add(shop_id)
            scored.append((shop_id, canonical, round(score, 3)))
    scored.sort(key=lambda x: -x[2])
    if scored:
        top = scored[0]
        result = MatchResult(level=5, shop_id=top[0], canonical_name=top[1],
                             candidates=scored, raw_name=raw)
        result.city, result.province, result.is_conflict = _shop_city(top[0])
        if record_history:
            repository.record_match(raw, normalized, top[0], 5, source_image)
        return result

    # L6/L7: 数据库无命中
    if record_history and normalized:
        repository.record_match(raw, normalized, None, 6, source_image)
    return MatchResult(level=6, raw_name=raw)


def batch_match(names, source_images=None):
    """批量匹配（record_history 记录每个）"""
    return {n: match(n, source_image=(source_images or {}).get(n, "")) for n in names}


def _build_result(level, shop, raw, normalized, record_history, source_image):
    result = MatchResult(level=level, shop_id=shop["shop_id"],
                         canonical_name=shop["canonical_name"], raw_name=raw)
    result.city, result.province, result.is_conflict = _shop_city(shop["shop_id"])
    if record_history:
        repository.record_match(raw, normalized, shop["shop_id"], level, source_image)
    return result


def _shop_city(shop_id):
    """取店铺主城市，仅采纳人工或人工投喂的确认记录。"""
    matches = repository.get_city_matches(shop_id)
    if not matches:
        return "", "", False
    confirmed = [m for m in matches
                 if m["status"] == "confirmed" and m["source"] in ("manual", "import")]
    if len(confirmed) > 1:
        return confirmed[0]["city"], confirmed[0].get("province", ""), True
    if confirmed:
        return confirmed[0]["city"], confirmed[0].get("province", ""), False
    # 无人工确认城市 -> 不标注（由原规则/人工处理）
    return "", "", False
