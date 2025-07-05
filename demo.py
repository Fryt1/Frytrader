
import sys
import os
import json

# 添加easytrader模块路径到Python搜索路径
current_dir = os.path.dirname(os.path.abspath(__file__))
easytrader_path = os.path.join(current_dir, 'easytrader')
if easytrader_path not in sys.path:
    sys.path.insert(0, easytrader_path)


import easytrader

config_files = "Frytrader\config.json"

user = easytrader.use('universal_client') # pyright: ignore[reportAttributeAccessIssue]

user.prepare(config_files)

balance = user.balance
print(f"✅ 账户余额: {balance}")


position = user.position
print(f"✅ 仓位: {position}")