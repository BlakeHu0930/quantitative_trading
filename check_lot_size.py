#!/usr/bin/env python
# -*- coding: utf-8 -*-

from src.data import get_longport_config
from longport.openapi import QuoteContext
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 长桥API配置

config = get_longport_config()

def check_lot_size(symbol):
    """查询股票的交易单位（手数）"""
    quote_ctx = QuoteContext(config)
    
    try:
        # 获取股票静态信息
        static_info = quote_ctx.static_info([symbol])
        print(f"股票 {symbol} 的静态信息:")
        print(static_info)
        
        # 打印手数信息
        for info in static_info:
            # 使用正确的属性名称
            print(f"Symbol: {info.symbol}")
            print(f"中文名称: {info.name_cn}")
            print(f"英文名称: {info.name_en}")
            print(f"交易所: {info.exchange}")
            print(f"货币: {info.currency}")
            print(f"手数(Lot Size): {info.lot_size}")
            print(f"总股本: {info.total_shares}")
            
            # 返回交易单位
            return info.lot_size
    except Exception as e:
        print(f"获取股票手数信息失败: {e}")
        return 100  # 默认值

if __name__ == "__main__":
    symbols = ["1810.HK", "700.HK", "9988.HK"]
    for symbol in symbols:
        lot_size = check_lot_size(symbol)
        print(f"股票 {symbol} 的交易单位为 {lot_size} 股/手")
        print("-" * 50) 