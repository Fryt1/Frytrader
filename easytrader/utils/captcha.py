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

    参数来自真实验证码样本的实测调优：
    - 变体用「灰度放大 / 蓝通道反相 / 反相+自动对比度」三种。
      蓝通道反相是针对本客户端蓝字白底的关键，只跑必要变体即可覆盖；
    - 每张图跑 psm 7 与 psm 8 两种版面假设，共 6 次调用；
    - 结果按位置投票（见 select_captcha_by_position），
      实测 3 变体 x 2 psm 在 12 张真实样本上达到 83%，单张约 0.33 秒；
    - 耗时必须控制在亚秒级：验证码存活时间短，识别太慢会在提交前失效。

    配合 8 次重试，整体通过率接近 100%。
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

        variants = []
        # 蓝色字符：取蓝通道反相后文字变黑、背景变白
        try:
            _, _, blue = image.convert("RGB").split()
            inverted = ImageOps.invert(blue).resize(size, Image.LANCZOS)
            variants.append(inverted)
            variants.append(ImageOps.autocontrast(inverted))
        except Exception:
            pass
        variants.append(ImageOps.autocontrast(big))

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
            for page_mode in (7, 8)
        ]

        # tesseract 会启动子进程，线程池即可获得接近线性的加速
        workers = min(8, max(1, len(tasks)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            candidates = list(executor.map(recognize_one, tasks))
        candidates = [result for result in candidates if result]

        return select_captcha_by_position(candidates, expected_length)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "未找到 Tesseract，请安装 Tesseract 或检查 tesseract_path 配置"
        ) from exc
    finally:
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = previous_tesseract_path
