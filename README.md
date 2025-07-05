# FryTrader 量化交易平台

> 🚀 **基于 EasyTrader 改进版本** - 增强了验证码自动识别功能，提供更稳定的自动化交易体验

[![PyPI Version](https://img.shields.io/pypi/v/easytrader.svg)](https://pypi.python.org/pypi/easytrader)
[![Python Version](https://img.shields.io/pypi/pyversions/easytrader.svg)](https://pypi.python.org/pypi/easytrader)
[![License](https://img.shields.io/github/license/shidenggui/easytrader.svg)](https://github.com/shidenggui/easytrader/blob/master/LICENSE)

## 关于 FryTrader

FryTrader 是基于知名开源项目 [EasyTrader](https://github.com/shidenggui/easytrader) 的改进版本，专注于提升验证码处理能力和交易稳定性。

### 📈 相比原版 EasyTrader 的改进

- ✅ **增强验证码识别** - 集成 Tesseract-OCR 引擎，支持智能验证码识别
- ✅ **自动重试机制** - 验证码识别失败时自动重试，提高成功率
- ✅ **手动备选方案** - 自动识别失败时可切换到手动输入
- ✅ **配置化管理** - 验证码相关参数可通过配置文件灵活调整
- ✅ **调试日志优化** - 增加详细的验证码处理日志，便于问题排查

## 功能特性

- 支持多种券商客户端自动化交易
- 雪球组合调仓和跟踪
- 支持券商官方量化接口(miniQMT)
- 远程操作客户端
- **🆕 智能验证码自动识别** (FryTrader 新增功能)
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
```

## 快速开始

### 使用 demo.py 测试

项目提供了一个完整的测试示例 `demo.py`，可以帮助您快速验证配置和连接：

#### 运行步骤

1. **配置文件准备**
   ```bash
   cp config.json.example config.json
   # 编辑 config.json 填入您的实际配置信息
   ```

2. **运行测试脚本**
   ```bash
   python demo.py
   ```

#### demo.py 代码说明

```python
import sys
import os
import json

# 导入 FryTrader (基于 easytrader 的增强版)
import easytrader

# 配置文件路径（相对于当前目录）
config_files = "config.json"

# 创建交易对象
user = easytrader.use('universal_client')

# 准备连接（使用配置文件）
user.prepare(config_files)

# 查询账户余额
balance = user.balance
print(f"✅ 账户余额: {balance}")

# 查询持仓信息
position = user.position
print(f"✅ 仓位: {position}")
```

这个示例展示了：
- ✅ 如何导入 FryTrader 模块 (基于 easytrader 增强版)
- ✅ 如何使用配置文件连接交易客户端
- ✅ 如何查询账户余额和持仓信息
- ✅ 基本的错误处理和信息输出
- ✅ 自动验证码处理 (FryTrader 增强功能)

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

### 🔐 智能验证码功能 (FryTrader 核心特性)

FryTrader 的验证码功能是在原版 EasyTrader 基础上全新开发的核心特性，支持同花顺等客户端的自动化交易：

#### ✨ 核心功能
1. **自动检测** - 智能检测包含"验证码"文字的窗口
2. **图片截取** - 自动截取验证码图片并保存调试信息
3. **智能识别** - 使用 Tesseract-OCR 引擎进行文字识别
4. **自动输入** - 将识别结果自动输入到验证码输入框
5. **重试机制** - 识别失败时自动重试，最多重试 5 次
6. **手动备选** - 自动识别失败时可切换到手动输入模式

#### 🛠️ 环境准备
- 安装 Tesseract-OCR (Windows: https://github.com/UB-Mannheim/tesseract/wiki)
- 确保 Tesseract 路径在系统 PATH 中或在配置文件中指定路径

#### ⚙️ 技术细节
- **验证码图片控件ID**: `0x965`
- **验证码输入框控件ID**: `0x964`
- **识别引擎**: Tesseract-OCR with custom configuration
- **重试策略**: 最多 5 次，每次间隔 1 秒
- **调试功能**: 自动保存验证码图片到 `captcha_debug/` 目录

#### 🔧 配置选项
通过 `config.json` 中的 `captcha` 部分可以自定义验证码处理参数：
```json
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
```

#### 🐛 故障排除
1. **Tesseract 未找到**
   - 确保已安装 Tesseract-OCR
   - 检查 PATH 环境变量或配置文件中的路径
2. **验证码识别失败**
   - 检查 `captcha_debug/` 目录中保存的验证码图片质量
   - 尝试调整 `recognition_config` 中的参数
   - 启用 `manual_input` 作为备选方案

### 雪球组合跟踪

```python
follower = easytrader.follower('xq')
follower.login(user='username', password='password')
follower.follow('组合URL', total_assets=100000)
```

## 📚 文档

- **FryTrader 文档**: 详细文档请参考项目 Wiki 页面
- **原版 EasyTrader 文档**: [https://easytrader.readthedocs.io](https://easytrader.readthedocs.io)

## 🤝 问题反馈

- **FryTrader 相关问题**: 请在本项目提交 issue
- **原版 EasyTrader 问题**: [https://github.com/shidenggui/easytrader/issues](https://github.com/shidenggui/easytrader/issues)

## 📄 许可证

本项目基于原版 EasyTrader 开发，遵循相同的开源许可证。

## 🙏 致谢

感谢 [shidenggui/easytrader](https://github.com/shidenggui/easytrader) 项目提供的优秀基础框架，FryTrader 在此基础上专注于验证码处理的优化和改进。
