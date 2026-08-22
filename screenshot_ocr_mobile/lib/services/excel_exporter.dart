/// Excel 导出服务
/// 用 excel Dart 包生成巡查表 .xlsx
/// 样式与桌面版模板一致：深蓝大标题(合并A:P)、亮蓝表头、居中边框、M/N列公式
/// 图片：标准 OOXML 浮动图片嵌入 O 列（WPS/Excel 通用），同时单独保存原图

import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'dart:ui' as ui;
import 'package:archive/archive.dart';
import 'package:excel/excel.dart';
import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import '../models/form_fields.dart';
import '../utils/constants.dart';
import 'product_normalizer.dart';
import 'summary_generator.dart';

class ExcelExporter {
  ExcelExporter._();

  static const _sheetName = '1.燕京即时零售渠道价格巡查表';

  /// 表头（与桌面版模板一致，含换行的完整列名）
  static const _headers = [
    '分公司',
    '所属主要区域',
    '区域内即时零售平台',
    '店铺名称',
    '平台在售燕京产品',
    '产品原价\n（商品标价）',
    '产品成交单价\n（最终付款价格）',
    '商品优惠/商品活动\n（店铺活动）',
    '满减活动\n（店铺活动）',
    '优惠卷\n（平台下发）',
    '红包\n（平台下发）',
    '打包、配送费',
    '产品理论成交价格\n（产品成交价格-打包、配送费）',
    '去除平台优惠价格\n（最终付款价格-打包、配送费+优惠卷+红包）',
    '图片',
    '备注',
  ];

  /// 列宽（与桌面版模板一致）
  static const _colWidths = [
    35.1, 25.3, 21.0, 24.6, 21.2, 22.7, 19.0, 19.0,
    13.0, 13.0, 13.0, 13.0, 13.0, 23.1, 18.6, 19.0,
  ];

  // =========================================================
  // 样式（与桌面版模板一致）
  // =========================================================
  /// 大标题：深蓝底白字 14号加粗
  static final _titleStyle = CellStyle(
    backgroundColorHex: ExcelColor.fromHexString('FF003366'),
    fontColorHex: ExcelColor.white,
    fontFamily: '微软雅黑',
    fontSize: 14,
    bold: true,
    horizontalAlign: HorizontalAlign.Center,
    verticalAlign: VerticalAlign.Center,
  );

