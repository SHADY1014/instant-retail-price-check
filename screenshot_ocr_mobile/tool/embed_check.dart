/// 图片嵌入验证：生成巡查表并注入 2 张图片
import 'dart:convert';
import 'dart:io';
import 'package:archive/archive.dart';
import 'package:excel/excel.dart';
import 'package:image/image.dart' as img;

// ===== 简化复制自 excel_exporter.dart =====
final _titleStyle = CellStyle(
  backgroundColorHex: ExcelColor.fromHexString('FF003366'),
  fontColorHex: ExcelColor.white, fontFamily: '微软雅黑', fontSize: 14, bold: true,
  horizontalAlign: HorizontalAlign.Center, verticalAlign: VerticalAlign.Center,
);
final _dataStyle = CellStyle(
  fontFamily: '微软雅黑', fontSize: 10,
  horizontalAlign: HorizontalAlign.Center, verticalAlign: VerticalAlign.Center,
  leftBorder: Border(borderStyle: BorderStyle.Thin), rightBorder: Border(borderStyle: BorderStyle.Thin),
  topBorder: Border(borderStyle: BorderStyle.Thin), bottomBorder: Border(borderStyle: BorderStyle.Thin),
);

CellValue? _cv(Object? v) {
  if (v == null) return null;
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

List<int> _embedImages(List<int> xlsxBytes, List<String> imagePaths) {
  final archive = ZipDecoder().decodeBytes(xlsxBytes);
  final drawings = StringBuffer()
    ..write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    ..write('<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">');
  final rels = StringBuffer()
    ..write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    ..write('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">');

  for (var i = 0; i < imagePaths.length; i++) {
    final rid = 'rId${i + 1}';
    final mediaName = 'image${i + 1}.png';
    final bytes = File(imagePaths[i]).readAsBytesSync();
    final decoded = img.decodeImage(bytes);
    final pngBytes = decoded == null
        ? bytes
        : img.encodePng(img.copyResize(decoded, width: 320));
    archive.addFile(ArchiveFile('xl/media/image${i + 1}.png', pngBytes.length, pngBytes));

    const extCx = 810000;
    const extCy = 1458000;
    final fromRow = i + 2;
    drawings.write('''
<xdr:oneCellAnchor>
  <xdr:from><xdr:col>14</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>$fromRow</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
  <xdr:ext cx="$extCx" cy="$extCy"/>
  <xdr:pic>
    <xdr:nvPicPr>
      <xdr:cNvPr id="${i + 2}" name="图片${i + 1}"/>
      <xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr>
    </xdr:nvPicPr>
    <xdr:blipFill>
      <a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="$rid"/>
      <a:stretch><a:fillRect/></a:stretch>
    </xdr:blipFill>
    <xdr:spPr>
      <a:xfrm><a:off x="0" y="0"/><a:ext cx="$extCx" cy="$extCy"/></a:xfrm>
      <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
    </xdr:spPr>
  </xdr:pic>
  <xdr:clientData/>
</xdr:oneCellAnchor>''');
    rels.write('<Relationship Id="$rid" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        'Target="../media/$mediaName"/>');
  }
  drawings.write('</xdr:wsDr>');
  rels.write('</Relationships>');

  final newFiles = <String, List<int>>{};
  final drawingBytes = utf8.encode(drawings.toString());
  final relsBytes = utf8.encode(rels.toString());
  newFiles['xl/drawings/drawing1.xml'] = drawingBytes;
  newFiles['xl/drawings/_rels/drawing1.xml.rels'] = relsBytes;

  final sheetFile = archive.findFile('xl/worksheets/sheet1.xml');
  if (sheetFile != null) {
    var sheetXml = utf8.decode(sheetFile.content as List<int>);
    if (!sheetXml.contains('<drawing')) {
      sheetXml = sheetXml.replaceFirst('</sheetData>', '</sheetData><drawing r:id="rId1"/>');
      newFiles['xl/worksheets/sheet1.xml'] = utf8.encode(sheetXml);
    }
  }

  final ctFile = archive.findFile('[Content_Types].xml');
  if (ctFile != null) {
    var ct = utf8.decode(ctFile.content as List<int>);
    if (!ct.contains('Extension="png"')) {
      ct = ct.replaceFirst('</Types>', '<Default ContentType="image/png" Extension="png"/></Types>');
      newFiles['[Content_Types].xml'] = utf8.encode(ct);
    }
  }

  final newArchive = Archive();
  for (final f in archive.files) {
    final replacement = newFiles.remove(f.name);
    if (replacement != null) {
      newArchive.addFile(ArchiveFile(f.name, replacement.length, replacement));
    } else {
      newArchive.addFile(f);
    }
  }
  for (final entry in newFiles.entries) {
    newArchive.addFile(ArchiveFile(entry.key, entry.value.length, entry.value));
  }

  return ZipEncoder().encode(newArchive)!;
}

void main() {
  final excel = Excel.createExcel();
  excel['1.燕京即时零售渠道价格巡查表'];
  if (excel.tables.containsKey('Sheet1')) excel.delete('Sheet1');
  final sheet = excel['1.燕京即时零售渠道价格巡查表'];
  for (var c = 0; c < 16; c++) sheet.setColumnWidth(c, 15);

  _setCell(sheet, 0, 0, '燕京啤酒全国即时零售渠道产品价格巡查表', _titleStyle);
  sheet.merge(CellIndex.indexByColumnRow(columnIndex: 0, rowIndex: 0), CellIndex.indexByColumnRow(columnIndex: 15, rowIndex: 0));

  for (var i = 0; i < 2; i++) {
    final row = i + 2;
    _setCell(sheet, row, 0, '漓泉销售公司', _dataStyle);
    _setCell(sheet, row, 3, '测试超市(第${i + 1}店)', _dataStyle);
    _setCell(sheet, row, 4, '漓泉1998啤酒 500ml*12瓶', _dataStyle);
    _setCell(sheet, row, 6, 50.0, _dataStyle);
    _setCell(sheet, row, 11, 2.0, _dataStyle);
    _setCell(sheet, row, 12, '=G${row + 1}-L${row + 1}', _dataStyle);
    _setCell(sheet, row, 13, '=G${row + 1}+J${row + 1}+K${row + 1}-L${row + 1}', _dataStyle);
    _setCell(sheet, row, 14, '', _dataStyle);
    sheet.setRowHeight(row, 25);
  }

  final raw = excel.encode()!;
  final images = ['/tmp/meituan_shots/shot1.png', '/tmp/meituan_shots/shot2.png'];
  final out = _embedImages(raw, images);
  File('/tmp/embed_check.xlsx').writeAsBytesSync(out);
  print('written: /tmp/embed_check.xlsx (${out.length} bytes)');
}
