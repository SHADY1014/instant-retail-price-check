#!/bin/bash
# 启动美团截图 OCR 自动填表系统
cd "$(dirname "$0")"

# 使用系统自带 Python 3.9（PyQt5 等依赖装在这里）
# 不依赖 PATH 解析，避免被 brew python3.12 覆盖
PYTHON="/usr/bin/python3"

# 启动程序；如果异常退出，暂停让用户看到错误
"$PYTHON" main.py
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "❌ 程序异常退出 (错误码: $EXIT_CODE)"
    echo "按回车键关闭窗口..."
    read
fi
