# EasyTrader 量化## 安装

详细的安装步骤请参考：[📋 安装指南 (INSTALL.md)](./INSTALL.md) Version](https://img.shields.io/pypi/v/easytrader.svg)](https://pypi.python.org/pypi/easytrader)
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

### 快速安装

```bash
git clone https://github.com/Fryt1/Frytrader.git
cd Frytrader
pip install -e .
```

### 📋 完整安装指南

**详细的安装步骤（包含虚拟环境配置、依赖安装、常见问题等）请参考**: [INSTALL.md](./INSTALL.md)

> 💡 **强烈推荐**: 使用虚拟环境安装，避免依赖冲突！详见安装指南。

## 配置说明

1. 复制配置文件模板：
```bash
cp config.json.example config.json
```

2. 编辑 config.json 文件，将占位符替换为您的实际信息：
```json
{
  "user": "YOUR_USERNAME",
  "password": "YOUR_PASSWORD",
  "exe_path": "YOUR_TRADING_SOFTWARE_PATH",
  "comm_password": "YOUR_COMM_PASSWORD",
  "account": "YOUR_ACCOUNT_NUMBER",
  "portfolio": "default",
  "captcha": {
    "tesseract_path": "C:/Program Files/Tesseract-OCR/tesseract.exe",
    "retry_times": 3,
    "manual_input": true,
    "recognition_config": {
      "whitelist": "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
      "psm": 8,
      "oem": 3
    }
  }
}
}
```

## 快速开始

### 运行示例

项目包含一个 `demo.py` 示例文件，展示了基本用法：

```python
import easytrader

# 配置文件路径
config_files = "config.json"

# 创建交易对象
user = easytrader.use('universal_client')

# 准备连接（使用配置文件）
user.prepare(config_files)

# 查询账户信息
balance = user.balance
print(f"✅ 账户余额: {balance}")

position = user.position
print(f"✅ 仓位: {position}")
```

### 基本交易操作

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
