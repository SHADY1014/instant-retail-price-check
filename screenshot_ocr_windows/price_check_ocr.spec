# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置：价格核查OCR.exe（单文件、无控制台）

在 Windows 上运行 build_windows.bat 或：
    pyinstaller 价格核查OCR.spec

关键收集项：
  - 模板.xlsx、data/shop_city.db 作为资源内嵌
  - rapidocr_onnxruntime 包数据（含 PP-OCRv4 模型 .onnx）
  - onnxruntime 动态库（onnxruntime_pybind11_state.pyd）
  - PyQt5 由 PyInstaller 内置 hook 自动收集
"""

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

# --- 收集 RapidOCR / onnxruntime 的包数据与动态库 ---
rapidocr_datas, rapidocr_binaries, rapidocr_hidden = collect_all('rapidocr_onnxruntime')
ort_datas, ort_binaries, ort_hidden = collect_all('onnxruntime')
pillow_datas, pillow_binaries, pillow_hidden = collect_all('PIL')

hiddenimports = (
    rapidocr_hidden + ort_hidden + pillow_hidden +
    [
        'onnxruntime.capi.onnxruntime_pybind11_state',
        'rapidocr_onnxruntime',
    ]
)

datas = rapidocr_datas + ort_datas + pillow_datas + [
    # 内嵌模板与初始数据库（运行时由 runtime_check 定位/复制）
    ('模板.xlsx', '.'),
    ('data/shop_city.db', 'data'),
]

binaries = rapidocr_binaries + ort_binaries + pillow_binaries

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pytest',
        'tkinter',
        'pandas',
        'matplotlib',
        'scipy',
        'IPython',
        'jupyter',
        'PySide2',
        'PySide6',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PriceCheckOCR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 无控制台窗口（windowed）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # 如需图标: icon='app.ico'
)
