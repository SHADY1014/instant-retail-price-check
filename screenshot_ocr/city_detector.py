"""
城市识别模块

通过本地知识、店名/分店证据和百度地图 POI 结果识别店铺所在城市。
完整店名（包括分店名）优先用于搜索；仅在证据得分和候选分差足够时自动填入。
歧义结果必须由界面人工确认，只有人工确认的城市才会写入知识库。
覆盖范围：广东、广西、海南、贵州、云南 五省地级市
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import database

logger = logging.getLogger(__name__)

def _lookup_learned_city(shop_name):
    """Read only manually confirmed city knowledge; never auto-learn here."""
    try:
        result = database.get_shop_city(shop_name)
        if (result and result.get("city") and not result.get("conflict")
                and result.get("level", 99) <= 4):
            return result["city"]
    except Exception as exc:
        logger.debug("learning DB lookup failed: %s", exc)
    return ""


def _batch_lookup_learned(shop_names):
    try:
        matched = database.batch_get_shop_city(list(shop_names))
        return {
            name: item["city"]
            for name, item in matched.items()
            if (item.get("city") and not item.get("conflict")
                and item.get("level", 99) <= 4)
        }
    except Exception as exc:
        logger.debug("learning DB batch lookup failed: %s", exc)
        return {}


def batch_lookup_local_cities(shop_names, restrict_cities=None):
    """Read only confirmed learning records within the selected city scope."""
    names = [name for name in shop_names if name]
    allowed = None if restrict_cities is None else set(restrict_cities)
    learned = _batch_lookup_learned(names)
    result = {}
    filtered = 0

    for name, city in learned.items():
        if allowed is None or city in allowed:
            result[name] = city
        else:
            filtered += 1

    logger.info(
        "local_city_lookup names=%d learned=%d filtered_out_of_range=%d unmatched=%d",
        len(names), len(result), filtered, len(names) - len(result),
    )
    return result


# =========================================================
# 粤桂琼黔地级市列表
# =========================================================
VALID_CITIES = {
    # 广东
    "广州市", "深圳市", "珠海市", "汕头市", "佛山市", "韶关市", "湛江市", "肇庆市",
    "江门市", "茂名市", "惠州市", "梅州市", "汕尾市", "河源市", "阳江市", "清远市",
    "东莞市", "中山市", "潮州市", "揭阳市", "云浮市",
    # 广西
    "南宁市", "柳州市", "桂林市", "梧州市", "北海市", "防城港市", "钦州市", "贵港市",
    "玉林市", "百色市", "贺州市", "河池市", "来宾市", "崇左市",
    # 海南
    "海口市", "三亚市", "三沙市", "儋州市",
    # 贵州
    "贵阳市", "六盘水市", "遵义市", "安顺市", "毕节市", "铜仁市",
    # 云南
    "昆明市", "曲靖市", "玉溪市", "保山市", "昭通市", "丽江市",
    "普洱市", "临沧市", "楚雄市", "红河市", "文山市", "西双版纳市",
    "大理市", "德宏市", "怒江市", "迪庆市",
}

# 城市简称 -> 标准名
CITY_KEYS = {c.replace("市", ""): c for c in VALID_CITIES}

# =========================================================
# 分店名关键词 -> 城市映射表
# 包含道路名、地标名、商圈名等
# =========================================================
BRANCH_KEYWORD_MAP = {
    # --- 南宁 ---
    "东葛": "南宁市", "民族大道": "南宁市", "金湖": "南宁市", "埌西": "南宁市",
    "佛子岭": "南宁市", "地王": "南宁市", "艺术学院": "南宁市", "仙葫": "南宁市",
    "五象": "南宁市", "万象城": "南宁市", "航洋": "南宁市", "世纪金源": "南宁市",
    "阳光100": "南宁市", "阳光一百": "南宁市", "大学东": "南宁市", "民族广场": "南宁市",
    # --- 柳州 ---
    "盛丰国际": "柳州市", "柳北": "柳州市", "柳南": "柳州市", "城中": "柳州市",
    "鱼峰": "柳州市", "柳江": "柳州市", "阳和": "柳州市", "万象": "柳州市",
    "步步高": "柳州市", "广场路": "柳州市",
    # --- 桂林 ---
    "七星": "桂林市", "象山": "桂林市", "叠彩": "桂林市", "秀峰": "桂林市",
    "临桂": "桂林市",
    # --- 广州 ---
    "水荫": "广州市", "昌岗": "广州市", "珠江新城": "广州市",
    "天河": "广州市", "越秀": "广州市", "海珠": "广州市",
    "番禺": "广州市", "黄埔": "广州市", "花都": "广州市", "南沙": "广州市",
    "北京南": "广州市", "白云区": "广州市", "白云": "广州市",
    "京溪": "广州市", "萧岗": "广州市", "石牌": "广州市", "海堤": "广州市",
    "荔湾": "广州市", "北京路": "广州市", "南洲": "广州市", "滨江东": "广州市",
    # --- 深圳 ---
    "华强北": "深圳市", "景田": "深圳市", "福田": "深圳市", "南山": "深圳市",
    "罗湖": "深圳市", "宝安": "深圳市", "龙华": "深圳市", "龙岗": "深圳市",
    "光明": "深圳市", "坪山": "深圳市", "下沙": "深圳市", "新洲": "深圳市",
    "福星": "深圳市", "东林公园": "深圳市", "水围": "深圳市", "石厦": "深圳市",
    # --- 东莞 ---
    "东岸村": "东莞市", "长安": "东莞市", "虎门": "东莞市", "厚街": "东莞市",
    "南城": "东莞市", "东城": "东莞市",
    # --- 佛山 ---
    "禅城": "佛山市", "南海": "佛山市", "顺德": "佛山市", "三水": "佛山市",
    "高明": "佛山市", "佛山一中": "佛山市", "佛山乐从大道": "佛山市",
    "依云上城": "佛山市", "宏宇景裕豪园": "佛山市", "桂城": "佛山市",
    "大沥": "佛山市", "乐从": "佛山市", "千灯湖": "佛山市", "御酌桂城街道": "佛山市",
    # --- 海口 ---
    "美兰": "海口市", "龙华区": "海口市", "秀英": "海口市", "琼山": "海口市",
    "海垦": "海口市", "南宝路": "海口市", "华庭南区": "海口市", "华庭": "海口市",
    "海甸岛": "海口市", "海甸": "海口市", "海秀": "海口市", "滨海": "海口市",
    "国贸": "海口市", "美苑": "海口市", "龙昆": "海口市", "凤翔": "海口市",
    "生生": "海口市", "生生国际": "海口市",
    # --- 三亚 ---
    "解放": "三亚市", "天涯": "三亚市", "吉阳": "三亚市", "海棠": "三亚市",
    "黄金广场": "三亚市", "榆亚": "三亚市", "海虹路": "三亚市", "胜利路": "三亚市",
    "金陵路口": "三亚市", "金陵": "三亚市", "龙湖天街": "三亚市", "大东海": "三亚市",
    "商品街": "三亚市", "春河路": "三亚市", "万达广场": "三亚市", "跃进街": "三亚市",
    "金鸡岭": "三亚市", "老鸿港": "三亚市", "三亚中心": "三亚市", "三亚": "三亚市",
    # --- 贵阳 ---
    "南明": "贵阳市", "云岩": "贵阳市", "观山湖": "贵阳市", "花溪": "贵阳市",
    "乌当": "贵阳市", "金融城": "贵阳市", "金阳": "贵阳市", "汇都国际": "贵阳市",
    "花果园": "贵阳市", "北站": "贵阳市",
    # --- 遵义 ---
    "红花岗": "遵义市", "汇川": "遵义市", "播州": "遵义市", "新蒲": "遵义市",
    "老城": "遵义市", "东风": "遵义市", "奥特莱斯": "遵义市", "广州路": "遵义市",
    "沙河": "遵义市", "港澳": "遵义市", "白杨小区": "遵义市",
    # --- 昆明 ---
    "五华": "昆明市", "盘龙": "昆明市", "官渡": "昆明市", "西山": "昆明市",
    "呈贡": "昆明市", "晋宁": "昆明市", "安宁": "昆明市", "云纺": "昆明市",
    "关南路": "昆明市", "关上": "昆明市", "富康城": "昆明市", "南屏街": "昆明市",
    "翠湖": "昆明市", "高原明珠": "昆明市", "关东": "昆明市", "西坝": "昆明市",
    "南湖": "昆明市", "莲都山超": "昆明市",
    # --- 南宁（补充） ---
    "海风路": "南宁市",
    # --- 曲靖 ---
    "麒麟": "曲靖市", "沾益": "曲靖市", "马龙": "曲靖市", "宣威": "曲靖市",
    # --- 玉溪 ---
    "红塔": "玉溪市", "江川": "玉溪市", "澄江": "玉溪市",
    # --- 大理 ---
    "下关": "大理市", "古城区": "大理市", "大理古城": "大理市",
    # --- 丽江 ---
    "古城": "丽江市", "玉龙": "丽江市",
    # --- 西双版纳 ---
    "景洪": "西双版纳市", "勐海": "西双版纳市", "勐腊": "西双版纳市",
    # --- 红河 ---
    "蒙自": "红河市", "个旧": "红河市", "开远": "红河市", "弥勒": "红河市",
    # --- 文山 ---
    "文山城": "文山市", "砚山": "文山市", "丘北": "文山市",
    # --- 楚雄 ---
    "鹿城": "楚雄市", "楚雄市": "楚雄市",
    # --- 昭通 ---
    "昭阳": "昭通市", "鲁甸": "昭通市",
    # --- 保山 ---
    "隆阳": "保山市", "腾冲": "保山市",
    # --- 普洱 ---
    "思茅": "普洱市", "宁洱": "普洱市",
    # --- 临沧 ---
    "临翔": "临沧市", "凤庆": "临沧市",
    # --- 德宏 ---
    "芒市": "德宏市", "瑞丽": "德宏市", "盈江": "德宏市",
    # --- 迪庆 ---
    "香格里拉": "迪庆市", "德钦": "迪庆市",
    # --- 怒江 ---
    "泸水": "怒江市", "福贡": "怒江市",
}


@dataclass(frozen=True)
class PoiCandidate:
    """A Baidu POI suggestion retained with its search context."""

    city: str
    poi_name: str
    rank: int
    query: str


@dataclass(frozen=True)
class CityCandidate:
    """One city ranked from local textual and POI evidence."""

    city: str
    score: int
    evidence: tuple[str, ...]
    poi_names: tuple[str, ...]


@dataclass(frozen=True)
class CityDecision:
    """A conservative city decision suitable for automatic or manual use."""

    city: str
    confidence: float
    auto_accept: bool
    reason: str
    candidates: tuple[CityCandidate, ...]


def _extract_city_from_name(shop_name):
    """Return all supported city names mentioned anywhere in a shop name."""
    return {full for key, full in CITY_KEYS.items() if key in shop_name}


def _compact_shop_name(name):
    """Remove non-semantic noise while keeping branch information intact."""
    compacted = re.sub(r'[•·\s]+', '', name or "")
    compacted = re.sub(r'(蜂乌准时达|蜂鸟准时达|商家自配送)', '', compacted)
    return compacted.strip()


def _clean_shop_name(name):
    """Return a branch-free fallback query for map search."""
    cleaned = re.sub(r'[（(].*?[）)]', '', _compact_shop_name(name))
    return cleaned.strip()


def _build_search_queries(shop_name):
    """Build ordered queries, keeping the complete branch name first."""
    full_name = _compact_shop_name(shop_name)
    branch_free = _clean_shop_name(shop_name)
    queries = [full_name]
    if branch_free and branch_free != full_name:
        queries.append(branch_free)

    if len(branch_free) > 6:
        shortened = re.sub(
            r'(超市|便利店|便利|百货|商行|量贩|精品|综合).*$', "", branch_free)
        if shortened and shortened != branch_free:
            queries.append(shortened)
        if len(shortened) > 4:
            queries.append(shortened[:4])
        if len(shortened) > 3:
            queries.append(shortened[:3])

    ordered = []
    for query in queries:
        if len(query) >= 2 and query not in ordered:
            ordered.append(query)
    return ordered


def _search_baidu_candidates(shop_name, timeout=10, restrict_cities=None):
    """Return ordered in-range POI candidates from Baidu map suggestions.

    The old interface returned a set of cities, which discarded the POI name
    and result order required to make a reliable choice.  Search the complete
    shop name, including its branch suffix, before broader fallback queries.
    """
    allowed = VALID_CITIES if restrict_cities is None else set(restrict_cities)
    if not allowed:
        return []

    for query_text in _build_search_queries(shop_name):
        query = urllib.parse.quote(query_text)
        url = (
            "https://map.baidu.com/su?wd="
            f"{query}&cid=1&type=0&newmap=1&from=webmap&prod=0"
        )
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36"
                ),
                "Referer": "https://map.baidu.com/",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="ignore")
            data = json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, OSError) as exc:
            logger.warning("百度地图搜索失败 (query=%s): %s", query_text, exc)
            continue

        candidates = []
        for rank, item in enumerate(data.get("s", [])[:10]):
            if not isinstance(item, str):
                continue
            parts = item.split("$")
            if len(parts) < 5:
                continue
            poi_name = parts[3].strip() if len(parts) > 3 else ""
            city = (parts[0] or (parts[5] if len(parts) > 5 else "")).strip()
            if city in allowed and poi_name:
                candidates.append(PoiCandidate(city, poi_name, rank, query_text))
        if candidates:
            return candidates
        time.sleep(0.2)
    return []


def _search_baidu_map(shop_name, timeout=10, restrict_cities=None):
    """Compatibility wrapper for callers that only need candidate cities."""
    return {
        candidate.city
        for candidate in _search_baidu_candidates(
            shop_name, timeout=timeout, restrict_cities=restrict_cities)
    }


def _extract_branch(shop_name):
    """Extract a parenthesized branch name, or use the full name as fallback."""
    matched = re.search(r'[（(](.+?)[）)]', shop_name or "")
    return matched.group(1) if matched else (shop_name or "")


def _infer_from_branch(shop_name):
    """Return all cities supported by branch-name keyword evidence."""
    branch = _extract_branch(shop_name)
    return {
        city for keyword, city in BRANCH_KEYWORD_MAP.items() if keyword in branch
    }


def _comparison_text(value):
    """Normalize text for a conservative, punctuation-insensitive comparison."""
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value or "").lower()


def _city_name_evidence(shop_name, allowed):
    """Score explicit city mentions without treating road names as conclusive."""
    scores = {}
    text = shop_name or ""
    for key, city in CITY_KEYS.items():
        if city not in allowed or key not in text:
            continue
        if city in text or re.search(
                re.escape(key) + r"(?:店|站|仓|分店|区域)", text):
            score = 100
            label = f"店名明确出现“{key}”"
        else:
            # For example, "广州路" may be a road in another city.
            score = 35
            label = f"店名包含城市词“{key}”（可能是道路名）"
        if score > scores.get(city, (0, ""))[0]:
            scores[city] = (score, label)
    return scores


def _branch_evidence(shop_name, allowed):
    """Score the longest matching branch keyword for every allowed city."""
    branch = _extract_branch(shop_name)
    matches = {}
    for keyword, city in BRANCH_KEYWORD_MAP.items():
        if city in allowed and keyword in branch:
            existing = matches.get(city, "")
            if len(keyword) > len(existing):
                matches[city] = keyword
    return {
        city: (70 + min(len(keyword) * 8, 40), f"分店关键词“{keyword}”")
        for city, keyword in matches.items()
    }


def _score_poi_candidate(shop_name, candidate):
    """Score a POI candidate using its name, branch text, rank and query."""
    shop_text = _comparison_text(shop_name)
    poi_text = _comparison_text(candidate.poi_name)
    branch_text = _comparison_text(_extract_branch(shop_name))
    base_text = _comparison_text(_clean_shop_name(shop_name))
    score = max(0, 15 - candidate.rank * 2)
    evidence = [f"地图第 {candidate.rank + 1} 条"]

    full_query = _compact_shop_name(shop_name)
    if candidate.query == full_query:
        score += 10
        evidence.append("完整店名搜索")

    similarity = SequenceMatcher(None, shop_text, poi_text).ratio()
    if shop_text and (shop_text in poi_text or poi_text in shop_text):
        score += 55
        evidence.append("店名与 POI 完整匹配")
    elif similarity >= 0.90:
        score += 50
        evidence.append("店名与 POI 高度相似")
    elif similarity >= 0.78:
        score += 32
        evidence.append("店名与 POI 相似")
    elif similarity >= 0.62:
        score += 16

    if branch_text and len(branch_text) >= 2 and branch_text in poi_text:
        score += 38
        evidence.append("POI 包含分店名")
    elif base_text and len(base_text) >= 4 and (
            base_text in poi_text or poi_text in base_text):
        score += 20
        evidence.append("POI 包含店铺主名称")
    return score, evidence


def _make_decision(shop_name, allowed, poi_candidates):
    """Build a city decision from scored local text and map POI evidence."""
    score_parts = {}
    evidence_by_city = {}
    poi_by_city = {}

    def add_evidence(city, score, label):
        score_parts.setdefault(city, []).append(score)
        evidence_by_city.setdefault(city, []).append(label)

    for city, (score, label) in _city_name_evidence(shop_name, allowed).items():
        add_evidence(city, score, label)
    for city, (score, label) in _branch_evidence(shop_name, allowed).items():
        add_evidence(city, score, label)

    poi_scores = {}
    poi_evidence = {}
    for candidate in poi_candidates:
        if candidate.city not in allowed:
            continue
        score, evidence = _score_poi_candidate(shop_name, candidate)
        if score > poi_scores.get(candidate.city, -1):
            poi_scores[candidate.city] = score
            poi_evidence[candidate.city] = evidence
        poi_by_city.setdefault(candidate.city, []).append(candidate.poi_name)

    for city, score in poi_scores.items():
        add_evidence(city, score, "；".join(poi_evidence[city]))

    candidates = []
    for city, scores in score_parts.items():
        candidates.append(CityCandidate(
            city=city,
            score=sum(scores),
            evidence=tuple(evidence_by_city[city]),
            poi_names=tuple(dict.fromkeys(poi_by_city.get(city, []))),
        ))
    candidates.sort(key=lambda item: (-item.score, item.city))
    ordered = tuple(candidates)
    if not ordered:
        return CityDecision("", 0.0, False, "未找到可用的城市候选", ordered)

    top = ordered[0]
    second_score = ordered[1].score if len(ordered) > 1 else 0
    score_gap = top.score - second_score
    confidence = min(0.99, top.score / 150.0)
    auto_accept = top.score >= 85 and score_gap >= 25
    if auto_accept:
        reason = f"证据得分 {top.score}，领先次优候选 {score_gap} 分"
    elif len(ordered) > 1 and score_gap < 25:
        reason = f"候选城市分差仅 {score_gap} 分，需要人工确认"
    else:
        reason = f"证据得分 {top.score}，不足以自动填入"
    return CityDecision(top.city, confidence, auto_accept, reason, ordered)


def _local_city_decision(city):
    """Return an always-approved decision for a confirmed local record."""
    candidate = CityCandidate(city, 200, ("本地人工确认知识库",), ())
    return CityDecision(city, 1.0, True, "命中本地人工确认知识库", (candidate,))


def _detect_city_decision(shop_name, allowed, use_network=True):
    """Implement conservative detection for a validated city scope."""
    if not shop_name or not allowed:
        return CityDecision("", 0.0, False, "店铺名或城市范围为空", ())

    cached = _lookup_learned_city(shop_name)
    if cached and cached in allowed:
        return _local_city_decision(cached)

    poi_candidates = []
    if use_network:
        poi_candidates = _search_baidu_candidates(
            shop_name, restrict_cities=allowed)
    return _make_decision(shop_name, allowed, poi_candidates)


def detect_city_decision(shop_name, use_network=True):
    """Return a conservative decision across all supported cities."""
    return _detect_city_decision(shop_name, set(VALID_CITIES), use_network)


def detect_city(shop_name, use_network=True):
    """Return a city only when the evidence is strong enough to auto-fill."""
    decision = detect_city_decision(shop_name, use_network)
    return decision.city if decision.auto_accept else ""


def detect_city_batch(shop_names, use_network=True, delay=0.3):
    """
    批量识别城市

    Args:
        shop_names: list[str] 店铺名称列表
        use_network: 是否使用网络搜索
        delay: 每次网络请求间隔（秒）

    Returns:
        dict: {shop_name: city}
    """
    results = {}

    # 先批量查本地数据库
    cached = _batch_lookup_learned(shop_names)
    for name, city in cached.items():
        results[name] = city

    # 未命中的再走完整识别流程
    pending = [n for n in shop_names if n and n not in results]
    for name in pending:
        city = detect_city(name, use_network=use_network)
        if city:
            results[name] = city
        if use_network:
            time.sleep(delay)
    return results


def detect_city_decision_in_region(shop_name, restrict_cities, use_network=True):
    """Return an evidence-based city decision within selected cities only."""
    allowed = set(restrict_cities or ())
    return _detect_city_decision(shop_name, allowed, use_network)


def detect_city_in_region(shop_name, restrict_cities):
    """Return an in-range city only when it is safe to fill automatically."""
    decision = detect_city_decision_in_region(shop_name, restrict_cities)
    return decision.city if decision.auto_accept else ""


def detect_city_batch_in_region(shop_names, restrict_cities, delay=0.3):
    """在指定城市集合内批量联网识别

    Args:
        shop_names: list[str] 店铺名列表
        restrict_cities: set 城市集合
        delay: 每次网络请求间隔（秒）

    Returns:
        dict: {shop_name: city}
    """
    results = {}

    # 先批量查本地数据库（数据库命中的直接用，不再走联网）
    cached = batch_lookup_local_cities(shop_names, restrict_cities)
    results.update(cached)

    # 未命中的走限定区域联网识别
    pending = [n for n in shop_names if n and n not in results]
    for name in pending:
        city = detect_city_in_region(name, restrict_cities)
        if city in set(restrict_cities or ()):
            results[name] = city
        time.sleep(delay)
    return results


if __name__ == "__main__":
    # 测试
    test_shops = [
        "酒驿栈（广州水荫路店）",
        "酒小二（深圳景田店）",
        "海马快来送酒（贵阳店）",
        "P9•酒农送酒（三亚解放店）",
        "24客超市（东葛店）",
        "歪马送酒（盛丰国际店）",
        "犀牛百货（金融城店）",
        "桂双百便利超市（佛子岭店）",
        "美宜佳（东岸村琼01288号店）",
        "京东酒世界（华强北店）",
    ]

    print("=== 城市识别测试 ===\n")
    for shop in test_shops:
        city = detect_city(shop, use_network=True)
        status = "✓" if city else "✗"
        print(f"  {status} {shop} -> {city or '未找到'}")
        time.sleep(0.3)
