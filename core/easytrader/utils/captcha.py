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


def recognize_verify_code(image_path, broker="ht", max_retries=3):
    """识别验证码，返回识别后的字符串，使用 tesseract 实现
    :param image_path: 图片路径
    :param broker: 券商 ['ht', 'yjb', 'gf', 'yh']
    :param max_retries: 最大重试次数
    :return recognized: verify code string"""

    if broker == "gf":
        return detect_gf_result(image_path)
    if broker in ["yh_client", "gj_client"]:
        return detect_yh_client_result(image_path)
    
    # 多轮识别尝试
    for attempt in range(max_retries):
        try:
            # 每次尝试使用不同的预处理参数
            if attempt == 1:
                # 第二次尝试：更强的去噪
                result = default_verify_code_detect(image_path, denoise_level=15)
            elif attempt == 2:
                # 第三次尝试：更高的对比度
                result = default_verify_code_detect(image_path, contrast=1.5)
            else:
                # 第一次尝试：默认参数
                result = default_verify_code_detect(image_path)
            
            if result and len(result) >= 4:  # 假设验证码通常为4个字符
                return result
                
        except Exception as e:
            print(f"验证码识别尝试 {attempt+1} 失败: {str(e)}")
    
    # 所有尝试都失败后返回空字符串或抛出异常
    return ""


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


def default_verify_code_detect(image_path, denoise_level=10, contrast=1.0):
    import cv2
    import numpy as np
    from PIL import Image

    # 1. 使用OpenCV读取并增强图像
    img = cv2.imread(image_path)
    
    # 2. 超分辨率处理 (2倍放大)
    img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    
    # 3. 转换为灰度图
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # 4. 自适应阈值二值化
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY, 11, 2)
    
    # 5. 去噪和对比度增强
    denoised = cv2.fastNlMeansDenoising(binary, None, denoise_level, 7, 21)
    # 对比度调整
    denoised = cv2.convertScaleAbs(denoised, alpha=contrast, beta=0)
    
    # 6. 转换为PIL Image并调用Tesseract
    pil_img = Image.fromarray(denoised)
    return invoke_tesseract_to_recognize(pil_img)


def detect_gf_result(image_path):
    from PIL import ImageFilter, Image

    img = Image.open(image_path)
    if hasattr(img, "width"):
        width, height = img.width, img.height
    else:
        width, height = img.size
    for x in range(width):
        for y in range(height):
            pixel = img.getpixel((x, y))
            if isinstance(pixel, tuple) and pixel < (100, 100, 100):
                img.putpixel((x, y), (256, 256, 256))
    gray = img.convert("L")
    # type: ignore
    two = gray.point(lambda p: 0 if p > 68 and p < 90 else 256)  # noqa
    min_res = two.filter(ImageFilter.MinFilter)
    med_res = min_res.filter(ImageFilter.MedianFilter)
    for _ in range(2):
        med_res = med_res.filter(ImageFilter.MedianFilter)
    return invoke_tesseract_to_recognize(med_res)


def invoke_tesseract_to_recognize(img):
    import pytesseract
    import os
    import time

    try:
        # 优化Tesseract配置
        config = '--psm 7 -c tessedit_char_whitelist=0123456789abcdefghijklmnopqrstuvwxyz'
        res = pytesseract.image_to_string(img, config=config)
        
        # 保存失败样本用于分析
        if not res or len(res) < 4:  # 假设验证码通常为4个字符
            save_failed_sample(img, res)
        
        valid_chars = re.findall("[0-9a-z]", res, re.IGNORECASE)
        return "".join(valid_chars)
    except FileNotFoundError:
        raise Exception(
            "tesseract 未安装，请至 https://github.com/tesseract-ocr/tesseract/wiki 查看安装教程"
        )

def save_failed_sample(img, result):
    """保存识别失败的验证码样本"""
    import os
    import datetime
    
    debug_dir = os.path.join(os.path.dirname(__file__), '../../captcha_debug')
    os.makedirs(debug_dir, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"failed_{timestamp}.png"
    filepath = os.path.join(debug_dir, filename)
    
    img.save(filepath)
    
    # 记录识别结果
    with open(os.path.join(debug_dir, 'failed_log.txt'), 'a') as f:
        f.write(f"{filename}: {result}\n")
