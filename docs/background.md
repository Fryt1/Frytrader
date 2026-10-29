# 远航版任务视图后台只读模式

复用已在运行的远航版及其内置下单窗口，把窗口留在 Windows 任务视图的另一个桌面，在不抢键盘、鼠标、前台和桌面的前提下读取资金、持仓、当日委托和当日成交。

## 工作原理

三件事组合起来才成立：

1. **任务视图隔离**：把远航版窗口移动到自己创建的任务视图桌面，用户工作桌面保持不变。
2. **定向控件消息**：菜单用 `TVM_SELECTITEM`，表格复制用 `WM_COMMAND 0xE122`，不点鼠标、不模拟全局按键。
3. **线程激活保护**：查询期间向下单窗口的单一 GUI 线程临时安装一个只拦截窗口激活的钩子（`HCBT_ACTIVATE` 返回 1）。这是解决验证码弹窗自己抢前台的关键。查询结束立即卸载。

验证码用 `ControlSendText` 写码、`ControlClick NA` 提交，都没有真实键鼠输入。

**明确不做**：不使用 `SendInput`、不调用 `SetForegroundWindow`/`ShowWindow`、不切换用户桌面、不修改客户端可执行文件、不读写账户凭据。

## 已验证结果

在本机现有远航版登录态上，通过公开接口连续两轮读取资金、持仓、当日委托、当日成交，8 次全部成功：

| 项目 | 结果 |
| --- | --- |
| 读取成功率 | 8/8 |
| 桌面/前台干扰 | 0 次（`interference: false`） |
| 当前桌面 | 前后一致，始终是工作桌面 |
| 客户端状态 | 运行中，无待处理验证码 |
| 保护宿主残留 | 无 |

耗时（含验证码处理）：资金约 0.44 秒；三类表格 1.2～6.7 秒，取决于验证码重试次数（本账户表格为空，OCR 误识别会触发重试）。

**这些结论的边界**：

- 账户当期持仓、委托、成交均为空表，已验证列名与表结构，但**非空数据尚未验证**。
- 未做长时间无人值守稳定性测试。
- 仅在 x86 远航版下单窗口上验收。

## 安装

```powershell
python -m pip install -e ".[background]"
```

还需要 AutoHotkey v2（查找顺序：`ahk_path` → `FRYTRADER_AHK` → `%LOCALAPPDATA%\Frytrader\runtime\AutoHotkey64.exe` → PATH → 常规安装目录）。查询过程不会自动下载或安装软件。

原生激活保护随包分发，源码在 `easytrader/native/`，可用 `easytrader/native/build.ps1` 复现构建。加载前会校验文件 SHA256 与 PE 架构。

## 用法

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

`background=True` 仅支持 `ths`；传给其他适配器会报 `NotImplementedError`，不会静默退回前台模式。后台模式的下单、撤单、登录、IPO 等操作一律抛 `PermissionError`。

## 为什么后台模式不支持下单

交易表单**在任何写入方式下都会取得前台焦点**，这是客户端行为，不是实现缺陷。已实测排除的路径：

| 方式 | 写入 | 抢前台 |
| --- | --- | --- |
| `WM_SETTEXT` / `WM_CHAR` / `EM_REPLACESEL` | 无效 | — |
| AutoHotkey `ControlSendText` | 无效 | — |
| `AttachThreadInput` + `SetFocus` | 成功 | 是 |
| 目标线程内钩子 + 激活拦截 | 成功 | 是，且无法恢复"无活动窗口" |
| UIA `ValuePattern.SetValue` | 成功 | 是，还会切换任务视图桌面 |
| 真实键盘输入 | 成功 | 是 |

关键限制：Windows 没有公开接口把线程的活动窗口恢复成"无"。只要走激活窗口的路径，在客户端原本没有活动窗口时都会留下可观测的状态改变。

**因此下单必须使用前台模式**，即 `easytrader.use("ths")` 加 `connect()`。查询仍可保持后台免打扰。

## 退出与窗口归属

默认 `close()` **只停止后台执行器**：不取消验证码、不把同花顺搬回工作桌面。避免退出路径再动客户端。需要恢复窗口位置时显式调用：

```python
user.restore_windows()
```

或使用 `BackgroundOptions(restore_on_close=True)`。查看客户端可随时用任务视图或 `Win + Ctrl + 左/右`。

## 失败规则

- 页面列名、剪贴板序号与所有者必须匹配，否则抛异常，不会把失败当空表。
- 检测到桌面变化、前台被客户端占用、窗口句柄或 PID 变化，立即停止，不尝试把焦点抢回来。
- 验证码截图必须双底色完整覆盖且两次像素一致，否则拒绝识别，不显示、不激活窗口。
- 验证码失败超限后留在后台等人工处理，不自动取消未知弹窗。
- 原生保护宿主异常退出会立刻阻断后续控件操作。

## 已知限制

- 表格复制使用系统剪贴板，不是独立剪贴板；检测到其他程序接管剪贴板会终止读取。
- 任务视图是窗口分组，不是独立键盘环境；真正的不抢焦点依赖线程激活保护，而不是任务视图本身。
- 激活保护按线程安装，可能影响该下单线程后续的窗口激活行为；保护只在查询期间存在，查询结束即卸载。
- **后台模式不支持下单、撤单、打新和逆回购**，原因见上文。这些操作需改用前台模式。
- 账户当前持仓、委托、成交均为空表，已验证列名与表结构，但非空数据尚未验证。
- 未做长时间无人值守稳定性测试。

## 测试

```powershell
python -m unittest tests.test_background_focus tests.test_background_trader tests.test_background_windows tests.test_screenshot
```

离线测试覆盖只读限制、列名与剪贴板所有者校验、重试、干扰停止、原生文件完整性、宿主生命周期、默认退出不操作客户端、复用已有桌面、失败回滚和截图资源释放。

实机验收脚本见 `tmp/background_api_smoke.py`（只读，可重复运行）。
