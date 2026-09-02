/// 字段解析器：OCR 文本 -> FormFields
/// 移植自桌面版 field_parser.py 的 parse_ocr_to_fields（与桌面版逻辑保持一致）
///
/// 解析步骤：
///   1. 平台识别（美团/淘宝/京东）
///   2. 店铺名称（4级降级策略）
///   3. 产品名称（品牌识别+规格提取）
///   4. 原价（新版"总价"行 / 旧版"商品总价"+配送打包累加）
///   5. 成交价（底部"共减"行等）
///   6. 商品优惠
///   7. 满减
///   8. 优惠券
///   9. 红包
///   10. 配送费

import '../models/form_fields.dart';
import '../models/ocr_result.dart';
import '../utils/constants.dart';
import '../utils/price_parser.dart';
import 'product_normalizer.dart';

class FieldParser {
  FieldParser._();

  static const _jdPlatform = '京东闪送';
  static const _jdServiceKeywords = ['京东秒送', '京东闪送'];

  /// 将 OCR 结果解析为表单字段
  static FormFields parse(List<OcrResult> ocrResults, {String region = ''}) {
    final fields = FormFields(region: region);
    if (ocrResults.isEmpty) return fields;

    final lines = ocrResults;
    final fullText = lines.map((e) => e.text).join('\n');

    // =========================================================
    // 步骤 1: 平台识别
    // =========================================================
    final isJd = _isJdCheckout(fullText);
    final isTaobao =
        ['蜂鸟', '淘金币', '淘金市', '淘宝', '提交订单'].any((k) => fullText.contains(k));
    fields.platform = isJd ? _jdPlatform : (isTaobao ? '淘宝闪购' : '美团闪购');

    // =========================================================
    // 步骤 2: 店铺名称（4级降级策略）
    // =========================================================
    fields.shopName = _findShopName(lines, fields.platform);

    // =========================================================
    // 步骤 3: 产品名称
    // =========================================================
    fields.productName = _findProductName(lines);
    // 数量缺失时标准化会回退为 12 瓶；没有任何容量×数量或瓶/听数量证据时，
    // 标记为需人工确认，避免把截断标题误当成整箱规格。
    if (fields.productName.isNotEmpty &&
        RegExp(r'\*12(瓶|听)$').hasMatch(fields.productName)) {
      final hasEvidence =
          RegExp(r'\d+\s*m[l1]\s*[*xX×]\s*\d+', caseSensitive: false)
                  .hasMatch(fullText) ||
              RegExp(r'\d+\s*(瓶|听|罐)').hasMatch(fullText);
      if (!hasEvidence) {
        fields.specUnreliable = true;
        if (!fields.remark.contains('产品规格需人工确认')) {
          fields.remark = fields.remark.isEmpty
              ? '产品规格需人工确认'
              : '${fields.remark}；产品规格需人工确认';
        }
      }
    }

    // =========================================================
    // 步骤 4: 原价
    // =========================================================
    fields.originalPrice = _findOriginalPrice(lines, fields.platform);

    // =========================================================
    // 步骤 5: 成交价
    // =========================================================
    fields.finalPrice = _findFinalPrice(lines, fields.platform);

    // =========================================================
    // 步骤 6: 商品优惠
    // =========================================================
    fields.shopDiscount = _findShopDiscount(lines, fields.platform);

    // =========================================================
    // 步骤 7: 满减
    // =========================================================
    fields.fullReduction = _findFullReduction(lines, fields.platform);

    // =========================================================
    // 步骤 8: 优惠券
    // =========================================================
    fields.coupon = _findCoupon(lines, fields.platform);

    // =========================================================
    // 步骤 9: 红包（仅淘宝）
    // =========================================================
    if (fields.platform == '淘宝闪购') {
      fields.redPacket = _findRedPacket(lines);
    }

    // =========================================================
    // 步骤 10: 配送费（打包费 + 配送费）
    // =========================================================
    double packFee = 0.0;
    double shipFee = 0.0;

    final packIdx = _findLineByKeyword(lines, '打包费');
    if (packIdx >= 0) {
      packFee = PriceParser.findPriceByXAlignment(lines, lines[packIdx].top);
    }

    final shipIdx = _findLineByKeyword(lines, '配送费');
    if (shipIdx >= 0) {
      // 配送费行可能有 "¥7 ¥1.5>" 格式（原价和优惠后），取最后一个
      // 使用专门的配送费提取函数处理 OCR 小数点丢失
      shipFee = PriceParser.findDeliveryFee(lines, lines[shipIdx].top);
    }

    if (fields.platform == _jdPlatform) {
      final jdShippingPrices = _findJdLabeledPrices(lines, '运费');
      final jdShippingPrice = _findJdShippingPrice(lines);
      if (jdShippingPrice > 0) {
        shipFee = jdShippingPrice;
      }
      final promo = RegExp(r'减\s*(\d+\.?\d*)\s*元运费').firstMatch(fullText);
      if (promo != null) {
        final promoRemark = '京东运费活动优惠${promo.group(1)}元';
        if (!fields.remark.contains(promoRemark)) {
          fields.remark = fields.remark.isEmpty
              ? promoRemark
              : '${fields.remark}；$promoRemark';
        }
      } else if (jdShippingPrices.length >= 2 && jdShippingPrices.last == 0) {
        final waived = jdShippingPrices[jdShippingPrices.length - 2];
        if (waived > 0) {
          final promoRemark = '京东运费优惠${_formatJdAmount(waived)}元';
          if (!fields.remark.contains(promoRemark)) {
            fields.remark = fields.remark.isEmpty
                ? promoRemark
                : '${fields.remark}；$promoRemark';
          }
        }
      }
    }

    fields.deliveryFee = packFee + shipFee;

    return fields;
  }

