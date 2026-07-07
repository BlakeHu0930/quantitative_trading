#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量化交易 CLI
用法: python3 cli.py <命令> [选项]   （自动使用/创建项目 venv，无需手动激活）

命令:
  analyze     分析股票（获取数据 + 指标 + 信号 + 回测）
  backtest    用指定参数回测
  optimize    策略参数优化（网格搜索）
  trade       启动自动交易
  info        查询股票基础信息
  run-all     完整流水线（分析 → 优化 → 可选自动交易）
  strategies  列出可用策略
  repl        交互模式（无参数运行等效）
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime


def _ensure_venv():
    """不在项目 venv 中运行时自动切换过去；venv 不存在则创建并安装依赖。"""
    root = os.path.dirname(os.path.abspath(__file__))
    venv_dir = os.path.join(root, "venv")
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    venv_py = os.path.join(venv_dir, bin_dir, "python")
    if os.path.realpath(sys.prefix) == os.path.realpath(venv_dir):
        return  # 已在项目 venv 中
    if not os.path.exists(venv_py):
        import subprocess
        import venv as venv_mod
        print("首次运行：正在创建虚拟环境并安装依赖（约 1-2 分钟）...")
        venv_mod.create(venv_dir, with_pip=True)
        subprocess.check_call(
            [venv_py, "-m", "pip", "install", "-q", "-r", os.path.join(root, "requirements.txt")]
        )
        print("✓ 依赖安装完成\n")
    os.execv(venv_py, [venv_py, os.path.abspath(__file__)] + sys.argv[1:])


_ensure_venv()

import pandas as pd

from src.config import (
    DEFAULT_STOCKS, DEFAULT_INDICATOR_PARAMS, DEFAULT_BACKTEST, DEFAULT_TRADE,
    analysis_csv_path, symbol_from_analysis_file, symbol_to_stem,
)
from src.strategies import STRATEGIES, DEFAULT_STRATEGIES, strategy_choices


# ─────────────────────────────────────────────
#  工具函数
# ─────────────────────────────────────────────

def _hr(char="─", width=60):
    print(char * width)


def _section(title):
    _hr()
    print(f"  {title}")
    _hr()


def _print_metrics(metrics: dict):
    print(f"  累计收益率   {metrics['cumulative_returns']:>+10.2f}%")
    print(f"  年化收益率   {metrics['annualized_returns']:>+10.2f}%")
    print(f"  最大回撤     {metrics['max_drawdown']:>10.2f}%")
    print(f"  夏普比率     {metrics['sharpe_ratio']:>10.4f}")
    print(f"  总交易次数   {metrics['total_trades']:>10d}")
    print(f"  胜率         {metrics['win_rate']:>10.2f}%")
    print(f"  最终资产     {metrics['final_portfolio']:>10,.2f}")


def _print_trade_log(trades: list, max_rows: int = 20):
    if not trades:
        print("  （无交易记录）")
        return
    fmt = "  {:>3}  {:<22}  {:<4}  {:>10.2f}  {:>8}  {:<20}  {:>+10.2f}%"
    print(f"  {'#':>3}  {'日期':<22}  {'方向':<4}  {'价格':>10}  {'股数':>8}  {'原因':<20}  {'盈亏':>11}")
    _hr("-")
    for i, t in enumerate(trades[:max_rows], 1):
        shares = t.get("shares", 0)
        reason = (t.get("reason") or "")[:20]
        pnl_pct = t.get("pnl_pct", 0.0)
        print(fmt.format(i, str(t["date"])[:22], t["side"], t["price"], int(shares), reason, pnl_pct))
    if len(trades) > max_rows:
        print(f"  … 共 {len(trades)} 笔，仅显示前 {max_rows} 笔")


def _save_run_params(path: str, params: dict):
    with open(path, "w") as f:
        json.dump(params, f, indent=2, ensure_ascii=False)


def _ensure_dirs():
    for d in ["results", "plots", "results/optimizations", "plots/optimizations"]:
        os.makedirs(d, exist_ok=True)


# ─────────────────────────────────────────────
#  核心分析流程（analyze / run-all 共用）
# ─────────────────────────────────────────────

