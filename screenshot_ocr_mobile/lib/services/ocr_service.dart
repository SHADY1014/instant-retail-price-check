/// OCR 服务：Google ML Kit 文字识别 + 坐标转换
/// 移植自桌面版 ocr_engine.py，保持输出格式与 macOS Vision 兼容

import 'dart:io';
import 'dart:ui' as ui;
import 'package:google_mlkit_text_recognition/google_mlkit_text_recognition.dart';
import '../models/ocr_result.dart';

class OcrService {
  OcrService._();

  static final _recognizer = TextRecognizer(
    script: TextRecognitionScript.chinese,
  );

  /// 对单张图片执行 OCR 识别
  ///
  /// 返回归一化坐标(0~1)的 OCR 结果列表，top 越大越靠上（与 Vision 一致）
  static Future<List<OcrResult>> runOcr(String imagePath) async {
    if (!File(imagePath).existsSync()) {
      throw FileSystemException('图片不存在', imagePath);
    }

    // 获取图片尺寸（dart:ui 内置解码，无需第三方包）
    final bytes = File(imagePath).readAsBytesSync();
    final codec = await ui.instantiateImageCodec(bytes);
    final frame = await codec.getNextFrame();
    final imgWidth = frame.image.width.toDouble();
    final imgHeight = frame.image.height.toDouble();

    // ML Kit OCR
    final inputImage = InputImage.fromFilePath(imagePath);
    final RecognizedText recognized =
        await _recognizer.processImage(inputImage);

    // 转换结果：像素坐标 -> 归一化坐标 + Y轴翻转
    final results = <OcrResult>[];
    for (final block in recognized.blocks) {
      for (final line in block.lines) {
        // ML Kit 的 Rect: left/top/right/bottom 像素坐标，Y轴从上往下
        final rect = line.boundingBox;
        final xMin = rect.left.toDouble();
        final xMax = rect.right.toDouble();
        final yMin = rect.top.toDouble();
        final yMax = rect.bottom.toDouble();

        // 转为归一化坐标
        final left = xMin / imgWidth;
        final width = (xMax - xMin) / imgWidth;
        // Y轴翻转：Vision 的 top 越大越靠上，用文本框底边翻转
        final top = 1.0 - (yMax / imgHeight);
        final height = (yMax - yMin) / imgHeight;

        results.add(OcrResult(
          text: line.text,
          confidence: 1.0, // ML Kit 不提供置信度，固定为 1.0
          left: left,
          top: top,
          width: width,
          height: height,
        ));
      }
    }

    // 按 top 降序（与 Vision 一致，top 越大越靠上）
    results.sort((a, b) => b.top.compareTo(a.top));
    return results;
  }

  /// 批量 OCR 识别
  static Future<Map<String, List<OcrResult>>> runOcrBatch(
    List<String> imagePaths, {
    void Function(int current, int total, String? path)? progressCallback,
  }) async {
    final results = <String, List<OcrResult>>{};
    final total = imagePaths.length;

    for (var i = 0; i < imagePaths.length; i++) {
      if (progressCallback != null) {
        progressCallback(i, total, imagePaths[i]);
      }
      try {
        final ocrData = await runOcr(imagePaths[i]);
        results[imagePaths[i]] = ocrData;
      } catch (e) {
        results[imagePaths[i]] = [];
      }
    }

    if (progressCallback != null) {
      progressCallback(total, total, null);
    }
    return results;
  }

  /// 释放资源
  static void dispose() {
    _recognizer.close();
  }
}