  // =========================================================
  // 工具函数
  // =========================================================
  static int _findLineByKeyword(List<OcrResult> lines, String keyword,
      {int start = 0}) {
    for (var i = start; i < lines.length; i++) {
      if (lines[i].text.contains(keyword)) return i;
    }
    return -1;
  }

  static bool _isJdCheckout(String fullText) {
    if (_jdServiceKeywords.any(fullText.contains)) return true;
    return fullText.contains('京东') &&
        fullText.contains('商品金额') &&
        fullText.contains('应付总额');
  }

  static List<double> _pricesOnRow(List<OcrResult> lines, double targetTop,
      {double xThreshold = AppConstants.priceRightThreshold}) {
    final prices = <double>[];
    for (final item in lines) {
      if ((item.top - targetTop).abs() >= AppConstants.alignNormal ||
          item.left <= xThreshold) {
        continue;
      }
      final matches = RegExp(r'[¥￥]\s*(\d+\.?\d*)').allMatches(item.text);
      for (final match in matches) {
        final value = PriceParser.extractPriceSafe('¥${match.group(1)}');
        // ¥0 是“已免运费”的有效实付金额，不能被当作缺失值丢掉。
        if (value >= 0) prices.add(value);
      }
    }
    return prices;
  }

  static List<double> _findJdLabeledPrices(
      List<OcrResult> lines, String keyword) {
    final idx = _findLineByKeyword(lines, keyword);
    if (idx < 0) return [];
    final rowPrices = _pricesOnRow(lines, lines[idx].top);
    if (rowPrices.isNotEmpty) return rowPrices;

    // OCR 可能把标签和金额拆成多个文本块，例如“应付总额 ¥29.”+“.9 共减¥4.3”。
    // 合并同一行后再提取，避免把 29.9 错读成 29。
    final merged = _mergeJdRowFragments(lines, lines[idx].top);
    final sources = [merged, lines[idx].text];
    for (final source in sources) {
      final prices = RegExp(r'[¥￥]\s*(\d+\.?\d*)')
          .allMatches(_fixJdDuplicatedDot(source))
          .map((match) => PriceParser.extractPriceSafe('¥${match.group(1)}'))
          .toList();
      if (prices.isNotEmpty) return prices;
    }
    return [];
  }

  static String _mergeJdRowFragments(List<OcrResult> lines, double targetTop) {
    final fragments = lines
        .where((item) =>
            (item.top - targetTop).abs() < AppConstants.alignNormal &&
            item.text.trim().isNotEmpty)
        .map((item) => MapEntry(item.left, item.text.trim()))
        .toList()
      ..sort((a, b) => a.key.compareTo(b.key));
    return fragments.map((entry) => entry.value).join();
  }

  static String _fixJdDuplicatedDot(String text) =>
      text.replaceAll(RegExp(r'\.{2,}'), '.');

  static String _cleanJdShopPrefix(String name) {
    var cleaned = name.trim();
    String previous;
    do {
      previous = cleaned;
      cleaned =
          cleaned.replaceFirst(RegExp(r'^(自营|秒送|闪送)[\s，,、·]*'), '').trim();
    } while (cleaned != previous);
    return cleaned;
  }

  static double _findJdShippingPrice(List<OcrResult> lines) {
    final prices = _findJdLabeledPrices(lines, '运费');
    if (prices.isEmpty) return 0.0;
    // “已免运费”本身就是实付 0 元；即使 OCR 漏掉右侧的 ¥0，也不能把
    // 划线运费误写进 L 列。
    final shippingIdx = _findLineByKeyword(lines, '运费');
    if (shippingIdx >= 0) {
      final rowText = _mergeJdRowFragments(lines, lines[shippingIdx].top);
      if (rowText.contains('已免运费') || rowText.contains('免运费')) {
        return 0.0;
      }
    }
    final price = prices.last;
    // 京东即时配送费通常不超过 40 元；OCR 漏小数点时将 48 还原为 4.8。
    if (price > 40 && price / 10 <= 40) {
      return double.parse((price / 10).toStringAsFixed(2));
    }
    return price;
  }

  static String _formatJdAmount(double value) {
    if (value == value.roundToDouble()) return value.toInt().toString();
    return value.toString();
  }

  static String _findJdShopName(List<OcrResult> lines) {
    final serviceItems = lines
        .where((item) => _jdServiceKeywords.any(item.text.contains))
        .toList();
    for (final serviceItem in serviceItems) {
      final candidates = <MapEntry<double, String>>[];
      for (final item in lines) {
        if ((item.top - serviceItem.top).abs() >= AppConstants.alignNormal ||
            item.left < 0.1 ||
            item.left >= 0.78) {
          continue;
        }
        final text = item.text.trim();
        if (text.isEmpty ||
            _jdServiceKeywords.any(text.contains) ||
            text == '秒送' ||
            text == '闪送' ||
            RegExp(r'^[¥￥\d\.\s/:：\-]+$').hasMatch(text)) {
          continue;
        }
        candidates.add(MapEntry(item.left, text));
      }
      if (candidates.isNotEmpty) {
        candidates.sort((a, b) => a.key.compareTo(b.key));
        return _cleanJdShopPrefix(candidates.first.value);
      }
    }
    for (final item in lines) {
      final text = item.text.trim();
      if (text.contains('京东酒世界')) {
        return _cleanJdShopPrefix(text);
      }
    }
    return '';
  }

  static double? _findJdProductTop(List<OcrResult> lines) {
    const productKeywords = [
      '漓泉',
      '燕京',
      '雪花',
      '勇闯',
      '青岛',
      '百威',
      '哈啤',
      '哈尔滨',
      '乌苏'
    ];
    for (final item in lines) {
      if (item.left < 0.5 &&
          item.top > 0.35 &&
          item.top < 0.85 &&
          productKeywords.any(item.text.contains)) {
        return item.top;
      }
    }
    return null;
  }

