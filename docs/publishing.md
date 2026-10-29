# 发布流程

本项目发布到 [PyPI](https://pypi.org/project/frytrader/)，发行名为 `frytrader`，Python 导入名保持 `easytrader` 不变。

## 前提

- 拥有该 PyPI 项目的维护权限
- 安装了构建与上传工具：

```powershell
python -m pip install --upgrade build twine
```

## 一、确定版本号

PyPI **不允许覆盖已发布的版本**，任何改动都必须递增版本号。版本号出现在两个地方，必须同步修改：

| 文件 | 字段 |
| --- | --- |
| `setup.py` | `version="x.y.z"` |
| `easytrader/__init__.py` | `__version__ = "x.y.z"` |

`.bumpversion.cfg` 里已配置好这两个文件，装了 `bumpversion` 时可用：

```powershell
bumpversion patch   # 0.1.0 -> 0.1.1
bumpversion minor   # 0.1.0 -> 0.2.0
```

手工改也可以，但**两处必须一致**，否则会出现元数据与运行时版本不符。

版本号语义建议：

- `patch`：bug 修复、文档修正
- `minor`：新增功能（例如新的适配器或后台模式增强）
- `major`：破坏性接口变更

## 二、发布前检查

```powershell
# 1. 离线测试必须全过
python -m unittest discover -s tests -p "test_*.py"

# 2. 清理旧产物
Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue

# 3. 重新构建
python -m build

# 4. 校验元数据与 README 渲染
python -m twine check dist/*
```

`twine check` 必须显示 `PASSED`。常见失败原因是 README 里有 PyPI 不支持的 HTML 或相对图片链接。

确认 wheel 内含原生运行时（后台模式依赖）：

```powershell
python -c "import zipfile,glob; z=zipfile.ZipFile(glob.glob('dist/*.whl')[0]); print([n for n in z.namelist() if 'native/bin' in n])"
```

应至少包含 `focus_guard32.dll` 和 `focus_host32.exe`。若为空，检查 `setup.py` 的 `package_data` 与 `MANIFEST.in`。

## 三、上传

**先在 TestPyPI 试装更稳**（可选，但推荐首次或改动较大时使用）：

```powershell
python -m twine upload --repository testpypi dist/*
python -m pip install --index-url https://test.pypi.org/simple/ frytrader
```

正式上传：

```powershell
python -m twine upload dist/*
```

运行时按提示输入：

- Username: `__token__`
- Password: 你的 PyPI API token

### 关于 token

- 生成地址：<https://pypi.org/manage/account/token/>
- 首次发布某个新项目时，token 的 scope 必须选 **Entire account**；项目创建成功后可以换成项目级 token，权限更小。
- **不要**把 token 写进仓库、脚本或聊天记录。若曾泄露，立即到上面的地址吊销并重新生成。
- 可用环境变量避免交互输入：

```powershell
$env:TWINE_USERNAME = '__token__'
$env:TWINE_PASSWORD = '<token>'
python -m twine upload dist/*
$env:TWINE_USERNAME = $null
$env:TWINE_PASSWORD = $null
```

也可写入 `%USERPROFILE%\.pypirc`，但注意保护该文件权限。

## 四、上传后验证

```powershell
# PyPI 是否已收录
python -m pip index versions frytrader

# 在干净环境实装验证
python -m venv C:\Temp\ft-verify
C:\Temp\ft-verify\Scripts\python -m pip install frytrader
C:\Temp\ft-verify\Scripts\python -c "import easytrader; print(easytrader.__version__)"
Remove-Item -Recurse -Force C:\Temp\ft-verify
```

PyPI 新版本在镜像源上有同步延迟（通常几分钟），验证时加 `--index-url https://pypi.org/simple` 可绕过镜像缓存。

## 五、提交与打标签

```powershell
git add -A
git commit -m "chore: release v0.1.1"
git tag v0.1.1
git push origin easytrader-fork
git push origin v0.1.1
```

## 常见问题

**403 Invalid or non-existent authentication information**

token 无效、已吊销、复制不完整，或用户名不是 `__token__`。先确认用户名，再重新生成 token。

**403 The user is not allowed to upload to project**

token 的 scope 不对。新项目必须用 Entire account；已有项目要用包含该项目权限的 token。

**400 File already exists**

同版本号已发布过。递增版本号后重新构建，不能覆盖。

**上传成功但 `pip install` 找不到**

镜像源缓存延迟。等待几分钟，或临时用 `--index-url https://pypi.org/simple`。

**`import easytrader` 装到了上游版本**

PyPI 上游存在同名包 `easytrader`。本项目只以 `frytrader` 发行，先 `pip uninstall easytrader frytrader` 再只安装 `frytrader`。

## 许可合规

本项目基于 MIT 许可证的上游 EasyTrader。发布时必须保留 `LICENSE` 中的原始版权声明（`Copyright (c) 2018 shidenggui`），并在 README/PyPI 描述中说明这是 fork。修改这两处前请先确认合规。
