/// 价格提取工具
/// 移植自桌面版 field_parser.py 的 _extract_price / _extract_price_safe / _find_price_by_x_alignment / _find_delivery_fee

import 'dart:math';
import '../models/ocr_result.dart';
import '../utils/constants.dart';

class PriceParser {
  PriceParser._();

  /// 基础价格提取：匹配 ¥65 / -¥8.1 / ¥0.5 / 39.4 / -¥461~ 等格式
  /// 注意: OCR 经常把小数点漏掉，如 ¥461 实际是 ¥4.61，此处只提取原始数字值
  static double? extractPrice(String text) {
    // 去掉波浪号等噪声字符
    var cleaned = text
        .replaceAll('～', '')
        .replaceAll('~', '')
        .replaceAll('＞', '')
        .replaceAll('>', '')
        .trim();
    // 优先匹配带 ¥ 的
    final m1 = RegExp(r'[¥￥]\s*(-?\d+\.?\d*)').firstMatch(cleaned);
    if (m1 != null) {
      return double.tryParse(m1.group(1)!);
    }
    // 纯数字（含小数）
    final m2 = RegExp(r'(-?\d+\.?\d*)').firstMatch(cleaned);
    if (m2 != null) {
      return double.tryParse(m2.group(1)!);
    }
    return null;
  }

  /// 安全价格提取：过滤掉明显不合理的值
  /// OCR 常见误读模式：
  ///   - 小数点被识别为 ～ 或 ~：-¥101～ 实际是 10.1
  ///   - 小数点完全丢失：¥461 实际是 4.61
  ///
  /// [maxVal] 该字段的合理上限。超过上限时依次尝试 /10、/100、/1000 缩小，
  /// 取第一个不超过上限的值。
  ///   原价/总价类上限 200（如 106.1、127.6），
  ///   优惠/红包类上限 100（如 -¥171 实际 17.1、-¥192 实际 19.2）
  static double extractPriceSafe(String text, {double maxVal = 200.0}) {
    // 先尝试从 "¥xxx～" 格式中修正小数点（OCR 把 . 读成 ～）
    // 例如 -¥101～ -> 10.1, -¥181～ -> 18.1
    // 但 -¥15～ -> 15（不修正，因为15元优惠是合理的）
    final mWave = RegExp(r'[¥￥]\s*(\d{3})[～~]').firstMatch(text);
    if (mWave != null) {
      final digits = mWave.group(1)!;
      // 3位数 -> 去掉最后一位，加小数点: 101 -> 10.1
      return double.tryParse('${digits.substring(0, digits.length - 1)}.${digits[digits.length - 1]}') ?? 0.0;
    }

    final raw = extractPrice(text);
    if (raw == null) return 0.0;
    var price = raw.abs();
    if (price == 0.0) return 0.0;

    // 如果价格超过合理上限，可能是小数点被 OCR 漏掉，依次尝试 /10、/100、/1000
    if (price > maxVal) {
      for (final divisor in [10, 100, 1000]) {
        final corrected = price / divisor;
        if (corrected <= maxVal) {
          return double.parse(corrected.toStringAsFixed(2));
        }
      }
      return double.parse((price / 1000).toStringAsFixed(2));
    }
    return price;
  }

