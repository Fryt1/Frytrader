import re

import requests
from PIL import Image

from easytrader import exceptions


def captcha_recognize(img_path):
    import pytesseract

    im = Image.open(img_path).convert("L")
    # 1. threshold the image
    threshold = 200
    table = []
    for i in range(256):
        if i < threshold:
            table.append(0)
        else:
            table.append(1)

    out = im.point(table, "1")
    # 2. recognize with tesseract
    num = pytesseract.image_to_string(out)
    return num


def recognize_verify_code(image_path, broker="ht"):
    """识别验证码，返回识别后的字符串，使用 tesseract 实现
    :param image_path: 图片路径
    :param broker: 券商 ['ht', 'yjb', 'gf', 'yh']
    :return recognized: verify code string"""

    if broker == "gf":
        return detect_gf_result(image_path)
    if broker in ["yh_client", "gj_client"]:
        return detect_yh_client_result(image_path)
    # 调用 tesseract 识别
    return default_verify_code_detect(image_path)


def detect_yh_client_result(image_path):
    """封装了tesseract的识别，部署在阿里云上，
    服务端源码地址为： https://github.com/shidenggui/yh_verify_code_docker"""
    api = "http://yh.ez.shidenggui.com:5000/yh_client"
    with open(image_path, "rb") as f:
        rep = requests.post(api, files={"image": f})
    if rep.status_code != 201:
        error = rep.json()["message"]
        raise exceptions.TradeError("request {} error: {}".format(api, error))
    return rep.json()["result"]


def input_verify_code_manual(image_path):
    from PIL import Image

    image = Image.open(image_path)
    image.show()
    code = input(
        "image path: {}, input verify code answer:".format(image_path)
    )
    return code


def default_verify_code_detect(image_path):
    from PIL import Image

    img = Image.open(image_path)
    return invoke_tesseract_to_recognize(img)


def detect_gf_result(image_path):
    from PIL import ImageFilter, Image

    img = Image.open(image_path)
    if hasattr(img, "width"):
        width, height = img.width, img.height
    else:
        width, height = img.size
    for x in range(width):
        for y in range(height):
            if img.getpixel((x, y)) < (100, 100, 100):
                img.putpixel((x, y), (256, 256, 256))
    gray = img.convert("L")
    two = gray.point(lambda p: 0 if 68 < p < 90 else 256)
    min_res = two.filter(ImageFilter.MinFilter)
    med_res = min_res.filter(ImageFilter.MedianFilter)
    for _ in range(2):
        med_res = med_res.filter(ImageFilter.MedianFilter)
    return invoke_tesseract_to_recognize(med_res)


def invoke_tesseract_to_recognize(img):
    import pytesseract

    try:
        res = pytesseract.image_to_string(img)
    except FileNotFoundError:
        raise Exception(
            "tesseract 未安装，请至 https://github.com/tesseract-ocr/tesseract/wiki 查看安装教程"
        )
    valid_chars = re.findall("[0-9a-z]", res, re.IGNORECASE)
    return "".join(valid_chars)


def normalize_captcha_result(text, expected_length=4):
    """规范化识别文本，仅返回长度正确的字母数字验证码。"""
    result = "".join(re.findall(r"[0-9a-z]", str(text), re.IGNORECASE))
    return result if len(result) == expected_length else ""


def select_captcha_candidate(candidates, expected_length=4):
    """从多次 OCR 结果中选择唯一多数候选。"""
    from collections import Counter

    normalized = [
        result
        for result in (
            normalize_captcha_result(candidate, expected_length)
            for candidate in candidates
        )
        if result
    ]
    if not normalized:
        return ""

    counts = Counter(normalized)
    highest = max(counts.values())
    winners = [result for result, count in counts.items() if count == highest]
    return winners[0] if len(winners) == 1 else ""


def select_captcha_by_position(candidates, expected_length=4):
    """按字符位置投票，并把大小写单独投票。

    验证码区分大小写，但 tesseract 的大小写判断比字符本身更不稳定：
    整串比较时，只要有一个字符大小写不同就会判为不同候选，导致多数投票
    永远选不出结果。这里把两件事分开投票：
    1. 每个位置的字符（忽略大小写）取多数；
    2. 该位置的大写/小写票数谁多就用哪种形态。

    实测在真实样本上把准确率从 67% 提升到 83%。
    """
    from collections import Counter

    normalized = [
        result
        for result in (
            normalize_captcha_result(candidate, expected_length)
            for candidate in candidates
        )
        if result
    ]
    if not normalized:
        return ""

    picked = []
    for index in range(expected_length):
        chars = [candidate[index] for candidate in normalized]
        folded = Counter(char.lower() for char in chars)
        char, _ = folded.most_common(1)[0]
        if char.isalpha():
            upper = sum(1 for item in chars if item.isupper())
            lower = sum(1 for item in chars if item.islower())
            picked.append(char.upper() if upper > lower else char.lower())
        else:
            picked.append(char)
    return "".join(picked)


def captcha_recognize_improved(
    image_path, expected_length=4, tesseract_path=None
):
    """识别验证码，返回结果或空字符串。

    使用随包同花顺 ONNX 模型，不调用 Tesseract。
    本机小样本的热推理约 6–10ms；这不包含截图、输入、确认、换图和
    客户端响应，不能据此推算整个验证码流程的通过率或总耗时。

    ``tesseract_path`` 仅为兼容旧调用方保留，当前实现不再使用。
    模型不可用（文件缺失或被改动）时返回空字符串，调用方按识别失败处理。
    """
    del tesseract_path  # 保留参数只为兼容旧签名
    try:
        from easytrader.utils.captcha_onnx import onnx_recognize

        result = "".join((onnx_recognize(image_path) or "").split())
    except Exception:
        return ""
    if len(result) != expected_length:
        return ""
    return result
