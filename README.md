# EasyTrader 量化交易工具

[![PyPI Version](https://img.shields.io/pypi/v/easytrader.svg)](https://pypi.python.org/pypi/easytrader)
[![Python Version](https://img.shields.io/pypi/pyversions/easytrader.svg)](https://pypi.python.org/pypi/easytrader)
[![License](https://img.shields.io/github/license/shidenggui/easytrader.svg)](https://github.com/shidenggui/easytrader/blob/master/LICENSE)

## 功能特性

- 支持多种券商客户端自动化交易
- 雪球组合调仓和跟踪
- 支持券商官方量化接口(miniQMT)
- 远程操作客户端
- 自动验证码识别
- 支持跟踪JoinQuant/RiceQuant模拟交易

## 安装

### 开发者安装
```bash
git clone https://github.com/Fryt1/Frytrader.git
cd Frytrader
pip install -e .
```

### 普通用户安装
```bash
pip install git+https://github.com/Fryt1/Frytrader.git
```

### 功能扩展安装

```bash
# 安装miniQMT支持
pip install easytrader[all]

# 基础安装已包含验证码等核心功能
pip install .
```

## 快速开始

### 基本使用

```python
import easytrader

# 创建交易对象
user = easytrader.use('ths')  # 同花顺客户端
user.connect(r'C:\xiadan.exe')  # 客户端路径

# 查询账户
print(user.balance)
print(user.position)

# 交易操作
user.buy('601318', price=50, amount=100)  # 买入
user.sell('601318', price=55, amount=100) # 卖出
```

### 验证码功能

验证码功能已默认集成，支持同花顺客户端自动化交易：

1. **自动检测**: 检测包含"验证码"文字的窗口
2. **图片截取**: 自动截取验证码图片
3. **智能识别**: 使用 Tesseract-OCR 识别验证码
4. **自动输入**: 将识别结果输入到验证码输入框
5. **重试机制**: 失败时最多重试 5 次

#### 环境准备
- 安装 Tesseract-OCR (Windows: https://github.com/UB-Mannheim/tesseract/wiki)
- 确保 Tesseract 路径在系统 PATH 中

#### 技术细节
- 验证码图片控件ID: `0x965`
- 验证码输入框控件ID: `0x964`
- 识别引擎: Tesseract-OCR
- 重试次数: 最多 5 次
- 超时设置: 每次识别超时 1 秒

#### 故障排除
1. **Tesseract 未找到**
   - 确保已安装 Tesseract-OCR
   - 检查 PATH 环境变量
2. **验证码识别失败**
   - 检查图片质量
   - 尝试调整 Tesseract 参数

### 雪球组合跟踪

```python
follower = easytrader.follower('xq')
follower.login(user='username', password='password')
follower.follow('组合URL', total_assets=100000)
```

## 文档

详细文档请参考: [https://easytrader.readthedocs.io](https://easytrader.readthedocs.io)

## 问题反馈

如有问题请提交issue: [https://github.com/shidenggui/easytrader/issues](https://github.com/shidenggui/easytrader/issues)
