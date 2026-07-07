#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""策略注册表：每个策略 = 买卖掩码 + 中文原因 + 可优化参数子空间。

signal_fn 约定: (df, p) -> (buy_mask, sell_mask, buy_reason, sell_reason)
- df 为已计算指标的 DataFrame，p 为合并默认值后的参数 dict
- 掩码为与 df.index 对齐的布尔 Series（NaN 比较结果为 False，天然跳过暖机期）
"""

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from src.config import OPTIMIZE_PARAM_RANGES


@dataclass(frozen=True)
class Strategy:
    key: str                 # 注册键（CLI --strategies 的取值）
    description: str
    signal_fn: Callable
    param_ranges: dict = field(default_factory=dict)  # 网格搜索子空间，可为空


def _ma_cross(df: pd.DataFrame, p: dict):
    ms, ml = p["ma_short"], p["ma_long"]
    buy = (df["MA_short"] > df["MA_long"]) & (df["MA_short"].shift(1) <= df["MA_long"].shift(1))
    sell = (df["MA_short"] < df["MA_long"]) & (df["MA_short"].shift(1) >= df["MA_long"].shift(1))
    return buy, sell, f"MA{ms}/MA{ml} 金叉", f"MA{ms}/MA{ml} 死叉"


def _rsi(df: pd.DataFrame, p: dict):
    ro, rb = p["rsi_oversold"], p["rsi_overbought"]
    return df["RSI"] < ro, df["RSI"] > rb, f"RSI 超卖 (<{ro})", f"RSI 超买 (>{rb})"


def _macd(df: pd.DataFrame, p: dict):
    buy = (df["MACD"] > df["MACD_Signal"]) & (df["MACD"].shift(1) <= df["MACD_Signal"].shift(1))
    sell = (df["MACD"] < df["MACD_Signal"]) & (df["MACD"].shift(1) >= df["MACD_Signal"].shift(1))
    return buy, sell, "MACD 金叉", "MACD 死叉"


def _bollinger(df: pd.DataFrame, p: dict):
    buy = df["close"] < df["BB_lower"]
    sell = df["close"] > df["BB_upper"]
    n, k = p["bb_period"], p["bb_std"]
    return buy, sell, f"跌破布林下轨 ({n}日,{k}σ)", f"突破布林上轨 ({n}日,{k}σ)"


def _trend(df: pd.DataFrame, p: dict):
    buy = (df["close"] > df["MA20"]) & (df["close"].shift(1) <= df["MA20"].shift(1))
    sell = (df["close"] < df["MA20"]) & (df["close"].shift(1) >= df["MA20"].shift(1))
    n = p["ma_trend"]
    return buy, sell, f"站上 MA{n} 趋势线", f"跌破 MA{n} 趋势线"


def _donchian(df: pd.DataFrame, p: dict):
    n = p["donchian_period"]
    upper = df["high"].rolling(n).max().shift(1)
    lower = df["low"].rolling(n).min().shift(1)
    return df["close"] > upper, df["close"] < lower, f"突破 {n} 日高点", f"跌破 {n} 日低点"


# 注册顺序即 strategies 子命令的展示顺序
STRATEGIES = {
    "ma_cross": Strategy(
        "ma_cross", "均线金叉/死叉（MA_short 上/下穿 MA_long）", _ma_cross,
        {k: OPTIMIZE_PARAM_RANGES[k] for k in ("ma_short", "ma_long")},
    ),
    "rsi": Strategy(
        "rsi", "RSI 超买超卖（低于/高于阈值）", _rsi,
        {k: OPTIMIZE_PARAM_RANGES[k] for k in ("rsi_period", "rsi_oversold", "rsi_overbought")},
    ),
    "macd": Strategy(
        "macd", "MACD 金叉/死叉（DIF 上/下穿 DEA）", _macd, {},
    ),
    "bollinger": Strategy(
        "bollinger", "布林带均值回归（收盘价穿越上/下轨）", _bollinger,
        {"bb_period": [15, 20, 25], "bb_std": [2, 2.5]},
    ),
    "trend": Strategy(
        "trend", "趋势线过滤（收盘价上/下穿 MA_trend）", _trend,
        {"ma_trend": [20, 30, 60]},
    ),
    "donchian": Strategy(
        "donchian", "唐奇安通道突破（N 日最高/最低价，需 high/low 列）", _donchian,
        {"donchian_period": [10, 20]},
    ),
}

# 与旧版硬编码 MA→RSI→MACD 分层一致，输出不变
DEFAULT_STRATEGIES = ["ma_cross", "rsi", "macd"]


def strategy_choices() -> list:
    return list(STRATEGIES)


def get_strategies(names: list) -> list:
    """按给定顺序返回 Strategy 对象（去重保序），未知键抛 ValueError。"""
    seen = []
    for name in names:
        if name not in STRATEGIES:
            raise ValueError(f"未知策略 {name!r}，可用: {', '.join(STRATEGIES)}")
        if name not in seen:
            seen.append(name)
    return [STRATEGIES[n] for n in seen]


def optimize_ranges_for(names: list) -> dict:
    """合并所选策略的参数子空间（按策略顺序，后者覆盖同名键）。"""
    ranges = {}
    for strat in get_strategies(names):
        ranges.update(strat.param_ranges)
    return ranges
