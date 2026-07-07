#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pandas as pd
from src.config import DEFAULT_INDICATOR_PARAMS


def generate_signals(df: pd.DataFrame, params: dict = None) -> pd.DataFrame:
    """
    生成交易信号（Trade_Signal）和原因说明（Signal_Reason）。

    信号值: 1=买入  -1=卖出  0=无操作
    优先级（后者覆盖前者）: MA交叉 → RSI → MACD
    """
    p = {**DEFAULT_INDICATOR_PARAMS, **(params or {})}
    df = df.copy()
    df["Trade_Signal"] = 0
    df["Signal_Reason"] = ""

    ms, ml = p["ma_short"], p["ma_long"]
    ro, rb = p["rsi_oversold"], p["rsi_overbought"]

    # MA 交叉
    golden = (df["MA_short"] > df["MA_long"]) & (df["MA_short"].shift(1) <= df["MA_long"].shift(1))
    death = (df["MA_short"] < df["MA_long"]) & (df["MA_short"].shift(1) >= df["MA_long"].shift(1))
    df.loc[golden, "Trade_Signal"] = 1
    df.loc[golden, "Signal_Reason"] = f"MA{ms}/MA{ml} 金叉"
    df.loc[death, "Trade_Signal"] = -1
    df.loc[death, "Signal_Reason"] = f"MA{ms}/MA{ml} 死叉"

    # RSI 超买超卖
    oversold = df["RSI"] < ro
    overbought = df["RSI"] > rb
    df.loc[oversold, "Trade_Signal"] = 1
    df.loc[oversold, "Signal_Reason"] = f"RSI 超卖 (<{ro})"
    df.loc[overbought, "Trade_Signal"] = -1
    df.loc[overbought, "Signal_Reason"] = f"RSI 超买 (>{rb})"

    # MACD 交叉
    macd_buy = (df["MACD"] > df["MACD_Signal"]) & (df["MACD"].shift(1) <= df["MACD_Signal"].shift(1))
    macd_sell = (df["MACD"] < df["MACD_Signal"]) & (df["MACD"].shift(1) >= df["MACD_Signal"].shift(1))
    df.loc[macd_buy, "Trade_Signal"] = 1
    df.loc[macd_buy, "Signal_Reason"] = "MACD 金叉"
    df.loc[macd_sell, "Trade_Signal"] = -1
    df.loc[macd_sell, "Signal_Reason"] = "MACD 死叉"

    return df
