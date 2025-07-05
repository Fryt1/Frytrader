# EasyTrader 验证码功能部署指南

## 快速开始

### 1. 环境准备

```bash
# 1. 克隆项目
git clone git@github.com:shidenggui/easytrader.git
cd easytrader

# 2. 安装 Tesseract-OCR (Windows)
# 下载安装包：https://github.com/UB-Mannheim/tesseract/wiki
# 或使用包管理器：
# choco install tesseract
# scoop install tesseract

# 3. 验证 Tesseract 安装
tesseract --version

# 4. 运行自动安装脚本
python setup_captcha.py
```

### 2. 手动安装

如果自动安装脚本失败，可以手动安装：

```bash
# 安装基础依赖
pip install -r requirements.txt

# 安装验证码相关依赖
pip install pytesseract>=0.3.7 Pillow>=8.0.0 opencv-python>=4.5.0

# 安装项目
pip install -e .
```

### 3. 使用示例

```python
import easytrader

# 创建交易客户端
trader = easytrader.use('ths')  # 通用同花顺

# 连接客户端
trader.connect(r'C:\path\to\xiadan.exe')

# 获取账户信息（如果出现验证码会自动处理）
balance = trader.balance
positions = trader.position

# 执行交易操作
# result = trader.buy('000001', 1, 10.0)
```

## 验证码功能详解

### 工作原理

1. **检测验证码窗口**：程序检测是否出现包含"验证码"文字的窗口
2. **截取验证码图片**：自动截取验证码图片并保存
3. **OCR识别**：使用 Tesseract-OCR 识别验证码文字
4. **自动输入**：将识别结果自动输入到验证码输入框
5. **确认提交**：模拟按下回车键确认
6. **重试机制**：如果识别失败，最多重试5次

### 修改的文件

- `easytrader/grid_strategies.py` - 在 Xls 类中添加验证码处理逻辑
- `requirements-captcha.txt` - 验证码相关依赖包
- `setup_captcha.py` - 自动安装脚本
- `example_captcha.py` - 使用示例
- `README-CAPTCHA.md` - 详细说明文档

### 关键参数配置

```python
# 验证码相关控件ID（可能需要根据实际情况调整）
CAPTCHA_IMAGE_CONTROL_ID = 0x965  # 验证码图片控件
CAPTCHA_INPUT_CONTROL_ID = 0x964  # 验证码输入框控件
```

## 故障排除

### 常见问题

1. **Tesseract 找不到**
   ```
   TesseractNotFoundError: tesseract is not installed or it's not in your PATH
   ```
   **解决方案**：
   - 确保 Tesseract-OCR 已正确安装
   - 将安装目录添加到 PATH 环境变量
   - 或手动指定路径：
   ```python
   import pytesseract
   pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
   ```

2. **验证码识别率低**
   
   **可能原因**：
   - 验证码图片质量差
   - Tesseract 参数未优化
   
   **解决方案**：
   - 检查保存的 `tmp.png` 文件质量
   - 调整图片预处理参数
   - 使用不同的 Tesseract 配置

3. **控件ID不匹配**
   
   **现象**：程序无法找到验证码相关控件
   
   **解决方案**：
   - 使用 Spy++ 或 UISpy 工具查看实际控件ID
   - 更新代码中的控件ID常量

### 调试技巧

1. **启用详细日志**：
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   ```

2. **检查验证码图片**：
   查看生成的 `tmp.png` 文件，确认截图是否正确

3. **手动测试OCR**：
   ```python
   from easytrader.utils.captcha import captcha_recognize
   result = captcha_recognize('tmp.png')
   print(f"识别结果: {result}")
   ```

## 性能优化

### 1. 提高识别准确率

```python
# 在 captcha.py 中优化图片预处理
def captcha_recognize(img_path):
    import pytesseract
    from PIL import Image, ImageFilter, ImageEnhance
    
    im = Image.open(img_path).convert("L")
    
    # 增强对比度
    enhancer = ImageEnhance.Contrast(im)
    im = enhancer.enhance(2.0)
    
    # 去噪
    im = im.filter(ImageFilter.MedianFilter())
    
    # 二值化
    threshold = 128
    table = []
    for i in range(256):
        if i < threshold:
            table.append(0)
        else:
            table.append(1)
    
    out = im.point(table, "1")
    
    # 使用特定配置识别
    config = '--psm 8 -c tessedit_char_whitelist=0123456789'
    num = pytesseract.image_to_string(out, config=config)
    return num.strip()
```

### 2. 缓存验证码结果

```python
# 避免重复识别相同验证码
captcha_cache = {}

def captcha_recognize_with_cache(img_path):
    import hashlib
    
    # 计算图片哈希
    with open(img_path, 'rb') as f:
        img_hash = hashlib.md5(f.read()).hexdigest()
    
    if img_hash in captcha_cache:
        return captcha_cache[img_hash]
    
    result = captcha_recognize(img_path)
    captcha_cache[img_hash] = result
    return result
```

## 安全注意事项

1. **验证码图片清理**：及时删除临时验证码图片文件
2. **日志脱敏**：确保日志中不包含敏感信息
3. **访问控制**：限制验证码识别功能的使用权限

## 贡献指南

欢迎提交 Pull Request 改进验证码功能：

1. Fork 此项目
2. 创建功能分支：`git checkout -b feature/improve-captcha`
3. 提交更改：`git commit -am 'Improve captcha recognition'`
4. 推送分支：`git push origin feature/improve-captcha`
5. 创建 Pull Request

## 联系方式

如有问题或建议，请：
- 提交 GitHub Issue
- 参考原项目的讨论区

## 许可证

本项目遵循与原 easytrader 项目相同的许可证。
