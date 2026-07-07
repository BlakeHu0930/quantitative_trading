#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import pandas as pd
from dotenv import load_dotenv
from longport.openapi import Config, QuoteContext, Period, AdjustType

load_dotenv()


def get_longport_config() -> Config:
    app_key = os.getenv("LONGPORT_APP_KEY")
    app_secret = os.getenv("LONGPORT_APP_SECRET")
    access_token = os.getenv("LONGPORT_ACCESS_TOKEN")
    if not all([app_key, app_secret, access_token]):
        raise EnvironmentError(
            "缺少 LongPort 凭证，请在 .env 中设置 "
            "LONGPORT_APP_KEY / LONGPORT_APP_SECRET / LONGPORT_ACCESS_TOKEN"
        )
    return Config(app_key=app_key, app_secret=app_secret, access_token=access_token)


_quote_ctx = None


def get_quote_context() -> QuoteContext:
    """复用同一个 QuoteContext（每次新建都要重新建立连接，代价高）。"""
    global _quote_ctx
    if _quote_ctx is None:
        _quote_ctx = QuoteContext(get_longport_config())
    return _quote_ctx


def fetch_ohlcv(symbol: str, count: int = 100, trade_session: int = 0) -> pd.DataFrame:
    """
    从 LongPort 获取日 K 线数据。

    Args:
        symbol:        股票代码，如 700.HK 或 AAPL.US
        count:         K 线根数（最大 1000）
        trade_session: 0=日内（默认），100=全时段（含盘前/盘后，美股有效）

    Returns:
        DataFrame(time, open, high, low, close, volume, turnover)
    """
    quote_ctx = get_quote_context()

    kwargs = dict(
        symbol=symbol,
        period=Period.Day,
        count=count,
        adjust_type=AdjustType.ForwardAdjust,
    )
    # trade_session 参数仅在 SDK 版本支持时传入
    try:
        candlesticks = quote_ctx.candlesticks(**kwargs, trade_session=trade_session)
    except TypeError:
        # 旧版 SDK 不支持 trade_session，降级处理
        candlesticks = quote_ctx.candlesticks(**kwargs)

    if not candlesticks:
        raise ValueError(f"未获取到 {symbol} 的 K 线数据")

    rows = [
        {
            "time": c.timestamp,
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": int(c.volume),
            "turnover": float(c.turnover),
        }
        for c in candlesticks
    ]
    return pd.DataFrame(rows)


def get_lot_size(symbol: str, quote_ctx: QuoteContext = None) -> int:
    """
    查询最小交易单位（手数）。
    美股固定为 1，港股一般 100，通过 API 确认实际值。
    可传入已有的 quote_ctx 复用连接。
    """
    from src.config import get_lot_size_default
    default = get_lot_size_default(symbol)
    try:
        if quote_ctx is None:
            quote_ctx = get_quote_context()
        infos = quote_ctx.static_info([symbol])
        items = infos if isinstance(infos, list) else getattr(infos, "infos", [])
        for info in items:
            lot = getattr(info, "lot_size", None)
            if lot and int(lot) > 0:
                return int(lot)
    except Exception:
        pass
    return default
