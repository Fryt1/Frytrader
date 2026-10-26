# Frytrader 安装指南

Frytrader 是 EasyTrader 的 fork，Python 导入名仍为 `easytrader`。请从本仓库安装，不要把 PyPI 原版与此 fork 混装。

## 系统要求

- Python 3.8 或更高版本
- Windows 10 或更高版本，用于 pywinauto GUI 客户端
- 已安装并可手动登录的受支持券商客户端

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

验证码识别需要单独安装 Tesseract OCR，并确保 `tesseract.exe` 在 `PATH` 中，或在 `config.json` 的 `captcha.tesseract_path` 指定路径。

## 配置

复制 `config.json.example` 为 `config.json`，填写账号与客户端路径。不要提交含真实账号或密码的配置文件。

`captcha.mode` 可用 `fast`、`balanced`、`accurate`；`manual_input` 只适用于有人值守的交互终端；`save_debug_samples` 默认关闭，开启后会把验证码图片保存在本地 `captcha_debug` 目录。

MiniQMT 的 Python 依赖可通过 `python -m pip install -e ".[miniqmt]"` 安装，但这不会授予券商权限，也不会解除券商端限制。

## 测试

可以运行离线单元测试：

```powershell
python -m unittest tests.test_xqtrader
```

`tests/test_easytrader.py` 包含环境变量启用的券商客户端集成测试，可能连接真实账户；没有明确配置隔离测试账户时不要启用。