  /// 清理尾部商品类目噪声（如"（啤酒•红洋酒）" / "•啤酒•红酒•洋酒"）
  static String _cleanShopName(String raw) {
    var s = raw;
    // 去掉括号内含酒类的后缀: "洪马嗨酒（啤酒•红洋酒）" -> "洪马嗨酒"
    s = s.replaceAll(RegExp(r'[（(]\s*(啤酒|红酒|洋酒|白酒).*?[）)]'), '');
    // 去掉•或·连接的酒类后缀: "1516酒盒子速配•啤酒•红酒" -> "1516酒盒子速配"
    s = s.replaceAll(RegExp(r'[•·]\s*(啤酒|红酒|洋酒|白酒).*$'), '');
    s = s.replaceAll(RegExp(r'(啤酒|红酒|洋酒|白酒)[•·].*$'), '');
    // 清理残留的尾部标点
    s = s.replaceAll(RegExp(r'[（(•·…\s]+$'), '');
    return s.trim();
  }

  /// 合并同一行（top 相近）的文本块，按 left 排序拼接
  static String _mergeSameLine(List<OcrResult> lines, double targetTop) {
    final items = <MapEntry<double, String>>[];
    for (final item in lines) {
      if ((item.top - targetTop).abs() < AppConstants.alignTight &&
          item.left < AppConstants.shopLeftLoose) {
        final t = item.text.trim();
        if (t.isNotEmpty && !RegExp(r'^[¥￥\d\.\s*/:：\-]+$').hasMatch(t)) {
          items.add(MapEntry(item.left, t));
        }
      }
    }
    items.sort((a, b) => a.key.compareTo(b.key));
    if (items.isEmpty) return '';
    return items.map((e) => e.value).join();
  }

  // =========================================================
  // 步骤 2: 店铺名称（4级降级策略）
  // =========================================================
  static const _shopKeywords = [
    '超市',
    '便利店',
    '店）',
    '店)',
    '店（',
    '店(',
    '送酒',
    '客超市',
    '商店',
    '嗨酒',
    '酒类',
    '酒行',
    '酒业',
    '酒零鹿',
    '鸡尾酒',
    '酒水',
    '酒栈',
    '酒屋',
  ];

  static const _nonShopTexts = [
    '送出',
    '送达',
    '去选择',
    '自配',
    '秒送',
    '闪送',
    '商品总价',
    '打包费',
    '配送费',
    '运费',
    '商家活动',
    '美团红包',
    '店铺券',
    '极速支付',
    '找人付',
    '共减',
    '收货人',
    '超时退费',
    '安心购',
    '赠',
  ];

