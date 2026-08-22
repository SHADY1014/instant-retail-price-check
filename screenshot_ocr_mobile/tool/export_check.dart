/// 导出格式验证脚本：用与 excel_exporter.dart 相同的样式逻辑生成巡查表
/// 运行: dart run tool/export_check.dart
/// 然后用 Python/openpyxl 检查格式

import 'dart:io';
import 'package:excel/excel.dart';

// ===== 以下与 excel_exporter.dart 完全一致 =====
const _headers = [
  '分公司', '所属主要区域', '区域内即时零售平台', '店铺名称', '平台在售燕京产品',
  '产品原价\n（商品标价）', '产品成交单价\n（最终付款价格）',
  '商品优惠/商品活动\n（店铺活动）', '满减活动\n（店铺活动）',
  '优惠卷\n（平台下发）', '红包\n（平台下发）', '打包、配送费',
  '产品理论成交价格\n（产品成交价格-打包、配送费）',
  '去除平台优惠价格\n（最终付款价格-打包、配送费+优惠卷+红包）',
  '图片', '备注',
];

const _colWidths = [
  35.1, 25.3, 21.0, 24.6, 21.2, 22.7, 19.0, 19.0,
  13.0, 13.0, 13.0, 13.0, 13.0, 23.1, 18.6, 19.0,
];

final _titleStyle = CellStyle(
  backgroundColorHex: ExcelColor.fromHexString('FF003366'),
  fontColorHex: ExcelColor.white,
  fontFamily: '微软雅黑',
  fontSize: 14,
  bold: true,
  horizontalAlign: HorizontalAlign.Center,
  verticalAlign: VerticalAlign.Center,
);

final _headerStyle = CellStyle(
  backgroundColorHex: ExcelColor.fromHexString('FF0070C0'),
  fontColorHex: ExcelColor.white,
  fontFamily: '微软雅黑',
  fontSize: 11,
  bold: true,
  horizontalAlign: HorizontalAlign.Center,
  verticalAlign: VerticalAlign.Center,
  textWrapping: TextWrapping.WrapText,
  leftBorder: Border(borderStyle: BorderStyle.Thin),
  rightBorder: Border(borderStyle: BorderStyle.Thin),
  topBorder: Border(borderStyle: BorderStyle.Thin),
  bottomBorder: Border(borderStyle: BorderStyle.Thin),
);

final _dataStyle = CellStyle(
  fontFamily: '微软雅黑',
  fontSize: 10,
  horizontalAlign: HorizontalAlign.Center,
  verticalAlign: VerticalAlign.Center,
  textWrapping: TextWrapping.WrapText,
  leftBorder: Border(borderStyle: BorderStyle.Thin),
  rightBorder: Border(borderStyle: BorderStyle.Thin),
  topBorder: Border(borderStyle: BorderStyle.Thin),
  bottomBorder: Border(borderStyle: BorderStyle.Thin),
);

CellValue? _cv(Object? v) {
  if (v == null) return null;
  if (v is CellValue) return v;
  if (v is String) {
    if (v.startsWith('=')) return FormulaCellValue(v.substring(1));
    return TextCellValue(v);
  }
  if (v is int) return IntCellValue(v);
  if (v is double) return DoubleCellValue(double.parse(v.toStringAsFixed(2)));
  if (v is bool) return BoolCellValue(v);
  return TextCellValue(v.toString());
}

void _setCell(Sheet sheet, int row, int col, Object? value, [CellStyle? style]) {
  final cell = sheet.cell(
    CellIndex.indexByColumnRow(columnIndex: col, rowIndex: row),
  );
  cell.value = _cv(value) ?? TextCellValue('');
  if (style != null) cell.cellStyle = style;
}

void main() {
  final excel = Excel.createExcel();
  const sheetName = '1.燕京即时零售渠道价格巡查表';
  excel[sheetName]; // 先创建目标表（delete 要求表数量 >= 2）
  if (excel.tables.containsKey('Sheet1')) {
    excel.delete('Sheet1');
  }
  final sheet = excel[sheetName];

  for (var c = 0; c < _colWidths.length; c++) {
    sheet.setColumnWidth(c, _colWidths[c]);
  }

  // 第1行: 大标题
  sheet.merge(
    CellIndex.indexByColumnRow(columnIndex: 0, rowIndex: 0),
    CellIndex.indexByColumnRow(columnIndex: 15, rowIndex: 0),
  );
  _setCell(sheet, 0, 0, '燕京啤酒全国即时零售渠道产品价格巡查表', _titleStyle);
  sheet.setRowHeight(0, 30);

  // 第2行: 表头
  for (var c = 0; c < _headers.length; c++) {
    _setCell(sheet, 1, c, _headers[c], _headerStyle);
  }
  sheet.setRowHeight(1, 45);

  // 数据行（模拟2条，含浮点精度测试）
  final rows = [
    ['漓泉销售公司', '南宁市', '美团闪购', '桂双百使和超市(商务区店)',
        '漓泉1998啤酒 500ml*9听', 53.5, 43.5, 0.0, 0.0, 10.0, 0.0, 0.0,
        '=G3-L3', '=G3+J3+K3-L3', 'img_0.jpg', ''],
    ['漓泉销售公司', '海口市', '美团闪购', '海蓝优选超市(龙华店)',
        '燕京U8 500ml*12瓶', 82.4, 58.29, 0.0, 0.0, 10.0, 0.0, 1.5,
        '=G4-L4', '=G4+J4+K4-L4', 'img_1.jpg', ''],
  ];
  for (var i = 0; i < rows.length; i++) {
    for (var c = 0; c < rows[i].length; c++) {
      final v = rows[i][c];
      _setCell(sheet, i + 2, c,
          v is String && !v.startsWith('=') ? v : (v is String && v.startsWith('=') ? v : v is num ? (v is int ? v : v.toDouble()) : v),
          _dataStyle);
    }
    sheet.setRowHeight(i + 2, 25);
  }

  final out = '/tmp/export_check.xlsx';
  File(out).writeAsBytesSync(excel.encode()!);
  print('written: $out');
  print('sheets: ${excel.tables.keys}');
}
