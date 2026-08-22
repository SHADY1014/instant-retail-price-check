/// 汇总表导出验证
import 'dart:io';
import 'package:excel/excel.dart';

final _titleStyle = CellStyle(
  backgroundColorHex: ExcelColor.fromHexString('FF003366'),
  fontColorHex: ExcelColor.white, fontFamily: '微软雅黑', fontSize: 14, bold: true,
  horizontalAlign: HorizontalAlign.Center, verticalAlign: VerticalAlign.Center,
);
final _headerStyle = CellStyle(
  backgroundColorHex: ExcelColor.fromHexString('FF0070C0'),
  fontColorHex: ExcelColor.white, fontFamily: '微软雅黑', fontSize: 11, bold: true,
  horizontalAlign: HorizontalAlign.Center, verticalAlign: VerticalAlign.Center,
  textWrapping: TextWrapping.WrapText,
  leftBorder: Border(borderStyle: BorderStyle.Thin), rightBorder: Border(borderStyle: BorderStyle.Thin),
  topBorder: Border(borderStyle: BorderStyle.Thin), bottomBorder: Border(borderStyle: BorderStyle.Thin),
);
final _dataStyle = CellStyle(
  fontFamily: '微软雅黑', fontSize: 10,
  horizontalAlign: HorizontalAlign.Center, verticalAlign: VerticalAlign.Center,
  leftBorder: Border(borderStyle: BorderStyle.Thin), rightBorder: Border(borderStyle: BorderStyle.Thin),
  topBorder: Border(borderStyle: BorderStyle.Thin), bottomBorder: Border(borderStyle: BorderStyle.Thin),
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
  return TextCellValue(v.toString());
}

void _setCell(Sheet sheet, int row, int col, Object? value, [CellStyle? style]) {
  final cell = sheet.cell(CellIndex.indexByColumnRow(columnIndex: col, rowIndex: row));
  cell.value = _cv(value) ?? TextCellValue('');
  if (style != null) cell.cellStyle = style;
}

void main() {
  final excel = Excel.createExcel();
  excel['汇总表'];
  if (excel.tables.containsKey('Sheet1')) excel.delete('Sheet1');
  final sheet = excel['汇总表'];

  const widths = [8.0, 14.0, 14.0, 10.0, 8.0, 8.0, 10.0, 10.0, 14.0, 14.0, 16.0, 12.0, 12.0, 12.0];
  for (var c = 0; c < widths.length; c++) sheet.setColumnWidth(c, widths[c]);

  const primaryLabel = '74.99元';
  const secondaryLabel = '70';

  _setCell(sheet, 0, 0, '一、分省价格合格率汇总表', _titleStyle);
  sheet.merge(CellIndex.indexByColumnRow(columnIndex: 0, rowIndex: 0), CellIndex.indexByColumnRow(columnIndex: 13, rowIndex: 0));
  sheet.setRowHeight(0, 30);
  _setCell(sheet, 1, 0, '合格标准：  漓泉1998（12瓶）≥74.99元  |  燕京U8（12瓶）≥60元', _dataStyle);
  sheet.merge(CellIndex.indexByColumnRow(columnIndex: 0, rowIndex: 1), CellIndex.indexByColumnRow(columnIndex: 13, rowIndex: 1));
  _setCell(sheet, 2, 0, '理论成交价总部定义：产品理论成交价格=产品成交价格-打包、配送费', _dataStyle);
  sheet.merge(CellIndex.indexByColumnRow(columnIndex: 0, rowIndex: 2), CellIndex.indexByColumnRow(columnIndex: 13, rowIndex: 2));

  final headers = ['省份', '产品名称', '规格', '合格线(元)', '总数',
    '合格数（$primaryLabel以上）', '不合格数', '合格率（$primaryLabel以上售价）',
    '${secondaryLabel}元以上价格', '${secondaryLabel}元以下价格',
    '合格率（${secondaryLabel}元以上售价）',
    '最低理论成交价', '最高理论成交价', '平均理论成交价'];
  for (var c = 0; c < headers.length; c++) _setCell(sheet, 3, c, headers[c], _headerStyle);
  sheet.setRowHeight(3, 30);

  // 数据行 row=4 (0-based), Excel 5 行
  const data = ['广东', '漓泉1998', '500ml*12瓶', 74.99, 5, 4, 1, '=F5/E5', 4, 1, '=I5/E5', 45.0, 75.0, 60.0];
  for (var c = 0; c < data.length; c++) _setCell(sheet, 4, c, data[c], _dataStyle);
  sheet.setRowHeight(4, 22);
  // 总计 row=5
  const total = ['总计', '', '', '', 5, 4, 1, '=F6/E6', 4, 1, '=I6/E6', '', '', ''];
  for (var c = 0; c < total.length; c++) _setCell(sheet, 5, c, total[c], _dataStyle);
  sheet.setRowHeight(5, 22);
  // 城市区块
  _setCell(sheet, 7, 0, '二、分地级市价格合格率汇总表', _titleStyle);
  sheet.merge(CellIndex.indexByColumnRow(columnIndex: 0, rowIndex: 7), CellIndex.indexByColumnRow(columnIndex: 13, rowIndex: 7));
  final cityHeaders = ['省份', '地级市', '产品名称', '规格', '合格线(元)', '总数',
    '合格数（$primaryLabel以上）', '不合格数', '合格率（$primaryLabel以上售价）',
    '${secondaryLabel}元以上价格', '${secondaryLabel}元以下价格',
    '合格率（${secondaryLabel}元以上售价）',
    '最低理论成交价', '最高理论成交价', '平均理论成交价'];
  for (var c = 0; c < cityHeaders.length; c++) _setCell(sheet, 8, c, cityHeaders[c], _headerStyle);
  const cityData = ['广东', '广州市', '漓泉1998', '500ml*12瓶', 74.99, 3, 3, 0, '=F10/E10', 3, 0, '=I10/E10', 50.0, 75.0, 61.0];
  for (var c = 0; c < cityData.length; c++) _setCell(sheet, 9, c, cityData[c], _dataStyle);

  File('/tmp/summary_check.xlsx').writeAsBytesSync(excel.encode()!);
  print('written');
}
