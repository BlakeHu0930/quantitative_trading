#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os

DEFAULT_STOCKS = [
    "700.HK",   # 腾讯控股
    "9988.HK",  # 阿里巴巴
    "1211.HK",  # 比亚迪
    "0941.HK",  # 中国移动
    "0175.HK",  # 吉利汽车
]

DEFAULT_US_STOCKS = [
    "AAPL.US",  # Apple
    "TSLA.US",  # Tesla
    "NVDA.US",  # NVIDIA
    "MSFT.US",  # Microsoft
    "AMZN.US",  # Amazon
]

DEFAULT_INDICATOR_PARAMS = {
    "ma_short": 5,
    "ma_long": 10,
    "ma_trend": 20,
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "bb_period": 20,
    "bb_std": 2,
}

DEFAULT_BACKTEST = {
    "initial_capital": 100000,
    "risk_free_rate": 0.02,
}

DEFAULT_TRADE = {
    "mode": "paper",
    "capital_limit": 100000,
    "max_positions": 5,
    "position_size": 0.2,
    "stop_loss": 0.05,
    "take_profit": 0.15,
    "check_interval": 10,
    "outside_rth": False,        # 美股盘前/盘后交易开关
    # tz=None 表示使用系统本地时间判断
    "trading_hours": {
        "HK": {"start": "09:30", "end": "16:00", "tz": None},
        "US": {"start": "09:30", "end": "16:00", "tz": "America/New_York"},
    },
}

OPTIMIZE_PARAM_RANGES = {
    "ma_short": [5, 10],
    "ma_long": [20, 30],
    "rsi_period": [10, 14],
    "rsi_oversold": [25, 30],
    "rsi_overbought": [70, 75],
}

# 网格搜索的参数顺序（与 OPTIMIZE_PARAM_RANGES 的键序一致）
OPTIMIZE_PARAM_NAMES = list(OPTIMIZE_PARAM_RANGES)


# 结果文件命名约定：700.HK ↔ results/700_HK_analysis.csv
def symbol_to_stem(symbol: str) -> str:
    return str(symbol).replace(".", "_")


def analysis_csv_path(symbol: str) -> str:
    return f"results/{symbol_to_stem(symbol)}_analysis.csv"


def symbol_from_analysis_file(path: str) -> str:
    """从文件路径推断股票代码，如 results/700_HK_analysis.csv → 700.HK。"""
    base = os.path.basename(path)
    for suffix in ("_analysis.csv", ".csv"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if base.endswith("_HK") or base.endswith("_US"):
        return base[:-3] + "." + base[-2:]
    return base

# 股票市场判断
def get_market(symbol: str) -> str:
    """根据股票代码后缀判断市场：HK / US / CN。"""
    s = str(symbol).upper()
    if s.endswith(".HK"):
        return "HK"
    if s.endswith(".US"):
        return "US"
    return "CN"


def get_currency(symbol: str) -> str:
    market = get_market(symbol)
    return {"HK": "HKD", "US": "USD"}.get(market, "HKD")


def get_lot_size_default(symbol: str) -> int:
    """美股 lot_size=1，港股默认 100（查 API 前的兜底值）。"""
    return 1 if get_market(symbol) == "US" else 100
