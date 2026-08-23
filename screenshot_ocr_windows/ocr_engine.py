"""
RapidOCR 封装（Windows 版）
使用 RapidOCR (onnxruntime + PP-OCRv4 模型) 替代 macOS Vision Framework，
返回与原版完全一致的格式。

为什么用 RapidOCR 而不是 PaddleOCR：
  - PaddlePaddle 3.3.0 + oneDNN 有已知 bug（ConvertPirAttribute2RuntimeAttribute 崩溃）
  - PaddlePaddle 在低端 CPU (i3) 上识别极慢（约60秒/张）
  - RapidOCR 基于 onnxruntime，速度提升 20-50 倍（约1-2秒/张）
  - 不依赖 paddlepaddle，绕开所有 paddle 生态问题

返回值：list[dict]，每个元素包含：
  - text: 识别文本
  - confidence: 置信度 (0~1)
  - left, top: 归一化坐标 (0~1)，top 越大越靠上（与 Vision 一致）
  - width, height: 归一化宽高

坐标转换说明：
  RapidOCR 返回 4 点像素坐标 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]，Y 轴从上往下
  Vision 格式为归一化 left/top/width/height，Y 轴从下往上（top 越大越靠上）
  转换：left = x_min/img_w, width = (x_max-x_min)/img_w
        top = 1.0 - y_max/img_h（底边翻转）, height = (y_max-y_min)/img_h
"""

import logging
import os
import sys

from PIL import Image

logger = logging.getLogger(__name__)

# Windows 命令行中文输出编码修复
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 项目根目录
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 全局 OCR 实例（首次调用时初始化）
_ocr_instance = None


def _get_ocr():
    """获取或初始化 RapidOCR 实例（单例，避免重复加载模型）

    模型（PP-OCRv4 中文，约15MB）随 rapidocr_onnxruntime 包自带，
    无需联网下载，离线可用。
    """
    global _ocr_instance
    if _ocr_instance is None:
        logger.info("正在初始化 RapidOCR...")
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:
            # DLL 加载失败通常是因为缺少 VC++ Redistributable 运行时
            if "DLL load failed" in str(e) or "onnxruntime_pybind11_state" in str(e):
                raise RuntimeError(
                    "onnxruntime DLL 加载失败。请安装 Microsoft Visual C++ "
                    "Redistributable 2015-2022 (x64)：\n"
                    "https://aka.ms/vs/17/release/vc_redist.x64.exe\n"
                    "（可放入程序目录后重新运行 install.bat 自动安装）"
                ) from e
            raise

        _ocr_instance = RapidOCR()
        logger.info("RapidOCR 初始化完成")
    return _ocr_instance


def run_ocr(image_path):
    """
    对单张图片执行 OCR 识别

    使用 RapidOCR 识别文字，并将坐标转换为与 macOS Vision 一致的格式：
    归一化坐标 (0~1)，top 越大越靠上。

    Args:
        image_path: 图片文件路径

    Returns:
        list[dict]: OCR 结果列表，每个元素包含:
            - text: 识别文本
            - confidence: 置信度 (0~1)
            - left, top: 归一化坐标 (0~1)，top 越大越靠上
            - width, height: 归一化宽高
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"图片不存在: {image_path}")

    engine = _get_ocr()
    result, _elapse = engine(image_path)

    # 获取图片尺寸用于坐标归一化
    with Image.open(image_path) as img:
        img_width, img_height = img.size

    output = []
    if result:
        for box, text, score in result:
            # box 是 4 点像素坐标 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)

            # 像素坐标 -> 归一化坐标
            left = float(x_min) / img_width
            width = float(x_max - x_min) / img_width
            # Y 轴翻转：Vision 的 top 越大越靠上，用文本框底边翻转
            top = 1.0 - (float(y_max) / img_height)
            height = float(y_max - y_min) / img_height

            output.append({
                "text": text,
                "confidence": float(score),
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            })

    # 按 top 降序（与 Vision 一致，top 越大越靠上，即图片上方的文字排在前面）
    output.sort(key=lambda x: x["top"], reverse=True)
    return output


def run_ocr_batch(image_paths, progress_callback=None, should_cancel=None):
    """
    批量 OCR 识别

    Args:
        image_paths: 图片路径列表
        progress_callback: 回调函数 (current_index, total, image_path)

    Returns:
        dict: {image_path: ocr_results}
    """
    # 预初始化 OCR 实例（只在首次有开销）
    _get_ocr()

    results = {}
    total = len(image_paths)

    for i, path in enumerate(image_paths):
        if should_cancel and should_cancel():
            logger.info("ocr_cancelled before index=%d total=%d", i + 1, total)
            break
        logger.info("ocr_start index=%d total=%d image=%s", i + 1, total, path)
        if progress_callback:
            progress_callback(i, total, path)

        try:
            ocr_data = run_ocr(path)
            results[path] = ocr_data
            logger.info("ocr_success index=%d image=%s", i + 1, path)
        except Exception as e:
            results[path] = {"error": str(e)}
            logger.exception("ocr_failed index=%d image=%s", i + 1, path)

    if progress_callback:
        progress_callback(total, total, None)

    return results


def run_ocr_parallel(image_paths, progress_callback=None, max_workers=8,
                     should_cancel=None):
    """
    并行批量 OCR（与 Mac 版接口一致）。

    Windows 端 PaddleOCR 模型实例非线程安全且引擎内部已多线程（paddle 默认
    按 CPU 核数并行），因此此处为串行包装：行为与 run_ocr_batch 一致，
    不额外引入并发以免模型竞争或内存翻倍。

    Args:
        image_paths: 图片路径列表
        progress_callback: 回调函数 (done_count, total, image_path)

    Returns:
        dict: {image_path: ocr_results}，OCR 失败的路径值为 {"error": ...}
    """
    return run_ocr_batch(image_paths, progress_callback, should_cancel)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python ocr_engine.py <图片路径>")
        sys.exit(1)

    data = run_ocr(sys.argv[1])
    for item in data:
        print(
            f"[{item['left']:.4f}, {item['top']:.4f}] "
            f"({item['confidence']:.2f}) {item['text']}"
        )
