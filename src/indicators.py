#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np
from src.config import DEFAULT_INDICATOR_PARAMS


def calculate_indicators(df: pd.DataFrame, params: dict = None) -> pd.DataFrame:
    """计算技术指标。params 中未指定的项目使用默认值。"""
    p = {**DEFAULT_INDICATOR_PARAMS, **(params or {})}
    df = df.copy()

    # 移动平均线
    df["MA_short"] = df["close"].rolling(window=p["ma_short"]).mean()
    df["MA_long"] = df["close"].rolling(window=p["ma_long"]).mean()
    df["MA20"] = df["close"].rolling(window=p["ma_trend"]).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=p["rsi_period"]).mean()
    avg_loss = loss.rolling(window=p["rsi_period"]).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))

    # MACD
    ema_fast = df["close"].ewm(span=p["macd_fast"], adjust=False).mean()
    ema_slow = df["close"].ewm(span=p["macd_slow"], adjust=False).mean()
    df["MACD"] = ema_fast - ema_slow
    df["MACD_Signal"] = df["MACD"].ewm(span=p["macd_signal"], adjust=False).mean()
    df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]

    # 布林带
    bb_mid = df["close"].rolling(window=p["bb_period"]).mean()
    bb_std = df["close"].rolling(window=p["bb_period"]).std()
    df["BB_mid"] = bb_mid
    df["BB_upper"] = bb_mid + p["bb_std"] * bb_std
    df["BB_lower"] = bb_mid - p["bb_std"] * bb_std

    return df
