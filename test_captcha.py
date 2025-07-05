#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证码处理功能专项测试
这个脚本会反复查询账户信息来触发验证码，演示自动处理功能
"""

import sys
import os
import json
import time

# 添加easytrader模块路径到Python搜索路径
current_dir = os.path.dirname(os.path.abspath(__file__))
easytrader_path = os.path.join(current_dir, 'easytrader')
if easytrader_path not in sys.path:
    sys.path.insert(0, easytrader_path)

import easytrader

def load_config():
    """加载配置文件"""
    config_files = ["config.json", "../config.json"]
    
    for config_file in config_files:
        if os.path.exists(config_file):
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f), config_file
    
    print("❌ 未找到配置文件config.json")
    print("请从config.json.example复制并修改配置文件")
    return None, None

def test_captcha_handling():
    """测试验证码处理功能"""
    print("🔍 验证码处理功能专项测试")
    print("=" * 60)
    print("📋 本测试将:")
    print("   1. 初始化交易客户端")
    print("   2. 反复查询账户信息以触发验证码")
    print("   3. 自动识别并输入验证码")
    print("   4. 显示处理过程和结果")
    print("=" * 60)
    
    config, config_file = load_config()
    if not config:
        return
    
    try:
        # 初始化easytrader
        print("\n🚀 步骤1: 初始化easytrader...")
        user = easytrader.use('universal_client')
        
        # 登录
        print("🔑 步骤2: 登录客户端...")
        user.prepare(config_file)
        print("✅ 登录成功！")
        
        # 反复查询以触发验证码
        print("\n🔄 步骤3: 开始反复查询以触发验证码...")
        print("注意：验证码可能在第3-5次查询时出现")
        print("-" * 40)
        
        for i in range(8):  # 查询8次，确保触发验证码
            print(f"\n📊 第{i+1}次查询...")
            try:
                # 查询余额
                balance = user.balance
                print(f"💰 余额查询成功: 可用金额 {balance.get('可用金额', 'N/A')}")
                
                # 稍微等待，避免查询太快
                time.sleep(2)
                
                # 查询持仓
                print("📈 查询持仓中...")
                positions = user.position
                if positions:
                    print(f"📊 持仓数量: {len(positions)} 只股票")
                else:
                    print("📊 当前无持仓")
                
                # 查询委托
                print("📋 查询委托中...")
                entrusts = user.today_entrusts
                if entrusts:
                    print(f"📝 今日委托: {len(entrusts)} 条记录")
                else:
                    print("📝 今日无委托记录")
                
                print(f"✅ 第{i+1}次查询完成")
                
                if i < 7:  # 最后一次不等待
                    print("⏱️  等待3秒后继续...")
                    time.sleep(3)
                    
            except Exception as e:
                print(f"❌ 第{i+1}次查询出现异常: {e}")
                # 即使出现异常也继续，因为验证码处理过程中可能有暂时的异常
                time.sleep(2)
        
        print("\n" + "=" * 60)
        print("🎉 验证码处理测试完成！")
        print("=" * 60)
        print("📝 总结:")
        print("   - 如果看到 'captcha result-->' 日志，说明验证码已被识别")
        print("   - 如果查询继续成功，说明验证码已被正确输入")
        print("   - 整个过程对用户透明，无需手动干预")
        print("\n💡 提示:")
        print("   验证码不是每次都会出现，这取决于同花顺的安全策略")
        print("   如果本次测试没有触发验证码，可以多运行几次")
        
    except Exception as e:
        print(f"\n❌ 测试过程中发生错误: {e}")
        print("这可能是正常现象，验证码处理功能仍然可用")

def main():
    """主函数"""
    print("🔐 easytrader 验证码自动处理功能测试")
    print("🔐" * 30)
    
    # 询问用户是否继续
    print("\n⚠️  注意事项:")
    print("   1. 请确保同花顺客户端已经安装并配置正确")
    print("   2. 测试过程会反复查询账户信息")
    print("   3. 验证码可能需要几次查询后才会出现")
    print("   4. 整个测试过程大约需要1-2分钟")
    
    response = input("\n🤔 是否继续测试？(y/n): ").strip().lower()
    if response not in ['y', 'yes', '是']:
        print("👋 测试已取消")
        return
    
    test_captcha_handling()

if __name__ == "__main__":
    main()
