"""
城市识别模块

通过四层策略识别店铺所在城市：
0. 本地数据库 - 查询历史识别记录（shop_city.db），命中则直接返回，零网络请求
1. 店名提取 - 店名中包含城市名（如"广州水荫路店"）
2. 百度地图搜索建议 - 搜索完整店名，从POI结果提取城市
3. 分店名关键词映射 - 从分店名中的道路/地标关键词推断城市

识别成功后自动写入本地数据库，下次直接命中。
覆盖范围：广东、广西、海南、贵州、云南 五省地级市
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import logging
import re
import time

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
    """Read local city knowledge with a safe legacy fallback.

    The learning database is authoritative.  ``shop_city.db`` is consulted
    only for names it cannot match, so existing Windows users keep access to
    their historical local records without promoting unverified legacy data
    into the learning database.  A non-``None`` empty city set means that no
    result is allowed; it must never mean "all cities".
    """
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

    legacy_count = 0
    try:
        import shop_city_db

        legacy = shop_city_db.batch_lookup([name for name in names if name not in learned])
        for name, city in legacy.items():
            if allowed is None or city in allowed:
                result[name] = city
                legacy_count += 1
            else:
                filtered += 1
    except Exception as exc:
        logger.warning("legacy local DB lookup failed: %s", exc)

    logger.info(
        "local_city_lookup names=%d learned=%d legacy=%d filtered_out_of_range=%d unmatched=%d",
        len(names), len([name for name in learned if name in result]), legacy_count,
        filtered, len(names) - len(result),
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


def _extract_city_from_name(shop_name):
    """第一层：从店名中提取城市关键词"""
    cities = set()
    for key, full in CITY_KEYS.items():
        if key in shop_name:
            cities.add(full)
    return cities


def _clean_shop_name(name):
    """去掉括号及内容、特殊字符、配送后缀，返回干净短名用于搜索"""
    cleaned = re.sub(r'[（(].*?[）)]', '', name)
    cleaned = re.sub(r'[•·•\s]+', '', cleaned)
    cleaned = re.sub(r'(蜂乌准时达|蜂鸟准时达|商家自配送)', '', cleaned)
    cleaned = cleaned.strip()
    return cleaned


def _search_baidu_map(shop_name, timeout=10, restrict_cities=None):
    """第二层：百度地图搜索建议接口

    改进策略：
    1. 去掉括号及内容后搜索短名（带括号的全名百度搜不到）
    2. 如果无结果，逐步缩短关键词再搜（去掉通用后缀 -> 取前4字 -> 取前3字）
    3. 只返回目标省份（粤桂琼黔滇）的城市，过滤无关结果
    4. 若指定 restrict_cities（如{"贵阳市","遵义市"}），只返回这些城市的结果

    Args:
        restrict_cities: set/None 限定返回的城市集合（如用户选定区域后）
    """
    cleaned = _clean_shop_name(shop_name)

    # 允许的城市集合：restrict_cities 优先，否则用全部 VALID_CITIES
    allowed = VALID_CITIES if restrict_cities is None else set(restrict_cities)
    if not allowed:
        return set()

    # 生成搜索词列表：清理后名 -> 逐步缩短
    queries = [cleaned]
    if len(cleaned) > 6:
        # 去掉末尾的通用后缀
        short = re.sub(r'(超市|便利店|便利|百货|商行|量贩|精品|综合).*$', '', cleaned)
        if short and short != cleaned:
            queries.append(short)
        if len(short) > 4:
            queries.append(short[:4])
        if len(short) > 3:
            queries.append(short[:3])

    for q in queries:
        if not q or len(q) < 2:
            continue
        query = urllib.parse.quote(q)
        url = f"https://map.baidu.com/su?wd={query}&cid=1&type=0&newmap=1&from=webmap&prod=0"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://map.baidu.com/",
        })
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw)
            cities = set()
            if 's' in data:
                for item in data['s'][:10]:
                    parts = item.split('$')
                    if len(parts) >= 5:
                        name = parts[3] if len(parts) > 3 else ''
                        city = parts[0] if parts[0] else (parts[5] if len(parts) > 5 else '')
                        # 只接受允许城市集合内的结果
                        if city in allowed and name:
                            cities.add(city)
            if cities:
                return cities
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as e:
            logger.warning("百度地图搜索失败 (query=%s): %s", q, e)
            continue
        time.sleep(0.2)

    return set()


def _infer_from_branch(shop_name):
    """第三层：从分店名关键词推断城市"""
    m = re.search(r'[（(](.+?)[）)]', shop_name)
    if not m:
        # 没有括号，用整个店名匹配
        branch = shop_name
    else:
        branch = m.group(1)

    cities = set()
    for keyword, city in BRANCH_KEYWORD_MAP.items():
        if keyword in branch:
            cities.add(city)
    return cities


def detect_city(shop_name, use_network=True):
    """
    综合城市识别

    Args:
        shop_name: 店铺名称
        use_network: 是否使用网络搜索（百度地图）

    Returns:
        str: 城市名（如"南宁市"），无法识别返回空字符串

    识别顺序：
        L0: 本地数据库查询（历史识别记录，零网络请求）
        L1: 店名直接含城市名（如"三亚昌运超市"）
        L3: 分店名关键词映射（如"美垦"->海口、"胜利路"->三亚）
        L2: 百度地图搜索（去掉括号搜短名，逐步缩短）

    识别成功后自动写入本地数据库，下次直接 L0 命中。
    """
    if not shop_name:
        return ""

    # 第0层：本地数据库查询（最快，零网络请求）
    cached = _lookup_learned_city(shop_name)
    if cached:
        return cached

    # 第一层：店名直接含城市名
    cities = _extract_city_from_name(shop_name)
    if cities:
        city = sorted(cities)[0]
        
        return city

    # 第三层：分店名关键词推断（优先于百度，因为路名/地标更精确）
    cities = _infer_from_branch(shop_name)
    if cities:
        city = sorted(cities)[0]
        
        return city

    # 第二层：百度地图搜索
    if use_network:
        cities = _search_baidu_map(shop_name)
        if cities:
            city = sorted(cities)[0]
            
            return city

    return ""


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


def detect_city_in_region(shop_name, restrict_cities):
    """在指定城市集合内联网识别店铺所在城市

    Args:
        shop_name: 店铺名
        restrict_cities: set 城市集合（如 {"贵阳市","遵义市"} ）

    Returns:
        str: 城市名（如"贵阳市"），无法识别返回空字符串

    识别顺序：
        L0: 本地数据库查询（仅当缓存城市在 restrict_cities 内时才采用）
        L1: 店名直接含城市名（且在 restrict_cities 内）
        L3: 分店名关键词映射（且在 restrict_cities 内）
        L2: 百度地图搜索（限定 restrict_cities）
    """
    allowed = set(restrict_cities or ())
    if not shop_name or not allowed:
        return ""

    # L0: 本地数据库（仅当缓存城市在限定区域内时才采用）
    cached = _lookup_learned_city(shop_name)
    if cached and cached in allowed:
        return cached

    # L1: 店名含城市名（限定范围内）
    cities = _extract_city_from_name(shop_name)
    if cities:
        in_range = [c for c in cities if c in allowed]
        if in_range:
            city = sorted(in_range)[0]
            
            return city

    # L3: 分店名关键词（限定范围内）
    cities = _infer_from_branch(shop_name)
    if cities:
        in_range = [c for c in cities if c in allowed]
        if in_range:
            city = sorted(in_range)[0]
            
            return city

    # L2: 百度搜索（限定城市集合）
    cities = _search_baidu_map(shop_name, restrict_cities=allowed)
    if cities:
        city = sorted(cities)[0]
        if city not in allowed:
            logger.warning("region lookup rejected out-of-range result shop=%r city=%r", shop_name, city)
            return ""
        
        return city

    return ""


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
