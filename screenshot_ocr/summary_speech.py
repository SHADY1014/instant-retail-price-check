"""
总结话术生成器 - 从巡查表 xlsx 智能生成汇报话术

读取巡查表数据，按省份->城市->平台->不合格店铺分组，
生成格式化的总结话术文本，可直接粘贴到工作群汇报。

示例输出：
  按总部即时零售管理组要求，每日对区域内即时零售渠道价格体系进行巡查。
  今日巡查云贵区域燕京U8产品美团闪购、淘宝闪购平台价格，
  现将结果同步云贵销售公司，请及时整改：

  1、云南区域巡店10家，不合格4家，其中3家价格低于55元：
  昆明市巡店10家，不合格4家，涉及系统：美团闪购：XXX（50）、XXX（55）；
  淘宝闪购：XXX（47.9），详见附件。
"""

import re
from collections import defaultdict, OrderedDict

from summary_generator import (
    _get_record_thresholds,
    _read_data_with_images,
    _short_name,
)


# =========================================================
# 省份简称映射（用于"XX区域""XX销售公司"）
# =========================================================
_PROVINCE_SHORT = {
    "云南": "云",
    "贵州": "贵",
    "广西": "广西",
    "广东": "广东",
    "海南": "海南",
}


def _format_region_name(provinces):
    """从省份列表生成区域简称。

    规则：
      - 单省份：用全称（如"广西"）
      - 多省份：取每省简称拼接（如 云南+贵州 -> "云贵"）
      - 含"广西"/"广东"时不取单字简称，保留全称拼接
    """
    if not provinces:
        return ""
    if len(provinces) == 1:
        return provinces[0]

    parts = []
    for p in provinces:
        # "广西"/"广东"/"海南" 等双字省保留全称
        if p in ("广西", "广东", "海南"):
            parts.append(p)
        else:
            parts.append(_PROVINCE_SHORT.get(p, p))
    return "".join(parts)


def _format_price(val):
    """格式化价格：去尾零（50.0 -> "50", 47.9 -> "47.9", 52.80 -> "52.8"）。"""
    if val is None:
        return ""
    try:
        f = float(val)
    except (ValueError, TypeError):
        return str(val)
    # 整数直接显示
    if f == int(f):
        return str(int(f))
    # 否则去尾零（最多2位小数）
    s = f"{f:.2f}"
    s = s.rstrip("0").rstrip(".")
    return s


def _get_platform(record):
    """从记录中提取平台名（C列 = all_columns[2]）。"""
    cols = record.get("all_columns", [])
    if len(cols) > 2 and cols[2]:
        return str(cols[2])
    return ""