  static String _findShopName(List<OcrResult> lines, String platform) {
    String shopName = '';

    if (platform == _jdPlatform) {
      shopName = _findJdShopName(lines);
    }

    // 策略1: 在"选择收货地址"下方范围内找包含店铺关键词的行
    final addrIdx = _findLineByKeyword(lines, '选择收货地址');
    // 京东专用策略优先级更高：一旦已经得到店铺名，不能再让通用地址
    // 策略覆盖它，否则“自营·秒送·店名”会把服务徽标前缀重新写回结果。
    if (shopName.isEmpty && addrIdx >= 0) {
      for (var i = addrIdx + 1; i < lines.length && i < addrIdx + 15; i++) {
        final text = lines[i].text.trim();
        if (text.isEmpty) continue;
        // 排除明显的非店铺文本
        if (_nonShopTexts.any((k) => text.contains(k))) continue;
        // 排除纯价格/数字行
        if (RegExp(r'^[¥￥\d\.\s*/]+(件|个|瓶|听|罐)?$').hasMatch(text)) {
          continue;
        }
        // 排除配送相关标签
        if (['美团快送', '1对1急送', '到店自取', '送货上门', '立即送出'].contains(text)) {
          continue;
        }
        // 优先匹配包含店铺关键词的行
        if (_shopKeywords.any((k) => text.contains(k))) {
          // 合并同一行(top相近)的其他文本块，拼接完整店铺名
          final raw = _mergeSameLine(lines, lines[i].top);
          final cleaned = _cleanShopName(raw.isNotEmpty ? raw : text);
          if (cleaned.isNotEmpty) {
            shopName = cleaned;
          }
          break;
        }
      }

      // 策略2: 如果策略1没找到，取"选择收货地址"下方第一个有效文本
      if (shopName.isEmpty) {
        for (var i = addrIdx + 1; i < lines.length && i < addrIdx + 15; i++) {
          final text = lines[i].text.trim();
          if (text.isEmpty) continue;
          if (_nonShopTexts.any((k) => text.contains(k))) continue;
          if (['美团快送', '1对1急送', '到店自取', '送货上门', '立即送出', '约']
              .any((k) => text.contains(k))) {
            continue;
          }
          // 排除纯价格/数字/时间行
          if (RegExp(r'^[¥￥\d\.\s*/:：\-]+$').hasMatch(text)) continue;
          // 排除规格/商品标题行
          if (['规格', '漓泉', '燕京', '啤酒', '整箱'].any((k) => text.contains(k))) {
            continue;
          }
          shopName = text;
          break;
        }
      }
    }

    // 备用: 全局找包含店铺关键词的行（排除淘宝未成年人提示等非店铺文本）
    if (shopName.isEmpty) {
      for (final item in lines) {
        final text = item.text.trim();
        if (['依据法律规定', '未成年人', '限制购买', '18周岁'].any((k) => text.contains(k))) {
          continue;
        }
        if (_shopKeywords.any((k) => text.contains(k)) &&
            item.left < AppConstants.shopLeftLoose) {
          // 排除商品行（但"嗨酒""酒行"等是店铺名的一部分，不应排除）
          if (['漓泉', '燕京', '规格'].any((k) => text.contains(k))) continue;
          // 只在包含"啤酒"且没有店铺关键词时排除
          final shopKwInText =
              _shopKeywords.where((k) => text.contains(k)).toList();
          if (text.contains('啤酒') && shopKwInText.isEmpty) continue;
          // 淘宝闪购店铺名行含前缀（"闪购"OCR可能误读为"沟购""河购"），去掉前缀
          var cleaned = text;
          if (platform == '淘宝闪购') {
            cleaned = cleaned.replaceAll(RegExp(r'^(闪购|沟购|河购)\s*'), '');
          }
          cleaned = _cleanShopName(cleaned);
          if (cleaned.isNotEmpty) {
            shopName = cleaned;
            break;
          }
        }
      }
    }

    // 备用2: 淘宝闪购店铺名以"闪购/沟购/河购"开头（OCR误读"闪"为"沟/河"）
    if (shopName.isEmpty && platform == '淘宝闪购') {
      for (final item in lines) {
        final text = item.text.trim();
        if (item.top > AppConstants.topTaobaoShop &&
            item.top < AppConstants.topTaobaoShopEnd &&
            item.left < AppConstants.shopLeftStrict) {
          final m = RegExp(r'^(闪购|沟购|河购)\s*(.+)').firstMatch(text);
          if (m != null) {
            var shop = m.group(2)!.trim();
            // 去掉尾部噪声
            shop = shop.replaceAll(RegExp(r'[•·…\s]+$'), '');
            // 去掉商品类目后缀（如 "啤酒•红酒•洋酒" 或 "•红酒•洋酒"）
            shop = shop.replaceAll(RegExp(r'(啤酒|红酒|洋酒|白酒)[•·].*$'), '');
            shop = shop.replaceAll(RegExp(r'[•·]\s*(啤酒|红酒|洋酒|白酒).*$'), '');
            shop = shop.trim();
            if (shop.isNotEmpty) {
              shopName = shop;
              break;
            }
          }
        }
      }
    }

    // 备用3: 美团没有"选择收货地址"的截图（已设地址的结算页）
    if (shopName.isEmpty && platform == '美团闪购') {
      for (final item in lines) {
        final text = item.text.trim();
        if (item.top > AppConstants.topMeituanShop &&
            item.top < AppConstants.topMeituanShopEnd &&
            item.left < AppConstants.shopLeftStrict) {
          // 排除非店铺文本
          if (['商品总价', '打包费', '配送费', '商家活动', '收货', '地址', '收货人', '送出', '送达']
              .any((k) => text.contains(k))) {
            continue;
          }
          // 排除纯价格/数字行
          if (RegExp(r'^[¥￥\d\.\s*/:：\-]+$').hasMatch(text)) continue;
          // 排除规格/商品标题行（但如果含店铺关键词则不排除）
          final hasShopKw = _shopKeywords.any((k) => text.contains(k));
          if (!hasShopKw) {
            if (['规格', '漓泉', '燕京', '啤酒', '整箱', 'ml']
                .any((k) => text.contains(k))) {
              continue;
            }
          }
          // 合并同一行文本
          final raw = _mergeSameLine(lines, item.top);
          final cleaned = _cleanShopName(raw.isNotEmpty ? raw : text);
          if (cleaned.isNotEmpty) {
            shopName = cleaned;
            break;
          }
        }
      }
    }

    return shopName;
  }

