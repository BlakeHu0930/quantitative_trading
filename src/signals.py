#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pandas as pd

from src.config import DEFAULT_INDICATOR_PARAMS
from src.strategies import DEFAULT_STRATEGIES, get_strategies


def generate_signals(df: pd.DataFrame, params: dict = None, strategies: list = None) -> pd.DataFrame:
    """
    生成交易信号（Trade_Signal）和原因说明（Signal_Reason）。

    信号值: 1=买入  -1=卖出  0=无操作
    strategies: 策略键列表（见 src/strategies.py），默认 MA交叉 → RSI → MACD；
                列表中靠后的策略信号覆盖靠前的。
    """
    p = {**DEFAULT_INDICATOR_PARAMS, **(params or {})}
    df = df.copy()
    df["Trade_Signal"] = 0
    df["Signal_Reason"] = ""

    for strat in get_strategies(strategies or DEFAULT_STRATEGIES):
        buy, sell, buy_reason, sell_reason = strat.signal_fn(df, p)
        df.loc[buy, "Trade_Signal"] = 1
        df.loc[buy, "Signal_Reason"] = buy_reason
        df.loc[sell, "Trade_Signal"] = -1
        df.loc[sell, "Signal_Reason"] = sell_reason

    return df
