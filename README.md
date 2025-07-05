# EasyTrader 3.0

这是基于原版 easytrader 的修改版本，专注于股票量化交易。

## 功能特性

- 进行股票量化交易
- 通用的同花顺客户端模拟操作
- 支持券商的 miniqmt 官方量化接口
- 支持雪球组合调仓和跟踪
- 支持远程操作客户端
- 支持跟踪 joinquant, ricequant 的模拟交易
- 增强的验证码识别功能

## 项目结构

```
easytrader3.0/
├── easytrader/           # 主要的 easytrader 库
├── captcha_debug/        # 验证码调试文件
├── config.json          # 配置文件
├── demo.py              # 演示文件
├── debug_captcha.py     # 验证码调试脚本
└── test_captcha.py      # 验证码测试脚本
```

## 安装和使用

### 安装依赖

```bash
pip install -r easytrader/requirements.txt
```

### 验证码功能

如果需要使用验证码识别功能：

```bash
pip install -r easytrader/requirements-captcha.txt
```

### 基本使用

```python
import easytrader

# 创建交易客户端
trader = easytrader.use('ht')
trader.prepare('config.json')

# 进行交易操作
# ...
```

## 配置

复制 `easytrader/config.json.example` 到 `config.json` 并填入你的配置信息。

## 文档

更详细的使用文档请参考 [easytrader 文档](https://easytrader.readthedocs.io/)

## 许可证

请查看 `easytrader/LICENSE` 文件了解许可证信息。

## 贡献

欢迎提交 Issues 和 Pull Requests。

## 免责声明

本项目仅供学习和研究使用，使用本项目进行实际交易产生的任何损失，作者不承担任何责任。请在使用前充分了解相关风险。
