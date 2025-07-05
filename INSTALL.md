# Frytrader 安装指南

## 重要说明

**Frytrader 是基于原版 easytrader 的增强版本，请不要使用 `pip install easytrader` 安装原版，必须从源码安装本项目。**

## 系统要求

- Python 3.6 或更高版本
- Windows 10 或更高版本（推荐）
- 支持的券商客户端（同花顺、国金证券等）

## 安装步骤

### 1. 克隆项目

```bash
git clone https://github.com/Fryt1/Frytrader.git
cd Frytrader
```

### 2. 安装 Python 依赖

```bash
# 升级 pip 到最新版本
python -m pip install --upgrade pip

# 安装 setuptools 和 wheel
pip install setuptools wheel

# 以开发模式安装项目
pip install -e .
```

### 3. 安装 Tesseract-OCR（验证码识别）

#### Windows:
1. 下载 Tesseract-OCR：https://github.com/UB-Mannheim/tesseract/wiki
2. 安装到默认路径：`C:\Program Files\Tesseract-OCR\`
3. 将 Tesseract 路径添加到系统 PATH 环境变量

#### 验证安装:
```bash
tesseract --version
```

### 4. 配置文件设置

1. 复制配置模板：
```bash
cp config.json.example config.json
```

2. 编辑 `config.json` 文件，替换以下占位符：
   - `YOUR_USERNAME`: 您的券商账号
   - `YOUR_PASSWORD`: 您的密码
   - `YOUR_TRADING_SOFTWARE_PATH`: 交易软件路径（如：`E:/tonghuashun/同花顺/xiadan.exe`）
   - `YOUR_COMM_PASSWORD`: 通讯密码
   - `YOUR_ACCOUNT_NUMBER`: 账户号码

### 5. 运行测试

```bash
python demo.py
```

## 常见问题

### Q1: 安装时出现 "FileNotFoundError: No such file or directory: '../README.md'"

**解决方案**: 这个问题已在最新版本中修复。请确保使用最新的代码。

### Q2: 安装时出现 "package directory 'easytrader' does not exist"

**解决方案**: 这个问题已在最新版本中修复，setup.py 已正确配置包路径。

### Q3: 找不到 Tesseract-OCR

**解决方案**: 
1. 确保已正确安装 Tesseract-OCR
2. 检查 PATH 环境变量
3. 在配置文件中指定正确的 tesseract_path

### Q4: 验证码识别失败

**解决方案**:
1. 检查 Tesseract-OCR 安装
2. 尝试手动输入模式：设置 `"manual_input": true`
3. 调整识别参数

### Q5: 连接券商客户端失败

**解决方案**:
1. 确保券商客户端已正常启动
2. 检查客户端路径是否正确
3. 确认账号密码正确
4. 尝试手动登录一次

## 支持的券商

- 同花顺
- 国金证券
- 华泰证券
- 银河证券
- 海通证券
- 万科证券
- 雅虎香港

## 获取帮助

如果遇到问题，请：

1. 查看本安装指南
2. 查看项目 README.md
3. 提交 Issue：https://github.com/Fryt1/Frytrader/issues

## 版本历史

- v0.24.0: 修复安装问题，优化包结构
- 更多版本信息请查看 CHANGELOG.md
