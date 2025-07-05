
import sys
import os
import json

# 导入已安装的 easytrader 模块
import easytrader

# 配置文件路径（相对于当前目录）
config_files = "config.json"

# 创建交易对象
user = easytrader.use('universal_client')

# 准备连接（使用配置文件）
user.prepare(config_files)

balance = user.balance
print(f"✅ 账户余额: {balance}")


position = user.position
print(f"✅ 仓位: {position}")