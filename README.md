# Frytrader

[![standard-readme compliant](https://img.shields.io/badge/readme%20style-standard-brightgreen.svg?style=flat-square)](https://github.com/RichardLitt/standard-readme)

中国券商 GUI 交易适配器，支持同花顺远航版隐藏窗口只读查询（验证码按需前台）。

Frytrader 是 [EasyTrader](https://github.com/shidenggui/easytrader) 的 fork，在保留上游交易核心和 `import easytrader` 导入名的前提下，增强了验证码识别、表格导出和后台只读能力。发行包名为 `frytrader`，与 PyPI 上游的 `easytrader` 区分；Python 导入名仍为 `easytrader`，已有代码无需修改。

## Table of Contents

- [安全](#安全)
- [背景](#背景)
- [安装](#安装)
- [用法](#用法)
- [API](#api)
- [配置](#配置)
- [测试](#测试)
- [维护者](#维护者)
- [致谢](#致谢)
- [贡献](#贡献)
- [许可证](#许可证)

## 安全

**本库可以驱动真实券商客户端并提交真实委托。误用会造成资金损失。**

- 不要将账号、密码、验证码截图或其他凭证提交到 Git、Issue 或日志。
- 不要把交易服务暴露到公网。仓库内的 `easytrader/server.py` **无鉴权**且提供 `/buy`、`/sell`、`/cancel_entrust`，默认监听 `0.0.0.0`，不要直接对外运行。
- GUI 自动化会遇到弹窗、客户端升级、焦点丢失和结果不明。下单前必须自行验证委托状态，不能仅凭界面返回判断成功，**结果未知时不要盲目重试**。
- 本软件按 MIT 许可证「按原样」提供，不附带任何担保。交易决策和损失由使用者自行承担。

## 背景

EasyTrader 提供多种券商 GUI、雪球和 MiniQMT 适配器。Frytrader 保留上游交易核心，并针对通用 Windows GUI 客户端做了三类增强：

1. **验证码识别**：随包提供同花顺专用 ONNX 模型，支持大小写字母与数字。
2. **表格导出**：修正空行丢弃、陈旧数据判断和失败诊断。
3. **隐藏窗口只读查询**：普通查询通过定向控件消息完成；验证码输入与确认可以临时占前台，结束后收起窗口并归还焦点。不再创建或切换任务视图桌面。

本项目不是券商原生 API。GUI 路线适合低频操作，不适合高频交易。MiniQMT 是可选的另一条路径，安装 Python 依赖不授予券商权限，也不会解除券商端限制。

## 安装

需要 Python 3.8 或更高。GUI 客户端自动化仅在 Windows 上可用。

从 PyPI 安装：

```console
$ pip install frytrader
```

从本仓库安装开发版：

```console
$ git clone https://github.com/Fryt1/Frytrader.git
$ cd Frytrader
$ python -m venv .venv
$ .\.venv\Scripts\Activate.ps1
$ python -m pip install -e .
```

后台模式使用主依赖，以下旧 extra 命令也可兼容使用：

```console
$ python -m pip install -e ".[background]"
```

安装可选 MiniQMT 依赖：

```console
$ python -m pip install -e ".[miniqmt]"
```

同花顺查询验证码使用随包 ONNX 模型，不需要 Tesseract。其他历史券商识别入口若使用 Tesseract，仍需单独安装并配置路径。

隐藏查询不需要 pyvda、AutoHotkey 或额外桌面；不在运行时自动下载或安装软件。

## 用法

查询账户：

```python
import easytrader

user = easytrader.use("universal_client")
user.prepare(config_path="config.json")

print(user.balance)
print(user.position)
```

同花顺远航版隐藏窗口只读查询（验证码允许临时前台）：

```python
import easytrader

user = easytrader.use("ths", background=True)
try:
    user.connect()          # 连接已登录的远航版，不启动新客户端
    print(user.balance)
    print(user.position)
    print(user.today_entrusts)
    print(user.today_trades)
finally:
    user.close()
```

本次同桌面只读验收四项均成功：资金 5 项、持仓空表、当日委托 16 条、当日成交空表；三个验证码均首次通过。资金约 0.55 秒，其余查询约 2.17–2.94 秒。查询结束主窗口隐藏、验证码已关闭、焦点归还。详见 [`docs/background.md`](docs/background.md)。

**尚未验证**：非空持仓/成交数据、长时间无人值守稳定性、非 x86 客户端、非 Windows 平台。通过查询测试不等于所有交易功能已验收。

**后台模式不支持交易**。买入、卖出、撤单、打新和逆回购需改用前台模式 `easytrader.use("ths")`，因为同花顺交易表单在写入时会取得前台焦点，这是客户端行为。查询对象与前台交易对象分别使用，不应把查询通过当成交易验收。

## API

入口为 `easytrader.use(broker)`。账户对象提供 `balance`、`position`、`today_entrusts`、`today_trades` 等查询属性，以及上游定义的 `buy`、`sell`、撤单等方法。

支持的客户端类型和交易方法见 [`docs/usage.md`](docs/usage.md)，MiniQMT 见 [`docs/miniqmt.md`](docs/miniqmt.md)。

后台只读模式限制：

- `background=True` 仅支持 `ths`。传给其他适配器会抛 `NotImplementedError`，不会静默退回前台模式。
- 后台模式不继承下单能力，`buy`、`sell`、`cancel_entrust`、`prepare`、`login`、`auto_ipo` 等一律抛 `PermissionError`。
- 默认 `close()` 停止查询执行器并保持主窗口隐藏；查询失败只清理本次创建的验证码。显式调用 `user.restore_windows()` 可显示原本可见的主窗口，不操作桌面。
- 默认 `allow_foreground_captcha=True`；设为 `False` 时查询遇验证码就失败，不发送前台输入。客户端自行显示的验证码仍可能短暂可见。

## 配置

复制 [`config.json.example`](config.json.example) 为 `config.json` 并填写账号、密码和客户端路径。`config.json` 已加入 `.gitignore`。

验证码选项位于 `captcha` 对象：

- `mode`：`fast`、`balanced` 或 `accurate`
- `manual_input`：OCR 失败后是否等待手动输入；无人值守时应保持 `false`
- `save_debug_samples`：是否把样本保存到本地 `captcha_debug` 目录，默认关闭
- `tesseract_path`：Tesseract 可执行文件路径；`null` 表示使用系统 `PATH`

这些选项只影响通用 GUI 客户端的 XLS 表格读取器，不改变券商端校验，也不赋予任何交易权限。

## 测试

运行离线测试（不连接真实账户）：

```console
$ python -m unittest discover -s tests -p "test_*.py"
```

`tests/test_easytrader.py` 中的集成测试受 `EZ_TEST_CLIENTS` 环境变量控制，会连接真实账户。没有隔离测试账户时不要启用。不要把真实委托作为 CI 测试。

发布新版本见 [`docs/publishing.md`](docs/publishing.md)。

## 维护者

[Fryt1](https://github.com/Fryt1)。问题请提交到 [Issues](https://github.com/Fryt1/Frytrader/issues)。

## 致谢

感谢 [shidenggui](https://github.com/shidenggui) 及 [EasyTrader](https://github.com/shidenggui/easytrader) 的所有贡献者提供上游项目和交易适配器。Frytrader 的发行基于其 MIT 许可证条款，原始版权声明保留在 [`LICENSE`](LICENSE) 中。

## 贡献

欢迎提交 Issue 和 Pull Request。提交前请：

1. 说明使用的券商客户端版本和复现步骤。
2. 移除日志、配置和截图中的账号、密码、资金等敏感信息。
3. 涉及 GUI 或 OCR 的改动附带脱离真实账户的测试。

### Contributors

<a href="https://github.com/Fryt1/Frytrader/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Fryt1/Frytrader" alt="Contributors" />
</a>

## 许可证

[MIT](LICENSE) © 2018 shidenggui, © 2024-2026 Fryt1。
