import '../models/form_fields.dart';

/// Returns the same manual-review reasons used by desktop builds.
List<String> reviewIssues(FormFields fields) {
  final remark = fields.remark;
  if (remark.contains('OCR失败') ||
      remark.contains('OCR 失败') ||
      remark.contains('OCR 未完成')) {
    return const ['OCR 识别失败'];
  }
  final issues = <String>[];
  if (fields.region.trim().isEmpty) issues.add('未确认所属区域');
  if (fields.shopName.trim().isEmpty) issues.add('未识别店铺名称');
  if (fields.productName.trim().isEmpty) issues.add('未识别产品名称');
  if (fields.originalPrice <= 0) issues.add('未识别产品原价');
  if (fields.finalPrice <= 0) issues.add('未识别成交价');
  return issues;
}
