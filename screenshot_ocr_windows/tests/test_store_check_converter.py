import os
import re
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from store_check_converter import (  # noqa: E402
    SourceRecord,
    convert_inspection_to_store_check,
    extract_spec,
    filter_u8_records,
    merge_records,
    normalize_shop_name,
    parse_source_workbook,
    threshold_for,
    validate_output_workbook,
)


class StoreCheckConverterTest(unittest.TestCase):
    def test_shop_suffix_longest_first_and_parentheses(self):
        key, display = normalize_shop_name("秒送 京东酒世界（富春花园云仓店）")
        self.assertEqual(key, "富春花园")
        self.assertEqual(display, "京东酒世界（富春花园云仓店）")
        key, display = normalize_shop_name("京东酒世界（滇池度假区仓店）")
        self.assertEqual(key, "滇池度假区")
        self.assertEqual(display, "京东酒世界（滇池度假区仓店）")

    def test_spec_normalization(self):
        self.assertEqual(extract_spec("燕京U8 500ml×12罐"), "500ml*12听")
        self.assertEqual(extract_spec("漓泉1998 500ml*9+3听"), "500ml*9+3听")

    def test_merge_uses_lowest_price_and_recalculates_status(self):
        def record(row, platform, price, theory):
            return SourceRecord(
                row_number=row,
                region="昆明市",
                shop_name="京东酒世界（富春花园仓店）",
                platform=platform,
                product_name="燕京U8 500ml*6瓶",
                spec="500ml*6瓶",
                final_price=price,
                theory_price=theory,
                shop_key="富春花园",
                display_name="京东酒世界（富春花园仓店）",
            )

        rows, pending, _ = merge_records([
            record(1, "美团闪购", 40, 36),
            record(2, "美团闪购", 38, 28),
            record(3, "京东闪送", 30, 30),
        ])
        self.assertFalse(pending)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].platform_values["美团闪购"].final_price, 38)
        self.assertEqual(rows[0].status, "是")

    def test_different_chains_same_area_are_not_merged(self):
        records = []
        for row, shop in enumerate(
            ("京东酒世界（南亚店）", "另一连锁（南亚店）"), start=1
        ):
            area, display = normalize_shop_name(shop)
            records.append(
                SourceRecord(
                    row_number=row,
                    region="昆明市",
                    shop_name=shop,
                    platform="美团闪购",
                    product_name="燕京U8 500ml*6瓶",
                    spec="500ml*6瓶",
                    final_price=30,
                    theory_price=30,
                    shop_key=f"{display.split('（', 1)[0]}|{area}",
                    display_name=display,
                )
            )
        rows, pending, _ = merge_records(records)
        self.assertFalse(pending)
        self.assertEqual(len(rows), 2)

    def test_conversion_scope_is_u8_only(self):
        base = dict(
            row_number=1,
            region="昆明市",
            shop_name="测试门店（南亚店）",
            platform="美团闪购",
            spec="500ml*6瓶",
            final_price=30,
            theory_price=30,
            shop_key="测试门店|南亚",
            display_name="测试门店（南亚店）",
        )
        u8 = SourceRecord(product_name="燕京U8 500ml*6瓶", **base)
        other = SourceRecord(
            product_name="漓泉1998 500ml*6瓶", row_number=2, **{k: v for k, v in base.items() if k != "row_number"}
        )
        kept, skipped = filter_u8_records([u8, other])
        self.assertEqual(kept, [u8])
        self.assertEqual(len(skipped), 1)

    def test_1998_province_thresholds(self):
        self.assertEqual(
            threshold_for("漓泉1998 500ml*12瓶", "500ml*12瓶", "广东省"),
            70,
        )
        self.assertEqual(
            threshold_for("漓泉1998 500ml*12瓶", "500ml*12瓶", "广西南宁市"),
            60,
        )
        self.assertEqual(
            threshold_for("漓泉1998 500ml*6瓶", "500ml*6瓶", "广东省"),
            29.9,
        )
        self.assertIsNone(
            threshold_for("漓泉1998 500ml*12瓶", "500ml*12瓶", "未知市")
        )

    def test_real_workbook_parse_and_output_integrity(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        source = os.path.join(
            root,
            "output",
            "巡查表_20260902_143723",
            "价格巡查表_20260902_143723.xlsx",
        )
        records, warnings = parse_source_workbook(source)
        self.assertEqual(len(records), 43)
        self.assertFalse(warnings)
        self.assertTrue(all(item.image_bytes for item in records))
        with tempfile.TemporaryDirectory() as output_dir:
            result = convert_inspection_to_store_check(
                source,
                output_dir=output_dir,
                output_name="转换测试.xlsx",
            )
            self.assertEqual(result.output_path, os.path.join(output_dir, "转换测试.xlsx"))
            self.assertTrue(os.path.isfile(result.output_path))
            self.assertEqual(validate_output_workbook(result.output_path), [])
            with zipfile.ZipFile(result.output_path) as archive:
                self.assertIn("xl/cellimages.xml", archive.namelist())
                self.assertIn("xl/_rels/cellimages.xml.rels", archive.namelist())
                sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
                ids = set(re.findall(r'"(ID_[A-F0-9]{32})"', sheet_xml))
                cellimages = archive.read("xl/cellimages.xml").decode("utf-8")
                self.assertTrue(ids)
                for image_id in ids:
                    self.assertIn(image_id, cellimages)
                self.assertNotIn("#REF!", sheet_xml)


if __name__ == "__main__":
    unittest.main()
