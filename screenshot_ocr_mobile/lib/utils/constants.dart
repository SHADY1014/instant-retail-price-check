/// 全局常量：合格规则、省份城市池、坐标阈值
/// 移植自桌面版 summary_generator.py / city_pool.py / field_parser.py

// =========================================================
// 合格标准规则
// =========================================================
class QualificationRule {
  final String productKeyword;
  final String spec;
  final double line;
  const QualificationRule(this.productKeyword, this.spec, this.line);
}

class AppConstants {
  AppConstants._();

  /// 合格规则表（与桌面版 QUALIFICATION_RULES 一致）
  static const qualificationRules = [
    QualificationRule('漓泉1998', '500ml*12瓶', 74.99),
    QualificationRule('漓泉1998', '500ml*12听', 74.99),
    QualificationRule('漓泉1998', '500ml*9听', 45.0),
    QualificationRule('漓泉1998', '500ml*9+3听', 45.0),
    QualificationRule('燕京U8', '500ml*12瓶', 60.0),
    QualificationRule('燕京U8', '500ml*12听', 60.0),
  ];

  /// 1998 产品名称变体
  static const keywords1998 = ['漓泉1998', '铂金1998', '特渠1998'];

  /// 判断是否为1998系列产品
  static bool is1998Product(String productName) {
    return keywords1998.any((kw) => productName.contains(kw));
  }

  /// 第二档阈值：1998=70，U8=55
  static double getSecondaryThreshold(String productName) {
    if (is1998Product(productName)) return 70.0;
    return 55.0;
  }

  /// 根据产品名和规格获取合格线
  static double? getQualificationLine(String productName, String spec) {
    String normalized = productName;
    // 1998 系列归一化
    for (final kw in ['铂金1998', '特渠1998']) {
      normalized = normalized.replaceAll(kw, '漓泉1998');
    }
    for (final rule in qualificationRules) {
      if (normalized.contains(rule.productKeyword) && spec == rule.spec) {
        return rule.line;
      }
    }
    return null;
  }

  // =========================================================
  // 省份 -> 城市池（与 city_pool.py 一致，共 61 个地级市）
  // =========================================================
  static const Map<String, List<String>> cityPool = {
    '广东': [
      '广州', '深圳', '珠海', '汕头', '佛山', '韶关', '湛江',
      '肇庆', '江门', '茂名', '惠州', '梅州', '汕尾', '河源',
      '阳江', '清远', '东莞', '中山', '潮州', '揭阳', '云浮'
    ],
    '广西': [
      '南宁', '柳州', '桂林', '梧州', '北海', '防城港',
      '钦州', '贵港', '玉林', '百色', '贺州', '河池',
      '来宾', '崇左'
    ],
    '海南': ['海口', '三亚', '三沙', '儋州'],
    '贵州': ['贵阳', '六盘水', '遵义', '安顺', '毕节', '铜仁'],
    '云南': [
      '昆明', '曲靖', '玉溪', '保山', '昭通', '丽江',
      '普洱', '临沧', '楚雄', '红河', '文山', '西双版纳',
      '大理', '德宏', '怒江', '迪庆'
    ],
  };

  static List<String> getProvinces() => cityPool.keys.toList();

  static List<String> getCities(String province) =>
      cityPool[province] ?? [];

  /// 城市名 -> "XX市" 格式
  static String formatRegion(String city) => '${city}市';

  // =========================================================
  // 坐标阈值（移植自 field_parser.py 硬编码值）
  // =========================================================
  /// 同行对齐容差
  static const double alignTight = 0.015;  // 店铺名合并
  static const double alignNormal = 0.02;  // 价格对齐
  static const double alignLoose = 0.05;   // 副标题

  /// 价格右侧边界
  static const double priceRightThreshold = 0.4;  // 优惠项
  static const double priceRightStrict = 0.5;     // 原价/通用

  /// 店铺名左边界
  static const double shopLeftStrict = 0.1;  // 精准
  static const double shopLeftLoose = 0.5;   // 宽松

  /// 垂直区域分段
  static const double topBottomPrice = 0.08;    // 底部成交价
  static const double topBottomAlt = 0.06;      // 最底部 ¥xx.x
  static const double topTaobaoTotal = 0.50;    // 淘宝"合计"行（页面中部结算明细）
  static const double topBottomRecommend = 0.15; // 底部推荐
  static const double topExchangeZone = 0.35;    // 换购区上限
  static const double topThumbLabel = 0.50;      // 缩略图标签下限
  static const double topThumbLabelEnd = 0.75;   // 缩略图标签上限
  static const double topMeituanShop = 0.55;     // 美团店铺名下限
  static const double topMeituanShopEnd = 0.68;  // 美团店铺名上限
  static const double topTaobaoShop = 0.80;      // 淘宝店铺名下限
  static const double topTaobaoShopEnd = 0.90;   // 淘宝店铺名上限
  static const double topNavLimit = 0.85;        // 顶部导航栏边界
}
