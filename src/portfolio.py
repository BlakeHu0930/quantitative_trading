#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np


def analyze_portfolio(stock_results: dict) -> dict:
    """
    多股票投资组合分析。

    Args:
        stock_results: {symbol: {"df": DataFrame, "metrics": dict}}

    Returns:
        dict 包含 correlation_matrix, annual_returns, annual_volatility,
             weights, portfolio_return, portfolio_volatility, portfolio_sharpe
    """
    returns_data = {
        sym: v["df"]["Returns"].dropna()
        for sym, v in stock_results.items()
        if "Returns" in v["df"].columns
    }
    if not returns_data:
        raise ValueError("没有可用的收益率数据")

    returns_df = pd.DataFrame(returns_data)
    correlation_matrix = returns_df.corr()

    annual_returns = {sym: stock_results[sym]["metrics"]["annualized_returns"] for sym in stock_results}
    annual_volatility = {sym: returns_data[sym].std() * np.sqrt(252) * 100 for sym in returns_data}

    # 基于夏普比率分配权重（负值偏移至正值域）
    sharpe_ratios = {sym: stock_results[sym]["metrics"]["sharpe_ratio"] for sym in stock_results}
    min_sharpe = min(sharpe_ratios.values())
    if min_sharpe <= 0:
        sharpe_ratios = {sym: v - min_sharpe + 0.01 for sym, v in sharpe_ratios.items()}
    total_sharpe = sum(sharpe_ratios.values())
    weights = {sym: v / total_sharpe for sym, v in sharpe_ratios.items()}

    portfolio_return = sum(weights[sym] * annual_returns[sym] for sym in weights)
    portfolio_volatility = sum(weights[sym] ** 2 * annual_volatility[sym] ** 2 for sym in weights) ** 0.5
    portfolio_sharpe = portfolio_return / portfolio_volatility if portfolio_volatility > 0 else 0.0

    return {
        "correlation_matrix": correlation_matrix,
        "annual_returns": annual_returns,
        "annual_volatility": annual_volatility,
        "weights": weights,
        "portfolio_return": portfolio_return,
        "portfolio_volatility": portfolio_volatility,
        "portfolio_sharpe": portfolio_sharpe,
    }
