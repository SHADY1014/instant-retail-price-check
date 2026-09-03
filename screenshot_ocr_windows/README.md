# 即时零售截图价格核查（Windows 版）

基于 RapidOCR 的美团、淘宝和京东闪送/秒送结算截图自动识别与填表系统。
适用于 Windows 10 x64，使用 RapidOCR (onnxruntime + PP-OCRv4) 替代 macOS Vision Framework。
识别速度快（i3 CPU 约 1-2 秒/张），无需联网下载模型。联网城市识别仅在用户明确授权后发送店铺名称，弱/歧义候选需人工确认。

首次使用请先阅读：[操作手册](操作手册.md)

## 打包为单文件 EXE

### 使用方式（最终用户）

把 `dist\PriceCheckOCR.exe` 复制到任意 Windows 10/11 x64 机器，**双击即可运行**，无需安装 Python 或任何依赖。

- 首次启动较慢（单文件自解压机制，约 10-30 秒），属正常现象
- 运行数据保存在 `%LOCALAPPDATA%\LQPriceCheck\`（数据库、导出文件）
- 运行日志保存在 `%LOCALAPPDATA%\LQPriceCheck\logs\application.log`，遇到闪退或识别失败请一并提供该文件
- 导出的 Excel（巡查表/汇总表）默认保存到**桌面**
- 启动时自动检查：系统版本/架构、磁盘空间、VC++ 运行库、OCR 组件，失败时给出中文提示

### 打包方法（开发者，在 Windows 机器上执行）

```bash
1. 把本目录复制到 Windows 机器（含 `模板.xlsx`、`data/shop_city.db` 历史迁移源）
2. 双击 build_windows.bat
   - 自动安装 PyInstaller + 依赖
   - 自动执行 pyinstaller price_check_ocr.spec
3. 产物: dist\PriceCheckOCR.exe（单文件，约 80-120MB）
```

### 打包原理

- **内嵌资源**：`模板.xlsx`、`data/shop_city.db` 历史迁移源、RapidOCR 模型（PP-OCRv4，随 pip 包）、onnxruntime 动态库全部打入 EXE
- **运行时解压**：onefile 模式首次启动解压到 `%TEMP%`，由 `runtime_check.resource_path()` 定位资源
- **可写数据**：学习库保存在 `%LOCALAPPDATA%\LQPriceCheck\data\ocr_learning.db`；人工确认、人工投喂和联网识别审计均写入该库。旧 `shop_city.db` 只作一次性历史迁移参考，不会自动填入城市
- **启动自检**（`runtime_check.py`）：Windows 10/11 64位 → 临时目录可写且 ≥1GB → 内嵌资源存在 → VC++ 2015-2022 x64 → OCR 动态库可导入

### 注意事项

- **杀毒软件误报**：未签名的 PyInstaller EXE 可能被 Windows Defender/杀软拦截，可添加信任或白名单；分发时建议附带 SHA-256 校验值
- **首次启动慢**：单文件自解压机制导致，后续启动正常；如需更快可改用目录版（onefolder）构建
- **VC++ 运行库**：目标机器仍需已安装 VC++ 2015-2022 x64（大多数 Win10/11 已预装，缺失时程序会提示并提供下载链接）

## 快速开始

### 首次安装（仅需一次）

1. **把 Python 安装包放进本目录**（**必须**）
   - 下载地址：https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
   - 文件名必须以 `python-3.x` 开头、`-amd64.exe` 结尾，例如 `python-3.11.9-amd64.exe`
   - ⚠️ **必须用 3.9 - 3.12 版本**（3.13/3.14 没有预编译包，会导致 Visual C++ 编译报错）

2. **把 VC++ 运行库安装包放进本目录**（**必须**）
   - 下载地址：https://aka.ms/vs/17/release/vc_redist.x64.exe
   - 文件名必须为 `vc_redist.x64.exe`
   - 这是 onnxruntime 必需的运行时，**没有它 OCR 会报 DLL 加载失败**

3. **双击 `install.bat`**
   - 自动静默安装 VC++ 运行库 + Python（目录里的安装包）
   - 自动安装 RapidOCR + PyQt5 等依赖（使用国内清华/阿里云镜像，**无需梯子**）
   - OCR 模型随 rapidocr_onnxruntime 包自带（PP-OCRv4，约 15MB），无需下载
   - 全程自动，等待完成即可

### 日常使用

- 双击 **`start.bat`** 即可运行程序
- 无需联网（OCR 模型随 pip 包自带）
- start.bat 会自动检查依赖，缺失时提示先运行 install.bat

## 目录结构

```
screenshot_ocr_windows/
├── main.py              ← 主程序入口（PyQt5 GUI，启动时调用自检）
├── runtime_check.py     ← 启动自检 + 资源定位（PyInstaller 支持）
├── ocr_engine.py        ← RapidOCR 封装（替代 macOS Vision）
├── field_parser.py      ← OCR 文本 -> 表单字段解析器
├── excel_writer.py      ← Excel 写入器（模板格式 + 公式 + WPS 嵌入图片）
├── summary_generator.py ← 价格合格率汇总表生成器
├── store_check_converter.py ← 巡查表 → 门店价格检查表转换器
├── summary_speech.py    ← 总结话术生成器
├── city_detector.py     ← 城市自动识别
├── city_pool.py         ← 城市池（广东/广西/海南/贵州/云南）
├── database/            ← 店铺/城市学习库、投喂与联网审计
├── shop_city_db.py      ← 旧版店铺城市库兼容模块（仅供历史迁移）
├── download_models.py   ← OCR 引擎验证脚本
├── test_compare.py      ← 测试脚本
├── install.bat          ← 一键安装脚本（首次使用）
├── start.bat             ← 启动脚本
├── build_windows.bat    ← 打包脚本（Windows 上执行，产出 EXE）
├── price_check_ocr.spec     ← PyInstaller 打包配置
├── requirements.txt     ← Python 依赖
├── README.md            ← 本文档
├── 模板.xlsx            ← Excel 模板
├── data/
│   └── shop_city.db     ← 历史店铺城市迁移源（不直接自动匹配）
└── models/              ← 预留目录（RapidOCR 模型随 pip 包自带）
```

## 环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 64位+ |
| Python | 3.10+（64位，安装时勾选 Add to PATH） |
| CPU | x86_64（Intel/AMD），支持 AVX 指令集 |
| 内存 | 4GB+ |
| 磁盘 | 1GB 可用空间（含模型文件） |
| VC++ 运行时 | Visual C++ Redistributable 2015-2022 x64（大多数系统已预装） |

## 安装依赖（手动方式）

如果 install.bat 失败，可手动执行：

```bash
# 1. 安装 RapidOCR 与 ONNX Runtime

