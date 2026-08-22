# ML Kit 文字识别插件（google_mlkit_text_recognition）
# R8 混淆会裁剪未直接引用的语种类（如韩文），导致运行时 ClassNotFoundException
-keep class com.google.mlkit.vision.text.** { *; }
-keep class com.google.mlkit.vision.common.** { *; }
-keep class com.google.mlkit.common.** { *; }