  // =========================================================
  // 步骤 3: 产品名称
  // =========================================================
  static String _findProductName(List<OcrResult> lines) {
    // 品牌关键词和"啤酒"要分开处理，"啤酒"太宽泛容易误匹配
    final brandKeywords = [
      '漓泉',
      '燕京',
      '燕.',
      '乌苏',
      '雪鹿',
      '雪花',
      '勇闯',
      'superx',
      '青岛',
      'tsingtao',
      '百威',
      'budweiser',
      '哈啤',
      '哈尔滨',
      'harbin',
      '老炮',
      '小蓝妖',
    ];
    // 这些词必须和品牌关键词同时出现才算商品行
    final suffixKeywords = [
      '纯生',
      '特酿',
      '经典',
      '清爽',
      '超爽',
      '冰醇',
      '原浆',
      '度',
      '°',
      '罐装',
      '瓶装',
      '听装',
      'ml',
      '啤',
    ];

    String productTitle = '';
    String specText = '';
    String subTitle = '';
    String extraText = '';
    double? titleTop;

    for (var i = 0; i < lines.length; i++) {
      final text = lines[i].text.trim();
      if (text.isEmpty || lines[i].left >= AppConstants.priceRightStrict) {
        continue;
      }
      // 排除搜索框、历史搜索、结算明细等
      if (['搜索', '历史', '商品总价', '商家活动', '美团红包', '店铺券', '配送费', '打包费']
          .any((k) => text.contains(k))) {
        continue;
      }
      // 排除底部推荐商品行（通常在 top < 0.15 区域）
      if (lines[i].top < AppConstants.topBottomRecommend) continue;
      // 排除顶部状态栏/导航栏（通常在 top > 0.85 区域）
      if (lines[i].top > AppConstants.topNavLimit) continue;
      // 排除"超值换购"推荐区域（top 0.15~0.35 是推荐商品，不是主商品）
      if (lines[i].top > AppConstants.topBottomRecommend &&
          lines[i].top < AppConstants.topExchangeZone) {
        continue;
      }
      // 排除店铺名称行
      if (['精酿啤酒馆', '啤酒馆', '酒保爷'].any((k) => text.contains(k))) continue;
      // 排除广告文案行
      if (['轻奢', '慢酿', '醇正', '清爽解渴', '不黏腻'].any((k) => text.contains(k))) {
        continue;
      }

      // 判断是否是商品标题行：
      // 1) 包含品牌关键词 + 规格后缀（度/°/罐装/ml/啤酒等）
      // 2) 或者包含品牌关键词 + "啤酒"（但排除店铺行）
      final hasBrand = brandKeywords
          .any((k) => text.toLowerCase().contains(k.toLowerCase()));
      final hasSuffix = suffixKeywords.any((k) => text.contains(k));

      // 商品标题行通常在 left=0.2 附近（商品区域），不是店铺名(left<0.1)
      bool isProduct = false;
      if (hasBrand && hasSuffix) {
        isProduct = true;
      } else if (hasBrand && lines[i].left > 0.15) {
        // 品牌词在商品区域（left > 0.15），即使没有后缀也算
        isProduct = true;
      }
      if (!isProduct) continue;

      productTitle = text;
      titleTop = lines[i].top;
      // 向下找规格行（标题后 8 行内）
      for (var j = i + 1; j < lines.length && j < i + 8; j++) {
        final spec = lines[j].text.trim();
        if (spec.contains('规格') ||
            (spec.contains('瓶') && spec.toLowerCase().contains('ml')) ||
            spec.contains('听') ||
            spec.contains('罐')) {
          specText = spec;
          break;
        }
      }
      // 找副标题行（标题后 6 行内，Y 坐标接近，不含"规格"，含度数/型号关键词）
      for (var j = i + 1; j < lines.length && j < i + 6; j++) {
        final sub = lines[j].text.trim();
        if (sub.contains('规格')) continue;
        if ((lines[j].top - titleTop!).abs() < AppConstants.alignLoose) {
          final hasDegree = RegExp(r'\d+\s*°\s*[Pp]?').hasMatch(sub) ||
              RegExp(r'\d+\s*度').hasMatch(sub);
          final hasType = ['经典', '清爽', '特酿', '纯生', '原浆', 'superX', 'superx']
              .any((k) => sub.contains(k));
          if (hasDegree || hasType) {
            subTitle = sub;
            break;
          }
        }
      }
      break;
    }

    // 提取缩略图标签文本（如 "12听装" "12瓶装" "全囊白啤" 等）
    // 这些文本在 left < 0.1 的缩略图区域，top 接近标题行
    for (final item in lines) {
      if (item.left < 0.12 &&
          item.top > AppConstants.topThumbLabel &&
          item.top < AppConstants.topThumbLabelEnd) {
        final t = item.text.trim();
        // 提取 "X听装" "X瓶装" "X罐装" 或 "白啤" "全麦白啤" 等关键词
        if (RegExp(r'\d+\s*(听|瓶|罐)装').hasMatch(t) ||
            t.contains('白啤') ||
            t.contains('全麦')) {
          extraText += ' $t';
        }
      }
    }

    // 标准化产品名称
    if (productTitle.isNotEmpty) {
      final productName = ProductNormalizer.normalize(
        productTitle,
        specText,
        subTitle: subTitle,
        extraText: extraText.trim(),
      );
      return productName;
    }
    return '';
  }