  /// 按 Y 坐标对齐找右侧的价格
  /// 美团结算页价格都在右侧（left > 0.5），标签在左侧
  /// 如果同一行有多个价格（如 ¥7 ¥1.5），取最后一个（实际支付价）
  /// 如果同一文本项中有多个价格（如 "¥15.6 ¥5.6>"），也取最后一个
  ///
  /// 修正：OCR 漏小数点导致 ¥0.3 被识别为 ¥03 -> float("03")=3.0
  /// 当数字以0开头且为2位数（如"03"），实际应为 0.X 格式
  static double findPriceByXAlignment(
    List<OcrResult> lines,
    double targetTop, {
    double xThreshold = AppConstants.priceRightThreshold,
  }) {
    final candidates = <double>[];
    for (final item in lines) {
      if ((item.top - targetTop).abs() < AppConstants.alignNormal &&
          item.left > xThreshold) {
        final text = item.text;
        // 提取文本中的所有价格（处理 "¥15.6 ¥5.6>" 这种多价格文本）
        final prices = RegExp(r'[¥￥]\s*(\d+\.?\d*)').allMatches(text).toList();
        if (prices.isNotEmpty) {
          // 取该文本项中的最后一个价格
          final raw = prices.last.group(1)!;
          var val = double.tryParse(raw)?.abs() ?? 0.0;
          // 修正：以0开头的2位数（如"03"），OCR漏了小数点，实际应为0.X
          if (raw.length == 2 && raw[0] == '0') {
            val = double.tryParse('0.${raw[1]}') ?? val;
          }
          candidates.add(val);
        } else {
          // 尝试纯数字
          final price = extractPrice(text);
          if (price != null && price != 0.0) {
            candidates.add(price.abs());
          }
        }
      }
    }
    if (candidates.isNotEmpty) {
      // 取最后一个候选价格（同一行最右侧的实际支付金额）
      return candidates.last;
    }
    return 0.0;
  }

  /// 配送费专用价格提取
  /// 配送费行常见格式：
  ///   - "¥7" 单一价格
  ///   - "¥7 ¥1.5>" 原价和优惠后价格，取最后一个（优惠后实际配送费）
  ///   - "¥8.2 ¥32>" OCR漏掉小数点，32实际是3.2
  ///   - "¥6免配送费" 表示配送费被免除，实际为0
  ///
  /// 修正逻辑：
  ///   1. 如果文本含"免配送费"/"配送费免"等字样，直接返回0
  ///   2. 否则取最后一个价格，如果它大于前面的价格（原价），
  ///      说明OCR漏了小数点，在倒数第二位插入小数点让它变小
  static double findDeliveryFee(
    List<OcrResult> lines,
    double targetTop, {
    double xThreshold = AppConstants.priceRightThreshold,
  }) {
    final allPrices = <double>[];
    for (final item in lines) {
      if ((item.top - targetTop).abs() < AppConstants.alignNormal &&
          item.left > xThreshold) {
        final text = item.text;
        // 检测"免配送费"等关键字，表示配送费已被免除
        if (text.contains('免配送费') ||
            text.contains('配送费免') ||
            text.contains('免运费')) {
          return 0.0;
        }
        final prices = RegExp(r'[¥￥]\s*(\d+\.?\d*)').allMatches(text).toList();
        if (prices.isNotEmpty) {
          for (final p in prices) {
            allPrices.add(double.tryParse(p.group(1)!)?.abs() ?? 0.0);
          }
        } else {
          final price = extractPrice(text);
          if (price != null && price != 0.0) {
            allPrices.add(price.abs());
          }
        }
      }
    }
    if (allPrices.isEmpty) return 0.0;

    // 取最后一个价格
    final lastPrice = allPrices.last;

    // 只有单个价格且异常大（>50）：OCR 漏了小数点，如 "¥69" 实际是 6.9
    // 配送费不会超过50元（即使超重上调），修正后也不应超过40
    if (allPrices.length == 1 && lastPrice > 50) {
      final corrected = lastPrice / 10;
      if (corrected <= 40) return corrected;
    }

    // 如果有前一个价格（原价），且最后一个价格 >= 前一个价格
    // 说明OCR漏了小数点，需要缩小：依次尝试 /10、/100、/1000
    // 例如 32 -> 3.2, 333 -> 3.33
    if (allPrices.length >= 2 && lastPrice >= allPrices[allPrices.length - 2]) {
      final prevPrice = allPrices[allPrices.length - 2];
      for (final divisor in [10, 100, 1000]) {
        final corrected = lastPrice / divisor;
        // 修正后必须小于原价才算有效（优惠后配送费不会高于原价）
        if (corrected < prevPrice) return corrected;
      }
    }
    return lastPrice;
  }
}
