"""
OCR 文本 → 表单字段解析器

将 macOS Vision OCR 识别出的文本列表解析为 Excel 表单字段。
基于美团闪购结算页截图的布局规律：
  - 店铺名称在 "选择收货地址" 下方
  - 商品名称在商品列表区域
  - 各种价格在结算明细区域（商品总价/打包费/配送费/商家活动/红包等）
  - 最终成交价在底部
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FormFields:
    """Excel 表单字段（对应 A~P 列）"""
    # A: 分公司 — 默认"燕京漓泉"
    branch_company: str = "漓泉销售公司"
    # B: 所属主要区域 — "XX市"
    region: str = ""
    # C: 区域内即时零售平台 — 美团截图固定为"美团闪购"
    platform: str = "美团闪购"
    # D: 店铺名称
    shop_name: str = ""
    # E: 平台在售产品
    product_name: str = ""
    # F: 产品原价（商品标价）
    original_price: float = 0.0
    # G: 产品成交单价（最终付款价格）
    final_price: float = 0.0
    # H: 商品优惠/商品活动（店铺活动）
    shop_discount: float = 0.0
    # I: 满减活动（店铺活动）
    full_reduction: float = 0.0
    # J: 优惠券（平台下发）
    coupon: float = 0.0
    # K: 红包（平台下发）
    red_packet: float = 0.0
    # L: 打包、配送费
    delivery_fee: float = 0.0
    # M: 产品理论成交价格 = G - L（公式，不直接存值）
    # N: 去除平台优惠价格 = G + J + K - L（公式）
    # O: 图片（由 excel_writer 插入）
    # P: 备注
    remark: str = ""

    def to_dict(self):
        return {
            "branch_company": self.branch_company,
            "region": self.region,
            "platform": self.platform,
            "shop_name": self.shop_name,
            "product_name": self.product_name,
            "original_price": self.original_price,
            "final_price": self.final_price,
            "shop_discount": self.shop_discount,
            "full_reduction": self.full_reduction,
            "coupon": self.coupon,
            "red_packet": self.red_packet,
            "delivery_fee": self.delivery_fee,
            "remark": self.remark,
        }


# 品牌到 Sheet 索引的映射
BRAND_SHEET_MAP = {
    "燕京": 0,  # 1.燕京即时零售渠道价格巡查表
    "雪花": 1,  # 2.雪花即时零售渠道价格巡查表
    "青岛": 2,  # 3.青岛即时零售渠道价格巡查表
    "百威": 3,  # 4.百威即时零售渠道价格巡查表
}

# 店铺名称关键词（多处店铺名识别共用，单一来源）
SHOP_KEYWORDS = [
    "超市", "便利店", "店）", "店)", "店（", "店(", "送酒", "速配",
    "商店", "嗨酒", "酒类", "酒行", "酒业", "酒栈", "酒零鹿",
    "鸡尾酒", "酒水", "酒屋",
]


def detect_brand(product_name):
    """
    根据产品名判断品牌分类，返回对应的 Sheet 索引

    规则:
      - 漓泉 / 燕京 -> 燕京表 (0)
      - 雪花 / 勇闯 -> 雪花表 (1)
      - 青岛 -> 青岛表 (2)
      - 百威 / 哈啤 / 哈尔滨 -> 百威表 (3)
      - 其他 -> 燕京表 (0)（默认）

    Returns:
        int: Sheet 索引 (0~3)
    """
    name = product_name or ""
    if "雪花" in name or "勇闯" in name:
        return BRAND_SHEET_MAP["雪花"]
    if "青岛" in name:
        return BRAND_SHEET_MAP["青岛"]
    if "百威" in name or "哈啤" in name or "哈尔滨" in name:
        return BRAND_SHEET_MAP["百威"]
    # 漓泉/燕京/乌苏/其他都归燕京表
    return BRAND_SHEET_MAP["燕京"]


def _extract_price(text):
    """
    从文本中提取价格数字，支持 ¥65 / -¥8.1 / ¥0.5 / 39.4 / -¥461~ 等格式
    注意: OCR 经常把小数点漏掉，如 ¥461 实际是 ¥4.61，
    但我们不做猜测，只提取原始数字值
    """
    # 去掉波浪号等噪声字符
    cleaned = text.replace("～", "").replace("~", "").replace("＞", "").replace(">", "").strip()
    # 匹配 ¥开头或纯数字（含小数），优先匹配带 ¥ 的
    m = re.search(r'[¥￥]\s*(-?\d+\.?\d*)', cleaned)
    if not m:
        m = re.search(r'(-?\d+\.?\d*)', cleaned)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 0.0
    return 0.0


def _extract_price_safe(text, max_val=200.0):
    """
    安全提取价格：过滤掉明显不合理的值
    OCR 常见误读模式：
      - 小数点被识别为 ～ 或 ~：-¥101～ 实际是 10.1，-¥181～ 实际是 18.1
      - 小数点完全丢失：¥461 实际是 4.61，¥1061 实际是 106.1

    max_val: 该字段的合理上限。超过上限时依次尝试 /10、/100、/1000 缩小，
             取第一个不超过上限的值。
             原价/总价类上限 200（如 106.1、127.6），
             优惠/红包类上限 100（如 -¥171 实际 17.1、-¥192 实际 19.2）
    """
    # 先尝试从"¥xxx～"格式中修正小数点（OCR把.读成～）
    # 例如 -¥101～ -> 10.1, -¥181～ -> 18.1
    # 但 -¥15～ -> 15（不修正，因为15元优惠是合理的）
    m_wave = re.search(r'[¥￥]\s*(\d{3})[～~]', text)
    if m_wave:
        digits = m_wave.group(1)
        # 3位数 -> 去掉最后一位，加小数点
        # 101 -> 10.1, 181 -> 18.1, 461 -> 46.1
        corrected = float(digits[:-1] + '.' + digits[-1])
        return corrected

    price = abs(_extract_price(text))
    if price == 0.0:
        return 0.0
    # 如果价格超过合理上限，可能是小数点被 OCR 漏掉，依次尝试 /10、/100、/1000
    if price > max_val:
        for divisor in (10, 100, 1000):
            corrected = price / divisor
            if corrected <= max_val:
                return round(corrected, 2)
        return round(price / 1000, 2)
    return price


def _find_line_by_keyword(lines, keyword, start=0):
    """在 OCR 行列表中查找包含关键词的行，返回行索引或 -1"""
    for i in range(start, len(lines)):
        if keyword in lines[i]["text"]:
            return i
    return -1


def _find_price_near_line(lines, idx, direction="after"):
    """
    在指定行附近查找价格（向右或向下一行）
    美团结算页中，价格通常在标签行的右侧（同一行）或下一行
    """
    if idx < 0 or idx >= len(lines):
        return 0.0

    # 同一行右侧找价格
    price = _extract_price(lines[idx]["text"])
    # 如果这行本身只有标签没有价格，price 会是0
    if price != 0.0:
        return abs(price)

    # 向后找1~2行
    if direction == "after":
        for offset in range(1, 3):
            ni = idx + offset
            if ni < len(lines):
                price = _extract_price(lines[ni]["text"])
                if price != 0.0:
                    return abs(price)

    return 0.0


def _find_price_by_x_alignment(lines, target_top, x_threshold=0.4):
    """
    按 Y 坐标对齐找右侧的价格
    美团结算页价格都在右侧（left > 0.5），标签在左侧
    如果同一行有多个价格（如 ¥7 ¥1.5），取最后一个（实际支付价）
    如果同一文本项中有多个价格（如 "¥15.6 ¥5.6>"），也取最后一个

    修正：OCR 漏小数点导致 ¥0.3 被识别为 ¥03 -> float("03")=3.0
    当数字以0开头且为2位数（如"03"），实际应为 0.X 格式
    """
    candidates = []
    for item in lines:
        # Y 坐标接近（同一行），X 在右侧
        if abs(item["top"] - target_top) < 0.02 and item["left"] > x_threshold:
            text = item["text"]
            # 提取文本中的所有价格（处理 "¥15.6 ¥5.6>" 这种多价格文本）
            prices = re.findall(r'[¥￥]\s*(\d+\.?\d*)', text)
            if prices:
                # 取该文本项中的最后一个价格
                raw = prices[-1]
                val = abs(float(raw))
                # 修正：以0开头的2位数（如"03"），OCR漏了小数点，实际应为0.X
                if len(raw) == 2 and raw[0] == '0':
                    val = float('0.' + raw[1])
                candidates.append(val)
            else:
                # 尝试纯数字
                price = _extract_price(text)
                if price != 0.0:
                    candidates.append(abs(price))

    if candidates:
        # 取最后一个候选价格（同一行最右侧的实际支付金额）
        return candidates[-1]
    return 0.0


def _find_delivery_fee(lines, target_top, x_threshold=0.4):
    """
    专门用于配送费的价格提取
    配送费行常见格式：
      - "¥7" 单一价格
      - "¥7 ¥1.5>" 原价和优惠后价格，取最后一个（优惠后实际配送费）
      - "¥8.2 ¥32>" OCR漏掉小数点，32实际是3.2
      - "¥6免配送费" 表示配送费被免除，实际为0

    修正逻辑：
      1. 如果文本含"免配送费"/"配送费免"等字样，直接返回0
      2. 否则取最后一个价格，如果它大于前面的价格（原价），
         说明OCR漏了小数点，在倒数第二位插入小数点让它变小
    """
    all_prices = []
    for item in lines:
        if abs(item["top"] - target_top) < 0.02 and item["left"] > x_threshold:
            text = item["text"]
            # 检测"免配送费"等关键字，表示配送费已被免除
            if "免配送费" in text or "配送费免" in text or "免运费" in text:
                return 0.0
            prices = re.findall(r'[¥￥]\s*(\d+\.?\d*)', text)
            if prices:
                all_prices.extend([abs(float(p)) for p in prices])
            else:
                price = _extract_price(text)
                if price != 0.0:
                    all_prices.append(abs(price))

    if not all_prices:
        return 0.0

    # 取最后一个价格
    last_price = all_prices[-1]

    # 只有单个价格且异常大（>50）：OCR 漏了小数点，如 "¥69" 实际是 6.9
    # 配送费不会超过50元（即使超重上调），修正后也不应超过40
    if len(all_prices) == 1 and last_price > 50:
        corrected = last_price / 10
        if corrected <= 40:
            return corrected

    # 如果有前一个价格（原价），且最后一个价格 >= 前一个价格
    # 说明OCR漏了小数点，需要缩小：依次尝试 /10、/100、/1000
    # 例如 32 -> 3.2, 333 -> 3.33（OCR 把 3.33 识别成 333）
    if len(all_prices) >= 2 and last_price >= all_prices[-2]:
        prev_price = all_prices[-2]
        for divisor in (10, 100, 1000):
            corrected = last_price / divisor
            # 修正后必须小于原价才算有效（优惠后配送费不会高于原价）
            if corrected < prev_price:
                return corrected
    return last_price


def _normalize_product_name(title, spec, sub_title="", extra_text=""):
    """
    将 OCR 识别的商品标题 + 规格标准化为干净格式
    参考表单命名规范:
      - 漓泉1998啤酒 500ml*12瓶
      - 燕京U8 500ml*12瓶
      - 雪花勇闯8度 500ml*12听
      - 雪花勇闯10度 500ml*12听
      - 青岛经典10度 500ml*12听
      - 百威9.7°啤酒 500ml*12听

    Args:
        title: OCR 识别的商品标题行（第一行）
        spec: OCR 识别的规格行，如 "规格：冰镇 500ml*12/箱"
        sub_title: OCR 识别的商品副标题行（第二行，如 "10°P经典罐装"）
        extra_text: 额外的 OCR 文本（如缩略图标签 "12听装"），用于补充规格信息

    Returns:
        str: 标准化后的产品名称
    """
    clean_title = title.strip()

    # 去掉常见前缀：【整箱】、【xxx】等
    clean_title = re.sub(r'^【[^】]*】\s*', '', clean_title)

    # 去掉 "12罐丨" "12罐|" "12罐 " 等数量前缀
    clean_title = re.sub(r'^\d+\s*[罐瓶听丨|]+\s*', '', clean_title)

    # 去掉 "冰镇" 等修饰词
    clean_title = clean_title.replace("冰镇", "")

    # 合并标题和副标题用于度数检测（包含规格用于640ml等特殊判断）
    combined = f"{clean_title} {sub_title} {extra_text} {spec}".strip()

    # =========================================================
    # 识别品牌和型号
    # =========================================================
    product_base = ""

    # --- 漓泉系列 ---
    # 注意：特酿/纯生/原浆等只是口味变体，不作为产品区分依据
    # 漓泉产品统一为 "漓泉1998啤酒"，按规格(ml和瓶/听/数量)区分
    # 不解析标题中的数字作为型号：OCR 常把 "8度" 等度数数字识别进标题
    # （如 "漓泉 小度特酿8度啤.."），导致输出 "漓泉8啤酒" 而非规范的 "漓泉1998啤酒"
    if "漓泉" in clean_title:
        product_base = "漓泉1998啤酒"

    # --- 燕京系列 ---
    # OCR 可能截断为 "燕." 或 "燕" 等
    elif "燕京" in clean_title or re.search(r'燕[.\s]*$', clean_title):
        model = ""
        m = re.search(r'U\s*(\d+)', clean_title, re.IGNORECASE)
        if m:
            num = m.group(1)
            # OCR 可能把 "U8 8°P" 识别为 "U88°P"，需要修正
            # 燕京只有 U8 这一款，如果数字是 "88" 则修正为 "8"
            if num == "88":
                num = "8"
            model = f"U{num}"
        else:
            m = re.search(r'8\s*°?\s*P', clean_title)
            if m:
                model = "U8"
        # U8 优先级最高，如果已找到 U8 不用 suffix 覆盖
        # 只有没找到 U8 时才用 suffix（特酿/纯生等）
        if not model:
            for suffix in ["纯生", "特酿", "原浆", "老炮", "小蓝妖"]:
                if suffix in clean_title:
                    model = suffix
                    break
        # 燕京目前只有 U8 一款产品，如果 OCR 只读到"燕京啤酒"无型号信息，默认 U8
        if not model:
            model = "U8"
        product_base = f"燕京{model}"

    # --- 雪花系列 ---
    elif "雪花" in clean_title or "勇闯" in clean_title or "snowbeer" in clean_title.lower():
        # 判断是否是 superx 系列
        is_superx = "superx" in clean_title.lower() or "superx" in sub_title.lower()

        # 提取度数
        degree = ""
        m = re.search(r'(\d+)\s*度', combined)
        if m:
            degree = m.group(1)
        elif re.search(r'(\d+)\s*°\s*[Pp]', combined):
            # 优先匹配 "8°P"（带P）
            m = re.search(r'(\d+)\s*°\s*[Pp]', combined)
            degree = m.group(1)
        elif re.search(r'(\d+)\s*°(?!\s*[Pp])', combined):
            # 匹配 "8°" 后面不跟P
            m = re.search(r'(\d+)\s*°(?!\s*[Pp])', combined)
            degree = m.group(1)
        elif re.search(r'superx\s*(\d+)', combined, re.IGNORECASE):
            m = re.search(r'superx\s*(\d+)', combined, re.IGNORECASE)
            degree = m.group(1)

        if is_superx:
            # superx 系列: "雪花啤酒8°P勇闯天涯superx"
            if degree:
                product_base = f"雪花啤酒{degree}°P勇闯天涯superx"
            else:
                product_base = "雪花啤酒8°P勇闯天涯superx"
        elif "老雪" in clean_title or "老雪花" in clean_title:
            # 老雪花系列: "雪花老雪12度"（规格固定640ml*12瓶）
            if degree:
                product_base = f"雪花老雪{degree}度"
            else:
                product_base = "雪花老雪12度"
        elif "勇闯" in clean_title or "勇闯" in sub_title:
            # 勇闯天涯系列: "雪花勇闯8度"
            if degree:
                product_base = f"雪花勇闯{degree}度"
            else:
                product_base = "雪花勇闯10度"
        elif "超爽" in clean_title or "超爽" in sub_title:
            # 雪花超爽系列: "雪花超爽8度"
            if degree:
                product_base = f"雪花超爽{degree}度"
            else:
                product_base = "雪花超爽8度"
        elif "老雪" in clean_title or "老雪花" in clean_title or "640" in combined:
            # 老雪/640ml瓶装系列（雪花640ml基本是老雪，OCR 可能漏读"老雪"只读"清爽"）
            if degree:
                product_base = f"雪花老雪{degree}度"
            else:
                product_base = "雪花老雪12度"
        elif "清爽" in clean_title or "清爽" in sub_title:
            # "清爽" 可能是产品线名，也可能是口味描述
            # 区分: "8°P清爽"(带P) -> 口味描述，归勇闯; "8°清爽"(不带P) -> 产品线名，归清爽
            has_degree_p = bool(re.search(r'\d+\s*°\s*[Pp]', combined))
            if has_degree_p:
                # "8°P清爽" -> 勇闯系列
                if degree:
                    product_base = f"雪花勇闯{degree}度"
                else:
                    product_base = "雪花勇闯8度"
            else:
                # "8°清爽" 或无度数 -> 清爽系列
                if degree:
                    product_base = f"雪花清爽{degree}度"
                else:
                    product_base = "雪花清爽8度"
        else:
            # 无勇闯/清爽/老雪/superx 关键字
            # "雪花啤酒 8°P" -> 默认清爽8度
            if degree:
                product_base = f"雪花清爽{degree}度"
            else:
                product_base = "雪花清爽8度"

    # --- 青岛系列 ---
    elif "青岛" in clean_title or "tsingtao" in clean_title.lower():
        # 提取度数: "11度" "110P" "11°P" "11°" "10°P" 等
        # 注意: OCR 常把 "11°P" 识别为 "110P"
        degree = ""
        m = re.search(r'(\d+)\s*度', combined)
        if m:
            degree = m.group(1)
        elif re.search(r'(\d+)\s*°\s*[Pp]', combined):
            m = re.search(r'(\d+)\s*°\s*[Pp]', combined)
            degree = m.group(1)
        elif re.search(r'(\d+)\s*°(?!\s*[Pp])', combined):
            m = re.search(r'(\d+)\s*°(?!\s*[Pp])', combined)
            degree = m.group(1)
        # OCR "110P" -> 11°P (11度)
        elif re.search(r'(\d{2})0[Pp]', combined):
            m = re.search(r'(\d{2})0[Pp]', combined)
            degree = m.group(1)

        # 按子类型优先级判断
        if "原浆" in combined or "7天" in combined:
            # 青岛啤酒7天13P原浆啤酒（特殊产品名）
            product_base = "青岛啤酒7天13P原浆啤酒"
        elif "奥古特" in combined:
            # 青岛12度奥古特
            if degree:
                product_base = f"青岛{degree}度奥古特"
            else:
                product_base = "青岛12度奥古特"
        elif "2000" in combined or "200." in combined or "200…" in combined:
            # 青岛2000 10度（OCR 可能将 "2000" 截断为 "200." 或 "200…"）
            if degree:
                product_base = f"青岛2000 {degree}度"
            else:
                product_base = "青岛2000 10度"
        elif "白啤" in combined or "全麦" in combined:
            # 青岛11度白啤
            if degree:
                product_base = f"青岛{degree}度白啤"
            else:
                product_base = "青岛11度白啤"
        elif "纯生" in combined:
            # 青岛纯生8度
            if degree:
                product_base = f"青岛纯生{degree}度"
            else:
                product_base = "青岛纯生8度"
        elif "冰醇" in combined or "冰纯" in combined:
            # 青岛冰醇8度
            if degree:
                product_base = f"青岛冰醇{degree}度"
            else:
                product_base = "青岛冰醇8度"
        elif "经典" in combined:
            # 青岛经典 / 青岛经典8度 / 青岛经典10度
            if degree:
                product_base = f"青岛经典{degree}度"
            else:
                # 标题中明确有"经典"关键字但无度数，默认10度
                product_base = "青岛经典10度"
        elif "清爽" in combined:
            # 青岛清爽8度
            if degree:
                product_base = f"青岛清爽{degree}度"
            else:
                product_base = "青岛清爽8度"
        else:
            # 无子类型关键字，按度数推断
            if degree == "10":
                # 10°P 无关键字 -> 青岛经典10度
                product_base = "青岛经典10度"
            elif degree == "8":
                # 8°P 无关键字 -> 青岛清爽8度
                product_base = "青岛清爽8度"
            elif degree == "11":
                # 11°P 无关键字 -> 青岛11度白啤（11度是白啤的标志度数）
                product_base = "青岛11度白啤"
            elif degree:
                product_base = f"青岛经典{degree}度"
            else:
                # 完全无度数信息，默认青岛经典10度
                product_base = "青岛经典10度"

    # --- 百威系列 ---
    elif "百威" in clean_title or "budweiser" in clean_title.lower():
        # 提取度数: "9.7°P" "9.7度" "9.7P" 等
        degree_val = ""
        m = re.search(r'(\d+\.?\d*)\s*°\s*[Pp]?', combined)
        if m:
            degree_val = m.group(1)
        elif re.search(r'(\d+\.?\d*)\s*度', combined):
            m = re.search(r'(\d+\.?\d*)\s*度', combined)
            degree_val = m.group(1)

        if "纯生" in combined:
            # 百威8°纯生啤酒
            if degree_val:
                product_base = f"百威{degree_val}°纯生啤酒"
            else:
                product_base = "百威8°纯生啤酒"
        elif "铝罐" in combined or "铝管" in combined:
            # 百威铝管啤酒（用户标准用"铝管"）
            product_base = "百威铝管啤酒"
        else:
            # 默认: 百威9.7°啤酒
            if degree_val:
                product_base = f"百威{degree_val}°啤酒"
            else:
                product_base = "百威9.7°啤酒"

    # --- 哈啤（哈尔滨啤酒）系列 ---
    # 命名格式: 哈尔滨{子类型}，不含度数和"啤酒"后缀
    # 如: 哈尔滨冰纯 / 哈尔滨小麦王 / 哈尔滨冰爽
    elif "哈啤" in clean_title or "哈尔滨" in clean_title or "harbin" in clean_title.lower():
        if "冰纯" in combined or "纯生" in combined or "冰…" in combined or "冰." in combined:
            # OCR 可能将"冰纯"截断为"冰…"或"冰."
            product_base = "哈尔滨冰纯"
        elif "小麦王" in combined or "小麦" in combined:
            product_base = "哈尔滨小麦王"
        elif "冰爽" in combined or "冰萃" in combined:
            product_base = "哈尔滨冰爽"
        else:
            product_base = "哈尔滨啤酒"

    # --- 乌苏系列 ---
    elif "乌苏" in clean_title:
        product_base = "乌苏啤酒"

    else:
        # 无法识别品牌，用清理后的标题
        product_base = clean_title.replace("瓶装", "").replace("听装", "").strip()
        product_base = re.sub(r'\d+\.\s*$', '', product_base).strip()

    # =========================================================
    # 从规格中提取 容量*数量+瓶/听
    # =========================================================
    clean_spec = spec.strip()
    clean_spec = re.sub(r'^规格\s*[：:]\s*', '', clean_spec)
    clean_spec = clean_spec.replace("冰镇", "").replace("常温", "").strip()

    # 提取容量: 500ml, 330ml, 600ml, 550ml, 640ml, 460ml, 310ml 等
    capacity = ""
    m = re.search(r'(\d+)\s*ml', clean_spec, re.IGNORECASE)
    if not m:
        # OCR 可能把 "ml" 识别为 "mL" 或 "m1"
        m = re.search(r'(\d+)\s*m\s*[l1]', clean_spec, re.IGNORECASE)
    if m:
        capacity = f"{m.group(1)}ml"
    if not capacity:
        # 从标题中提取
        m = re.search(r'(\d+)\s*ml', clean_title, re.IGNORECASE)
        if m:
            capacity = f"{m.group(1)}ml"
    # 特殊: "1L" / "1l" 格式（青岛原浆 1L*1瓶）
    if not capacity:
        m = re.search(r'(\d+)\s*[Ll]\b', clean_spec)
        if not m:
            m = re.search(r'(\d+)\s*[Ll]\b', clean_title)
        if m:
            capacity = f"{m.group(1)}L"
    if not capacity:
        if "老雪" in product_base:
            # 老雪花固定640ml
            capacity = "640ml"
        elif any(brand in product_base for brand in ["漓泉", "燕京", "雪花", "青岛", "百威", "哈尔滨", "乌苏"]):
            # 已识别品牌但无容量信息，默认 500ml
            capacity = "500ml"

    # 提取数量和单位
    # 注意：要排除 "约500件" 这种搜索框文本（已在spec_text中不会出现）
    count = ""
    unit = "瓶"
    # 优先匹配 "12瓶" "12听" "12罐" 格式，同时支持 "9罐/件+3罐" 这种组合规格
    m = re.search(r'(\d+)\s*(瓶|听|罐|只|支)', clean_spec)
    if m:
        count = m.group(1)
        # OCR 常将 "听" 识别为 "罐"，统一映射为 "听"（参考表命名规范）
        unit = m.group(2)
        if unit == "罐":
            unit = "听"
        # 检查是否有 "+N罐/听" 后缀（如 "9罐/件+3罐" -> "9+3听"）
        m_plus = re.search(r'\+\s*(\d+)\s*(瓶|听|罐|只|支)', clean_spec)
        if m_plus:
            plus_count = m_plus.group(1)
            count = f"{count}+{plus_count}"
    else:
        # 匹配 "500ml*12" 格式（* 后面的数字是数量）
        m = re.search(r'\*\s*(\d+)\s*$', clean_spec)
        if m:
            count = m.group(1)
            unit = "瓶"  # 默认瓶
        else:
            m = re.search(r'(\d+)\s*/\s*(箱|件)', clean_spec)
            if m:
                count = m.group(1)
                unit = "瓶"
            # 注意：不再用 re.search(r'(\d+)', clean_spec) 兜底，
            # 因为会误匹配 "约500件" 等无关数字

    # 如果规格行没找到，从标题中提取 "500ml*12罐" 或 "500mLx12听" 格式
    if not count:
        m = re.search(r'[*xX×]\s*(\d+)\s*(瓶|听|罐|只|支)', clean_title)
        if m:
            count = m.group(1)
            unit = m.group(2)
            if unit == "罐":
                unit = "听"
        else:
            m = re.search(r'(\d+)\s*(瓶|听|罐|只|支)', clean_title)
            if m:
                count = m.group(1)
                unit = m.group(2)
                if unit == "罐":
                    unit = "听"

    # 从缩略图标签提取 "12听装" "12瓶装" "12罐装" 格式
    if not count or (unit == "瓶" and "听" in extra_text):
        m = re.search(r'(\d+)\s*(听|罐)装', extra_text)
        if m:
            count = m.group(1)
            unit = "听"  # 缩略图标签优先用"听"

    # 如果还是没找到数量，默认12
    if not count and capacity:
        count = "12"

    # 特殊产品规格修正
    # 青岛原浆: OCR 可能识别为 "11*1桶"，实际应为 "1L*1瓶"
    if "原浆" in product_base and "7天" in product_base:
        capacity = "1L"
        count = "1"
        unit = "瓶"

    # 拼接规格部分
    spec_part = ""
    if capacity and count:
        spec_part = f"{capacity}*{count}{unit}"
    elif capacity:
        spec_part = capacity

    # =========================================================
    # 拼接最终产品名
    # =========================================================
    if spec_part:
        return f"{product_base} {spec_part}"
    return product_base


def parse_ocr_to_fields(ocr_results, region=""):
    """
    将 OCR 结果解析为表单字段

    Args:
        ocr_results: list[dict]，ocr_engine.run_ocr() 的返回值
        region: 所属主要区域（"XX市"），由 GUI 传入

    Returns:
        FormFields
    """
    fields = FormFields()
    fields.region = region

    if not ocr_results:
        return fields

    lines = ocr_results
    texts = [item["text"] for item in lines]
    full_text = "\n".join(texts)

    # =========================================================
    # C: 平台识别 - 区分"美团闪购"和"淘宝闪购"
    # =========================================================
    # 淘宝闪购特征: "蜂鸟"/"淘金币"(OCR可能误读为"淘金市")/"淘宝"/"提交订单"+"合计"/"商家自配送"
    # 美团闪购特征: "极速支付"/"找人付"/"美团红包"/"共减"等
    is_taobao = any(k in full_text for k in ["蜂鸟", "淘金币", "淘金市", "淘宝", "提交订单"])
    if is_taobao:
        fields.platform = "淘宝闪购"
    else:
        fields.platform = "美团闪购"

    # =========================================================
    # D: 店铺名称 - "选择收货地址" 下方的店铺名行
    # =========================================================
    # 优先策略：店铺卡片行 = 店铺名 + 同行"共N件约Xkg"（美团/淘宝结算页标准布局）
    # 页面顶部可能有入口店名（如"雀嘻嘻•24小时自助棋牌"在收货人上方），
    # 但实际订单店铺是配送方式下方、商品上方的卡片行（如"惠到家（远东店） 共1件约6kg"）
    if not fields.shop_name:
        for item in lines:
            text = item["text"].strip()
            if not text or item["left"] >= 0.15:
                continue
            target_top = item["top"]
            # 同行（top 相近）存在"共N件"文本 = 店铺卡片
            has_count = False
            for item2 in lines:
                if abs(item2["top"] - target_top) < 0.02 and re.search(r'共\s*\d+\s*件', item2["text"]):
                    has_count = True
                    break
            if not has_count:
                continue
            # 排除纯价格/数字行
            if re.match(r'^[¥￥\d\.\s*/:：\-]+$', text):
                continue
            # 合并同一行(top相近)的其他文本块，拼接完整店铺名
            same_line_texts = []
            for item2 in lines:
                if abs(item2["top"] - target_top) < 0.02 and item2["left"] < 0.5:
                    t = item2["text"].strip()
                    if t and not re.match(r'^[¥￥\d\.\s*/:：\-]+$', t):
                        same_line_texts.append((item2["left"], t))
            same_line_texts.sort(key=lambda x: x[0])
            if same_line_texts:
                raw = "".join(t for _, t in same_line_texts)
                # 清理尾部商品类目噪声
                raw = re.sub(r'[（(]\s*(啤酒|红酒|洋酒|白酒).*?[）)]', '', raw)
                raw = re.sub(r'[•·]\s*(啤酒|红酒|洋酒|白酒).*$', '', raw)
                raw = re.sub(r'(啤酒|红酒|洋酒|白酒)[•·].*$', '', raw)
                raw = re.sub(r'[（(•·…\s]+$', '', raw)
                raw = raw.strip()
                if raw:
                    fields.shop_name = raw
                    break

    # 以下为原逻辑（仅在卡片模式未命中时生效）
    addr_idx = _find_line_by_keyword(lines, "选择收货地址")
    if addr_idx >= 0:
        # 策略1: 在"选择收货地址"下方范围内找包含店铺关键词的行
        shop_keywords = SHOP_KEYWORDS
        for i in range(addr_idx + 1, min(addr_idx + 15, len(lines))):
            text = lines[i]["text"].strip()
            if not text:
                continue
            # 排除明显的非店铺文本
            if any(k in text for k in ["送出", "送达", "去选择", "自配", "商品总价",
                                        "打包费", "配送费", "商家活动", "美团红包",
                                        "店铺券", "极速支付", "找人付", "共减",
                                        "收货人", "超时退费", "安心购", "赠"]):
                continue
            # 排除纯价格/数字行（含"件""个"等量词）
            if re.match(r'^[¥￥\d\.\s*/]+(件|个|瓶|听|罐)?$', text):
                continue
            # 排除配送相关标签
            if text in ["美团快送", "1对1急送", "到店自取", "送货上门", "立即送出"]:
                continue
            # 优先匹配包含店铺关键词的行
            if any(kw in text for kw in shop_keywords):
                # 合并同一行(top相近)的其他文本块，拼接完整店铺名
                # 例如 "易捷速购" + "（柳州跃进店）" 在同一行但不同left
                target_top = lines[i]["top"]
                same_line_texts = []
                for item in lines:
                    if abs(item["top"] - target_top) < 0.015 and item["left"] < 0.5:
                        t = item["text"].strip()
                        if t and not re.match(r'^[¥￥\d\.\s*/:：\-]+$', t):
                            same_line_texts.append((item["left"], t))
                # 按left排序拼接
                same_line_texts.sort(key=lambda x: x[0])
                if same_line_texts:
                    raw = "".join(t for _, t in same_line_texts)
                else:
                    raw = text
                # 清理尾部商品类目噪声（如"酒零鹿•现调鸡尾酒•啤酒洋酒红酒•白酒酒杯酒具•"）
                raw = re.sub(r'[（(]\s*(啤酒|红酒|洋酒|白酒).*?[）)]', '', raw)
                raw = re.sub(r'[•·]\s*(啤酒|红酒|洋酒|白酒).*$', '', raw)
                raw = re.sub(r'(啤酒|红酒|洋酒|白酒)[•·].*$', '', raw)
                raw = re.sub(r'[（(•·…\s]+$', '', raw)
                raw = raw.strip()
                if raw:
                    fields.shop_name = raw
                break

        # 策略2: 如果策略1没找到，取"选择收货地址"下方第一个有效文本
        if not fields.shop_name:
            for i in range(addr_idx + 1, min(addr_idx + 15, len(lines))):
                text = lines[i]["text"].strip()
                if not text:
                    continue
                # 排除所有非店铺文本
                if any(k in text for k in ["送出", "送达", "去选择", "自配",
                                            "美团快送", "1对1急送", "到店自取",
                                            "送货上门", "立即送出", "商品总价",
                                            "打包费", "配送费", "商家活动",
                                            "美团红包", "店铺券", "极速支付",
                                            "找人付", "共减", "收货人", "超时退费",
                                            "安心购", "赠", "约"]):
                    continue
                # 排除纯价格/数字/时间行
                if re.match(r'^[¥￥\d\.\s*/:：\-]+$', text):
                    continue
                # 排除规格/商品标题行
                if any(k in text for k in ["规格", "漓泉", "燕京", "啤酒", "整箱"]):
                    continue
                fields.shop_name = text
                break

    # 备用：全局找包含店铺关键词的行
    # 排除淘宝未成年人提示等非店铺文本
    _EXCLUDE_TEXTS = ["依据法律规定", "未成年人", "限制购买", "18周岁"]
    if not fields.shop_name:
        for item in lines:
            text = item["text"].strip()
            # 排除淘宝未成年人提示行
            if any(k in text for k in _EXCLUDE_TEXTS):
                continue
            if any(k in text for k in SHOP_KEYWORDS) and item["left"] < 0.5:
                # 排除商品行（但"嗨酒""酒行"等是店铺名的一部分，不应排除）
                if any(k in text for k in ["漓泉", "燕京", "规格"]):
                    continue
                # 只在包含"啤酒"且没有店铺关键词时排除
                # 例如"洪马嗨酒（啤酒•红洋酒）"含"嗨酒"是店铺名，不应排除
                shop_kw_in_text = [k for k in SHOP_KEYWORDS if k in text]
                if "啤酒" in text and not shop_kw_in_text:
                    continue
                # 淘宝闪购店铺名行含前缀（"闪购"OCR可能误读为"沟购""河购"等），去掉前缀
                if fields.platform == "淘宝闪购":
                    text = re.sub(r'^(闪购|沟购|河购)\s*', '', text)
                # 清理尾部商品类目噪声（如"（啤酒•红洋酒）" / "•红酒•洋酒"）
                text = re.sub(r'[（(]\s*(啤酒|红酒|洋酒|白酒).*?[）)]', '', text)
                text = re.sub(r'[•·]\s*(啤酒|红酒|洋酒|白酒).*$', '', text)
                text = re.sub(r'(啤酒|红酒|洋酒|白酒)[•·].*$', '', text)
                text = re.sub(r'[（(•·…\s]+$', '', text)
                text = text.strip()
                if text:
                    fields.shop_name = text
                    break

    # 备用2: 淘宝闪购店铺名以"闪购/沟购/河购"开头（OCR误读"闪"为"沟/河"）
    # 店铺名行特征: top 0.83~0.88, left < 0.1, 以这些前缀开头
    # 例如 "闪购 三亚楚龙百货商行" / "河购 美兰酒速到" / "闪购 1516酒盒子速配啤酒•红酒•洋酒••"
    if not fields.shop_name and fields.platform == "淘宝闪购":
        for item in lines:
            text = item["text"].strip()
            if 0.80 < item["top"] < 0.90 and item["left"] < 0.1:
                m = re.match(r'^(闪购|沟购|河购)\s*(.+)', text)
                if m:
                    shop = m.group(2).strip()
                    # 去掉尾部噪声（如 "•红酒•洋酒••"）
                    shop = re.sub(r'[•·…\s]+$', '', shop)
                    # 去掉商品类目后缀（如 "啤酒•红酒•洋酒" 或 "•红酒•洋酒"）
                    # "1516酒盒子速配啤酒•红酒•洋酒" -> "1516酒盒子速配"
                    shop = re.sub(r'(啤酒|红酒|洋酒|白酒|洋酒)[•·].*$', '', shop)
                    shop = re.sub(r'[•·]\s*(啤酒|红酒|洋酒|白酒).*$', '', shop)
                    shop = shop.strip()
                    if shop:
                        fields.shop_name = shop
                        break

    # 备用3: 美团没有"选择收货地址"的截图（已设地址的结算页）
    # 店铺名在收货地址行和商品之间，通常 top 0.55~0.68, left < 0.1
    # 例如 "1516酒盒子速配" / "1516酒盒子速配（解放店）" / "酒分赞酒行（天河）"
    if not fields.shop_name and fields.platform == "美团闪购":
        for item in lines:
            text = item["text"].strip()
            if 0.55 < item["top"] < 0.68 and item["left"] < 0.1:
                # 排除非店铺文本
                if any(k in text for k in ["商品总价", "打包费", "配送费", "商家活动",
                                            "收货", "地址", "收货人", "送出", "送达"]):
                    continue
                # 排除纯价格/数字行
                if re.match(r'^[¥￥\d\.\s*/:：\-]+$', text):
                    continue
                # 排除规格/商品标题行（但如果含店铺关键词则不排除）
                shop_kws = SHOP_KEYWORDS
                has_shop_kw = any(k in text for k in shop_kws)
                if not has_shop_kw:
                    if any(k in text for k in ["规格", "漓泉", "燕京", "啤酒", "整箱", "ml"]):
                        continue
                # 合并同一行文本
                target_top = item["top"]
                same_line_texts = []
                for item2 in lines:
                    if abs(item2["top"] - target_top) < 0.015 and item2["left"] < 0.5:
                        t = item2["text"].strip()
                        if t and not re.match(r'^[¥￥\d\.\s*/:：\-]+$', t):
                            same_line_texts.append((item2["left"], t))
                same_line_texts.sort(key=lambda x: x[0])
                if same_line_texts:
                    raw = "".join(t for _, t in same_line_texts)
                else:
                    raw = text
                # 清理尾部商品类目噪声（如"（啤酒•红洋酒）" / "•啤酒•红酒•洋酒"）
                # 去掉括号内含酒类的后缀: "洪马嗨酒（啤酒•红洋酒）" -> "洪马嗨酒"
                raw = re.sub(r'[（(]\s*(啤酒|红酒|洋酒|白酒).*?[）)]', '', raw)
                # 去掉•或·连接的酒类后缀: "1516酒盒子速配•啤酒•红酒" -> "1516酒盒子速配"
                raw = re.sub(r'[•·]\s*(啤酒|红酒|洋酒|白酒).*$', '', raw)
                raw = re.sub(r'(啤酒|红酒|洋酒|白酒)[•·].*$', '', raw)
                # 清理残留的尾部标点
                raw = re.sub(r'[（(•·…\s]+$', '', raw)
                raw = raw.strip()
                if raw:
                    fields.shop_name = raw
                    break

    # =========================================================
    # E: 产品名称 - 标准化为 "品牌型号 容量*数量瓶/听"
    # =========================================================
    # 找商品名称行（包含各品牌关键词）
    # 注意：品牌关键词和"啤酒"要分开处理，"啤酒"太宽泛容易误匹配
    brand_keywords = [
        "漓泉", "燕京", "燕.", "乌苏", "雪鹿", "雪花", "勇闯", "superx",
        "青岛", "tsingtao", "百威", "budweiser", "哈啤", "哈尔滨", "harbin",
        "老炮", "小蓝妖",
    ]
    # 这些词必须和品牌关键词同时出现才算商品行
    product_suffix_keywords = ["纯生", "特酿", "经典", "清爽", "超爽", "冰醇", "原浆", "度", "°", "罐装", "瓶装", "听装", "ml", "啤"]

    product_title = ""
    spec_text = ""
    sub_title = ""

    for i, item in enumerate(lines):
        text = item["text"].strip()
        if not text or item["left"] >= 0.5:
            continue
        # 排除搜索框、历史搜索、结算明细等
        if any(k in text for k in ["搜索", "历史", "商品总价", "商家活动", "美团红包",
                                    "店铺券", "配送费", "打包费"]):
            continue
        # 排除底部推荐商品行（通常在 top < 0.15 区域）
        if item["top"] < 0.15:
            continue
        # 排除顶部状态栏/导航栏（通常在 top > 0.85 区域，如 "百威" "雪花" 等品牌名）
        if item["top"] > 0.85:
            continue
        # 排除"超值换购"推荐区域（top 0.15~0.35 的品牌关键词行是推荐商品，不是主商品）
        if 0.15 < item["top"] < 0.35:
            continue
        # 排除店铺名称行（包含"店）""超市"等且包含"精酿啤酒馆"等）
        if any(k in text for k in ["精酿啤酒馆", "啤酒馆", "酒保爷"]):
            continue
        # 排除广告文案行
        if any(k in text for k in ["轻奢", "慢酿", "醇正", "清爽解渴", "不黏腻"]):
            continue

        # 判断是否是商品标题行：
        # 1) 包含品牌关键词 + 规格后缀（度/°/罐装/ml/啤酒等）
        # 2) 或者包含品牌关键词 + "啤酒"（但排除店铺行）
        has_brand = any(kw in text for kw in brand_keywords)
        has_suffix = any(kw in text for kw in product_suffix_keywords)

        # 商品标题行通常在 left=0.2 附近（商品区域），不是店铺名(left<0.1)
        is_product = False
        if has_brand and has_suffix:
            is_product = True
        elif has_brand and item["left"] > 0.15:
            # 品牌词在商品区域（left > 0.15），即使没有后缀也算
            # 处理 OCR 截断的情况，如 "12罐|【整箱】雪花⋯" "雪花 勇闯天涯superX 8."
            is_product = True

        if not is_product:
            continue

        # 清理 OCR 噪声
        product_title = text
        # 向下找规格行和副标题行
        for j in range(i + 1, min(i + 8, len(lines))):
            spec = lines[j]["text"].strip()
            if "规格" in spec or ("瓶" in spec and "ml" in spec.lower()) or "听" in spec or "罐" in spec:
                spec_text = spec
                break
        # 找副标题行（度数信息或型号信息）
        # 副标题在标题下方，Y坐标接近，不含"规格"
        title_top = item["top"]
        for j in range(i + 1, min(i + 6, len(lines))):
            sub = lines[j]["text"].strip()
            if "规格" in sub:
                continue
            if abs(lines[j]["top"] - title_top) < 0.05:
                # 检查是否含度数信息或型号关键词
                has_degree = re.search(r'\d+\s*°\s*[Pp]?', sub) or re.search(r'\d+\s*度', sub)
                has_type = any(k in sub for k in ["经典", "清爽", "特酿", "纯生", "原浆", "superX", "superx"])
                if has_degree or has_type:
                    sub_title = sub
                    break
        break

    # 提取缩略图标签文本（如 "12听装" "12瓶装" "全囊白啤" 等）
    # 这些文本在 left < 0.1 的缩略图区域，top 接近标题行
    extra_text = ""
    for item in lines:
        if item["left"] < 0.12 and 0.50 < item["top"] < 0.75:
            t = item["text"].strip()
            # 提取 "X听装" "X瓶装" "X罐装" 或 "白啤" "全麦白啤" 等关键词
            if re.search(r'\d+\s*(听|瓶|罐)装', t) or "白啤" in t or "全麦" in t:
                extra_text += " " + t

    # 标准化产品名称
    if product_title:
        fields.product_name = _normalize_product_name(product_title, spec_text, sub_title, extra_text.strip())

    # =========================================================
    # F: 产品原价 — 统一为"含配送打包的总价"口径
    # 新版美团UI: "总价"行（金额已含配送+打包，直接取，如 73.9 = 66+6.9+1）
    # 旧版美团/淘宝UI: "商品总价"行（金额不含配送+打包，需累加配送费原价+打包费，
    #   使不同版本记录口径一致、可比较）
    # 注意：OCR 常漏掉小数点（73.9 识别成 739），用 _extract_price_safe 自动修正
    # =========================================================
    total_idx = -1
    for i, item in enumerate(lines):
        t = item["text"].strip().replace("～", "").replace("~", "")
        if t == "总价" or t.startswith("总价"):
            total_idx = i
            break
    if total_idx >= 0:
        # 新版UI: 总价行已含配送打包，直接取
        target_top = lines[total_idx]["top"]
        for item in lines:
            if abs(item["top"] - target_top) < 0.02 and item["left"] > 0.4:
                price = _extract_price_safe(item["text"])
                if price > 0:
                    fields.original_price = price
                    break
        if fields.original_price == 0.0:
            fields.original_price = _find_price_by_x_alignment(lines, target_top)
    else:
        # 旧版UI: "商品总价"不含配送打包，累加配送费(划线原价)+打包费
        total_idx = _find_line_by_keyword(lines, "商品总价")
        if total_idx >= 0:
            target_top = lines[total_idx]["top"]
            goods_price = 0.0
            for item in lines:
                if abs(item["top"] - target_top) < 0.02 and item["left"] > 0.4:
                    goods_price = _extract_price_safe(item["text"])
                    break
            if goods_price == 0.0:
                goods_price = _find_price_by_x_alignment(lines, target_top)
            if goods_price > 0:
                # 打包费
                pack_fee = 0.0
                pack_idx = _find_line_by_keyword(lines, "打包费")
                if pack_idx >= 0:
                    pack_fee = _find_price_by_x_alignment(lines, lines[pack_idx]["top"])
                # 配送费：取划线原价（第一个价格），与新版总价用配送原价口径一致
                ship_fee = 0.0
                ship_idx = _find_line_by_keyword(lines, "配送费")
                if ship_idx >= 0:
                    ship_top = lines[ship_idx]["top"]
                    for item in lines:
                        if abs(item["top"] - ship_top) < 0.02 and item["left"] > 0.4:
                            text = item["text"]
                            if any(k in text for k in ["免配送费", "配送费免", "免运费"]):
                                ship_fee = 0.0
                                break
                            prices = re.findall(r'[¥￥]\s*(\d+\.?\d*)', text)
                            if prices:
                                val = abs(float(prices[0]))
                                # 原价也可能漏小数点（¥69 实际 6.9）
                                if val > 50:
                                    val = val / 10
                                ship_fee = val
                                break
                            price = _extract_price(text)
                            if price != 0.0:
                                ship_fee = abs(price)
                                break
                fields.original_price = round(goods_price + pack_fee + ship_fee, 2)

    # 备用：找商品列表中的 ¥xx/件（原价标价）
    if fields.original_price == 0.0:
        for item in lines:
            text = item["text"]
            # "¥65/件" 这种格式是原价
            # 跳过"优惠价¥50.5/件"这类行——那是优惠价，不是划线原价
            if "优惠" in text:
                continue
            m = re.search(r'[¥￥](\d+\.?\d*)/件', text)
            if m:
                fields.original_price = float(m.group(1))
                break

    # 淘宝闪购备用：部分页面（如天猫超市）无"商品总价"行，商品标题右侧的¥xx即为原价
    if fields.original_price == 0.0 and fields.platform == "淘宝闪购":
        for item in lines:
            text = item["text"].strip()
            if any(kw in text for kw in ["燕京", "漓泉", "雪花", "青岛", "百威", "哈尔滨"]) and item["left"] < 0.3:
                target_top = item["top"]
                for item2 in lines:
                    if abs(item2["top"] - target_top) < 0.02 and item2["left"] > 0.5:
                        price = _extract_price_safe(item2["text"])
                        if price > 0:
                            fields.original_price = price
                            break
                if fields.original_price > 0:
                    break

    # =========================================================
    # G: 产品成交单价 - 底部总价 "¥39.4共减¥27.1" 这种格式
    # =========================================================
    # 美团结算页最底部有一行 "¥39.4共减¥271~"，是最终成交价
    # OCR 可能把底部拆成多个文本块，如 "¥53" + ".4 共减¥8.1八"
    # 需要合并底部同一行的文本再提取

    # 策略1: 找 "¥xx.x共减" 连在一起的完整格式
    found_final = False
    for item in lines:
        text = item["text"]
        # 必须同时包含¥和共减，且¥在共减前面
        if "共减" in text and item["top"] < 0.08:
            # 提取"共减"前面的价格
            before_gj = text.split("共减")[0]
            m = re.search(r'[¥￥](\d+\.?\d*)', before_gj)
            if m:
                # OCR 可能漏小数点（¥631 实际是 63.1），用 safe 修正
                fields.final_price = _extract_price_safe("¥" + m.group(1), 200)
                found_final = True
                break

    # 策略2: 淘宝闪购 - "合计 ¥67.3" / "合计¥66.5" / "已优惠¥24 ¥67.3" 格式
    if not found_final and fields.platform == "淘宝闪购":
        for item in lines:
            text = item["text"].strip()
            # 结算明细的"合计"行在页面中部（top<0.5），底部支付栏也有"合计"（OCR易漏小数点）
            # 取先遇到的（更靠上的）结算明细行，金额更可靠
            if "合计" in text and item["top"] < 0.50:
                # "已优惠¥24 ¥70.3" 有两个价格，取最后一个（成交价）
                prices = re.findall(r'[¥￥](\d+\.?\d*)', text)
                if prices:
                    fields.final_price = _extract_price_safe("¥" + prices[-1], 200)
                    found_final = True
                    break

    # 策略2: 合并底部同一行(top相近)的所有文本块，再提取
    if not found_final:
        bottom_items = [item for item in lines if item["top"] < 0.08]
        if bottom_items:
            # 找最底部的行（top最小的）
            min_top = min(item["top"] for item in bottom_items)
            same_line = [item for item in bottom_items if abs(item["top"] - min_top) < 0.015]
            # 按left排序，从左到右拼接
            same_line.sort(key=lambda x: x["left"])
            merged_text = "".join(item["text"] for item in same_line)
            # 从合并文本中提取成交价：找"¥xx.x共减"格式
            m = re.search(r'[¥￥](\d+\.?\d*)\s*共?减', merged_text)
            if m:
                fields.final_price = _extract_price_safe("¥" + m.group(1), 200)
            else:
                # 如果没有"共减"，取最左侧的¥价格
                prices = re.findall(r'[¥￥](\d+\.?\d*)', merged_text)
                if prices:
                    fields.final_price = _extract_price_safe("¥" + prices[0], 200)

    # 备用：找最底部（top < 0.06）的 ¥xx.x 格式价格
    if fields.final_price == 0.0:
        for item in lines:
            text = item["text"].strip()
            m = re.match(r'^[¥￥](\d+\.?\d*)', text)
            if m and item["top"] < 0.06:
                fields.final_price = _extract_price_safe("¥" + m.group(1), 200)
                break

    # =========================================================
    # H: 商品优惠/商品活动 - "减 商家活动" 行
    # =========================================================
    discount_idx = _find_line_by_keyword(lines, "商家活动")
    if discount_idx < 0:
        discount_idx = _find_line_by_keyword(lines, "商品活动")
    if discount_idx < 0:
        # 新版美团 UI: "减 活动 -¥8.5"（OCR 可能拆成"减"+"活动"两个文本块）
        # 特征: left < 0.5 的行含"活动"，且同行(top相近)有"减"标签
        for i, item in enumerate(lines):
            if item["left"] < 0.5 and "活动" in item["text"]:
                for item2 in lines:
                    if abs(item2["top"] - item["top"]) < 0.02 and "减" in item2["text"]:
                        discount_idx = i
                        break
                if discount_idx >= 0:
                    break
    if discount_idx >= 0:
        # 商家活动优惠金额，OCR 可能漏掉小数点（如 ¥461 实际 ¥4.61）
        target_top = lines[discount_idx]["top"]
        for item in lines:
            if abs(item["top"] - target_top) < 0.02 and item["left"] > 0.4:
                fields.shop_discount = _extract_price_safe(item["text"], 100)
                break

    # 淘宝闪购: "活动优惠" 行（如 -¥3）
    if fields.platform == "淘宝闪购" and fields.shop_discount == 0.0:
        act_idx = _find_line_by_keyword(lines, "活动优惠")
        if act_idx >= 0:
            target_top = lines[act_idx]["top"]
            for item in lines:
                if abs(item["top"] - target_top) < 0.02 and item["left"] > 0.4:
                    price = _extract_price_safe(item["text"], 100)
                    if price > 0:
                        fields.shop_discount = price
                        break

    # 淘宝闪购: "商品优惠" 行 - 右侧通常无金额，从底部"已优惠¥xx"提取
    if fields.platform == "淘宝闪购" and fields.shop_discount == 0.0:
        disc_idx = _find_line_by_keyword(lines, "商品优惠")
        if disc_idx >= 0:
            # 先尝试同行右侧找价格
            target_top = lines[disc_idx]["top"]
            for item in lines:
                if abs(item["top"] - target_top) < 0.02 and item["left"] > 0.4:
                    price = _extract_price_safe(item["text"], 100)
                    if price > 0:
                        fields.shop_discount = price
                        break
            # 如果同行没有价格，从底部"已优惠¥xx"提取
            if fields.shop_discount == 0.0:
                for item in lines:
                    text = item["text"].strip()
                    if "已优惠" in text and item["top"] < 0.20:
                        # 格式如 "已优惠¥20.1 ¥70.2" 或 "已优惠 ¥20.1 ¥70.2"
                        m = re.search(r'已优惠\s*[¥￥]\s*(\d+\.?\d*)', text)
                        if m:
                            fields.shop_discount = float(m.group(1))
                            break

    # =========================================================
    # I: 满减活动 - "满减" 或 "店铺券/商品券" 行
    # 注意：店铺券/商品券归入满减活动(I列)，不归入优惠券(J列)
    # =========================================================
    mr_idx = _find_line_by_keyword(lines, "满减")
    if mr_idx >= 0:
        price = _find_price_by_x_alignment(lines, lines[mr_idx]["top"])
        fields.full_reduction = price

    # 店铺券/商品券 -> 满减活动(I列)
    shop_coupon_idx = _find_line_by_keyword(lines, "店铺券")
    if shop_coupon_idx < 0:
        shop_coupon_idx = _find_line_by_keyword(lines, "商品券")
    if shop_coupon_idx >= 0:
        target_top = lines[shop_coupon_idx]["top"]
        # 检查是否"暂无可用"
        has_no = False
        for item in lines:
            if abs(item["top"] - target_top) < 0.02 and "暂无" in item["text"]:
                has_no = True
                break
        if not has_no:
            for item in lines:
                if abs(item["top"] - target_top) < 0.02 and item["left"] > 0.4:
                    price = _extract_price_safe(item["text"], 100)
                    if price > 0:
                        fields.full_reduction = price
                        break
            if fields.full_reduction == 0.0:
                fields.full_reduction = _find_price_by_x_alignment(lines, target_top)

    # 淘宝闪购: "店铺/商品红包" 行 -> 满减活动(I列)
    if fields.platform == "淘宝闪购" and fields.full_reduction == 0.0:
        shop_red_idx = _find_line_by_keyword(lines, "店铺/商品红包")
        if shop_red_idx < 0:
            shop_red_idx = _find_line_by_keyword(lines, "商品红包")
        if shop_red_idx >= 0:
            target_top = lines[shop_red_idx]["top"]
            # 排除"无可用红包"
            has_no = False
            for item in lines:
                if abs(item["top"] - target_top) < 0.02 and "无可用" in item["text"]:
                    has_no = True
                    break
            if not has_no:
                for item in lines:
                    if abs(item["top"] - target_top) < 0.02 and item["left"] > 0.4:
                        price = _extract_price_safe(item["text"])
                        if price > 0:
                            fields.full_reduction = price
                            break

    # =========================================================
    # J: 优惠券 - "神券" 行（不含"不参加神券优惠"的商品描述）
    # 美团红包行实际显示的也是神券，归入优惠券(J列)
    # =========================================================
    # 优先找"神券"行，但排除"不参加神券优惠"这种商品描述行
    coupon_idx = -1
    for i, item in enumerate(lines):
        if ("神券" in item["text"] or "神劵" in item["text"]) and "不参加" not in item["text"]:
            coupon_idx = i
            break
    if coupon_idx >= 0:
        target_top = lines[coupon_idx]["top"]
        # 检查是否"暂无可用"
        has_no_coupon = False
        for item in lines:
            if abs(item["top"] - target_top) < 0.02 and "暂无" in item["text"]:
                has_no_coupon = True
                break
        if not has_no_coupon:
            for item in lines:
                if abs(item["top"] - target_top) < 0.02 and item["left"] > 0.4:
                    fields.coupon = _extract_price_safe(item["text"], 100)
                    break
            if fields.coupon == 0.0:
                fields.coupon = _find_price_by_x_alignment(lines, target_top)

    # 如果优惠券还没找到，从"美团红包"行找（美团红包行实际是神券）
    if fields.coupon == 0.0:
        red_idx = _find_line_by_keyword(lines, "美团红包")
        if red_idx >= 0:
            target_top = lines[red_idx]["top"]
            # 检查是否"暂无可用"或"最高可享"
            has_no_coupon = False
            for item in lines:
                if abs(item["top"] - target_top) < 0.02 and ("暂无" in item["text"] or "最高可享" in item["text"]):
                    has_no_coupon = True
                    break
            if not has_no_coupon:
                for item in lines:
                    if abs(item["top"] - target_top) < 0.02 and item["left"] > 0.4:
                        price = _extract_price_safe(item["text"], 100)
                        if price > 0 and price < 100:
                            fields.coupon = price
                            break

    # =========================================================
    # K: 红包 - 淘宝闪购有"平台红包"，美团基本为0
    # 美团红包行实际显示的是神券，已归入优惠券(J列)
    # =========================================================
    if fields.platform == "淘宝闪购":
        red_idx = _find_line_by_keyword(lines, "平台红包")
        if red_idx >= 0:
            target_top = lines[red_idx]["top"]
            for item in lines:
                if abs(item["top"] - target_top) < 0.02 and item["left"] > 0.4:
                    fields.red_packet = _extract_price_safe(item["text"], 100)
                    break
            if fields.red_packet == 0.0:
                fields.red_packet = _find_price_by_x_alignment(lines, target_top)
    else:
        fields.red_packet = 0.0

    # =========================================================
    # L: 打包配送费 - "打包费" + "配送费" 之和
    # =========================================================
    pack_fee = 0.0
    ship_fee = 0.0

    pack_idx = _find_line_by_keyword(lines, "打包费")
    if pack_idx >= 0:
        pack_fee = _find_price_by_x_alignment(lines, lines[pack_idx]["top"])

    ship_idx = _find_line_by_keyword(lines, "配送费")
    if ship_idx >= 0:
        # 配送费行可能有 "¥7 ¥1.5>" 格式（原价和优惠后），取最后一个
        # 使用专门的配送费提取函数处理 OCR 小数点丢失
        ship_fee = _find_delivery_fee(lines, lines[ship_idx]["top"])

    fields.delivery_fee = pack_fee + ship_fee

    return fields


def parse_ocr_batch(ocr_results_map, region=""):
    """
    批量解析 OCR 结果

    Args:
        ocr_results_map: dict {image_path: ocr_results}
        region: 所属主要区域

    Returns:
        dict {image_path: FormFields}
    """
    results = {}
    for path, ocr_data in ocr_results_map.items():
        if isinstance(ocr_data, dict) and "error" in ocr_data:
            results[path] = None
        else:
            results[path] = parse_ocr_to_fields(ocr_data, region)
    return results


if __name__ == "__main__":
    # 测试
    import json
    import sys

    if len(sys.argv) < 2:
        print("用法: python field_parser.py <ocr_json_file>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        ocr_data = json.load(f)

    fields = parse_ocr_to_fields(ocr_data, "南宁市人民政政府")
    for k, v in fields.to_dict().items():
        print(f"  {k}: {v}")
