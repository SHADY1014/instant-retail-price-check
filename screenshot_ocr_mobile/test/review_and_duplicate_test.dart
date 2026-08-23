import 'package:flutter_test/flutter_test.dart';
import 'package:price_check_ocr/models/form_fields.dart';
import 'package:price_check_ocr/services/duplicate_checker.dart';
import 'package:price_check_ocr/services/review_rules.dart';

void main() {
  test('review rules match desktop required fields', () {
    final issues = reviewIssues(FormFields(productName: '燕京U8'));
    expect(issues, contains('未确认所属区域'));
    expect(issues, contains('未识别成交价'));
  });

  test('duplicate groups use shop platform city and theory price', () {
    final rows = [
      FormFields(
          region: '广州市', shopName: '测试店', productName: '燕京U8', finalPrice: 60),
      FormFields(
          region: '广州市', shopName: '测试店', productName: '燕京U8', finalPrice: 60),
      FormFields(
          region: '佛山市', shopName: '测试店', productName: '燕京U8', finalPrice: 60),
    ];
    expect(findDuplicateGroups(rows), [
      [0, 1]
    ]);
  });
}
