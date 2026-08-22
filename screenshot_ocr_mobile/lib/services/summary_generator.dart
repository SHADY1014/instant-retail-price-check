/// 汇总表数据生成
/// 移植自桌面版 summary_generator.py 统计逻辑：
///   合格判定 = 理论成交价 >= 合格线 - 0.1（允许 0.1 元误差）
///   理论成交价 = 产品成交价格 - 打包、配送费（M 列）
///   第二档阈值：1998=70，U8=55（低于合格线的宽松统计线）

import '../models/form_fields.dart';
import '../utils/constants.dart';
import 'product_normalizer.dart';

/// 分省汇总行
class ProvinceRow {
  final String province;
  final String productName;
  final String spec;
  final double qualLine;
  final int count;
  final int passed;
  final int failed;
  final double rate; // 合格率 0~100
  final int aboveCount; // 第二档阈值以上数量
  final int belowCount; // 第二档阈值以下数量
  final double aboveRate; // 第二档合格率 0~100
  final double minPrice;
  final double maxPrice;
  final double avgPrice;

  ProvinceRow({
    required this.province,
    required this.productName,
    required this.spec,
    required this.qualLine,
    required this.count,
    required this.passed,
    required this.failed,
    required this.rate,
    required this.aboveCount,
    required this.belowCount,
    required this.aboveRate,
    required this.minPrice,
    required this.maxPrice,
    required this.avgPrice,
  });
}

/// 分地级市汇总行
class CityRow {
  final String province;
  final String region; // "XX市"
  final String productName;
  final String spec;
  final double qualLine;
  final int count;
  final int passed;
  final int failed;
  final double rate;
  final int aboveCount;
  final int belowCount;
  final double aboveRate;
  final double minPrice;
  final double maxPrice;
  final double avgPrice;

  CityRow({
    required this.province,
    required this.region,
    required this.productName,
    required this.spec,
    required this.qualLine,
    required this.count,
    required this.passed,
    required this.failed,
    required this.rate,
    required this.aboveCount,
    required this.belowCount,
    required this.aboveRate,
    required this.minPrice,
    required this.maxPrice,
    required this.avgPrice,
  });
}

/// 汇总数据（分省 + 分地级市 + 总计）
class SummaryData {
  final List<ProvinceRow> provinceRows;
  final List<CityRow> cityRows;
  final int totalCount;
  final int totalPass;
  final int totalFail;
  final double totalRate;
  final int totalAbove;
  final int totalBelow;
  final double totalAboveRate;
  /// 主产品合格线（用于表头显示，如 74.99 / 60）
  final double primaryLine;
  /// 第二档阈值（用于表头显示，如 70 / 55）
  final double secondaryLine;

  SummaryData({
    required this.provinceRows,
    required this.cityRows,
    required this.totalCount,
    required this.totalPass,
    required this.totalFail,
    required this.totalRate,
    required this.totalAbove,
    required this.totalBelow,
    required this.totalAboveRate,
    required this.primaryLine,
    required this.secondaryLine,
  });
}

class SummaryGenerator {
  SummaryGenerator._();

  /// 从巡查记录生成汇总数据
  static SummaryData build(List<FormFields> fields) {
    // 预计算每条记录：省份 / 产品名 / 规格 / 合格线 / 是否合格 / 理论价
    final records = <_Record>[];
    for (final f in fields) {
      final spec = ProductNormalizer.extractSpec(f.productName);
      final qualLine = AppConstants.getQualificationLine(f.productName, spec);
      if (qualLine == null) continue; // 非目标产品（非1998/U8）不计入汇总
      final theoryPrice = f.theoryPrice;
      records.add(_Record(
        province: _provinceOf(f.region),
        region: f.region,
        productName: f.productName,
        spec: spec,
        qualLine: qualLine,
        theoryPrice: theoryPrice,
        // 允许 0.1 元误差（与桌面版一致）
        passed: theoryPrice >= qualLine - 0.1,
      ));
    }

    // 分省 × 产品名 × 规格 分组
    final provGroups = <String, List<_Record>>{};
    for (final r in records) {
      provGroups
          .putIfAbsent('${r.province}\u0000${r.productName}\u0000${r.spec}',
              () => [])
          .add(r);
    }

    final provinceRows = <ProvinceRow>[];
    final keys = provGroups.keys.toList()..sort();
    for (final key in keys) {
      final items = provGroups[key]!;
      provinceRows.add(_buildProvinceRow(items));
    }

    // 分地级市 × 产品名 × 规格 分组
    final cityGroups = <String, List<_Record>>{};
    for (final r in records) {
      cityGroups
          .putIfAbsent('${r.province}\u0000${r.region}\u0000${r.productName}\u0000${r.spec}',
              () => [])
          .add(r);
    }

    final cityRows = <CityRow>[];
    final cityKeys = cityGroups.keys.toList()..sort();
    for (final key in cityKeys) {
      final items = cityGroups[key]!;
      final first = items.first;
      final stats = _stats(items);
      cityRows.add(CityRow(
        province: first.province,
        region: first.region,
        productName: _shortName(first.productName),
        spec: first.spec,
        qualLine: first.qualLine,
        count: stats.count,
        passed: stats.passed,
        failed: stats.failed,
        rate: stats.rate,
        aboveCount: stats.aboveCount,
        belowCount: stats.belowCount,
        aboveRate: stats.aboveRate,
        minPrice: stats.minPrice,
        maxPrice: stats.maxPrice,
        avgPrice: stats.avgPrice,
      ));
    }

    final totalCount = records.length;
    final totalPass = records.where((r) => r.passed).length;
    final totalAbove =
        records.where((r) => r.theoryPrice >= _secondaryOf(r.productName)).length;
    // 表头主合格线/第二档阈值：取第一条记录（同一批次通常只有一个产品）
    double primaryLine = 0.0;
    double secondaryLine = 0.0;
    if (records.isNotEmpty) {
      primaryLine = records.first.qualLine;
      secondaryLine = _secondaryOf(records.first.productName);
    }

    return SummaryData(
      provinceRows: provinceRows,
      cityRows: cityRows,
      totalCount: totalCount,
      totalPass: totalPass,
      totalFail: totalCount - totalPass,
      totalRate: totalCount > 0 ? totalPass / totalCount * 100 : 0.0,
      totalAbove: totalAbove,
      totalBelow: totalCount - totalAbove,
      totalAboveRate: totalCount > 0 ? totalAbove / totalCount * 100 : 0.0,
      primaryLine: primaryLine,
      secondaryLine: secondaryLine,
    );
  }