  // =========================================================
  // 步骤 4: 原价 — 统一为"含配送打包的总价"口径
  // 新版美团UI: "总价"行（金额已含配送+打包，直接取）
  // 旧版美团/淘宝UI: "商品总价"行（金额不含配送+打包，需累加配送费原价+打包费）
  // =========================================================
  static double _findOriginalPrice(List<OcrResult> lines, String platform) {
    double originalPrice = 0.0;

    // 新版UI: "总价"行已含配送打包，直接取
    int totalIdx = -1;
    for (var i = 0; i < lines.length; i++) {
      final t = lines[i].text.trim().replaceAll('～', '').replaceAll('~', '');
      if (t == '总价' || t.startsWith('总价')) {
        totalIdx = i;
        break;
      }
    }
    if (totalIdx >= 0) {
      final targetTop = lines[totalIdx].top;
      for (final item in lines) {
        if ((item.top - targetTop).abs() < AppConstants.alignNormal &&
            item.left > 0.4) {
          final price = PriceParser.extractPriceSafe(item.text);
          if (price > 0) {
            originalPrice = price;
            break;
          }
        }
      }
      if (originalPrice == 0.0) {
        originalPrice = PriceParser.findPriceByXAlignment(lines, targetTop);
      }
    } else {
      // 旧版UI: "商品总价"不含配送打包，累加配送费(划线原价)+打包费
      final goodsIdx = _findLineByKeyword(lines, '商品总价');
      if (goodsIdx >= 0) {
        final targetTop = lines[goodsIdx].top;
        var goodsPrice = 0.0;
        for (final item in lines) {
          if ((item.top - targetTop).abs() < AppConstants.alignNormal &&
              item.left > 0.4) {
            goodsPrice = PriceParser.extractPriceSafe(item.text);
            break;
          }
        }
        if (goodsPrice == 0.0) {
          goodsPrice = PriceParser.findPriceByXAlignment(lines, targetTop);
        }
        if (goodsPrice > 0) {
          // 打包费
          var packFee = 0.0;
          final packIdx = _findLineByKeyword(lines, '打包费');
          if (packIdx >= 0) {
            packFee =
                PriceParser.findPriceByXAlignment(lines, lines[packIdx].top);
          }
          // 配送费：取划线原价（第一个价格），与新版总价用配送原价口径一致
          var shipFee = 0.0;
          final shipIdx = _findLineByKeyword(lines, '配送费');
          if (shipIdx >= 0) {
            final shipTop = lines[shipIdx].top;
            for (final item in lines) {
              if ((item.top - shipTop).abs() < AppConstants.alignNormal &&
                  item.left > 0.4) {
                final text = item.text;
                if (['免配送费', '配送费免', '免运费'].any((k) => text.contains(k))) {
                  shipFee = 0.0;
                  break;
                }
                final prices =
                    RegExp(r'[¥￥]\s*(\d+\.?\d*)').allMatches(text).toList();
                if (prices.isNotEmpty) {
                  var val =
                      double.tryParse(prices.first.group(1)!)?.abs() ?? 0.0;
                  // 原价也可能漏小数点（¥69 实际 6.9）
                  if (val > 50) val = val / 10;
                  shipFee = val;
                  break;
                }
                final price = PriceParser.extractPrice(text);
                if (price != null && price != 0.0) {
                  shipFee = price.abs();
                  break;
                }
              }
            }
          }
          originalPrice =
              double.parse((goodsPrice + packFee + shipFee).toStringAsFixed(2));
        }
      }
    }

    // 备用: 找商品列表中的 ¥xx/件（原价标价，跳过"优惠价"行）
    if (originalPrice == 0.0) {
      for (final item in lines) {
        final text = item.text;
        // "¥65/件" 这种格式是原价；跳过"优惠价¥50.5/件"这类行
        if (text.contains('优惠')) continue;
        final m = RegExp(r'[¥￥](\d+\.?\d*)/件').firstMatch(text);
        if (m != null) {
          originalPrice = double.tryParse(m.group(1)!) ?? 0.0;
          break;
        }
      }
    }

    // 淘宝闪购备用: 部分页面（如天猫超市）无"商品总价"行，商品标题右侧的¥xx即为原价
    if (originalPrice == 0.0 && platform == '淘宝闪购') {
      for (final item in lines) {
        final text = item.text.trim();
        if (['燕京', '漓泉', '雪花', '青岛', '百威', '哈尔滨']
                .any((kw) => text.contains(kw)) &&
            item.left < 0.3) {
          final targetTop = item.top;
          for (final item2 in lines) {
            if ((item2.top - targetTop).abs() < AppConstants.alignNormal &&
                item2.left > AppConstants.priceRightStrict) {
              final price = PriceParser.extractPriceSafe(item2.text);
              if (price > 0) {
                originalPrice = price;
                break;
              }
            }
          }
          if (originalPrice > 0) break;
        }
      }
    }

    // 京东秒送没有“总价/商品总价”行：商品卡片右侧通常显示划线原价和
    // 当前商品金额，分别对应 F 列商品标价与后续理论成交价。
    if (platform == _jdPlatform) {
      final productTop = _findJdProductTop(lines);
      final productPrices =
          productTop == null ? <double>[] : _pricesOnRow(lines, productTop);
      final amountPrices = _findJdLabeledPrices(lines, '商品金额');
      if (productPrices.isNotEmpty) {
        originalPrice = productPrices.first;
      } else if (amountPrices.isNotEmpty) {
        originalPrice = amountPrices.first;
      }
    }

    return originalPrice;
  }

  // =========================================================
  // 步骤 5: 成交价 - 底部总价 "¥39.4共减¥27.1" 这种格式
  // =========================================================
  static double _findFinalPrice(List<OcrResult> lines, String platform) {
    double finalPrice = 0.0;
    var foundFinal = false;

    if (platform == _jdPlatform) {
      // 京东使用“应付总额 ¥64.8 共减¥5”，不依赖美团底部布局。
      final totalPrices = _findJdLabeledPrices(lines, '应付总额');
      if (totalPrices.isNotEmpty) {
        finalPrice = totalPrices.first;
        foundFinal = true;
      }
    }

    // 策略1: 找 "¥xx.x共减" 连在一起的完整格式
    for (final item in lines) {
      final text = item.text;
      // 必须同时包含¥和共减，且¥在共减前面
      if (text.contains('共减') && item.top < AppConstants.topBottomPrice) {
        // 提取"共减"前面的价格
        final beforeGj = text.split('共减')[0];
        final m = RegExp(r'[¥￥](\d+\.?\d*)').firstMatch(beforeGj);
        if (m != null) {
          // OCR 可能漏小数点（¥631 实际是 63.1），用 safe 修正
          final price = PriceParser.extractPriceSafe('¥${m.group(1)}');
          if (price > 0) {
            finalPrice = price;
            foundFinal = true;
            break;
          }
        }
      }
    }

    // 策略2: 淘宝闪购 - "合计 ¥67.3" / "已优惠¥24 ¥67.3" 格式
    if (!foundFinal && platform == '淘宝闪购') {
      for (final item in lines) {
        final text = item.text.trim();
        // 结算明细的"合计"行在页面中部（top<0.5），取先遇到的结算明细行
        if (text.contains('合计') && item.top < AppConstants.topTaobaoTotal) {
          // "已优惠¥24 ¥70.3" 有两个价格，取最后一个（成交价）
          final prices = RegExp(r'[¥￥](\d+\.?\d*)').allMatches(text).toList();
          if (prices.isNotEmpty) {
            final price =
                PriceParser.extractPriceSafe('¥${prices.last.group(1)}');
            if (price > 0) {
              finalPrice = price;
              foundFinal = true;
              break;
            }
          }
        }
      }
    }

    // 策略3: 合并底部同一行(top相近)的所有文本块，再提取
    if (!foundFinal) {
      final bottomItems = lines
          .where((item) => item.top < AppConstants.topBottomPrice)
          .toList();
      if (bottomItems.isNotEmpty) {
        // 找最底部的行（top最小的）
        final minTop =
            bottomItems.map((e) => e.top).reduce((a, b) => a < b ? a : b);
        final sameLine = bottomItems
            .where((e) => (e.top - minTop).abs() < AppConstants.alignTight)
            .toList()
          ..sort((a, b) => a.left.compareTo(b.left));
        final mergedText = sameLine.map((e) => e.text).join();
        // 从合并文本中提取成交价：找"¥xx.x共减"格式
        final m = RegExp(r'[¥￥](\d+\.?\d*)\s*共?减').firstMatch(mergedText);
        if (m != null) {
          final price = PriceParser.extractPriceSafe('¥${m.group(1)}');
          if (price > 0) {
            finalPrice = price;
            foundFinal = true;
          }
        } else {
          // 如果没有"共减"，取最左侧的¥价格
          final prices =
              RegExp(r'[¥￥](\d+\.?\d*)').allMatches(mergedText).toList();
          if (prices.isNotEmpty) {
            final price =
                PriceParser.extractPriceSafe('¥${prices.first.group(1)}');
            if (price > 0) {
              finalPrice = price;
              foundFinal = true;
            }
          }
        }
      }
    }

    // 备用: 找最底部（top < 0.06）的 ¥xx.x 格式价格
    if (finalPrice == 0.0) {
      for (final item in lines) {
        final text = item.text.trim();
        final m = RegExp(r'^[¥￥](\d+\.?\d*)').firstMatch(text);
        if (m != null && item.top < AppConstants.topBottomAlt) {
          final price = PriceParser.extractPriceSafe('¥${m.group(1)}');
          if (price > 0) {
            finalPrice = price;
            break;
          }
        }
      }
    }

    return finalPrice;
  }

