"""
macOS Vision API OCR 封装
通过 Swift 脚本调用原生 Vision 框架，返回识别文本+坐标+置信度
完全本地运行，零网络请求

性能优化：首次使用时用 swiftc 编译为二进制缓存，后续直接调用二进制，
省去每张图重复编译 Swift 源码的开销（100张图可省数分钟）。
"""

import hashlib
import json
import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

# Swift 脚本：调用 VNRecognizeTextRequest 做 OCR
_SWIFT_SCRIPT = r'''
import Cocoa
import Vision
import Foundation

let imagePath = CommandLine.arguments[1]

guard let imageData = try? Data(contentsOf: URL(fileURLWithPath: imagePath)),
      let image = NSImage(data: imageData),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fputs("Failed to load image: \(imagePath)\n", stderr)
    exit(1)
}

let semaphore = DispatchSemaphore(value: 0)
var results: [[String: Any]] = []

let request = VNRecognizeTextRequest { request, error in
    guard let observations = request.results as? [VNRecognizedTextObservation] else {
        semaphore.signal()
        return
    }
    for observation in observations {
        if let candidate = observation.topCandidates(1).first {
            let bbox = observation.boundingBox
            results.append([
                "text": candidate.string as Any,
                "confidence": Double(candidate.confidence) as Any,
                "left": Double(bbox.origin.x) as Any,
                "top": Double(bbox.origin.y) as Any,
                "width": Double(bbox.width) as Any,
                "height": Double(bbox.height) as Any,
            ] as [String: Any])
        }
    }
    semaphore.signal()
}

request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en", "zh-Hant"]
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try? handler.perform([request])
semaphore.wait()

// 按 top 降序（Vision 坐标 Y 轴从下往上，top 越大越靠图片上方）
results.sort { ($0["top"] as! Double) > ($1["top"] as! Double) }

let jsonData = try! JSONSerialization.data(withJSONObject: results, options: [])
let jsonString = String(data: jsonData, encoding: .utf8)!
print(jsonString)
'''

# =========================================================
# 二进制缓存路径（与代码同目录的 data 子目录）
# =========================================================
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_SWIFT_SOURCE_PATH = os.path.join(_DATA_DIR, ".ocr_helper.swift")
_BINARY_PATH = os.path.join(_DATA_DIR, ".ocr_helper")
_HASH_PATH = os.path.join(_DATA_DIR, ".ocr_helper.hash")

# 缓存编译结果，避免每次调用都检查文件
_cached_binary = None
_cache_checked = False


def _get_script_hash():
    """计算 Swift 脚本内容的 SHA-256 哈希（用于判断是否需要重新编译）"""
    return hashlib.sha256(_SWIFT_SCRIPT.encode("utf-8")).hexdigest()


def _get_compiled_binary():
    """获取编译好的二进制路径，如果需要则先编译

    Returns:
        str: 二进制路径，如果编译失败返回 None（回退到 swift 解释执行）
    """
    global _cached_binary, _cache_checked

    if _cache_checked:
        return _cached_binary

    _cache_checked = True
    current_hash = _get_script_hash()

    # 检查缓存的二进制是否有效（存在 + 哈希匹配）
    if os.path.exists(_BINARY_PATH) and os.path.exists(_HASH_PATH):
        try:
            with open(_HASH_PATH, "r") as f:
                cached_hash = f.read().strip()
            if cached_hash == current_hash:
                _cached_binary = _BINARY_PATH
                logger.debug("OCR 二进制缓存命中，跳过编译")
                return _cached_binary
        except OSError:
            pass

    # 需要编译：写入源码 + swiftc 编译
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)

        # 写入 Swift 源码
        with open(_SWIFT_SOURCE_PATH, "w", encoding="utf-8") as f:
            f.write(_SWIFT_SCRIPT)

        # 用 swiftc -O 编译为二进制（-O 优化性能）
        logger.info("正在编译 OCR 二进制缓存（仅首次，约数秒）...")
        result = subprocess.run(
            ["swiftc", "-O", _SWIFT_SOURCE_PATH, "-o", _BINARY_PATH],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            logger.warning("swiftc 编译失败，回退到 swift 解释模式: %s", result.stderr[:300])
            _cached_binary = None
            return None

        # 写入哈希文件
        with open(_HASH_PATH, "w") as f:
            f.write(current_hash)

        _cached_binary = _BINARY_PATH
        logger.info("OCR 二进制编译成功: %s", _BINARY_PATH)
        return _cached_binary

    except FileNotFoundError:
        # swiftc 不存在
        logger.warning("swiftc 未找到，回退到 swift 解释模式")
        _cached_binary = None
        return None
    except subprocess.TimeoutExpired:
        logger.warning("swiftc 编译超时，回退到 swift 解释模式")
        _cached_binary = None
        return None
    except Exception as e:
        logger.warning("编译二进制异常，回退到 swift 解释模式: %s", e)
        _cached_binary = None
        return None


def _run_with_swift_fallback(image_path):
    """回退模式：用 swift 命令直接执行源码（每次都会编译，较慢）"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".swift", delete=False, encoding="utf-8"
    ) as f:
        f.write(_SWIFT_SCRIPT)
        script_path = f.name

    try:
        result = subprocess.run(
            ["swift", script_path, image_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Vision OCR 失败 (code={result.returncode}):\n{result.stderr[:500]}"
            )
        output = result.stdout.strip()
        return json.loads(output) if output else []
    finally:
        os.unlink(script_path)


def run_ocr(image_path):
    """
    对单张图片执行 OCR 识别

    首次调用时编译 Swift 脚本为二进制缓存，后续直接调用二进制。
    编译失败时回退到 swift 解释执行模式。

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

    binary = _get_compiled_binary()

    if binary:
        # 使用编译好的二进制（快）
        result = subprocess.run(
            [binary, image_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Vision OCR 失败 (code={result.returncode}):\n{result.stderr[:500]}"
            )
        output = result.stdout.strip()
        return json.loads(output) if output else []
    else:
        # 回退到 swift 解释执行（慢，但兼容）
        return _run_with_swift_fallback(image_path)


def run_ocr_parallel(image_paths, progress_callback=None, max_workers=8):
    """
    并行批量 OCR 识别（macOS Vision 内部按图像并行，实测 8 workers 吞吐最佳：
    0.35s/张(串行) -> 0.14s/张；16+ workers 无增益且推高系统负载）

    Args:
        image_paths: 图片路径列表
        progress_callback: 回调函数 (done_count, total, image_path)
        max_workers: 线程数，默认 8

    Returns:
        dict: {image_path: ocr_results}，OCR 失败的路径值为 {"error": ...}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = {}
    total = len(image_paths)
    if total == 0:
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(run_ocr, p): p for p in image_paths}
        done = 0
        for fut in as_completed(futures):
            path = futures[fut]
            done += 1
            if progress_callback:
                progress_callback(done, total, path)
            try:
                results[path] = fut.result()
            except Exception as e:
                results[path] = {"error": str(e)}

    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python ocr_engine.py <图片路径>")
        sys.exit(1)

    data = run_ocr(sys.argv[1])
    for item in data:
        print(
            f"[{item['left']:.4f}, {item['top']:.4f}] "
            f"({item['confidence']:.2f}) {item['text']}"
        )