def generate_speech(xlsx_path, provinces=None):
    """从巡查表生成总结话术。

    Args:
        xlsx_path: 巛查表 xlsx 文件路径
        provinces: 选中省份列表，None 表示包含所有省份

    Returns:
        str: 格式化的总结话术文本

    说明：
        U8和1998不会同时查，话术根据实际数据自适应：
        - U8批次：合格线60元，第二档55元
        - 1998批次：广东合格线70元/第二档65元，广西合格线60元/第二档55元
    """
    records = _read_data_with_images(xlsx_path)
    if not records:
        return "未从巡查表中读取到有效数据。"

    # 按省份筛选
    if provinces:
        records = [r for r in records if r["province"] in provinces]
        if not records:
            return f"选中的省份 {provinces} 下没有匹配的记录。"

    # 提取产品名（去重，保持顺序）
    product_names = list(OrderedDict.fromkeys(
        _short_name(r["product_name"]) for r in records
    ))
    product_str = "、".join(product_names)

    # 提取平台名（去重，保持顺序，美团闪购优先）
    platforms = list(OrderedDict.fromkeys(
        _get_platform(r) for r in records if _get_platform(r)
    ))
    # 固定平台排序：美团闪购在前，淘宝闪购在后，其他按原序
    _PLATFORM_ORDER = {"美团闪购": 0, "淘宝闪购": 1}
    platforms.sort(key=lambda p: _PLATFORM_ORDER.get(p, 99))
    platform_str = "、".join(platforms)

    # 提取省份列表（去重，保持顺序，云贵优先）
    province_list = list(OrderedDict.fromkeys(
        r["province"] for r in records if r["province"]
    ))
    # 省份排序：云贵优先，广东广西海南在后
    _PROVINCE_ORDER = {"云南": 0, "贵州": 1, "广西": 2, "广东": 3, "海南": 4}
    province_list.sort(key=lambda p: _PROVINCE_ORDER.get(p, 99))
    region_name = _format_region_name(province_list)

    # ---- 开场白 ----
    speech_lines = []
    speech_lines.append(
        f"按总部即时零售管理组要求，每日对区域内即时零售渠道价格体系进行巡查。"
        f"今日巡查{region_name}{product_str}产品{platform_str}平台价格，"
        f"现将结果同步{region_name}销售公司，请及时整改："
    )

    # ---- 按城市分组（不再按省份分层）----
    prov_groups = defaultdict(list)
    for r in records:
        prov_groups[r["province"]].append(r)

    # 收集所有城市（按省份顺序、城市出现顺序）
    city_order = []
    city_records = defaultdict(list)
    for province in province_list:
        for r in prov_groups[province]:
            city = r["region"]
            if city not in city_records:
                city_order.append(city)
            city_records[city].append(r)

    # 每个城市一行，序号递增
    city_seq = 0
    for city in city_order:
        city_all = city_records[city]
        city_total = len(city_all)
        # 不合格判定：按每条记录的省份合格线重新计算（不依赖旧缓存值）。
        city_failed = [
            r for r in city_all
            if r["theory_price"] < _get_record_thresholds(r)[0] - 0.1
        ]
        city_failed_count = len(city_failed)

        # 城市名去掉"市"后缀
        city_display = city.replace("市", "") if city.endswith("市") else city

        city_seq += 1

        if city_failed_count == 0:
            speech_lines.append(
                f"{city_seq}、{city_display}巡店{city_total}家，全部合格。"
            )
            continue

        city_below_thresholds = sorted({
            _get_record_thresholds(r)[1] for r in city_failed
        })
        city_below = sum(
            1 for r in city_failed
            if r["theory_price"] < _get_record_thresholds(r)[1]
        )
        below_label = "、".join(
            f"{_format_price(value)}元" for value in city_below_thresholds
        )

        # 按平台分组不合格店铺
        plat_groups = defaultdict(list)
        for r in city_failed:
            plat = _get_platform(r) or "未知平台"
            plat_groups[plat].append(r)

        # 每个平台只列1家（取最低价的），超出的用"等"
        platforms_with_shops = [p for p in platforms if plat_groups.get(p)]
        plat_parts = []
        for plat in platforms_with_shops:
            shops = plat_groups[plat]
            if not shops:
                continue
            # 按价格升序排序，取最低价的那家
            shops_sorted = sorted(shops, key=lambda r: r["theory_price"])
            lowest_shop = shops_sorted[0]
            # 店铺名去掉括号及内容（分店后缀），如"乐购达超市（北京路店）"->"乐购达超市"
            shop_name = re.sub(r'[（(].*?[）)]', '', lowest_shop['shop_name']).strip()
            part = f"{plat}：{shop_name}（{_format_price(lowest_shop['theory_price'])}）"
            if len(shops) > 1:
                part += "等"
            plat_parts.append(part)

        line = (
            f"{city_seq}、{city_display}巡店{city_total}家，"
            f"不合格{city_failed_count}家，"
            f"其中{city_below}家价格低于{below_label}，"
            f"涉及系统：{'；'.join(plat_parts)}，详见附页。"
        )
        speech_lines.append(line)

    return "\n".join(speech_lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python summary_speech.py <巡查表.xlsx> [省份1,省份2,...]")
        sys.exit(1)

    xlsx_path = sys.argv[1]
    provinces = sys.argv[2].split(",") if len(sys.argv) > 2 else None

    text = generate_speech(xlsx_path, provinces)
    print(text)
