# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

零手动步骤：`cli.py` 启动时自动切换到项目 venv，venv 不存在则自动创建并安装 `requirements.txt`（pandas numpy matplotlib longport python-dotenv tqdm prompt_toolkit）。直接运行即可：

```bash
python3 cli.py        # 或 ./cli.py，首次运行自动装依赖
```

Required `.env` file in project root (no AI key needed):
```
LONGPORT_APP_KEY="..."
LONGPORT_APP_SECRET="..."
LONGPORT_ACCESS_TOKEN="..."
```

## CLI — 单一入口 `cli.py`

```bash
# 交互模式（REPL）：子命令补全、历史记录、use <代码> 设置当前股票
python cli.py            # 无参数进入
python cli.py repl       # 等效

# 查看可用策略及参数空间
python cli.py strategies

# 分析单只股票（港股 / 美股）
python cli.py analyze --symbol 700.HK
python cli.py analyze --symbol AAPL.US

# 分析多只股票 + 投资组合
python cli.py analyze --symbols 700.HK 9988.HK 1211.HK --portfolio
python cli.py analyze --symbols AAPL.US TSLA.US NVDA.US --portfolio

# 指定参数回测（可复现）
python cli.py backtest --symbol 700.HK --ma-short 5 --ma-long 20 --capital 200000

# 从已有 CSV 回测（离线，不需要 LongPort）
python cli.py backtest --file results/700_HK_analysis.csv

# 指定策略组合（analyze / backtest / optimize / run-all 均支持）
python cli.py backtest --file results/700_HK_analysis.csv --strategies bollinger trend

# 策略参数优化（网格搜索，多进程）
python cli.py optimize --symbol 700.HK --metric sharpe_ratio
# --metric: sharpe_ratio | returns | drawdown

# 从已有文件优化（跳过数据拉取）
python cli.py optimize --file results/700_HK_analysis.csv

# 查询股票基础信息（手数 / 最新价）
python cli.py info --symbol 700.HK

# 启动自动交易（paper 模式，安全）
python cli.py trade --mode paper --stop-loss 5 --take-profit 15
python cli.py trade --symbol AAPL.US --mode paper --outside-rth   # 美股含盘前盘后

# 完整流水线
python cli.py run-all
python cli.py run-all --symbols 700.HK 9988.HK --auto-trade --mode paper
```

## Architecture

**目录结构:**
```
cli.py                      ← 唯一 CLI 入口
src/
  config.py                 ← 默认参数、股票列表、常量
  data.py                   ← LongPort 数据拉取 (fetch_ohlcv, get_lot_size)
  indicators.py             ← calculate_indicators(df, params)
  strategies.py             ← 策略注册表 STRATEGIES（每个策略 = 掩码 + 原因 + 优化子空间）
  signals.py                ← generate_signals(df, params, strategies) — 含 Signal_Reason 列
  backtest.py               ← backtest(df, capital) → (df, metrics, trades)
  portfolio.py              ← analyze_portfolio(stock_results)
  optimizer.py              ← optimize_strategy(df, param_ranges, metric, strategies)
  trader.py                 ← AutoTrader class + trading_loop()
  repl.py                   ← 交互模式（prompt_toolkit 补全/历史，缺依赖时回退 input）
results/                    ← 分析 CSV、回测结果、优化 JSON、交易状态
plots/optimizations/        ← 优化图表
```

**数据流:**
1. `src/data.py` → 拉取 OHLCV
2. `src/indicators.py` → 计算 MA / RSI / MACD / 布林带
3. `src/signals.py` → 生成 Trade_Signal + Signal_Reason（可解释）
4. `src/backtest.py` → 回测，返回完整 trades 列表（含盈亏、原因）
5. `src/optimizer.py` → 多进程网格搜索最优参数
6. `src/trader.py` → 读取 `*_analysis.csv` 中的 Trade_Signal，执行实盘/模拟交易

**可复现性:** 每次分析同时保存 `<symbol>_params.json`，记录所有参数。

**可解释性:** `Signal_Reason` 列说明每个信号的触发条件（如 "MA5/MA10 金叉"、"RSI 超卖 (<30)"）。

**旧文件（仍可独立运行，但不再维护）:**
- `quant_trading.py`, `multi_stock_analysis.py`, `strategy_optimizer.py`, `auto_trader.py`, `run_all_analysis.py`

## Indicators

- MA_short / MA_long / MA20: 移动平均线（默认 5/10/20）
- RSI(14): 相对强弱指数，超卖 <30 / 超买 >70
- MACD(12,26,9) + MACD_Signal + MACD_Hist
- BB_upper / BB_mid / BB_lower: 布林带（20 日，2σ）

## Signal Logic

策略注册表见 `src/strategies.py`，CLI 用 `--strategies` 自由组合，列表中**靠后的策略信号覆盖靠前的**。

| key | 策略 | 可优化参数 |
|---|---|---|
| ma_cross | 均线金叉/死叉（默认） | ma_short, ma_long |
| rsi | RSI 超买超卖（默认） | rsi_period, rsi_oversold, rsi_overbought |
| macd | MACD 金叉/死叉（默认） | — |
| bollinger | 布林带均值回归 | bb_period, bb_std |
| trend | MA_trend 趋势线穿越 | ma_trend |
| donchian | 唐奇安通道突破（需 high/low） | donchian_period |

默认策略集 `ma_cross rsi macd`（与旧版行为一致）:
- Buy (1):  MA 金叉 | RSI < 30 | MACD 金叉
- Sell (-1): MA 死叉 | RSI > 70 | MACD 死叉

优化器网格 = 所选策略声明的参数子空间的并集；`_params.json` 与优化 JSON 均记录 `strategies` 字段以保证可复现。

## Risk Control

- 止损 / 止盈: CLI 参数 `--stop-loss` / `--take-profit`（单位 %）
- 最大持仓数: `--max-positions`
- 单仓比例: `--position-size`（单位 %）
- 默认 paper 模式，live 模式需显式 `--mode live`

## Output Locations

| Artifact | Path |
|---|---|
| 股票分析 | `results/<SYM>_analysis.csv` |
| 运行参数 | `results/<SYM>_analysis_params.json` |
| 回测结果 | `results/<SYM>_backtest_<ts>.csv` |
| 投资组合 | `results/portfolio_allocation.csv` |
| 交易状态 | `results/trading_state.json` |
| 优化结果 | `results/optimizations/<sym>/optimization_<ts>.json` |
| 汇总报告 | `results/summary_report_<ts>.md` |
| 优化图表 | `plots/optimizations/` |
