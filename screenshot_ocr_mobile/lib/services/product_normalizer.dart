/// 产品名标准化服务
/// 移植自桌面版 field_parser.py 的 _normalize_product_name（行 278-682）
/// 支持 7 大品牌系列识别 + 规格提取（含 9+3听 组合格式）

class ProductNormalizer {
  ProductNormalizer._();

  /// 将 OCR 识别的商品标题 + 规格标准化为干净格式
  ///
  /// 示例:
  ///   漓泉1998 -> 漓泉1998啤酒 500ml*12瓶
  ///   燕京U8 -> 燕京U8 500ml*12瓶
  ///   雪花勇闯8度 -> 雪花勇闯8度 500ml*12听
  static String normalize(
    String title,
    String spec, {
    String subTitle = '',
    String extraText = '',
  }) {
    var cleanTitle = title.trim();

    // 去掉前缀：【整箱】等
    cleanTitle = cleanTitle.replaceAll(RegExp(r'^【[^】]*】\s*'), '');
    // 去掉 "12罐丨" "12瓶|" 等数量前缀
    cleanTitle = cleanTitle.replaceAll(RegExp(r'^\d+\s*[罐瓶听丨|]+\s*'), '');
    // 去掉 "冰镇"
    cleanTitle = cleanTitle.replaceAll('冰镇', '');

    // 合并标题和副标题用于度数检测
    final combined = '$cleanTitle $subTitle $extraText $spec'.trim();

    // =========================================================
    // 识别品牌和型号
    // =========================================================
    String productBase = '';

    // --- 漓泉系列 ---
    // 注意：特酿/纯生/原浆等只是口味变体，不作为产品区分依据
    // 漓泉产品统一为 "漓泉1998啤酒"，按规格(ml和瓶/听/数量)区分
    // 不解析标题中的数字作为型号：OCR 常把 "8度" 等度数数字识别进标题
    if (cleanTitle.contains('漓泉')) {
      productBase = '漓泉1998啤酒';
    }
    // --- 燕京系列 ---
    else if (cleanTitle.contains('燕京') ||
        RegExp(r'燕[.\s]*$').hasMatch(cleanTitle)) {
      var model = '';
      final m = RegExp(r'U\s*(\d+)', caseSensitive: false).firstMatch(cleanTitle);
      if (m != null) {
        var num = m.group(1)!;
        if (num == '88') num = '8'; // OCR 误读修正
        model = 'U$num';
      } else {
        final m2 = RegExp(r'8\s*°?\s*P').firstMatch(cleanTitle);
        if (m2 != null) {
          model = 'U8';
        }
      }
      // 后缀识别（只有没找到 U8 时才用）
      if (model.isEmpty) {
        for (final suffix in ['纯生', '特酿', '原浆', '老炮', '小蓝妖']) {
          if (cleanTitle.contains(suffix)) {
            model = suffix;
            break;
          }
        }
      }
      model = model.isEmpty ? 'U8' : model; // 默认 U8
      productBase = '燕京$model';
    }
    // --- 雪花系列 ---
    else if (cleanTitle.contains('雪花') ||
        cleanTitle.contains('勇闯') ||
        cleanTitle.toLowerCase().contains('snowbeer')) {
      final isSuperx = cleanTitle.toLowerCase().contains('superx') ||
          subTitle.toLowerCase().contains('superx');

      // 提取度数（优先级：度 > °P > ° > superx 数字）
      String? degree;
      final d1 = RegExp(r'(\d+)\s*度').firstMatch(combined);
      if (d1 != null) {
        degree = d1.group(1);
      } else if (RegExp(r'(\d+)\s*°\s*[Pp]').hasMatch(combined)) {
        degree = RegExp(r'(\d+)\s*°\s*[Pp]').firstMatch(combined)!.group(1);
      } else if (RegExp(r'(\d+)\s*°(?!\s*[Pp])').hasMatch(combined)) {
        degree = RegExp(r'(\d+)\s*°(?!\s*[Pp])').firstMatch(combined)!.group(1);
      } else if (RegExp(r'superx\s*(\d+)', caseSensitive: false).hasMatch(combined)) {
        degree = RegExp(r'superx\s*(\d+)', caseSensitive: false)
            .firstMatch(combined)!
            .group(1);
      }

      if (isSuperx) {
        // superx 系列: "雪花啤酒8°P勇闯天涯superx"
        productBase = degree != null
            ? '雪花啤酒${degree}°P勇闯天涯superx'
            : '雪花啤酒8°P勇闯天涯superx';
      } else if (cleanTitle.contains('老雪') || cleanTitle.contains('老雪花')) {
        productBase = degree != null ? '雪花老雪${degree}度' : '雪花老雪12度';
      } else if (cleanTitle.contains('勇闯') || subTitle.contains('勇闯')) {
        productBase = degree != null ? '雪花勇闯${degree}度' : '雪花勇闯10度';
      } else if (cleanTitle.contains('超爽') || subTitle.contains('超爽')) {
        productBase = degree != null ? '雪花超爽${degree}度' : '雪花超爽8度';
      } else if (cleanTitle.contains('老雪') || cleanTitle.contains('老雪花') ||
          combined.contains('640')) {
        // 老雪/640ml 瓶装系列（雪花640ml基本是老雪）
        productBase = degree != null ? '雪花老雪${degree}度' : '雪花老雪12度';
      } else if (cleanTitle.contains('清爽') || subTitle.contains('清爽')) {
        // "清爽" 可能是产品线名，也可能是口味描述
        // "8°P清爽"(带P) -> 口味描述，归勇闯; "8°清爽"(不带P) -> 产品线名，归清爽
        final hasDegreeP = RegExp(r'\d+\s*°\s*[Pp]').hasMatch(combined);
        if (hasDegreeP) {
          productBase = degree != null ? '雪花勇闯${degree}度' : '雪花勇闯8度';
        } else {
          productBase = degree != null ? '雪花清爽${degree}度' : '雪花清爽8度';
        }
      } else {
        // 无勇闯/清爽/老雪/superx 关键字，默认清爽
        productBase = degree != null ? '雪花清爽${degree}度' : '雪花清爽8度';
      }
    }
    // --- 青岛系列 ---
    else if (cleanTitle.contains('青岛') ||
        cleanTitle.toLowerCase().contains('tsingtao')) {
      // 提取度数: "11度" "110P" "11°P" "11°" "10°P" 等
      String? degree;
      final d1 = RegExp(r'(\d+)\s*度').firstMatch(combined);
      if (d1 != null) {
        degree = d1.group(1);
      } else if (RegExp(r'(\d+)\s*°\s*[Pp]').hasMatch(combined)) {
        degree = RegExp(r'(\d+)\s*°\s*[Pp]').firstMatch(combined)!.group(1);
      } else if (RegExp(r'(\d+)\s*°(?!\s*[Pp])').hasMatch(combined)) {
        degree = RegExp(r'(\d+)\s*°(?!\s*[Pp])').firstMatch(combined)!.group(1);
      } else if (RegExp(r'(\d{2})0[Pp]').hasMatch(combined)) {
        // OCR 把 "11°P" 读成 "110P"
        degree = RegExp(r'(\d{2})0[Pp]').firstMatch(combined)!.group(1);
      }

      // 按子类型优先级判断
      if (combined.contains('原浆') || combined.contains('7天')) {
        // 青岛啤酒7天13P原浆啤酒（特殊产品名）
        productBase = '青岛啤酒7天13P原浆啤酒';
      } else if (combined.contains('奥古特')) {
        productBase = degree != null ? '青岛${degree}度奥古特' : '青岛12度奥古特';
      } else if (combined.contains('2000') || combined.contains('200.') ||
          combined.contains('200…')) {
        // 青岛2000 10度（OCR 可能将 "2000" 截断为 "200." 或 "200…"）
        productBase = degree != null ? '青岛2000 ${degree}度' : '青岛2000 10度';
      } else if (combined.contains('白啤') || combined.contains('全麦')) {
        // 青岛11度白啤
        productBase = degree != null ? '青岛${degree}度白啤' : '青岛11度白啤';
      } else if (combined.contains('纯生')) {
        productBase = degree != null ? '青岛纯生${degree}度' : '青岛纯生8度';
      } else if (combined.contains('冰醇') || combined.contains('冰纯') ||
          combined.contains('冰…') || combined.contains('冰.')) {
        productBase = degree != null ? '青岛冰醇${degree}度' : '青岛冰醇8度';
      } else if (combined.contains('经典')) {
        productBase = degree != null ? '青岛经典${degree}度' : '青岛经典10度';
      } else if (combined.contains('清爽')) {
        productBase = degree != null ? '青岛清爽${degree}度' : '青岛清爽8度';
      } else {
        // 无子类型关键字，按度数推断
        if (degree == '10') {
          productBase = '青岛经典10度';
        } else if (degree == '8') {
          productBase = '青岛清爽8度';
        } else if (degree == '11') {
          // 11度是白啤的标志度数
          productBase = '青岛11度白啤';
        } else if (degree != null) {
          productBase = '青岛经典${degree}度';
        } else {
          productBase = '青岛经典10度';
        }
      }
    }
    // --- 百威系列 ---
    else if (cleanTitle.contains('百威') ||
        cleanTitle.toLowerCase().contains('budweiser')) {
      // 提取度数: "9.7°P" "9.7度" "9.7P" 等
      String? degreeVal;
      final m = RegExp(r'(\d+\.?\d*)\s*°\s*[Pp]?').firstMatch(combined);
      if (m != null) {
        degreeVal = m.group(1);
      } else {
        final m2 = RegExp(r'(\d+\.?\d*)\s*度').firstMatch(combined);
        if (m2 != null) degreeVal = m2.group(1);
      }

      if (combined.contains('纯生')) {
        productBase = degreeVal != null
            ? '百威${degreeVal}°纯生啤酒'
            : '百威8°纯生啤酒';
      } else if (combined.contains('铝罐') || combined.contains('铝管')) {
        // 百威铝管啤酒（用户标准用"铝管"）
        productBase = '百威铝管啤酒';
      } else {
        productBase = degreeVal != null
            ? '百威${degreeVal}°啤酒'
            : '百威9.7°啤酒';
      }
    }
    // --- 哈啤（哈尔滨啤酒）系列 ---
    else if (cleanTitle.contains('哈啤') ||
        cleanTitle.contains('哈尔滨') ||
        cleanTitle.toLowerCase().contains('harbin')) {
      if (combined.contains('冰纯') || combined.contains('纯生') ||
          combined.contains('冰…') || combined.contains('冰.')) {
        // OCR 可能将"冰纯"截断为"冰…"或"冰."
        productBase = '哈尔滨冰纯';
      } else if (combined.contains('小麦王') || combined.contains('小麦')) {
        productBase = '哈尔滨小麦王';
      } else if (combined.contains('冰爽') || combined.contains('冰萃')) {
        productBase = '哈尔滨冰爽';
      } else {
        productBase = '哈尔滨啤酒';
      }
    }
    // --- 乌苏系列 ---
    else if (cleanTitle.contains('乌苏')) {
      productBase = '乌苏啤酒';
    }
    // --- 其他 ---
    else {
      productBase = cleanTitle
          .replaceAll('瓶装', '')
          .replaceAll('听装', '')
          .trim();
      // 去掉尾部 "8." 这类 OCR 截断残留
      productBase = productBase.replaceAll(RegExp(r'\d+\.\s*$'), '').trim();
    }

    // =========================================================
    // 从规格中提取 容量*数量+瓶/听
    // =========================================================
    var cleanSpec = spec.trim();
    cleanSpec = cleanSpec.replaceAll(RegExp(r'^规格\s*[：:]\s*'), '');
    cleanSpec = cleanSpec.replaceAll('冰镇', '').replaceAll('常温', '').trim();

    // 提取容量
    String capacity = '';
    var m = RegExp(r'(\d+)\s*ml', caseSensitive: false).firstMatch(cleanSpec);
    if (m == null) {
      m = RegExp(r'(\d+)\s*m\s*[l1]', caseSensitive: false).firstMatch(cleanSpec);
    }
    if (m != null) {
      capacity = '${m.group(1)}ml';
    }
    if (capacity.isEmpty) {
      m = RegExp(r'(\d+)\s*ml', caseSensitive: false).firstMatch(cleanTitle);
      if (m != null) capacity = '${m.group(1)}ml';
    }
    if (capacity.isEmpty) {
      m = RegExp(r'(\d+)\s*[Ll]\b').firstMatch(cleanSpec);
      if (m == null) m = RegExp(r'(\d+)\s*[Ll]\b').firstMatch(cleanTitle);
      if (m != null) capacity = '${m.group(1)}L';
    }
    if (capacity.isEmpty) {
      if (productBase.contains('老雪')) {
        capacity = '640ml';
      } else if (['漓泉', '燕京', '雪花', '青岛', '百威', '哈尔滨', '乌苏']
          .any((b) => productBase.contains(b))) {
        capacity = '500ml';
      }
    }

    // 提取数量和单位
    String count = '';
    String unit = '瓶';

    // 优先匹配 "12瓶" "12听" "12罐" 格式
    m = RegExp(r'(\d+)\s*(瓶|听|罐|只|支)').firstMatch(cleanSpec);
    if (m != null) {
      count = m.group(1)!;
      unit = m.group(2)!;
      if (unit == '罐') unit = '听';

      // 检查 "+N罐/听" 后缀（如 "9罐/件+3罐" -> "9+3听"）
      final mPlus = RegExp(r'\+\s*(\d+)\s*(瓶|听|罐|只|支)').firstMatch(cleanSpec);
      if (mPlus != null) {
        count = '$count+${mPlus.group(1)}';
      }
    } else {
      // "500ml*12" 格式
      m = RegExp(r'\*\s*(\d+)\s*$').firstMatch(cleanSpec);
      if (m != null) {
        count = m.group(1)!;
        unit = '瓶';
      } else {
        // "12/箱" 或 "12/件" 格式
        m = RegExp(r'(\d+)\s*/\s*(箱|件)').firstMatch(cleanSpec);
        if (m != null) {
          count = m.group(1)!;
          unit = '瓶';
        }
      }
    }

    // 从标题中提取
    if (count.isEmpty) {
      m = RegExp(r'[*xX×]\s*(\d+)\s*(瓶|听|罐|只|支)').firstMatch(cleanTitle);
      if (m != null) {
        count = m.group(1)!;
        unit = m.group(2)!;
        if (unit == '罐') unit = '听';
      } else {
        m = RegExp(r'(\d+)\s*(瓶|听|罐|只|支)').firstMatch(cleanTitle);
        if (m != null) {
          count = m.group(1)!;
          unit = m.group(2)!;
          if (unit == '罐') unit = '听';
        }
      }
    }

    // 从缩略图标签提取 "12听装"
    if (count.isEmpty || (unit == '瓶' && extraText.contains('听'))) {
      m = RegExp(r'(\d+)\s*(听|罐)装').firstMatch(extraText);
      if (m != null) {
        count = m.group(1)!;
        unit = '听';
      }
    }

    // 默认12
    if (count.isEmpty && capacity.isNotEmpty) {
      count = '12';
    }

    // 青岛原浆特殊修正
    if (productBase.contains('原浆') && productBase.contains('7天')) {
      capacity = '1L';
      count = '1';
      unit = '瓶';
    }

    // 拼接规格
    String specPart = '';
    if (capacity.isNotEmpty && count.isNotEmpty) {
      specPart = '$capacity*$count$unit';
    } else if (capacity.isNotEmpty) {
      specPart = capacity;
    }

    // 拼接最终产品名
    if (specPart.isNotEmpty) {
      return '$productBase $specPart';
    }
    return productBase;
  }

  /// 从产品名中提取规格，如 '500ml*12瓶' 或 '500ml*9+3听'
  static String extractSpec(String productName) {
    // 支持 "9+3听" 组合格式
    var m = RegExp(r'(\d+ml\*\d+\+\d+[瓶听罐])').firstMatch(productName);
    if (m != null) {
      return m.group(1)!.replaceAll('罐', '听');
    }
    m = RegExp(r'(\d+ml\*\d+[瓶听罐])').firstMatch(productName);
    if (m != null) {
      return m.group(1)!.replaceAll('罐', '听');
    }
    return '';
  }
}
