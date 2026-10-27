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


def captcha_recognize_improved(
    image_path, expected_length=4, tesseract_path=None
):
    """尝试多种图像预处理，用多数投票选择结果，仍不确定时返回空结果。

    验证码区分大小写，因此不能做大小写归一；投票按原样比较。
    对同花顺这类蓝字白底的验证码，蓝通道反相 + 多阈值二值化能显著提高准确率。

    注意：验证码有较短的存活时间，串行跑完全部变体约需 5 秒，会导致验证码
    在提交前就失效。这里用线程池并行调用 tesseract，把耗时压到 1 秒左右。
    """
    from concurrent.futures import ThreadPoolExecutor

    from PIL import Image, ImageOps
    import pytesseract

    previous_tesseract_path = pytesseract.pytesseract.tesseract_cmd
    try:
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path

        image = Image.open(image_path)
        gray = image.convert("L")
        scale = 3
        size = (gray.width * scale, gray.height * scale)
        big = gray.resize(size, Image.LANCZOS)

        variants = [big, ImageOps.autocontrast(big)]

        # 蓝色字符：取蓝通道反相后文字变黑、背景变白
        try:
            _, _, blue = image.convert("RGB").split()
            inverted = ImageOps.invert(blue).resize(size, Image.LANCZOS)
            variants.append(inverted)
            variants.append(ImageOps.autocontrast(inverted))
        except Exception:
            pass

        # 多个阈值二值化
        for threshold in (100, 140, 180):
            variants.append(
                big.point(lambda p, t=threshold: 0 if p < t else 255)
            )

        whitelist = (
            "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "abcdefghijklmnopqrstuvwxyz"
        )

        def recognize_one(task):
            variant, page_mode = task
            config = (
                "--oem 3 --psm {} -c tessedit_char_whitelist={}".format(
                    page_mode, whitelist
                )
            )
            try:
                text = pytesseract.image_to_string(variant, config=config)
            except Exception:
                return ""
            return normalize_captcha_result(text, expected_length)

        tasks = [
            (variant, page_mode)
            for variant in variants
            for page_mode in (7, 8, 6, 13)
        ]

        # tesseract 会启动子进程，线程池即可获得接近线性的加速
        workers = min(8, max(1, len(tasks)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            candidates = list(executor.map(recognize_one, tasks))
        candidates = [result for result in candidates if result]

        return select_captcha_candidate(candidates, expected_length)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "未找到 Tesseract，请安装 Tesseract 或检查 tesseract_path 配置"
        ) from exc
    finally:
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = previous_tesseract_path