  /// 表头：亮蓝底白字 11号加粗 换行 边框
  static final _headerStyle = CellStyle(
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

  /// 数据：微软雅黑 10号 居中 边框
  static final _dataStyle = CellStyle(
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

  /// excel 4.x 需要显式 CellValue 包装，统一转换（数值保留2位避免浮点噪声）
  /// 注意：公式字符串不带 "=" 前缀（excel 包序列化后 Excel 会自动加 "="）
  static CellValue? _cv(Object? v) {
    if (v == null) return null;
    if (v is CellValue) return v;
    if (v is String) {
      // "=..." 开头作为公式（去掉 "=" 前缀，避免双重等号）
      if (v.startsWith('=')) return FormulaCellValue(v.substring(1));
      return TextCellValue(v);
    }
    if (v is int) return IntCellValue(v);
    if (v is double) {
      return DoubleCellValue(double.parse(v.toStringAsFixed(2)));
    }
    if (v is bool) return BoolCellValue(v);
    return TextCellValue(v.toString());
  }

  static List<CellValue?> _row(List<Object?> cells) =>
      cells.map(_cv).toList();

  /// 写入单元格并应用样式
  static void _setCell(Sheet sheet, int row, int col, Object? value,
      [CellStyle? style]) {
    final cell = sheet.cell(
      CellIndex.indexByColumnRow(columnIndex: col, rowIndex: row),
    );
    cell.value = _cv(value) ?? TextCellValue('');
    if (style != null) cell.cellStyle = style;
  }

  /// 删除 excel 包默认创建的 Sheet1（保留目标表）
  /// 注意：excel 包的 delete 要求表数量 >= 2，必须先创建目标表再删默认表
  static void _removeDefaultSheet(Excel excel, String keepSheet) {
    excel[keepSheet]; // 确保存在目标表
    if (excel.tables.containsKey('Sheet1')) {
      excel.delete('Sheet1');
    }
  }

  /// 导出巡查表
  /// 返回 (excel路径, 图片保存目录)
  static Future<(String, String)> exportInspectionSheet(
    List<FormFields> fields,
  ) async {
    final docsDir = await getApplicationDocumentsDirectory();
    final outputDir = Directory(p.join(docsDir.path, 'LQPriceCheck', 'output'));
    if (!outputDir.existsSync()) {
      outputDir.createSync(recursive: true);
    }

    final timestamp = _timestamp();
    final sessionDir = Directory(p.join(outputDir.path, '巡查表_$timestamp'));
    if (!sessionDir.existsSync()) {
      sessionDir.createSync(recursive: true);
    }

    final outputPath = p.join(sessionDir.path, '价格巡查表_$timestamp.xlsx');

    // 创建 Excel
    final excel = Excel.createExcel();
    _removeDefaultSheet(excel, _sheetName);
    final sheet = excel[_sheetName];

    // 列宽（与桌面模板一致，columnIndex 为 0-based）
    for (var c = 0; c < _colWidths.length; c++) {
      sheet.setColumnWidth(c, _colWidths[c]);
    }

    // 第1行: 大标题（合并 A1:P1）
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

    // 数据行（第3行起）
    for (var i = 0; i < fields.length; i++) {
      final f = fields[i];
      final excelRow = i + 3; // Excel 行号（1-based）
      final values = <Object?>[
        f.branchCompany,
        f.region,
        f.platform,
        f.shopName,
        f.productName,
        f.originalPrice,
        f.finalPrice,
        f.shopDiscount,
        f.fullReduction,
        f.coupon,
        f.redPacket,
        f.deliveryFee,
        // M列: 理论成交价 = G - L（公式，与桌面版一致）
        '=G$excelRow-L$excelRow',
        // N列: 去除平台优惠价 = G + J + K - L（公式）
        '=G$excelRow+J$excelRow+K$excelRow-L$excelRow',
        // O列: 图片嵌入（浮动图片定位到该单元格，不留文件名）
        '',
        f.remark,
      ];
      for (var c = 0; c < values.length; c++) {
        _setCell(sheet, i + 2, c, values[c], _dataStyle);
      }
      sheet.setRowHeight(i + 2, 25); // rowIndex 为 0-based
    }

    // 保存图片（单独保存原图）
    final imgDir = Directory(p.join(sessionDir.path, 'images'));
    if (!imgDir.existsSync()) {
      imgDir.createSync(recursive: true);
    }
    for (final f in fields) {
      if (f.imagePath != null && File(f.imagePath!).existsSync()) {
        try {
          File(f.imagePath!).copySync(p.join(imgDir.path, p.basename(f.imagePath!)));
        } catch (_) {}
      }
    }

    final rawBytes = excel.encode();
    if (rawBytes == null) {
      throw Exception('Excel 编码失败');
    }

    // 嵌入图片：
    // 1) dart:ui 原生解码生成缩略图（异步、并发2、targetWidth 下采样）
    // 2) zip 注入放入 Isolate，不阻塞 UI 线程
    final hasImages = fields.any(
        (f) => f.imagePath != null && File(f.imagePath!).existsSync());
    List<int> finalBytes = rawBytes;
    if (hasImages) {
      await _prepareThumbnails(fields);
      final thumbs = Map<String, ({Uint8List bytes, int width, int height, String ext})>.from(_thumbCache);
      finalBytes = await compute(_embedImagesIsolate, (
        xlsxBytes: rawBytes,
        thumbs: thumbs,
        entries: [
          for (var i = 0; i < fields.length; i++)
            if (fields[i].imagePath != null &&
                thumbs.containsKey(fields[i].imagePath))
              (row: i + 2, path: fields[i].imagePath!),
        ],
      ));
    }
    File(outputPath).writeAsBytesSync(finalBytes);

    return (outputPath, imgDir.path);
  }

  /// Isolate 入口：zip 注入图片（同步计算，但在独立 isolate 执行不卡 UI）
  static List<int> _embedImagesIsolate(({
    List<int> xlsxBytes,
    Map<String, ({Uint8List bytes, int width, int height, String ext})> thumbs,
    List<({int row, String path})> entries,
  }) params) {
    return _embedImagesWithThumbs(
        params.xlsxBytes, params.entries, params.thumbs);
  }

  // =========================================================
  // 图片嵌入：向 xlsx (zip) 注入标准 OOXML 浮动图片
  // 1. xl/media/imageN.jpg     缩略图字节
  // 2. xl/drawings/drawing1.xml  oneCellAnchor 定位 O 列对应行
  // 3. xl/drawings/_rels/drawing1.xml.rels
  // 4. sheet1.xml 插入 <drawing r:id="rId1"/> 引用
  // 5. [Content_Types].xml 注册 jpg 类型
  // =========================================================
  /// zip 注入图片（同步计算；在 Isolate 中执行以避免卡 UI）
  static List<int> _embedImagesWithThumbs(
    List<int> xlsxBytes,
    List<({int row, String path})> entries,
    Map<String, ({Uint8List bytes, int width, int height, String ext})> thumbs,
  ) {
    final archive = ZipDecoder().decodeBytes(xlsxBytes);
    if (entries.isEmpty) return xlsxBytes;

    // 收集 (数据行, 媒体名, 宽高)
    final items = <({int row, String mediaName, int width, int height, String path})>[];
    for (var i = 0; i < entries.length; i++) {
      final e = entries[i];
      final thumb = thumbs[e.path];
      if (thumb != null) {
        items.add((
          row: e.row,
          path: e.path,
          mediaName: 'image${i + 1}.${thumb.ext}',
          width: thumb.width,
          height: thumb.height,
        ));
      }
    }
    if (items.isEmpty) return xlsxBytes;

    // 待替换/新增的文件映射（name -> bytes）
    final newFiles = <String, List<int>>{};

    // 生成 drawing + rels + media
    final drawings = StringBuffer()
      ..write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
      ..write('<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
          'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">');
    final rels = StringBuffer()
      ..write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
      ..write('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">');

    for (var i = 0; i < items.length; i++) {
      final item = items[i];
      final rid = 'rId${i + 1}';
      final thumb = thumbs[item.path]!;
      newFiles['xl/media/${item.mediaName}'] = thumb.bytes;

      // 显示尺寸按图片实际纵横比计算：宽固定 85px，高按比例（EMU: 1px=9525）
      const dispW = 85.0;
      final dispH = dispW * item.height / item.width;
      final extCx = (dispW * 9525).round();
      final extCy = (dispH * 9525).round();
      drawings.write('''
<xdr:oneCellAnchor>
  <xdr:from><xdr:col>14</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>${item.row}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
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
          'Target="../media/${item.mediaName}"/>');
    }
    drawings.write('</xdr:wsDr>');
    rels.write('</Relationships>');

    // 替换 drawing1.xml（excel 包已生成空 drawing）+ 新增 rels
    newFiles['xl/drawings/drawing1.xml'] = utf8.encode(drawings.toString());
    newFiles['xl/drawings/_rels/drawing1.xml.rels'] = utf8.encode(rels.toString());

    // sheet1.xml 插入 drawing 引用（excel 包只写 rels 未写引用）
    final sheetFile = archive.findFile('xl/worksheets/sheet1.xml');
    if (sheetFile != null) {
      var sheetXml = utf8.decode(sheetFile.content as List<int>);
      if (!sheetXml.contains('<drawing')) {
        sheetXml = sheetXml.replaceFirst(
            '</sheetData>', '</sheetData><drawing r:id="rId1"/>');
        newFiles['xl/worksheets/sheet1.xml'] = utf8.encode(sheetXml);
      }
    }

    // [Content_Types].xml 注册 jpg/png
    final ctFile = archive.findFile('[Content_Types].xml');
    if (ctFile != null) {
      var ct = utf8.decode(ctFile.content as List<int>);
      final needsJpg = items.any((e) => e.mediaName.endsWith('.jpg'));
      final needsPng = items.any((e) => e.mediaName.endsWith('.png'));
      final additions = StringBuffer();
      if (needsJpg && !ct.contains('Extension="jpg"')) {
        additions.write('<Default ContentType="image/jpeg" Extension="jpg"/>');
      }
      if (needsPng && !ct.contains('Extension="png"')) {
        additions.write('<Default ContentType="image/png" Extension="png"/>');
      }
      if (additions.isNotEmpty) {
        ct = ct.replaceFirst('</Types>', '$additions</Types>');
        newFiles['[Content_Types].xml'] = utf8.encode(ct);
      }
    }

    // 重建 archive（archive 包 removeFile 有索引错位 bug，用重建方式替换文件）
    final newArchive = Archive();
    for (final f in archive.files) {
      final replacement = newFiles.remove(f.name);
      if (replacement != null) {
        newArchive.addFile(
            ArchiveFile(f.name, replacement.length, replacement));
      } else {
        newArchive.addFile(f);
      }
    }
    for (final entry in newFiles.entries) {
      newArchive.addFile(
          ArchiveFile(entry.key, entry.value.length, entry.value));
    }

    return ZipEncoder().encode(newArchive)!;
  }

  // =========================================================
  // 缩略图生成（性能关键路径）
  // 用 dart:ui 原生解码器（Skia/Impeller，C++ 实现）+ targetWidth 直接下采样：
  // 3000px 原图不会整张解码到内存，速度约为纯 Dart image 包的 10 倍以上，
  // 且全程异步不阻塞 UI 线程，避免导出卡死/ANR 闪退
  // =========================================================
  static final _thumbCache = <String, ({Uint8List bytes, int width, int height, String ext})>{};

  /// 批量生成缩略图（并发 2，控制内存峰值）
  /// 返回成功生成的数量
  static Future<int> _prepareThumbnails(List<FormFields> fields) async {
    final paths = fields
        .map((f) => f.imagePath)
        .whereType<String>()
        .where((path) => File(path).existsSync() && !_thumbCache.containsKey(path))
        .toSet()
        .toList();
    if (paths.isEmpty) return 0;

    const concurrency = 2; // 并发解码数（内存友好）
    var next = 0;
    var done = 0;
    Future<void> worker() async {
      while (true) {
        final i = next++;
        if (i >= paths.length) break;
        try {
          final t = await _decodeThumbnail(paths[i]);
          if (t != null) {
            _thumbCache[paths[i]] = t;
            done++;
          }
        } catch (_) {}
      }
    }

    await Future.wait([
      for (var k = 0; k < concurrency && k < paths.length; k++) worker(),
    ]);
    return done;
  }

  /// 单张缩略图：dart:ui 解码（targetWidth=320 直接下采样）-> PNG 编码
  /// dart:ui 只支持 PNG 编码，体积比 JPEG 大（~150KB/张），但解码快且不阻塞
  static Future<({Uint8List bytes, int width, int height, String ext})?>
      _decodeThumbnail(String path) async {
    final data = File(path).readAsBytesSync();
    final codec = await ui.instantiateImageCodec(
      data,
      targetWidth: 320, // 解码时直接缩放，避免 3000px 原图占内存
    );
    final frame = await codec.getNextFrame();
    final image = frame.image;
    final width = image.width;
    final height = image.height;

    // 读回字节（PNG）
    final byteData = await image.toByteData(format: ui.ImageByteFormat.png);
    image.dispose();
    codec.dispose();
    if (byteData == null) return null;
    return (
      bytes: byteData.buffer.asUint8List(),
      width: width,
      height: height,
      ext: 'png',
    );
  }

  /// 导出汇总表
  /// 返回 xlsx 路径
  static Future<String> exportSummarySheet(
    SummaryData summary, {
    String? outputDir,
  }) async {
    final dir = outputDir ?? await _defaultOutputDir();
    final timestamp = _timestamp();
    final outputPath = p.join(dir, '价格合格率汇总表_$timestamp.xlsx');

    final excel = Excel.createExcel();
    _removeDefaultSheet(excel, '汇总表');
    final sheet = excel['汇总表'];

    // 列宽（与桌面版 _build_province_summary 一致，0-based 索引）
    const widths = [8.0, 14.0, 14.0, 10.0, 8.0, 8.0, 10.0, 10.0, 14.0, 14.0, 16.0, 12.0, 12.0, 12.0];
    for (var c = 0; c < widths.length; c++) {
      sheet.setColumnWidth(c, widths[c]);
    }

    // 主合格线/第二档标签（与桌面版一致）
    final primaryLabel =
        summary.primaryLine == summary.primaryLine.roundToDouble()
            ? '${summary.primaryLine.toInt()}元'
            : '${summary.primaryLine}元';
    final secondaryLabel = summary.secondaryLine ==
            summary.secondaryLine.roundToDouble()
        ? '${summary.secondaryLine.toInt()}'
        : '${summary.secondaryLine}';

    // ===== 一、分省价格合格率汇总表 =====
    _setCell(sheet, 0, 0, '一、分省价格合格率汇总表', _titleStyle);
    sheet.merge(
      CellIndex.indexByColumnRow(columnIndex: 0, rowIndex: 0),
      CellIndex.indexByColumnRow(columnIndex: 13, rowIndex: 0),
    );
    sheet.setRowHeight(0, 30);

    // 合格标准行（只显示实际出现的产品规格）
    final seenNameSpec = <String>[];
    for (final r in summary.provinceRows) {
      final key = '${r.productName}\u0000${r.spec}\u0000${r.qualLine}';
      if (!seenNameSpec.contains(key)) seenNameSpec.add(key);
    }
    final stdParts = seenNameSpec.map((key) {
      final parts = key.split('\u0000');
      final nm = parts[0];
      final sp = parts[1].replaceAll('500ml*', '');
      final ln = double.parse(parts[2]);
      final lnStr = ln == ln.roundToDouble() ? '${ln.toInt()}' : '$ln';
      return '$nm（$sp）≥$lnStr元';
    }).toList();
    _setCell(
        sheet, 1, 0, '合格标准：  ${stdParts.join('  |  ')}', _subtitleStyle());
    sheet.merge(
      CellIndex.indexByColumnRow(columnIndex: 0, rowIndex: 1),
      CellIndex.indexByColumnRow(columnIndex: 13, rowIndex: 1),
    );
    _setCell(
      sheet,
      2,
      0,
      '理论成交价总部定义：产品理论成交价格=产品成交价格-打包、配送费',
      _subtitleStyle(),
    );
    sheet.merge(
      CellIndex.indexByColumnRow(columnIndex: 0, rowIndex: 2),
      CellIndex.indexByColumnRow(columnIndex: 13, rowIndex: 2),
    );
    sheet.setRowHeight(1, 20);
    sheet.setRowHeight(2, 20);

    // 表头
    const provHeader = [
      '省份', '产品名称', '规格', '合格线(元)', '总数',
    ];
    final provHeaderFull = [
      ...provHeader,
      '合格数（$primaryLabel以上）', '不合格数',
      '合格率（$primaryLabel以上售价）',
      '$secondaryLabel元以上价格', '$secondaryLabel元以下价格',
      '合格率（$secondaryLabel元以上售价）',
      '最低理论成交价', '最高理论成交价', '平均理论成交价',
    ];
    for (var c = 0; c < provHeaderFull.length; c++) {
      _setCell(sheet, 3, c, provHeaderFull[c], _headerStyle);
    }
    sheet.setRowHeight(3, 30);

    // 数据行（第5行起）
    var row = 3;
    for (final r in summary.provinceRows) {
      row++;
      final values = <Object?>[
        r.province,
        r.productName,
        r.spec,
        r.qualLine,
        r.count,
        r.passed,
        r.failed,
        // 合格率公式 =F/E（与桌面版一致）
        '=F${row + 1}/E${row + 1}',
        r.aboveCount,
        r.belowCount,
        // 第二档合格率公式 =I/E
        '=I${row + 1}/E${row + 1}',
        r.minPrice,
        r.maxPrice,
        r.avgPrice.toStringAsFixed(1),
      ];
      for (var c = 0; c < values.length; c++) {
        _setCell(sheet, row, c, values[c], _dataStyle);
      }
      sheet.setRowHeight(row, 22);
    }

    // 总计行
    row++;
    final totalValues = <Object?>[
      '总计', '', '', '',
      summary.totalCount,
      summary.totalPass,
      summary.totalFail,
      '=F${row + 1}/E${row + 1}',
      summary.totalAbove,
      summary.totalBelow,
      '=I${row + 1}/E${row + 1}',
      '', '', '',
    ];
    for (var c = 0; c < totalValues.length; c++) {
      _setCell(sheet, row, c, totalValues[c], _totalStyle());
    }
    sheet.setRowHeight(row, 22);

    // ===== 二、分地级市价格合格率汇总表 =====
    final cityStart = row + 2; // 留1行空隙
    _setCell(sheet, cityStart, 0, '二、分地级市价格合格率汇总表', _titleStyle);
    sheet.merge(
      CellIndex.indexByColumnRow(columnIndex: 0, rowIndex: cityStart),
      CellIndex.indexByColumnRow(columnIndex: 13, rowIndex: cityStart),
    );
    sheet.setRowHeight(cityStart, 30);

    final cityHeader = [
      '省份', '地级市', '产品名称', '规格', '合格线(元)', '总数',
      '合格数（$primaryLabel以上）', '不合格数',
      '合格率（$primaryLabel以上售价）',
      '$secondaryLabel元以上价格', '$secondaryLabel元以下价格',
      '合格率（$secondaryLabel元以上售价）',
      '最低理论成交价', '最高理论成交价', '平均理论成交价',
    ];
    for (var c = 0; c < cityHeader.length; c++) {
      _setCell(sheet, cityStart + 1, c, cityHeader[c], _headerStyle);
    }
    sheet.setRowHeight(cityStart + 1, 30);

    var cityRow = cityStart + 1;
    for (final r in summary.cityRows) {
      cityRow++;
      final values = <Object?>[
        r.province,
        r.region,
        r.productName,
        r.spec,
        r.qualLine,
        r.count,
        r.passed,
        r.failed,
        '=F${cityRow + 1}/E${cityRow + 1}',
        r.aboveCount,
        r.belowCount,
        '=I${cityRow + 1}/E${cityRow + 1}',
        r.minPrice,
        r.maxPrice,
        r.avgPrice.toStringAsFixed(1),
      ];
      for (var c = 0; c < values.length; c++) {
        _setCell(sheet, cityRow, c, values[c], _dataStyle);
      }
      sheet.setRowHeight(cityRow, 22);
    }

    final bytes = excel.encode();
    if (bytes == null) {
      throw Exception('Excel 编码失败');
    }
    File(outputPath).writeAsBytesSync(bytes);
    return outputPath;
  }

  /// 汇总表副标题行样式（灰色 10 号 左对齐）
  static CellStyle _subtitleStyle() {
    return CellStyle(
      fontFamily: '微软雅黑',
      fontSize: 10,
      horizontalAlign: HorizontalAlign.Left,
      verticalAlign: VerticalAlign.Center,
    );
  }

  /// 汇总表总计行样式（加粗 浅蓝底）
  static CellStyle _totalStyle() {
    return CellStyle(
      fontFamily: '微软雅黑',
      fontSize: 10,
      bold: true,
      backgroundColorHex: ExcelColor.fromHexString('FFDDEBF7'),
      horizontalAlign: HorizontalAlign.Center,
      verticalAlign: VerticalAlign.Center,
      leftBorder: Border(borderStyle: BorderStyle.Thin),
      rightBorder: Border(borderStyle: BorderStyle.Thin),
      topBorder: Border(borderStyle: BorderStyle.Thin),
      bottomBorder: Border(borderStyle: BorderStyle.Thin),
    );
  }

  static Future<String> _defaultOutputDir() async {
    final docsDir = await getApplicationDocumentsDirectory();
    final dir = Directory(p.join(docsDir.path, 'LQPriceCheck', 'output'));
    if (!dir.existsSync()) {
      dir.createSync(recursive: true);
    }
    return dir.path;
  }

  static String _timestamp() {
    final now = DateTime.now();
    String two(int v) => v.toString().padLeft(2, '0');
    return '${now.year}${two(now.month)}${two(now.day)}_'
        '${two(now.hour)}${two(now.minute)}${two(now.second)}';
  }
}