def _run_single_analysis(symbol: str, days: int, capital: float, indicator_params: dict,
                         strategies: list = None) -> dict:
    """获取数据 → 指标 → 信号 → 回测，返回 {df, metrics, trades}。"""
    from src.data import fetch_ohlcv
    from src.indicators import calculate_indicators
    from src.signals import generate_signals
    from src.backtest import backtest

    print(f"  正在获取 {symbol} K 线数据（{days} 根日 K）...")
    df = fetch_ohlcv(symbol, count=days)

    print("  计算技术指标...")
    df = calculate_indicators(df, indicator_params)

    print(f"  生成交易信号（策略: {' '.join(strategies or DEFAULT_STRATEGIES)}）...")
    df = generate_signals(df, indicator_params, strategies=strategies)

    print("  执行回测...")
    df, metrics, trades = backtest(df, initial_capital=capital)

    return {"df": df, "metrics": metrics, "trades": trades}


def _analyze_and_save(sym: str, days: int, capital: float, indicator_params: dict, trade_config: dict,
                      strategies: list = None):
    """分析单只股票，打印报告，保存 CSV + 参数 JSON。返回 (result, csv_path)。"""
    result = _run_single_analysis(sym, days, capital, indicator_params, strategies=strategies)
    _print_analysis_report(sym, result, indicator_params, trade_config)
    csv_path = analysis_csv_path(sym)
    result["df"].to_csv(csv_path, index=False)
    _save_run_params(
        csv_path.replace(".csv", "_params.json"),
        {"symbol": sym, "days": days, "capital": capital,
         "indicator_params": indicator_params, "strategies": strategies or DEFAULT_STRATEGIES,
         "timestamp": datetime.now().isoformat()},
    )
    return result, csv_path


