import sys
import os
import json
from datetime import datetime

# 添加easytrader模块路径到Python搜索路径
current_dir = os.path.dirname(os.path.abspath(__file__))
easytrader_path = os.path.join(current_dir, 'easytrader')
if easytrader_path not in sys.path:
    sys.path.insert(0, easytrader_path)

import easytrader

def debug_captcha_recognition():
    """调试验证码识别"""
    
    # 配置文件
    config_files = "config.json"
    
    # 创建用户实例
    user = easytrader.use('universal_client')
    user.prepare(config_files)
    
    print("开始调试验证码识别...")
    print("当出现验证码时，程序会保存验证码图片到 captcha_debug 文件夹")
    
    # 创建调试文件夹
    debug_folder = "captcha_debug"
    if not os.path.exists(debug_folder):
        os.makedirs(debug_folder)
    
    try:
        # 获取账户余额（应该不会触发验证码）
        balance = user.balance
        print(f"✅ 账户余额: {balance}")
        
        # 获取仓位信息（可能触发验证码）
        print("正在获取仓位信息，可能会出现验证码...")
        position = user.position
        print(f"✅ 仓位: {position}")
        
    except Exception as e:
        print(f"❌ 错误: {e}")
        print("程序执行失败，但验证码图片可能已保存到 captcha_debug 文件夹")

if __name__ == "__main__":
    debug_captcha_recognition()