  // =========================================================
  // 步骤 6: 商品优惠/商品活动 - "减 商家活动" 行
  // =========================================================
  static double _findShopDiscount(List<OcrResult> lines, String platform) {
    double discount = 0.0;

    if (platform == _jdPlatform) {
      final productTop = _findJdProductTop(lines);
      final productPrices =
          productTop == null ? <double>[] : _pricesOnRow(lines, productTop);
      final amountPrices = _findJdLabeledPrices(lines, '商品金额');
      if (productPrices.isNotEmpty &&
          amountPrices.isNotEmpty &&
          productPrices.first > amountPrices.first) {
        return double.parse(
            (productPrices.first - amountPrices.first).toStringAsFixed(2));
      }
      return 0.0;
    }

    var discountIdx = _findLineByKeyword(lines, '商家活动');
    if (discountIdx < 0) {
      discountIdx = _findLineByKeyword(lines, '商品活动');
    }
    if (discountIdx < 0) {
      // 新版美团 UI: "减 活动 -¥8.5"（OCR 可能拆成"减"+"活动"两个文本块）
      // 特征: left < 0.5 的行含"活动"，且同行(top相近)有"减"标签
      for (final item in lines) {
        if (item.left < AppConstants.shopLeftLoose &&
            item.text.contains('活动')) {
          final hasReduce = lines.any((item2) =>
              (item2.top - item.top).abs() < AppConstants.alignNormal &&
              item2.text.contains('减'));
          if (hasReduce) {
            discountIdx = lines.indexOf(item);
            break;
          }
        }
      }
    }
    if (discountIdx >= 0) {
      // 商家活动优惠金额，OCR 可能漏掉小数点（如 ¥461 实际 ¥4.61）
      final targetTop = lines[discountIdx].top;
      for (final item in lines) {
        if ((item.top - targetTop).abs() < AppConstants.alignNormal &&
            item.left > 0.4) {
          final price = PriceParser.extractPriceSafe(item.text, maxVal: 100);
          if (price > 0) {
            discount = price;
            break;
          }
        }
      }
    }

    // 淘宝闪购: "活动优惠" 行（如 -¥3）
    if (platform == '淘宝闪购' && discount == 0.0) {
      final actIdx = _findLineByKeyword(lines, '活动优惠');
      if (actIdx >= 0) {
        final targetTop = lines[actIdx].top;
        for (final item in lines) {
          if ((item.top - targetTop).abs() < AppConstants.alignNormal &&
              item.left > 0.4) {
            final price = PriceParser.extractPriceSafe(item.text, maxVal: 100);
            if (price > 0) {
              discount = price;
              break;
            }
          }
        }
      }
    }

    // 淘宝闪购: "商品优惠" 行 - 右侧通常无金额，从底部"已优惠¥xx"提取
    if (platform == '淘宝闪购' && discount == 0.0) {
      final discIdx = _findLineByKeyword(lines, '商品优惠');
      if (discIdx >= 0) {
        // 先尝试同行右侧找价格
        final targetTop = lines[discIdx].top;
        for (final item in lines) {
          if ((item.top - targetTop).abs() < AppConstants.alignNormal &&
              item.left > 0.4) {
            final price = PriceParser.extractPriceSafe(item.text, maxVal: 100);
            if (price > 0) {
              discount = price;
              break;
            }
          }
        }
        // 如果同行没有价格，从底部"已优惠¥xx"提取
        if (discount == 0.0) {
          for (final item in lines) {
            final text = item.text.trim();
            if (text.contains('已优惠') &&
                item.top < AppConstants.topBottomRecommend) {
              // 格式如 "已优惠¥20.1 ¥70.2"
              final m = RegExp(r'已优惠\s*[¥￥]\s*(\d+\.?\d*)').firstMatch(text);
              if (m != null) {
                discount = double.tryParse(m.group(1)!) ?? 0.0;
                break;
              }
            }
          }
        }
      }
    }

    return discount;
  }

