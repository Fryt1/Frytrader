# Frytrader

[![standard-readme compliant](https://img.shields.io/badge/readme%20style-standard-brightgreen.svg?style=flat-square)](https://github.com/RichardLitt/standard-readme)

低频 GUI 交易适配器，增强验证码识别与表格导出。

Frytrader 是 EasyTrader 的 fork，保留上游适配器和 `import easytrader` 接口。仓库名为 `Frytrader`，发行包和 Python 导入名仍为 `easytrader`，这是为了兼容现有项目代码；请从本仓库安装，而不是安装 PyPI 上游版。

## 目录

- [安全](#安全)
- [背景](#背景)
- [安装](#安装)
- [用法](#用法)
- [配置](#配置)
- [测试](#测试)
- [API](#api)
- [维护者](#维护者)
- [致谢](#致谢)
- [如何贡献](#如何贡献)
- [许可证](#许可证)

## 安全

此库可以驱动真实券商客户端并提交真实委托。不要把账号凭证提交到 Git 或 Issue；不要把未鉴权的远程交易服务暴露到公网。GUI 自动化可能遇到弹窗、客户端升级、焦点丢失或结果不明；生产使用前必须验证委托状态，不能仅凭点击返回判断成功，也不要在结果未知时盲目重试。

## 背景

EasyTrader 提供多种券商 GUI、雪球和 MiniQMT 适配器。Frytrader 保留上游交易核心，并为通用 Windows GUI 客户端增强验证码识别、XLS 导出等待和失败诊断；包名仍为 `easytrader` 以兼容现有项目。

本项目不是券商原生 API。GUI 路线适合低频操作，不适合高频交易。MiniQMT Python 依赖可选安装，但安装依赖不授予券商权限，也不会解除券商端限制。

## 安装

需要 Python 3.8 或更高版本。GUI 客户端自动化需要 Windows。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

验证码识别需要另行安装 Tesseract OCR，并让 `tesseract.exe` 可从 `PATH` 访问，或在配置中指定路径。安装可选 MiniQMT 依赖：

```powershell
python -m pip install -e ".[miniqmt]"
```

## 用法

```python
import easytrader

user = easytrader.use("universal_client")
user.prepare(config_path="config.json")

print(user.balance)
print(user.position)
```

支持的客户端类型、登录方式和交易方法见 [`docs/usage.md`](docs/usage.md)。示例只查询账户；真实买卖操作需要明确指定账户、标的、价格和数量，并核对客户端回报。

Windows 远航版支持任务视图后台只读模式：`easytrader.use("ths", background=True)`，在不抢键盘、鼠标和前台的前提下读取资金、持仓、委托与成交。已在本机登录态上连续两轮 8/8 读取成功、零桌面干扰；非空表格与长时间稳定性尚未验证。用法、限制与依赖见 [`docs/background.md`](docs/background.md)。此前的[底层独立桌面实验](docs/desktop_control.md)仍保留，未作为选定方案。

## 配置

复制 [`config.json.example`](config.json.example) 为 `config.json`，填写账号、密码和客户端路径。`config.json` 已加入 `.gitignore`。

验证码选项位于 `captcha` 对象：

- `mode`：`fast`、`balanced` 或 `accurate`
- `manual_input`：OCR 失败后是否等待交互式手动输入；无人值守时应保持 `false`
- `save_debug_samples`：是否将验证码样本保存在本地 `captcha_debug` 目录，默认关闭
- `tesseract_path`：Tesseract 可执行文件路径；`null` 表示使用系统 `PATH`

该设置只影响通用 GUI 客户端的 XLS 表格读取器，不更改券商验证，也不赋予交易权限。

## 测试

运行离线单元测试：

```powershell
python -m unittest tests.test_xqtrader
```

`tests/test_easytrader.py` 中的客户端集成测试受 `EZ_TEST_CLIENTS` 环境变量控制，可能连接真实账户。未配置隔离测试账户时不要启用。验证码识别尚未以真实券商截图建立准确率基准。

## API

入口为 `easytrader.use(broker)`，账户对象提供 `balance`、`position`、`today_entrusts`、`today_trades` 等查询属性，以及上游定义的 `buy`、`sell`、撤单和其他适配器方法。详见 [`docs/usage.md`](docs/usage.md) 和 [`docs/miniqmt.md`](docs/miniqmt.md)。

## 维护者

[Fryt1](https://github.com/Fryt1)。问题请提交到 [Frytrader Issues](https://github.com/Fryt1/Frytrader/issues)。

## 致谢

感谢 [shidenggui/EasyTrader](https://github.com/shidenggui/easytrader) 及其贡献者提供上游项目和交易适配器。

## 如何贡献

欢迎提交 Issue 和 Pull Request。请先说明券商客户端和复现步骤；日志、配置和截图必须移除账号、密码、资金及其他敏感信息。涉及 GUI 或 OCR 的改动应提供脱离真实账户的测试；不要用真实委托作为 CI 测试。

## 许可证

[MIT](LICENSE)。原始上游版权声明为 © 2018 shidenggui，详见 [`LICENSE`](LICENSE)。
