#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import multiprocessing as mp
from itertools import product
from functools import partial
from datetime import datetime

import pandas as pd
from tqdm import tqdm

from src.config import symbol_to_stem
from src.strategies import DEFAULT_STRATEGIES, optimize_ranges_for
from src.indicators import calculate_indicators
from src.signals import generate_signals
from src.backtest import backtest

# 优化目标 → (metrics 字段, 符号)；drawdown 取负，统一为“越大越好”
_METRIC_KEYS = {
    "sharpe_ratio": ("sharpe_ratio", 1),
    "returns": ("annualized_returns", 1),
    "drawdown": ("max_drawdown", -1),
}

_FAILED_SCORE = -999.0


def top_results(results: list, n: int) -> list:
    """按得分降序取前 n 个 (params_tuple, score)。"""
    return sorted(results, key=lambda x: x[1], reverse=True)[:n]


def format_params(param_names: list, params: tuple) -> str:
    """(5, 20, ...) → "ma_short=5,ma_long=20,..."；空网格显示"默认参数"。"""
    return ",".join(f"{k}={v}" for k, v in zip(param_names, params)) or "默认参数"


def _evaluate_params(params, param_names, df, metric="sharpe_ratio", strategies=None):
    custom_params = dict(zip(param_names, params))
    try:
        df_copy = calculate_indicators(df, custom_params)
        df_copy = generate_signals(df_copy, custom_params, strategies=strategies)
        _, metrics, _ = backtest(df_copy)
        key, sign = _METRIC_KEYS.get(metric, _METRIC_KEYS["sharpe_ratio"])
        return params, sign * metrics[key]
    except Exception:
        return params, _FAILED_SCORE


def optimize_strategy(
    df: pd.DataFrame,
    param_ranges: dict = None,
    metric: str = "sharpe_ratio",
    strategies: list = None,
) -> tuple:
    """
    网格搜索最优策略参数。网格取自所选策略声明的参数子空间。

    Returns:
        results         list of (params_tuple, score)
        best_params     dict
        best_value      float
        param_names     list — results 中元组的参数名顺序
    """
    if param_ranges is not None:
        ranges = dict(param_ranges)
    elif strategies:
        ranges = optimize_ranges_for(strategies)
    else:
        ranges = optimize_ranges_for(DEFAULT_STRATEGIES)
    param_names = list(ranges)
    # ranges 为空时 product() → [()]，即"用默认参数评估一次"
    combinations = list(product(*(ranges[k] for k in param_names)))

    print(f"参数组合数: {len(combinations)}  使用指标: {metric}"
          f"  策略: {' '.join(strategies or DEFAULT_STRATEGIES)}")
    num_cores = max(1, mp.cpu_count() - 1)
    print(f"并行核心数: {num_cores}")

    eval_fn = partial(_evaluate_params, param_names=param_names, df=df,
                      metric=metric, strategies=strategies)
    with mp.Pool(processes=num_cores) as pool:
        results = list(tqdm(pool.imap(eval_fn, combinations), total=len(combinations)))

    if all(score == _FAILED_SCORE for _, score in results):
        print("警告: 所有参数组合均评估失败，请检查数据列是否满足所选策略要求（如 donchian 需要 high/low）")

    best = max(results, key=lambda x: x[1])
    best_params = dict(zip(param_names, best[0]))
    best_value = -best[1] if metric == "drawdown" else best[1]

    return results, best_params, best_value, param_names


def save_optimization_results(
    results: list,
    best_params: dict,
    best_value: float,
    symbol: str,
    metric: str,
    param_names: list,
    strategies: list = None,
) -> str:
    """保存优化结果到 JSON，返回文件路径。"""
    os.makedirs(f"results/optimizations/{symbol}", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    path = f"results/optimizations/{symbol}/optimization_{ts}.json"
    top20 = top_results(results, 20)
    payload = {
        "symbol": symbol,
        "timestamp": ts,
        "metric": metric,
        "strategies": strategies or DEFAULT_STRATEGIES,
        "best_params": best_params,
        "best_value": float(best_value),
        "top_results": [
            {"params": dict(zip(param_names, r[0])), "value": float(r[1])}
            for r in top20
        ],
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def plot_optimization_results(results: list, metric: str, symbol: str, param_names: list, top_n: int = 20) -> str:
    """绘制 top_n 参数组合的柱状图，返回保存路径。"""
    # matplotlib 仅在此处使用；放在模块顶层会拖慢 mp.Pool 每个 worker 的启动
    import matplotlib.pyplot as plt

    os.makedirs("plots/optimizations", exist_ok=True)
    sorted_results = top_results(results, top_n)
    labels = [format_params(param_names, r[0]) for r in sorted_results]
    values = [r[1] for r in sorted_results]
    metric_names = {"sharpe_ratio": "Sharpe Ratio", "returns": "Ann. Return (%)", "drawdown": "Max Drawdown (%)"}

    plt.figure(figsize=(16, 6))
    plt.bar(range(len(values)), values)
    plt.xticks(range(len(values)), labels, rotation=90, fontsize=8)
    plt.title(f"{symbol} — Top {top_n} Param Combos ({metric_names.get(metric, metric)})")
    plt.ylabel(metric_names.get(metric, metric))
    plt.tight_layout()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"plots/optimizations/{symbol_to_stem(symbol)}_opt_{metric}_{ts}.png"
    plt.savefig(path)
    plt.close()
    return path
