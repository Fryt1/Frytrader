# -*- coding: utf-8 -*-
"""同花顺验证码的 ONNX 识别通道。

模型来自 Fryt1/easyths 的同花顺验证码资产，模型及外部数据合计约 8.5MB。
使用随包固定哈希权重、模型 metadata 字符表与 CTC 贪心解码，不调用
Tesseract。热推理时间不包含截图、输入、确认或客户端响应。

运行依赖为 numpy、Pillow、onnx、onnxruntime，不需要 PyTorch。
"""

import hashlib
import threading
from pathlib import Path

import numpy as np
from PIL import Image

__all__ = ["onnx_recognize", "model_available", "MODEL_SHA256"]

MODEL_DIR = Path(__file__).resolve().parent / "captcha_model"
MODEL_PATH = MODEL_DIR / "captcha_ocr.onnx"
DATA_PATH = MODEL_DIR / "captcha_ocr.onnx.data"

# 随包分发的固定权重，加载前校验，避免被替换成别的模型。
MODEL_SHA256 = (
    "e84985fbc645abe2c775be11d12bf3e8d90148a9a8c81ab6592d7e312a3d6c26"
)
DATA_SHA256 = (
    "fa5c20f905549f1798c8508e8abec642fcc8c3849f6ab3f9c8f4fdbd95053ae7"
)

_lock = threading.Lock()
_session = None
_meta = None


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_available():
    """模型文件是否齐全（不校验哈希，供快速判断用）。"""
    return MODEL_PATH.is_file() and DATA_PATH.is_file()


def _load():
    """惰性加载模型；失败返回 None，由调用方回退到 tesseract。"""
    global _session, _meta
    if _session is not None:
        return _session
    with _lock:
        if _session is not None:
            return _session
        if not model_available():
            return None
        # 校验随包权重，不一致就拒绝加载，绝不静默用别的模型
        if _sha256(MODEL_PATH) != MODEL_SHA256:
            return None
        if _sha256(DATA_PATH) != DATA_SHA256:
            return None
        import onnx
        import onnxruntime as ort

        model = onnx.load(str(MODEL_PATH))
        metadata = {p.key: p.value for p in model.metadata_props}
        characters = metadata["character"]
        size = (int(metadata["img_w"]), int(metadata["img_h"]))
        channels = int(metadata.get("nc", 1))
        session = ort.InferenceSession(
            str(MODEL_PATH), providers=["CPUExecutionProvider"]
        )
        _meta = {
            "characters": characters,
            "blank": len(characters),
            "size": size,
            "channels": channels,
            "input": session.get_inputs()[0].name,
        }
        _session = session
        return _session


def _ctc_decode(indices, characters, blank):
    """CTC 贪心解码：合并连续重复，去掉 blank。"""
    out = []
    previous = -1
    for index in indices:
        if index != previous and index != blank and index < len(characters):
            out.append(characters[index])
        previous = index
    return "".join(out)


def onnx_recognize(image_path):
    """识别一张验证码，返回 4 位字符串；不可用时返回空串。

    模型输入是灰度图，缩放到模型自带的 ``img_w x img_h``（实测 256x64），
    用 LANCZOS 重采样、归一化到 [0,1]。实测不要做反相处理，反相后准确率归零。
    """
    try:
        session = _load()
    except Exception:
        return ""
    if session is None or _meta is None:
        return ""
    try:
        image = Image.open(image_path)
        if _meta["channels"] == 1:
            image = image.convert("L")
        else:
            image = image.convert("RGB")
        image = image.resize(_meta["size"], Image.LANCZOS)
        array = np.asarray(image, dtype=np.float32) / 255.0
        if _meta["channels"] == 1:
            array = np.expand_dims(array, axis=0)
        else:
            array = np.transpose(array, (2, 0, 1))
        array = np.expand_dims(array, axis=0)
        output = session.run(None, {_meta["input"]: array})[0]
        indices = output.argmax(axis=1)[0].tolist()
        return _ctc_decode(indices, _meta["characters"], _meta["blank"])
    except Exception:
        return ""
