/// 同步验证脚本：验证移动端解析逻辑与桌面版关键规则一致
/// 运行: dart run tool/sync_check.dart

import '../lib/models/ocr_result.dart';
import '../lib/services/product_normalizer.dart';
import '../lib/utils/price_parser.dart';

var passCount = 0;
var failCount = 0;

void check(String name, Object? actual, Object? expected) {
  final ok = actual == expected;
  if (ok) {
    passCount++;
    print('  ✅ $name: $actual');
  } else {
    failCount++;
    print('  ❌ $name: 期望 $expected, 实际 $actual');
  }
}

OcrResult line(String text, {double left = 0.2, double top = 0.5}) =>
    OcrResult(text: text, confidence: 1.0, left: left, top: top, width: 0.1, height: 0.02);

void main() {
  print('== 产品名标准化（与桌面版 _normalize_product_name 一致） ==');

  // 漓泉系列：恒输出 漓泉1998啤酒（不解析标题数字）
  check('漓泉 小度特酿8度啤',
      ProductNormalizer.normalize('漓泉 小度特酿8度啤', '规格：冰镇 500ml*12瓶'),
      '漓泉1998啤酒 500ml*12瓶');
  check('【整箱】12罐 漓泉1998啤酒',
      ProductNormalizer.normalize('【整箱】12罐 漓泉1998啤酒', '500ml*12罐'),
      '漓泉1998啤酒 500ml*12听');
  check('9罐/件+3罐 组合规格',
      ProductNormalizer.normalize('漓泉1998啤酒', '规格：9罐/件+3罐 500ml'),
      '漓泉1998啤酒 500ml*9+3听');

  // 燕京系列
  check('燕京U88°P 误读修正',
      ProductNormalizer.normalize('燕京U88°P', '500ml*12瓶'),
      '燕京U8 500ml*12瓶');
  check('燕京无型号默认U8',
      ProductNormalizer.normalize('燕京啤酒', '500ml*12听'),
      '燕京U8 500ml*12听');

  // 雪花系列
  check('雪花勇闯无度数默认10度',
      ProductNormalizer.normalize('雪花勇闯天涯', '500ml*12听'),
      '雪花勇闯10度 500ml*12听');
  check('雪花勇闯8度',
      ProductNormalizer.normalize('雪花勇闯8度', '500ml*12听'),
      '雪花勇闯8度 500ml*12听');
  check('雪花老雪无度数默认12度',
      ProductNormalizer.normalize('雪花老雪', '640ml*12瓶'),
      '雪花老雪12度 640ml*12瓶');
  check('雪花superx 8°P',
      ProductNormalizer.normalize('雪花啤酒8°P勇闯天涯superx', '500ml*12听'),
      '雪花啤酒8°P勇闯天涯superx 500ml*12听');
  check('雪花清爽 8°P 归勇闯',
      ProductNormalizer.normalize('雪花清爽8°P', '500ml*12听'),
      '雪花勇闯8度 500ml*12听');

  // 青岛系列
  check('青岛白啤默认11度',
      ProductNormalizer.normalize('青岛白啤', '500ml*12听'),
      '青岛11度白啤 500ml*12听');
  check('青岛11度白啤（度数在前）',
      ProductNormalizer.normalize('青岛白啤11度', '500ml*12听'),
      '青岛11度白啤 500ml*12听');
  check('青岛经典10度',
      ProductNormalizer.normalize('青岛经典10度', '500ml*12听'),
      '青岛经典10度 500ml*12听');
  check('青岛110P 误读',
      ProductNormalizer.normalize('青岛啤酒110P', '500ml*12听'),
      '青岛11度白啤 500ml*12听');
  check('青岛奥古特默认12度',
      ProductNormalizer.normalize('青岛奥古特', '500ml*12听'),
      '青岛12度奥古特 500ml*12听');
  check('青岛冰醇保留冰醇',
      ProductNormalizer.normalize('青岛冰醇', '500ml*12听'),
      '青岛冰醇8度 500ml*12听');

  // 百威系列
  check('百威9.7度',
      ProductNormalizer.normalize('百威啤酒9.7度', '500ml*12听'),
      '百威9.7°啤酒 500ml*12听');
  check('百威铝罐→铝管',
      ProductNormalizer.normalize('百威铝罐', '500ml*12听'),
      '百威铝管啤酒 500ml*12听');

  // 哈啤系列
  check('哈尔滨纯生→冰纯',
      ProductNormalizer.normalize('哈尔滨纯生', '500ml*12瓶'),
      '哈尔滨冰纯 500ml*12瓶');
  check('哈尔滨小麦→小麦王',
      ProductNormalizer.normalize('哈尔滨小麦', '500ml*12瓶'),
      '哈尔滨小麦王 500ml*12瓶');

  print('== 价格修正（与桌面版 _extract_price_safe 一致） ==');

  check('波浪号修正 -¥101～', PriceParser.extractPriceSafe('-¥101～'), 10.1);
  check('波浪号修正 ¥181～(优惠类max100)', PriceParser.extractPriceSafe('-¥181～', maxVal: 100), 18.1);
  check('波浪号半角 ~', PriceParser.extractPriceSafe('-¥101~'), 10.1);
  check('¥461 优惠类 max100', PriceParser.extractPriceSafe('¥461', maxVal: 100), 46.1);
  check('¥1061 原价类 max200', PriceParser.extractPriceSafe('¥1061'), 106.1);
  check('正常价格 ¥65', PriceParser.extractPriceSafe('¥65'), 65.0);
  check('¥03 两位0开头', PriceParser.findPriceByXAlignment([line('¥03', left: 0.6)], 0.5), 0.3);
  check('多价格取最后 ¥15.6 ¥5.6',
      PriceParser.findPriceByXAlignment([line('¥15.6 ¥5.6>', left: 0.6)], 0.5),
      5.6);

  print('== 配送费（与桌面版 _find_delivery_fee 一致） ==');

  check('¥7 单一', PriceParser.findDeliveryFee([line('¥7', left: 0.6)], 0.5), 7.0);
  check('¥7 ¥1.5 取最后', PriceParser.findDeliveryFee([line('¥7 ¥1.5>', left: 0.6)], 0.5), 1.5);
  check('¥69 单价格>50 修正', PriceParser.findDeliveryFee([line('¥69', left: 0.6)], 0.5), 6.9);
  check('¥8.2 ¥32 修正3.2',
      PriceParser.findDeliveryFee([line('¥8.2 ¥32>', left: 0.6)], 0.5), 3.2);
  check('免配送费', PriceParser.findDeliveryFee([line('¥6免配送费', left: 0.6)], 0.5), 0.0);

  print('\n结果: $passCount 通过, $failCount 失败');
  if (failCount > 0) {
    throw Exception('存在失败用例');
  }
}