pip install "onnxruntime==1.20.1" rapidocr_onnxruntime

# 2. 安装界面与 Excel 依赖
pip install PyQt5 openpyxl Pillow

# 3. 验证 OCR 引擎
python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print('OCR engine OK')"
```

## 使用流程

1. **导入截图**：拖拽图片 / 选择图片 / 上传压缩包
2. **选择省份+城市**：OCR 识别完成后弹出省份城市选择（支持多省多选）
3. **核对修正**：在表格中检查识别结果；“仅看待核对”会筛出缺少区域、店铺、产品或关键价格的行
4. **失败恢复**：识别过程中可取消；识别失败或取消的图片可点击“重试失败项”单独重跑
5. **城市与重复核查**：按需使用联网识别城市、统一设置店铺城市和查重核查；联网结果会保留分店名与 POI 匹配证据，歧义项默认不填入，待人工确认后才会写入知识库。联网仅发送店铺名称，并在本地记录授权、请求范围、候选和最终裁决
6. **导出 Excel**：导出前会提示待核对条数，确认后生成巡查表
7. **生成汇总表/话术**：选择巡查表 xlsx 生成价格合格率汇总报告或总结话术
8. **生成门店价格检查表**：点击「门店价格检查表」，选择巡查表 xlsx；输出总部 12 列检查表及转换日志

### 1998 价格标准

1998 的 500ml*12 瓶/12 听规格按省份执行：广东第一档合格线 70 元、第二档 65 元；广西第一档合格线 60 元、第二档 55 元。9 装规格继续按 45 元执行。

汇总表中的“合格率”按第一档合格线计算；“第二档合格率”按第二档线以上数量计算，低于第二档线的数量单独列示。混合广东、广西数据时，表头显示“各省标准”，每个省份行按本省阈值统计。

## OCR 引擎说明

### RapidOCR（Windows 版）

- 基于 onnxruntime 的 OCR 引擎（PP-OCRv4 中文模型）
- 模型随 pip 包自带（约 15MB），离线可用
- 识别速度快：i3-12100 CPU 约 1-2 秒/张（PaddleOCR 需 60 秒/张）
- 返回文字坐标（像素坐标 -> 归一化坐标转换）
- 坐标格式与 macOS Vision 版完全兼容，field_parser.py 无需修改

### 与 macOS 版的差异

| | macOS 版 | Windows 版 |
|---|---|---|
| OCR 引擎 | Vision Framework (Swift) | RapidOCR (onnxruntime) |
| 安装方式 | Xcode Command Line Tools | pip install（install.bat 自动） |
| 模型 | 系统内置 | pip 包自带（PP-OCRv4，15MB） |
| 识别精度 | 高 | 高（中文优化） |
| 坐标格式 | 归一化(0~1) | 归一化(0~1)（自动转换） |
| 离线使用 | 支持 | 支持（模型随程序打包） |

## 常见问题

### Q: 双击 start.bat 闪退/报 ModuleNotFoundError？
- 报 `No module named 'PyQt5'` 等说明依赖未安装，先双击 install.bat
- start.bat 已内置依赖检查，缺失时会明确提示

### Q: install.bat 提示"未找到 Python"？
- 把 Python 安装包（如 python-3.11.9-amd64.exe）放进本目录后重新运行
- 或手动安装 Python 3.10+，安装时勾选 "Add Python to PATH"
- 安装后重启命令行窗口

### Q: install.bat 安装依赖时网络报错？
- 脚本已内置清华镜像重试，一般会自动恢复
- 确认网络连接后重新运行 install.bat

### Q: OCR 报 DLL 加载失败？（onnxruntime_pybind11_state）
- 缺少 VC++ Redistributable 运行库
- 把 vc_redist.x64.exe 放进目录，重新运行 install.bat
- 下载：https://aka.ms/vs/17/release/vc_redist.x64.exe

### Q: 模型相关报错？
- 模型已随程序打包，一般不会缺失
- 如果 models/ 目录被误删，运行 install.bat 或 download_models.py 会重新下载

Design By 创新业务中心-江凯豪