def _print_analysis_report(symbol: str, result: dict, indicator_params: dict, trade_config: dict):
    p = {**DEFAULT_INDICATOR_PARAMS, **indicator_params}  # 合并默认值
    df = result["df"]
    metrics = result["metrics"]
    trades = result["trades"]
    last = df.iloc[-1]

    print()
    _hr("═")
    print(f"  分析报告: {symbol}  |  {len(df)} 根日 K  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    _hr("═")

    _section("行情摘要")
    print(f"  最新收盘价  {float(last['close']):>10.2f}")
    print(f"  MA{p['ma_short']:>2}         {float(last['MA_short']):>10.2f}   "
          f"MA{p['ma_long']:>2}  {float(last['MA_long']):>10.2f}   "
          f"MA20  {float(last['MA20']):>10.2f}")
    print(f"  RSI         {float(last['RSI']):>10.2f}")
    print(f"  MACD        {float(last['MACD']):>10.4f}   信号线  {float(last['MACD_Signal']):>10.4f}")

    # 最近 5 个非零信号
    sig_rows = df[df["Trade_Signal"] != 0].tail(5)
    _section("最近信号（最新 5 条）")
    if sig_rows.empty:
        print("  （无信号）")
    else:
        for _, row in sig_rows.iterrows():
            direction = "买入 ▲" if row["Trade_Signal"] == 1 else "卖出 ▼"
            print(f"  {str(row['time'])[:10]}  {direction}  {float(row['close']):>8.2f}  {row['Signal_Reason']}")

    _section(f"回测结果（初始资金 {trade_config['capital_limit']:,.0f}）")
    _print_metrics(metrics)

    _section("风险参数")
    sl = trade_config["stop_loss"]
    tp = trade_config["take_profit"]
    mp_ = trade_config["max_positions"]
    ps = trade_config["position_size"]
    print(f"  止损阈值    {-sl*100:>+10.1f}%")
    print(f"  止盈阈值    {tp*100:>+10.1f}%")
    print(f"  最大持仓    {mp_:>10} 只")
    print(f"  单仓比例    {ps*100:>10.0f}%")

    print()
    if trades:
        _section("交易记录（最近 10 笔）")
        _print_trade_log(trades, max_rows=10)


# ─────────────────────────────────────────────
#  子命令处理函数
# ─────────────────────────────────────────────

def cmd_analyze(args):
    _ensure_dirs()
    symbols = args.symbols if args.symbols else [args.symbol]
    indicator_params = _build_indicator_params(args)
    trade_config = _build_trade_config(args)
    capital = args.capital

    all_results = {}
    for sym in symbols:
        print(f"\n{'═'*60}")
        print(f"  分析: {sym}")
        print(f"{'═'*60}")
        try:
            result, csv_path = _analyze_and_save(sym, args.days, capital, indicator_params, trade_config,
                                                 strategies=args.strategies)
            all_results[sym] = result
            print(f"\n  ✓ 结果已保存: {csv_path}")
        except Exception as e:
            print(f"  ✗ {sym} 分析失败: {e}")
            import traceback; traceback.print_exc()

    # 投资组合分析
    if len(all_results) > 1 and args.portfolio:
        pf = _compute_portfolio(all_results)
        if pf:
            _print_portfolio(pf)


def cmd_backtest(args):
    _ensure_dirs()
    indicator_params = _build_indicator_params(args)
    capital = args.capital

    # 加载数据：优先 --file，否则从 LongPort 拉取
    if args.file:
        print(f"从文件加载数据: {args.file}")
        df_raw = pd.read_csv(args.file)
    else:
        from src.data import fetch_ohlcv
        print(f"获取 {args.symbol} K 线数据（{args.days} 根）...")
        df_raw = fetch_ohlcv(args.symbol, count=args.days)

    from src.indicators import calculate_indicators
    from src.signals import generate_signals
    from src.backtest import backtest

    df = calculate_indicators(df_raw, indicator_params)
    df = generate_signals(df, indicator_params, strategies=args.strategies)
    df, metrics, trades = backtest(df, initial_capital=capital)

    sym = args.symbol or (args.file and symbol_from_analysis_file(args.file)) or "backtest"

    _section(f"回测结果  {sym}  初始资金 {capital:,.0f}")
    _print_metrics(metrics)

    full_params = {**DEFAULT_INDICATOR_PARAMS, **indicator_params}
    _section("指标参数")
    for k, v in full_params.items():
        print(f"  {k:<20} {v}")

    _section("完整交易记录")
    _print_trade_log(trades, max_rows=50)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = f"results/{symbol_to_stem(sym)}_backtest_{ts}.csv"
    df.to_csv(out, index=False)
    _save_run_params(out.replace(".csv", "_params.json"),
                     {"symbol": sym, "indicator_params": indicator_params,
                      "strategies": args.strategies or DEFAULT_STRATEGIES,
                      "capital": capital, "timestamp": datetime.now().isoformat()})
    print(f"\n  ✓ 回测结果已保存: {out}")


def cmd_optimize(args):
    _ensure_dirs()
    from src.optimizer import (
        optimize_strategy, save_optimization_results, plot_optimization_results,
        top_results, format_params,
    )

    # 加载数据
    if args.file:
        print(f"从文件加载数据: {args.file}")
        df = pd.read_csv(args.file)
    elif args.symbol:
        csv = analysis_csv_path(args.symbol)
        if os.path.exists(csv):
            print(f"使用已有分析文件: {csv}")
            df = pd.read_csv(csv)
        else:
            from src.data import fetch_ohlcv
            from src.indicators import calculate_indicators
            from src.signals import generate_signals
            print(f"获取 {args.symbol} K 线数据...")
            df = fetch_ohlcv(args.symbol, count=200)
            df = calculate_indicators(df)
            df = generate_signals(df, strategies=args.strategies)
    else:
        print("错误：请指定 --symbol 或 --file")
        sys.exit(1)

    if not args.no_limit and len(df) > 100:
        print(f"限制数据到最近 100 条（使用 --no-limit 禁用）")
        df = df.tail(100).reset_index(drop=True)

    print(f"\n开始优化（指标: {args.metric}）...")
    results, best_params, best_value, param_names = optimize_strategy(
        df, metric=args.metric, strategies=args.strategies)

    sym = args.symbol or (args.file and symbol_from_analysis_file(args.file)) or "unknown"
    _section(f"优化结果  {sym}  指标: {args.metric}  策略: {' '.join(args.strategies or DEFAULT_STRATEGIES)}")
    print(f"  最优参数:")
    for k, v in best_params.items():
        print(f"    {k:<20} {v}")
    metric_label = {"sharpe_ratio": "夏普比率", "returns": "年化收益率(%)", "drawdown": "最大回撤(%)"}
    print(f"\n  最优 {metric_label.get(args.metric, args.metric)}: {best_value:.4f}")

    _section("前 10 参数组合")
    top10 = top_results(results, 10)
    for rank, (params, score) in enumerate(top10, 1):
        print(f"  #{rank:>2}  {format_params(param_names, params)}  得分 {score:.4f}")

    json_path = save_optimization_results(results, best_params, best_value, sym, args.metric,
                                          param_names, strategies=args.strategies)
    chart_path = plot_optimization_results(results, args.metric, sym, param_names)
    print(f"\n  ✓ 结果已保存: {json_path}")
    print(f"  ✓ 图表已保存: {chart_path}")


def cmd_trade(args):
    _ensure_dirs()
    trade_config = _build_trade_config(args)
    from src.trader import trading_loop
    print(f"启动自动交易 (模式: {trade_config['mode']})")
    print(f"  止损: {trade_config['stop_loss']:.0%}  止盈: {trade_config['take_profit']:.0%}")
    print(f"  最大持仓: {trade_config['max_positions']} 只  单仓比例: {trade_config['position_size']:.0%}")
    if trade_config.get("outside_rth"):
        print("  美股盘前/盘后交易: 已启用 (outside_rth=ANY_TIME)")
    print("  按 Ctrl+C 停止\n")
    target = getattr(args, "symbol", None)  # trade 子命令有 --symbol；run-all 没有（扫描所有文件）
    asyncio.run(trading_loop(target_symbol=target, trade_config=trade_config))


def cmd_info(args):
    from src.data import fetch_ohlcv, get_lot_size
    sym = args.symbol
    print(f"\n{sym} 基础信息")
    _hr()
    try:
        lot = get_lot_size(sym)
        print(f"  最小交易单位（手）: {lot} 股")
    except Exception as e:
        print(f"  查询手数失败: {e}")
    try:
        df = fetch_ohlcv(sym, count=5)
        last = df.iloc[-1]
        print(f"  最新收盘价:         {float(last['close']):.2f}")
        print(f"  最新交易日:         {last['time']}")
    except Exception as e:
        print(f"  查询行情失败: {e}")


def cmd_strategies(args):
    _section("可用策略")
    for strat in STRATEGIES.values():
        mark = "[默认]" if strat.key in DEFAULT_STRATEGIES else "      "
        print(f"  {strat.key:<10} {mark}  {strat.description}")
        if strat.param_ranges:
            ranges = "  ".join(f"{k}={v}" for k, v in strat.param_ranges.items())
            print(f"             优化空间: {ranges}")
        else:
            print("             （无可优化参数）")
    _hr()
    print("  用法: --strategies <key> [<key> ...]，列表中靠后的策略信号覆盖靠前的")
    print(f"  默认: {' '.join(DEFAULT_STRATEGIES)}")


def cmd_repl(args):
    from src.repl import run_repl
    run_repl(build_parser())


def cmd_run_all(args):
    _ensure_dirs()
    symbols = args.symbols if args.symbols else DEFAULT_STOCKS
    indicator_params = _build_indicator_params(args)
    trade_config = _build_trade_config(args)
    capital = args.capital

    print(f"\n{'═'*60}")
    print(f"  完整流水线  |  股票: {', '.join(symbols)}")
    print(f"{'═'*60}\n")

    all_results = {}
    for sym in symbols:
        print(f"\n── {sym} ──")
        try:
            result, csv_path = _analyze_and_save(sym, args.days, capital, indicator_params, trade_config,
                                                 strategies=args.strategies)
            all_results[sym] = result
            print(f"  ✓ {csv_path}")
        except Exception as e:
            print(f"  ✗ {sym} 失败: {e}")

    if len(all_results) > 1:
        pf = _compute_portfolio(all_results)
        if pf:
            _print_portfolio(pf)
            _save_portfolio_csv(pf)

    # 优化
    if not args.no_optimize and all_results:
        first_sym = next(iter(all_results))
        csv_path = analysis_csv_path(first_sym)
        if os.path.exists(csv_path):
            print(f"\n── 策略优化: {first_sym} ──")
            try:
                from src.optimizer import optimize_strategy, save_optimization_results, plot_optimization_results
                df_opt = pd.read_csv(csv_path).tail(100).reset_index(drop=True)
                results, best_params, best_value, param_names = optimize_strategy(
                    df_opt, strategies=args.strategies)
                json_path = save_optimization_results(results, best_params, best_value, first_sym,
                                                      "sharpe_ratio", param_names, strategies=args.strategies)
                chart_path = plot_optimization_results(results, "sharpe_ratio", first_sym, param_names)
                print(f"  ✓ 优化完成  最优参数: {best_params}")
                print(f"  ✓ {json_path}")
                print(f"  ✓ {chart_path}")
            except Exception as e:
                print(f"  ✗ 优化失败: {e}")

    # 生成汇总报告
    _write_summary_report(all_results, symbols)

    # 自动交易
    if args.auto_trade:
        print("\n── 启动自动交易 ──")
        cmd_trade(args)


# ─────────────────────────────────────────────
#  辅助：投资组合输出
# ─────────────────────────────────────────────

def _compute_portfolio(all_results: dict):
    """计算投资组合分析，失败时打印错误并返回 None。"""
    from src.portfolio import analyze_portfolio
    try:
        return analyze_portfolio(all_results)
    except Exception as e:
        print(f"  投资组合分析失败: {e}")
        return None


def _print_portfolio(pf: dict):
    _section("投资组合分析")
    print("  推荐权重:")
    for sym, w in pf["weights"].items():
        ar = pf["annual_returns"][sym]
        av = pf["annual_volatility"][sym]
        print(f"    {sym:<10} {w*100:>6.2f}%  年化收益 {ar:>+7.2f}%  波动率 {av:>6.2f}%")
    print(f"\n  组合期望年化收益: {pf['portfolio_return']:+.2f}%")
    print(f"  组合期望年化波动: {pf['portfolio_volatility']:.2f}%")
    print(f"  组合夏普比率:     {pf['portfolio_sharpe']:.4f}")
    print("\n  相关性矩阵:")
    print(pf["correlation_matrix"].to_string(float_format=lambda x: f"{x:.3f}"))


def _save_portfolio_csv(pf: dict):
    rows = [
        {"Symbol": sym, "Weight(%)": pf["weights"][sym]*100,
         "Annual_Return(%)": pf["annual_returns"][sym],
         "Annual_Volatility(%)": pf["annual_volatility"][sym]}
        for sym in pf["weights"]
    ]
    pd.DataFrame(rows).to_csv("results/portfolio_allocation.csv", index=False)
    print("  ✓ results/portfolio_allocation.csv")


def _write_summary_report(all_results: dict, symbols: list):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"results/summary_report_{ts}.md"
    lines = [f"# 量化分析汇总报告\n\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"]
    lines.append(f"## 分析股票\n{', '.join(symbols)}\n")
    for sym, res in all_results.items():
        m = res["metrics"]
        lines.append(f"## {sym}\n")
        lines.append(f"| 指标 | 值 |\n|---|---|\n"
                     f"| 累计收益率 | {m['cumulative_returns']:+.2f}% |\n"
                     f"| 年化收益率 | {m['annualized_returns']:+.2f}% |\n"
                     f"| 最大回撤 | {m['max_drawdown']:.2f}% |\n"
                     f"| 夏普比率 | {m['sharpe_ratio']:.4f} |\n"
                     f"| 总交易次数 | {m['total_trades']} |\n"
                     f"| 胜率 | {m['win_rate']:.2f}% |\n")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(f"  ✓ 汇总报告: {path}")


# ─────────────────────────────────────────────
#  参数构建
# ─────────────────────────────────────────────

def _build_indicator_params(args) -> dict:
    keys = ("ma_short", "ma_long", "rsi_period", "rsi_oversold", "rsi_overbought")
    return {k: getattr(args, k) for k in keys if getattr(args, k, None)}


def _build_trade_config(args) -> dict:
    cfg = dict(DEFAULT_TRADE)
    if getattr(args, "mode", None):
        cfg["mode"] = args.mode
    if getattr(args, "stop_loss", None) is not None:
        cfg["stop_loss"] = args.stop_loss / 100
    if getattr(args, "take_profit", None) is not None:
        cfg["take_profit"] = args.take_profit / 100
    if getattr(args, "max_positions", None):
        cfg["max_positions"] = args.max_positions
    if getattr(args, "position_size", None) is not None:
        cfg["position_size"] = args.position_size / 100
    if getattr(args, "capital", None):
        cfg["capital_limit"] = args.capital
    cfg["outside_rth"] = getattr(args, "outside_rth", False)
    return cfg


# ─────────────────────────────────────────────
#  argparse 构建
# ─────────────────────────────────────────────

def _add_indicator_args(parser):
    g = parser.add_argument_group("指标参数（不指定则使用默认值）")
    g.add_argument("--ma-short", type=int, metavar="N", help=f"短期均线周期 (默认 {DEFAULT_INDICATOR_PARAMS['ma_short']})")
    g.add_argument("--ma-long", type=int, metavar="N", help=f"长期均线周期 (默认 {DEFAULT_INDICATOR_PARAMS['ma_long']})")
    g.add_argument("--rsi-period", type=int, metavar="N", help=f"RSI 周期 (默认 {DEFAULT_INDICATOR_PARAMS['rsi_period']})")
    g.add_argument("--rsi-oversold", type=int, metavar="N", help=f"RSI 超卖阈值 (默认 {DEFAULT_INDICATOR_PARAMS['rsi_oversold']})")
    g.add_argument("--rsi-overbought", type=int, metavar="N", help=f"RSI 超买阈值 (默认 {DEFAULT_INDICATOR_PARAMS['rsi_overbought']})")


def _add_strategy_args(parser):
    parser.add_argument("--strategies", nargs="+", choices=strategy_choices(), metavar="STRAT",
                        help=f"启用的策略集，后者信号覆盖前者 (默认 {' '.join(DEFAULT_STRATEGIES)}；"
                             f"可选 {' '.join(strategy_choices())}，用 strategies 命令查看说明)")


def _add_trade_args(parser):
    g = parser.add_argument_group("风险参数")
    g.add_argument("--mode", choices=["paper", "live"], default=DEFAULT_TRADE["mode"],
                   help=f"交易模式 (默认 {DEFAULT_TRADE['mode']})")
    g.add_argument("--stop-loss", type=float, default=DEFAULT_TRADE["stop_loss"] * 100, metavar="%",
                   help=f"止损百分比 (默认 {DEFAULT_TRADE['stop_loss'] * 100:g})")
    g.add_argument("--take-profit", type=float, default=DEFAULT_TRADE["take_profit"] * 100, metavar="%",
                   help=f"止盈百分比 (默认 {DEFAULT_TRADE['take_profit'] * 100:g})")
    g.add_argument("--max-positions", type=int, default=DEFAULT_TRADE["max_positions"],
                   help=f"最大持仓股票数 (默认 {DEFAULT_TRADE['max_positions']})")
    g.add_argument("--position-size", type=float, default=DEFAULT_TRADE["position_size"] * 100, metavar="%",
                   help=f"单仓占总资金比例%% (默认 {DEFAULT_TRADE['position_size'] * 100:g})")
    g.add_argument("--outside-rth", action="store_true",
                   help="美股盘前/盘后交易（outside regular trading hours，仅美股有效）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="量化交易系统 — 无 AI 依赖，可回测，可解释，可复现",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例（港股）:
  python cli.py analyze --symbol 700.HK
  python cli.py analyze --symbols 700.HK 9988.HK --portfolio
  python cli.py backtest --symbol 700.HK --ma-short 5 --ma-long 20 --capital 200000
  python cli.py optimize --symbol 700.HK --metric sharpe_ratio
  python cli.py trade --mode paper --stop-loss 5 --take-profit 15
  python cli.py info --symbol 700.HK

示例（美股）:
  python cli.py analyze --symbol AAPL.US
  python cli.py analyze --symbols AAPL.US TSLA.US NVDA.US --portfolio
  python cli.py backtest --symbol AAPL.US --capital 50000
  python cli.py optimize --symbol AAPL.US --metric sharpe_ratio
  python cli.py trade --symbol AAPL.US --mode paper --outside-rth
  python cli.py info --symbol AAPL.US

  python cli.py run-all --auto-trade

其他:
  python cli.py                    # 无参数进入交互模式（REPL）
  python cli.py strategies         # 查看可用策略
  python cli.py backtest --file results/700_HK_analysis.csv --strategies bollinger trend
""",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ── analyze ──────────────────────────────
    p_analyze = sub.add_parser("analyze", help="分析股票")
    grp = p_analyze.add_mutually_exclusive_group(required=True)
    grp.add_argument("--symbol", help="单只股票代码，如 700.HK")
    grp.add_argument("--symbols", nargs="+", metavar="SYM", help="多只股票")
    p_analyze.add_argument("--days", type=int, default=100, help="K 线根数 (默认 100)")
    p_analyze.add_argument("--capital", type=float, default=DEFAULT_BACKTEST["initial_capital"],
                           help=f"回测初始资金 (默认 {DEFAULT_BACKTEST['initial_capital']})")
    p_analyze.add_argument("--portfolio", action="store_true", help="多股票时输出投资组合分析")
    _add_strategy_args(p_analyze)
    _add_indicator_args(p_analyze)
    _add_trade_args(p_analyze)
    p_analyze.set_defaults(func=cmd_analyze)

    # ── backtest ──────────────────────────────
    p_bt = sub.add_parser("backtest", help="用指定参数回测")
    p_bt.add_argument("--symbol", help="股票代码（与 --file 二选一）")
    p_bt.add_argument("--file", help="使用已有 CSV 文件")
    p_bt.add_argument("--days", type=int, default=100, help="K 线根数 (默认 100)")
    p_bt.add_argument("--capital", type=float, default=DEFAULT_BACKTEST["initial_capital"],
                      help=f"初始资金 (默认 {DEFAULT_BACKTEST['initial_capital']})")
    _add_strategy_args(p_bt)
    _add_indicator_args(p_bt)
    p_bt.set_defaults(func=cmd_backtest)

    # ── optimize ──────────────────────────────
    p_opt = sub.add_parser("optimize", help="策略参数优化")
    p_opt.add_argument("--symbol", help="股票代码")
    p_opt.add_argument("--file", help="使用已有 CSV 文件")
    p_opt.add_argument("--metric", choices=["sharpe_ratio", "returns", "drawdown"],
                       default="sharpe_ratio", help="优化目标 (默认 sharpe_ratio)")
    p_opt.add_argument("--no-limit", action="store_true", help="不限制数据量（默认最近 100 条）")
    _add_strategy_args(p_opt)
    p_opt.set_defaults(func=cmd_optimize)

    # ── trade ──────────────────────────────
    p_trade = sub.add_parser("trade", help="启动自动交易")
    p_trade.add_argument("--symbol", help="只交易该股票（不填则扫描所有分析文件）")
    _add_trade_args(p_trade)
    p_trade.set_defaults(func=cmd_trade)

    # ── info ──────────────────────────────
    p_info = sub.add_parser("info", help="查询股票基础信息（手数 / 最新价）")
    p_info.add_argument("--symbol", required=True, help="股票代码")
    p_info.set_defaults(func=cmd_info)

    # ── run-all ──────────────────────────────
    p_all = sub.add_parser("run-all", help="完整流水线")
    p_all.add_argument("--symbols", nargs="+", metavar="SYM",
                       help=f"股票列表 (默认 {' '.join(DEFAULT_STOCKS)})")
    p_all.add_argument("--days", type=int, default=100, help="K 线根数 (默认 100)")
    p_all.add_argument("--capital", type=float, default=DEFAULT_BACKTEST["initial_capital"],
                       help=f"初始资金 (默认 {DEFAULT_BACKTEST['initial_capital']})")
    p_all.add_argument("--no-optimize", action="store_true", help="跳过参数优化步骤")
    p_all.add_argument("--auto-trade", action="store_true", help="完成后启动自动交易")
    _add_strategy_args(p_all)
    _add_indicator_args(p_all)
    _add_trade_args(p_all)
    p_all.set_defaults(func=cmd_run_all)

    # ── strategies ──────────────────────────────
    p_strat = sub.add_parser("strategies", help="列出可用策略及参数空间")
    p_strat.set_defaults(func=cmd_strategies)

    # ── repl ──────────────────────────────
    p_repl = sub.add_parser("repl", help="进入交互模式（python cli.py 无参数等效）")
    p_repl.set_defaults(func=cmd_repl)

    return parser


# ─────────────────────────────────────────────
#  入口
# ─────────────────────────────────────────────

def main():
    parser = build_parser()
    # 无参数直接进 REPL（subparsers required=True，须在 parse_args 之前拦截）
    if len(sys.argv) == 1:
        from src.repl import run_repl
        run_repl(parser)
        return
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
