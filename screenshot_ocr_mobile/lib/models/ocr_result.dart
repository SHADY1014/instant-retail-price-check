/// OCR 识别结果的单条文本项
/// 坐标为归一化值(0~1)，top 越大越靠上（与 macOS Vision 一致）
class OcrResult {
  final String text;
  final double confidence;
  final double left;
  final double top;
  final double width;
  final double height;

  OcrResult({
    required this.text,
    required this.confidence,
    required this.left,
    required this.top,
    required this.width,
    required this.height,
  });

  factory OcrResult.fromMap(Map<String, dynamic> map) {
    return OcrResult(
      text: map['text'] as String? ?? '',
      confidence: (map['confidence'] as num?)?.toDouble() ?? 0.0,
      left: (map['left'] as num?)?.toDouble() ?? 0.0,
      top: (map['top'] as num?)?.toDouble() ?? 0.0,
      width: (map['width'] as num?)?.toDouble() ?? 0.0,
      height: (map['height'] as num?)?.toDouble() ?? 0.0,
    );
  }
}
