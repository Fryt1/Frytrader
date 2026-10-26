# 安装

### 同花顺客户端设置

需要对客户端按以下设置，不然会导致下单时价格出错以及客户端超时锁定

* 系统设置 > 界面设置: 界面不操作超时时间设为 0
* 系统设置 > 交易设置: 默认买入价格/买入数量/卖出价格/卖出数量 都设置为 空

同时客户端不能最小化也不能处于精简模式

### 云端部署建议

在云服务上部署时，使用自带的远程桌面会有问题，推荐使用 TightVNC

### 登陆时的验证码识别

券商如果登陆需要识别验证码的话需要安装 tesseract：

* `tesseract` : 非 `pytesseract`, 需要单独安装, [地址](https://github.com/tesseract-ocr/tesseract/wiki),保证在命令行下 `tesseract` 可用

或者你也可以手动登陆后在通过 `easytrader` 调用，此时 `easytrader` 在登陆过程中会直接识别到已登陆的窗口。

### 安装本 fork

从 Frytrader 仓库根目录运行：

```shell
python -m pip install -e .
```

不要同时安装 PyPI 上的原版 `easytrader`，两者都会提供 `easytrader` 导入包。验证码识别还需要单独安装 Tesseract OCR；配置方式见仓库根目录的 `config.json.example`。

### MiniQMT 可选依赖

```shell
python -m pip install -e ".[miniqmt]"
```

这只安装 Python 依赖，不会授予券商权限或解除券商限制。

