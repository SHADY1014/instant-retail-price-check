# 即时零售截图价格核查

面向内部价格巡查的跨平台工具：将美团、淘宝和京东闪送/秒送结算截图识别为结构化记录，支持人工核对、城市确认、查重和 Excel 巡查表导出。

## 版本组成

- `screenshot_ocr/`：macOS 桌面版，使用 Vision OCR，并提供汇总表与总结话术。
- `screenshot_ocr_windows/`：Windows EXE 源码，使用 RapidOCR/ONNX Runtime；发布版无需安装 Python，但需要 VC++ 运行库。
- `screenshot_ocr_mobile/`：Flutter Android 版，保留截图识别、人工核对、查重和巡查表导出，不包含桌面端汇总与话术。

三端共用字段解析和价格规则；macOS/Windows 桌面端使用保守的城市证据评分，Android 当前仍采用简化投票策略，发布前需补齐候选展示与人工裁决。OCR 和字段解析默认离线；桌面端联网城市识别仅在用户主动授权并选择城市白名单后发送店铺名称，候选、人工裁决和请求时间保存在本地审计库。

## 快速开始（macOS）

```bash
cd screenshot_ocr
pip3 install -r requirements.txt
python3 main.py
```

需要 macOS 13+、Python 3.9+ 和 Xcode Command Line Tools。Windows 打包说明见 [`screenshot_ocr_windows/README.md`](screenshot_ocr_windows/README.md)，Android 构建说明见 [`screenshot_ocr_mobile/README.md`](screenshot_ocr_mobile/README.md)。

## 测试

```bash
python3 -m unittest discover -s screenshot_ocr/tests -p 'test*.py'
python3 -m unittest discover -s screenshot_ocr_windows/tests -p 'test*.py'
cd screenshot_ocr_mobile && flutter test
```

项目仍以 macOS 为基准开发；Windows 虚拟机和 Android 真机发布前应使用带人工真值的截图再做准确率统计。
