import 'package:flutter_test/flutter_test.dart';
import 'package:price_check_ocr/models/ocr_result.dart';
import 'package:price_check_ocr/services/field_parser.dart';
import 'package:price_check_ocr/services/review_rules.dart';

OcrResult ocr(String text, double left, double top) => OcrResult(
      text: text,
      left: left,
      top: top,
      width: 0.1,
      height: 0.02,
      confidence: 1.0,
    );

void main() {
  test('parses JD checkout and separates product/shipping discounts', () {
    final fields = FieldParser.parse([
      ocr('秒送', 0.06, 0.770),
      ocr('京东酒世界（富春花园店）', 0.145, 0.770),
      ocr('京东秒送', 0.78, 0.770),
      ocr('【整箱】燕京U8 8°P啤酒瓶装', 0.215, 0.727),
      ocr('¥80 ¥60', 0.797, 0.727),
      ocr('商品金额', 0.06, 0.571),
      ocr('¥60', 0.832, 0.571),
      ocr('运费 活动减5元运费', 0.057, 0.529),
      ocr('¥5.8 ¥0.8', 0.746, 0.525),
      ocr('应付总额 ¥60.8 共减¥5', 0.041, 0.108),
    ]);

    expect(fields.platform, '京东闪送');
    expect(fields.shopName, '京东酒世界（富春花园店）');
    expect(fields.productName, '燕京U8 500ml*12瓶');
    expect(fields.originalPrice, 80.0);
    expect(fields.shopDiscount, 20.0);
    expect(fields.finalPrice, 60.8);
    expect(fields.deliveryFee, 0.8);
    expect(fields.remark, contains('京东运费活动优惠5元'));
  });

  test('repairs missing decimal point in JD shipping OCR', () {
    final fields = FieldParser.parse([
      ocr('京东秒送', 0.78, 0.770),
      ocr('燕京U8 500ml*12瓶', 0.215, 0.727),
      ocr('¥75 ¥60', 0.797, 0.727),
      ocr('商品金额', 0.06, 0.571),
      ocr('¥60', 0.832, 0.571),
      ocr('运费 活动减5元运费', 0.057, 0.529),
      ocr('¥98 ¥48', 0.746, 0.525),
      ocr('应付总额 ¥64.8', 0.041, 0.108),
    ]);

    expect(fields.deliveryFee, 4.8);
  });

  test('flags missing JD package quantity for manual review', () {
    final fields = FieldParser.parse([
      ocr('京东秒送', 0.78, 0.770),
      ocr('京东酒世界（富春花园店）', 0.145, 0.770),
      ocr('【啤酒小站】燕京啤酒 燕京U8小.', 0.218, 0.519),
      ocr('¥31.9', 0.848, 0.517),
      ocr('商品金额', 0.06, 0.365),
      ocr('¥31.9', 0.807, 0.362),
      ocr('运费', 0.057, 0.323),
      ocr('已免运费 ¥4.8 ¥0', 0.642, 0.323),
      ocr('应付总额 ¥31.9', 0.041, 0.106),
    ]);

    expect(fields.specUnreliable, isTrue);
    expect(reviewIssues(fields), contains('产品规格需人工确认'));
    expect(fields.remark, contains('京东运费优惠4.8元'));
  });

  test('treats waived JD shipping as zero when OCR drops ¥0', () {
    final fields = FieldParser.parse([
      ocr('京东秒送', 0.78, 0.770),
      ocr('京东酒世界（富春花园店）', 0.145, 0.770),
      ocr('燕京U8 500ml*12瓶', 0.218, 0.519),
      ocr('¥80 ¥60', 0.807, 0.517),
      ocr('商品金额', 0.06, 0.365),
      ocr('¥60', 0.807, 0.362),
      ocr('运费', 0.057, 0.323),
      ocr('已免运费 ¥4.8', 0.642, 0.323),
      ocr('应付总额 ¥60', 0.041, 0.106),
    ]);

    expect(fields.deliveryFee, 0.0);
  });

  test('keeps JD shop prefix cleaned when OCR puts the store at the left edge',
      () {
    // 真实截图中店铺文本可能位于 left≈0.06，与“京东秒送”分成两块。
    // 通用地址策略不能覆盖京东专用策略清理后的店名。
    final fields = FieldParser.parse([
      ocr('请选择收货地址', 0.05, 0.847),
      ocr('自营·秒送 京东酒世界（富春花园店）', 0.06, 0.562),
      ocr('京东秒送①', 0.78, 0.562),
      ocr('燕京U8 8°P啤酒瓶装', 0.218, 0.519),
      ocr('¥80 ¥60', 0.807, 0.517),
      ocr('商品金额', 0.06, 0.365),
      ocr('¥60', 0.807, 0.362),
      ocr('运费', 0.057, 0.323),
      ocr('已免运费 ¥5 ¥0', 0.642, 0.323),
      ocr('应付总额 ¥60', 0.041, 0.106),
    ]);

    expect(fields.shopName, '京东酒世界（富春花园店）');
  });

  test('maps an applied JD coupon to the platform coupon field', () {
    final fields = FieldParser.parse([
      ocr('京东秒送', 0.78, 0.770),
      ocr('京东酒世界（富春花园店）', 0.145, 0.770),
      ocr('燕京U8 500ml*12瓶', 0.218, 0.519),
      ocr('¥80 ¥60', 0.807, 0.517),
      ocr('商品金额', 0.06, 0.365),
      ocr('¥60', 0.807, 0.362),
      ocr('运费', 0.057, 0.323),
      ocr('已免运费 ¥5 ¥0', 0.642, 0.323),
      ocr('优惠券', 0.06, 0.280),
      ocr('-¥10', 0.807, 0.280),
      ocr('应付总额 ¥50', 0.041, 0.106),
    ]);

    expect(fields.coupon, 10.0);
  });
}