  static ProvinceRow _buildProvinceRow(List<_Record> items) {
    final first = items.first;
    final stats = _stats(items);
    return ProvinceRow(
      province: first.province,
      productName: _shortName(first.productName),
      spec: first.spec,
      qualLine: first.qualLine,
      count: stats.count,
      passed: stats.passed,
      failed: stats.failed,
      rate: stats.rate,
      aboveCount: stats.aboveCount,
      belowCount: stats.belowCount,
      aboveRate: stats.aboveRate,
      minPrice: stats.minPrice,
      maxPrice: stats.maxPrice,
      avgPrice: stats.avgPrice,
    );
  }

  /// 第二档阈值：1998=70，其他（U8）=55
  static double _secondaryOf(String productName) {
    return AppConstants.getSecondaryThreshold(productName);
  }

  /// 分组统计
  static _Stats _stats(List<_Record> items) {
    final count = items.length;
    final passed = items.where((r) => r.passed).length;
    final secondary = _secondaryOf(items.first.productName);
    final above = items.where((r) => r.theoryPrice >= secondary).length;
    final prices = items.map((r) => r.theoryPrice).toList()..sort();
    final minPrice = prices.isNotEmpty ? prices.first : 0.0;
    final maxPrice = prices.isNotEmpty ? prices.last : 0.0;
    final avg = prices.isNotEmpty
        ? prices.reduce((a, b) => a + b) / prices.length
        : 0.0;
    return _Stats(
      count: count,
      passed: passed,
      failed: count - passed,
      rate: count > 0 ? passed / count * 100 : 0.0,
      aboveCount: above,
      belowCount: count - above,
      aboveRate: count > 0 ? above / count * 100 : 0.0,
      minPrice: minPrice,
      maxPrice: maxPrice,
      avgPrice: double.parse(avg.toStringAsFixed(1)),
    );
  }

  /// 从区域名（"XX市"）反查省份
  static String _provinceOf(String region) {
    if (region.isEmpty) return '';
    for (final entry in AppConstants.cityPool.entries) {
      for (final city in entry.value) {
        if (region.contains(city)) return entry.key;
      }
    }
    return '';
  }

  /// 简化产品名：去掉规格部分和"啤酒"后缀
  static String _shortName(String fullName) {
    var name = fullName.replaceAll(RegExp(r'\s*\d+ml.*$'), '');
    name = name.replaceAll('啤酒', '');
    return name.trim();
  }
}

class _Record {
  final String province;
  final String region;
  final String productName;
  final String spec;
  final double qualLine;
  final double theoryPrice;
  final bool passed;

  _Record({
    required this.province,
    required this.region,
    required this.productName,
    required this.spec,
    required this.qualLine,
    required this.theoryPrice,
    required this.passed,
  });
}

class _Stats {
  final int count;
  final int passed;
  final int failed;
  final double rate;
  final int aboveCount;
  final int belowCount;
  final double aboveRate;
  final double minPrice;
  final double maxPrice;
  final double avgPrice;

  _Stats({
    required this.count,
    required this.passed,
    required this.failed,
    required this.rate,
    required this.aboveCount,
    required this.belowCount,
    required this.aboveRate,
    required this.minPrice,
    required this.maxPrice,
    required this.avgPrice,
  });
}
