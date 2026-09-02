# 即时零售截图价格核查（移动端 Flutter）

Flutter 原生移动应用，提供 4 个核心功能：

1. **OCR 识别**：导入截图 → Google ML Kit 文字识别 → 字段解析（支持美团、淘宝、京东闪送/秒送）。取消城市范围选择不会自动跨城匹配，可稍后手动重新识别城市。
2. **数据库读取城市**：本地 SQLite（预置 619 条店铺-城市映射）
3. **联网读取城市**：百度地图搜索（可选开启）
4. **导出 Excel**：生成保留 M/N 公式和图片的巡查表 .xlsx 并可分享
5. **核对与查重**：待核对筛选、失败图片重试、导出前提示、重复记录核查

## 目录结构

```
screenshot_ocr_mobile/
├── lib/
│   ├── main.dart                    ← 入口
│   ├── models/
│   │   ├── form_fields.dart         ← 表单字段模型（A~P列）
│   │   └── ocr_result.dart          ← OCR 结果模型
│   ├── services/
│   │   ├── ocr_service.dart         ← ML Kit OCR + 坐标转换
│   │   ├── field_parser.dart        ← OCR -> FormFields 解析
│   │   ├── product_normalizer.dart  ← 产品名标准化（7品牌+规格）
│   │   ├── city_detector.dart       ← 城市识别（L0库/L1店名/L3关键词/L2百度）
│   │   ├── shop_city_db.dart        ← SQLite 数据库
│   │   ├── excel_exporter.dart      ← Excel 导出
│   │   ├── review_rules.dart        ← 待核对规则
│   │   └── duplicate_checker.dart  ← 查重规则
│   ├── pages/
│   │   └── home_page.dart           ← 单页应用（导入/识别/列表/导出）
│   └── utils/
│       ├── constants.dart           ← 合格规则/城市池/坐标阈值
│       └── price_parser.dart        ← 价格提取
├── assets/
│   ├── shop_city.db                 ← 预置店铺-城市数据库（619条）
│   └── 模板.xlsx                    ← Excel 模板（备用）
├── android/app/src/main/AndroidManifest.xml
└── pubspec.yaml
```

## 开发环境

- Flutter SDK 3.x（Dart 3.x）
- Android Studio（Android SDK）

## 运行

```bash
flutter pub get
flutter run
```

## 打包 APK

```bash
flutter build apk --release
# 产物: build/app/outputs/flutter-apk/app-release.apk
```

## 功能说明

### 1. OCR 识别
- 相册多选截图（image_picker）
- Google ML Kit 中文文字识别（离线）
- 坐标转换：ML Kit 像素坐标 → 归一化(0~1) + Y轴翻转（与桌面版一致）
- 字段解析：移植桌面版 field_parser.py 逻辑（平台/店铺/产品/价格/优惠/配送费），支持京东“商品金额/应付总额/运费”结算结构

### 2. 数据库读取城市
- 首次启动从 assets 复制 shop_city.db（619 条店铺-城市映射）到应用文档目录
- L0 直接查询本地 SQLite，零网络请求
- 人工确认的店铺-城市关系自动写回数据库（积累）
- 已选择限定城市时，本地库与联网结果都会严格限制在所选范围内

### 3. 联网读取城市（可选）
- 界面开关"联网识别"开启后，未命中数据库/关键词的店铺走百度地图搜索
- 与桌面版相同的接口和解析逻辑（map.baidu.com/su）
- 每次识别前可选限定省份+城市（更精准）

### 4. 导出 Excel
- 生成巡查表 .xlsx（A~L 列数据、M/N Excel 公式、备注）
- 使用标准 OOXML 图片嵌入 O 列，并在导出目录保留原图副本
- 保存到 `Documents/LQPriceCheck/output/`，可一键分享

## 与桌面版差异

| 功能 | 桌面版 | 移动版 |
|------|--------|--------|
| OCR | RapidOCR (onnxruntime) | Google ML Kit |
| Excel 图片 | WPS DISPIMG 嵌入 | 标准 OOXML 嵌入 + 原图副本 |
| 汇总表/话术 | 有 | 不接入移动端，保持轻量巡查流程 |
| 查重核查 | 有 | 有（按店铺+平台+城市+理论成交价） |
| OCR 失败重试 | 有 | 有（可取消、失败项单独重试） |
| 待核对筛选/导出提示 | 有 | 有 |

## 权限说明

- INTERNET：联网识别城市
- READ_MEDIA_IMAGES / READ_EXTERNAL_STORAGE：读取相册
- WRITE_EXTERNAL_STORAGE：导出文件保存

Design By 创新业务中心-江凯豪