  // =========================================================
  // 步骤 7: 满减活动 - "满减" 或 "店铺券/商品券" 行
  // 注意：店铺券/商品券归入满减活动(I列)，不归入优惠券(J列)
  // =========================================================
  static double _findFullReduction(List<OcrResult> lines, String platform) {
    double fullReduction = 0.0;

    final mrIdx = _findLineByKeyword(lines, '满减');
    if (mrIdx >= 0) {
      fullReduction =
          PriceParser.findPriceByXAlignment(lines, lines[mrIdx].top);
    }

    // 店铺券/商品券 -> 满减活动(I列)
    var shopCouponIdx = _findLineByKeyword(lines, '店铺券');
    if (shopCouponIdx < 0) {
      shopCouponIdx = _findLineByKeyword(lines, '商品券');
    }
    if (shopCouponIdx >= 0) {
      final targetTop = lines[shopCouponIdx].top;
      // 检查是否"暂无可用"
      final hasNo = lines.any((item) =>
          (item.top - targetTop).abs() < AppConstants.alignNormal &&
          item.text.contains('暂无'));
      if (!hasNo) {
        for (final item in lines) {
          if ((item.top - targetTop).abs() < AppConstants.alignNormal &&
              item.left > 0.4) {
            final price = PriceParser.extractPriceSafe(item.text, maxVal: 100);
            if (price > 0) {
              fullReduction = price;
              break;
            }
          }
        }
        if (fullReduction == 0.0) {
          fullReduction = PriceParser.findPriceByXAlignment(lines, targetTop);
        }
      }
    }

    // 淘宝闪购: "店铺/商品红包" 行 -> 满减活动(I列)
    if (platform == '淘宝闪购' && fullReduction == 0.0) {
      var shopRedIdx = _findLineByKeyword(lines, '店铺/商品红包');
      if (shopRedIdx < 0) {
        shopRedIdx = _findLineByKeyword(lines, '商品红包');
      }
      if (shopRedIdx >= 0) {
        final targetTop = lines[shopRedIdx].top;
        // 排除"无可用红包"
        final hasNo = lines.any((item) =>
            (item.top - targetTop).abs() < AppConstants.alignNormal &&
            item.text.contains('无可用'));
        if (!hasNo) {
          for (final item in lines) {
            if ((item.top - targetTop).abs() < AppConstants.alignNormal &&
                item.left > 0.4) {
              final price = PriceParser.extractPriceSafe(item.text);
              if (price > 0) {
                fullReduction = price;
                break;
              }
            }
          }
        }
      }
    }

    return fullReduction;
  }

  // =========================================================
  // 步骤 8: 优惠券 - "神券" 行（不含"不参加神券优惠"的商品描述）
  // 美团红包行实际显示的也是神券，归入优惠券(J列)
  // =========================================================
  static double _findCoupon(List<OcrResult> lines, String platform) {
    double coupon = 0.0;

    // 优先找"神券"行，但排除"不参加神券优惠"这种商品描述行
    int couponIdx = -1;
    for (final item in lines) {
      if ((item.text.contains('神券') || item.text.contains('神劵')) &&
          !item.text.contains('不参加')) {
        couponIdx = lines.indexOf(item);
        break;
      }
    }
    if (couponIdx >= 0) {
      final targetTop = lines[couponIdx].top;
      // 检查是否"暂无可用"
      final hasNo = lines.any((item) =>
          (item.top - targetTop).abs() < AppConstants.alignNormal &&
          item.text.contains('暂无'));
      if (!hasNo) {
        for (final item in lines) {
          if ((item.top - targetTop).abs() < AppConstants.alignNormal &&
              item.left > 0.4) {
            final price = PriceParser.extractPriceSafe(item.text, maxVal: 100);
            if (price > 0) {
              coupon = price;
              break;
            }
          }
        }
        if (coupon == 0.0) {
          coupon = PriceParser.findPriceByXAlignment(lines, targetTop);
        }
      }
    }

    // 如果优惠券还没找到，从"美团红包"行找（美团红包行实际是神券）
    if (coupon == 0.0) {
      final redIdx = _findLineByKeyword(lines, '美团红包');
      if (redIdx >= 0) {
        final targetTop = lines[redIdx].top;
        // 检查是否"暂无可用"或"最高可享"
        final hasNo = lines.any((item) =>
            (item.top - targetTop).abs() < AppConstants.alignNormal &&
            (item.text.contains('暂无') || item.text.contains('最高可享')));
        if (!hasNo) {
          for (final item in lines) {
            if ((item.top - targetTop).abs() < AppConstants.alignNormal &&
                item.left > 0.4) {
              final price =
                  PriceParser.extractPriceSafe(item.text, maxVal: 100);
              if (price > 0 && price < 100) {
                coupon = price;
                break;
              }
            }
          }
        }
      }
    }

    // 京东使用“优惠券”作为平台券入口；与美团“神券/红包”同属 J 列。
    // 只读取同一标签行的金额，避免把商品卡片或应付总额中的金额误当优惠券。
    if (platform == _jdPlatform && coupon == 0.0) {
      final jdCouponPrices = _findJdLabeledPrices(lines, '优惠券');
      final positivePrices = jdCouponPrices.where((price) => price > 0);
      if (positivePrices.isNotEmpty) coupon = positivePrices.last;
    }

    return coupon;
  }

  // =========================================================
  // 步骤 9: 红包 - 淘宝闪购有"平台红包"，美团基本为0
  // =========================================================
  static double _findRedPacket(List<OcrResult> lines) {
    final redIdx = _findLineByKeyword(lines, '平台红包');
    if (redIdx >= 0) {
      final targetTop = lines[redIdx].top;
      for (final item in lines) {
        if ((item.top - targetTop).abs() < AppConstants.alignNormal &&
            item.left > 0.4) {
          final price = PriceParser.extractPriceSafe(item.text, maxVal: 100);
          if (price > 0) {
            return price;
          }
        }
      }
      return PriceParser.findPriceByXAlignment(lines, targetTop);
    }
    return 0.0;
  }
}
