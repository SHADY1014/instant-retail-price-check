"""京东秒送结算页字段解析回归测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from field_parser import parse_ocr_to_fields


def _ocr(text, left, top):
    return {
        "text": text,
        "left": left,
        "top": top,
        "width": 0.1,
        "height": 0.02,
        "confidence": 1.0,
    }


JD_CHECKOUT = [
    _ocr("请选择收货地址", 0.05, 0.847),
    _ocr("秒送", 0.06, 0.770),
    _ocr("京东酒世界-啤酒小站（环城南路云仓）", 0.145, 0.770),
    _ocr("京东秒送①", 0.78, 0.770),
    _ocr("【啤酒小站】【整箱】燕京U8 8°", 0.215, 0.727),
    _ocr("¥75 ¥60", 0.797, 0.727),
    _ocr("商品金额", 0.06, 0.571),
    _ocr("¥60", 0.832, 0.571),
    _ocr("运费 活动减5元运费 ①", 0.057, 0.529),
    _ocr("¥9.8 ¥4.8）", 0.746, 0.525),
    _ocr("优惠券", 0.06, 0.440),
    _ocr("无可用〉", 0.788, 0.442),
    _ocr("应付总额 ¥64.8 共减¥5", 0.041, 0.108),
    _ocr("立即支付", 0.424, 0.053),
]


class JdFieldParserTests(unittest.TestCase):

    def test_jd_checkout_maps_product_and_payment_fields(self):
        fields = parse_ocr_to_fields(JD_CHECKOUT)

        self.assertEqual(fields.platform, "京东闪送")
        self.assertEqual(fields.shop_name, "京东酒世界-啤酒小站（环城南路云仓）")
        self.assertEqual(fields.product_name, "燕京U8 500ml*12瓶")
        self.assertEqual(fields.original_price, 75.0)
        self.assertEqual(fields.final_price, 64.8)
        self.assertEqual(fields.shop_discount, 15.0)
        self.assertEqual(fields.delivery_fee, 4.8)
        self.assertEqual(fields.final_price - fields.delivery_fee, 60.0)
        # 标题截断无数量证据 -> 规格存疑；运费活动优惠同时保留
        self.assertTrue(fields.spec_unreliable)
        self.assertIn("产品规格需人工确认", fields.remark)
        self.assertIn("京东运费活动优惠5元", fields.remark)

    def test_jd_service_alias_is_detected_without_store_name(self):
        fields = parse_ocr_to_fields([
            _ocr("京东闪送", 0.78, 0.770),
            _ocr("商品金额", 0.06, 0.571),
            _ocr("¥60", 0.832, 0.571),
            _ocr("应付总额 ¥60", 0.041, 0.108),
        ])
        self.assertEqual(fields.platform, "京东闪送")

    def test_jd_truncated_title_with_star_count_is_reliable(self):
        """标题「500ml*6 默认」数量来自 OCR，不标记规格存疑。"""
        fields = parse_ocr_to_fields([
            _ocr("秒送", 0.06, 0.770),
            _ocr("京东酒世界-啤酒小站（环城南路云仓）", 0.145, 0.770),
            _ocr("燕京U8瓶装啤酒500ml*6 默认", 0.218, 0.520),
            _ocr("¥29.9", 0.842, 0.516),
            _ocr("商品金额", 0.06, 0.365),
            _ocr("¥29.9", 0.804, 0.362),
            _ocr("运费 ①", 0.057, 0.323),
            _ocr("已免运费 ¥4.3¥0〉", 0.642, 0.323),
            _ocr("应付总额 ¥29.", 0.041, 0.108),
            _ocr(".9 共减¥4.3", 0.247, 0.107),
            _ocr("立即支付", 0.424, 0.054),
        ])
        self.assertEqual(fields.product_name, "燕京U8 500ml*6瓶")
        self.assertFalse(fields.spec_unreliable)
        # 应付总额碎片 ¥29. + .9 拼接为 29.9；已免运费实付 ¥0
        self.assertEqual(fields.final_price, 29.9)
        self.assertEqual(fields.delivery_fee, 0.0)
        self.assertIn("京东运费优惠4.3元", fields.remark)

    def test_jd_shop_prefix_is_cleaned(self):
        """「自营 秒送」徽标前缀应被清理，不进入店铺名。"""
        fields = parse_ocr_to_fields([
            _ocr("秒送", 0.06, 0.770),
            _ocr("自营 秒送 京东酒世界（富春花园店）", 0.145, 0.770),
            _ocr("京东秒送①", 0.78, 0.770),
            _ocr("【啤酒小站】燕京啤酒 燕京U8小.", 0.218, 0.519),
            _ocr("¥31.9", 0.848, 0.517),
            _ocr("商品金额", 0.06, 0.365),
            _ocr("¥31.9", 0.807, 0.362),
            _ocr("运费 ①", 0.057, 0.323),
            _ocr("已免运费 ¥4.8 ¥0〉", 0.642, 0.323),
            _ocr("应付总额￥31.9 共减¥4.8", 0.041, 0.106),
            _ocr("立即支付", 0.424, 0.054),
        ])
        self.assertEqual(fields.shop_name, "京东酒世界（富春花园店）")
        # 标题截断无数量证据 -> 默认 12 且规格存疑
        self.assertEqual(fields.product_name, "燕京U8 500ml*12瓶")
        self.assertTrue(fields.spec_unreliable)
        self.assertEqual(fields.final_price, 31.9)
        self.assertEqual(fields.delivery_fee, 0.0)

    def test_jd_waived_shipping_without_zero_amount_stays_zero(self):
        """“已免运费”文字足以确认实付为 0，即使 OCR 漏掉 ¥0。"""
        fields = parse_ocr_to_fields([
            _ocr("京东秒送", 0.78, 0.770),
            _ocr("京东酒世界（富春花园店）", 0.145, 0.770),
            _ocr("燕京U8 500ml*12瓶", 0.218, 0.519),
            _ocr("¥80 ¥60", 0.807, 0.517),
            _ocr("商品金额", 0.06, 0.365),
            _ocr("¥60", 0.807, 0.362),
            _ocr("运费", 0.057, 0.323),
            _ocr("已免运费 ¥4.8", 0.642, 0.323),
            _ocr("应付总额 ¥60", 0.041, 0.106),
        ])
        self.assertEqual(fields.delivery_fee, 0.0)

    def test_jd_coupon_row_is_mapped_to_platform_coupon(self):
        fields = parse_ocr_to_fields([
            _ocr("京东秒送", 0.78, 0.770),
            _ocr("京东酒世界（富春花园店）", 0.145, 0.770),
            _ocr("燕京U8 500ml*12瓶", 0.218, 0.519),
            _ocr("¥80 ¥60", 0.807, 0.517),
            _ocr("商品金额", 0.06, 0.365),
            _ocr("¥60", 0.807, 0.362),
            _ocr("运费", 0.057, 0.323),
            _ocr("已免运费 ¥5 ¥0", 0.642, 0.323),
            _ocr("优惠券", 0.06, 0.280),
            _ocr("-¥10", 0.807, 0.280),
            _ocr("应付总额 ¥50", 0.041, 0.106),
        ])
        self.assertEqual(fields.coupon, 10.0)


if __name__ == "__main__":
    unittest.main()
