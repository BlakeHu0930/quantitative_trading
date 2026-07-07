#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np
from src.config import DEFAULT_BACKTEST


def backtest(
    df: pd.DataFrame,
    initial_capital: float = None,
    risk_free_rate: float = None,
) -> tuple:
    """
    回测策略。

    返回:
        df_result   附加了 Position / Cash / Holdings / Portfolio / Drawdown 列的 DataFrame
        metrics     dict: cumulative_returns, annualized_returns, max_drawdown,
                          sharpe_ratio, total_trades, win_rate, final_portfolio
        trades      list[dict]: 每笔买卖记录
    """
    if initial_capital is None:
        initial_capital = DEFAULT_BACKTEST["initial_capital"]
    if risk_free_rate is None:
        risk_free_rate = DEFAULT_BACKTEST["risk_free_rate"]

    df = df.copy().reset_index(drop=True)

    # 确保信号列存在
    if "Trade_Signal" not in df.columns:
        raise ValueError("DataFrame 缺少 Trade_Signal 列，请先调用 generate_signals()")

    position = 0
    cash = float(initial_capital)
    trades = []
    open_trade = None  # 记录当前持仓的买入信息
    has_reason = "Signal_Reason" in df.columns

    positions_arr = [0] * len(df)
    cash_arr = [cash] * len(df)
    holdings_arr = [0.0] * len(df)
    portfolio_arr = [cash] * len(df)

    for i in range(1, len(df)):
        signal = int(df.at[i - 1, "Trade_Signal"])
        price = float(df.at[i, "open"])
        reason = str(df.at[i - 1, "Signal_Reason"]) if has_reason else ""
        date = df.at[i, "time"]

        if signal == 1 and position == 0:
            shares = int(cash / price)
            if shares > 0:
                cost = shares * price
                cash -= cost
                position = shares
                open_trade = {"date": date, "price": price, "shares": shares, "cost": cost, "reason": reason}
                trades.append({
                    "date": date, "side": "BUY", "price": price,
                    "shares": shares, "reason": reason, "pnl": 0.0, "pnl_pct": 0.0,
                })
        elif signal == -1 and position > 0:
            proceeds = position * price
            buy_cost = open_trade["cost"] if open_trade else 0.0
            pnl = proceeds - buy_cost
            pnl_pct = pnl / buy_cost * 100 if buy_cost else 0.0
            cash += proceeds
            trades.append({
                "date": date, "side": "SELL", "price": price,
                "shares": position, "reason": reason, "pnl": pnl, "pnl_pct": pnl_pct,
            })
            position = 0
            open_trade = None

        holdings = position * price
        positions_arr[i] = position
        cash_arr[i] = cash
        holdings_arr[i] = holdings
        portfolio_arr[i] = cash + holdings

    df["Position"] = positions_arr
    df["Cash"] = cash_arr
    df["Holdings"] = holdings_arr
    df["Portfolio"] = portfolio_arr

    df["Returns"] = df["Portfolio"].pct_change()
    final_portfolio = df.iloc[-1]["Portfolio"]
    cumulative_returns = (final_portfolio / initial_capital - 1) * 100
    annualized_returns = ((1 + cumulative_returns / 100) ** (252 / len(df)) - 1) * 100

    cum_ret = (1 + df["Returns"].fillna(0)).cumprod()
    cum_max = cum_ret.cummax()
    df["Drawdown"] = (cum_max - cum_ret) / cum_max * 100
    max_drawdown = float(df["Drawdown"].max())

    excess = df["Returns"].dropna() - risk_free_rate / 252
    sharpe = float(np.sqrt(252) * excess.mean() / excess.std()) if excess.std() != 0 else 0.0

    sell_trades = [t for t in trades if t["side"] == "SELL"]
    winning = [t for t in sell_trades if t["pnl"] > 0]
    win_rate = len(winning) / len(sell_trades) * 100 if sell_trades else 0.0

    metrics = {
        "cumulative_returns": float(cumulative_returns),
        "annualized_returns": float(annualized_returns),
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe,
        "total_trades": len(sell_trades),
        "win_rate": win_rate,
        "final_portfolio": float(final_portfolio),
    }

    return df, metrics, trades
