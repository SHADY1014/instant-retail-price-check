"""
验证 RapidOCR 引擎可用性

RapidOCR (rapidocr_onnxruntime) 安装时自带 PP-OCRv4 中文模型（约15MB），
无需单独下载模型。本脚本仅验证引擎能否正常初始化。

用法: python download_models.py
"""

import os
import sys

# Windows 命令行中文输出编码修复
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

print("=" * 50)
print("  验证 OCR 引擎")
print("=" * 50)

try:
    from rapidocr_onnxruntime import RapidOCR

    print("  RapidOCR 已安装，正在初始化（首次约10秒）...")
    ocr = RapidOCR()
    print()
    print("  ✅ OCR 引擎正常，程序可直接使用。")
    sys.exit(0)

except ImportError as e:
    print()
    print(f"  ❌ RapidOCR 未安装: {e}")
    print("     请先运行 install.bat 安装依赖。")
    sys.exit(1)
except Exception as e:
    print()
    print(f"  ❌ OCR 引擎初始化失败: {e}")
    print("     请重新运行 install.bat。")
    sys.exit(1)
