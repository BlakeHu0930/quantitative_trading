# 量化交易系统

基于长桥（LongPort）OpenAPI 的量化交易分析与自动交易工具。无 AI 依赖，纯技术指标驱动——**可回测、可解释、可复现**。

支持港股与美股（`700.HK` / `AAPL.US`）。

## 功能特点

- 单一 CLI 入口 + 交互式 REPL（子命令补全、历史记录）
- 技术指标：MA / RSI / MACD / 布林带 / 唐奇安通道
- **可插拔策略注册表**：6 种内置策略自由组合，信号带中文触发原因（`Signal_Reason`）
- 历史回测：完整交易记录（含每笔盈亏）、夏普比率、最大回撤、胜率
- 策略参数优化：多进程网格搜索，网格随所选策略动态生成
- 多股票组合分析：相关性矩阵、基于夏普比率的权重优化
- 自动交易：paper 模拟盘 / live 实盘，止损止盈、交易时段门控、防重复下单
- 可复现：每次运行的参数（含策略集）保存为 JSON sidecar

## 快速开始

```bash
git clone <repository-url>
cd quantitative_trading

# 配置 LongPort API 密钥（https://open.longportapp.com 申请）
cat > .env <<'ENV'
LONGPORT_APP_KEY="your_app_key"
LONGPORT_APP_SECRET="your_app_secret"
LONGPORT_ACCESS_TOKEN="your_access_token"
ENV

# 直接运行——首次运行自动创建虚拟环境并安装依赖，无需手动 pip install / activate
python3 cli.py
```

需要 Python 3.9+。依赖见 `requirements.txt`（pandas、numpy、matplotlib、longport、python-dotenv、tqdm、prompt_toolkit），由 `cli.py` 自动安装。

## 交互模式（REPL）

`python3 cli.py` 无参数进入交互模式：

```
量化交易 REPL — help 查看命令, use <代码> 设置当前股票, exit 退出
quant> use AAPL.US
quant[AAPL.US]> analyze              # 自动带上 --symbol AAPL.US
quant[AAPL.US]> backtest
quant[AAPL.US]> trade --mode paper
quant[AAPL.US]> exit
```

支持子命令 / 选项 / 股票代码 / 策略名补全（Tab），历史记录保存在 `~/.quant_cli_history`。

## 命令一览

```bash
# 分析：拉数据 → 指标 → 信号 → 回测，保存 CSV
python3 cli.py analyze --symbol 700.HK
python3 cli.py analyze --symbols 700.HK 9988.HK 1211.HK --portfolio   # 多股票 + 组合分析
python3 cli.py analyze --symbol AAPL.US --strategies bollinger trend  # 指定策略组合

# 回测：指定参数（可复现）；--file 离线跑，不需要 LongPort
python3 cli.py backtest --symbol 700.HK --ma-short 5 --ma-long 20 --capital 200000
python3 cli.py backtest --file results/700_HK_analysis.csv --strategies donchian

# 优化：多进程网格搜索最优参数
python3 cli.py optimize --symbol 700.HK --metric sharpe_ratio   # sharpe_ratio | returns | drawdown
python3 cli.py optimize --file results/700_HK_analysis.csv --strategies bollinger donchian

# 自动交易（默认 paper 模拟盘）
python3 cli.py trade --symbol AAPL.US --mode paper
python3 cli.py trade --symbol AAPL.US --mode paper --outside-rth   # 美股含盘前盘后

# 其他
python3 cli.py strategies            # 列出全部策略及可优化参数
python3 cli.py info --symbol 700.HK  # 手数 / 最新价
python3 cli.py run-all               # 完整流水线：分析 → 组合 → 优化 → 汇总报告
python3 cli.py run-all --symbols 700.HK 9988.HK --auto-trade --mode paper
```

## 策略

策略注册表见 `src/strategies.py`，用 `--strategies` 自由组合，列表中**靠后的策略信号覆盖靠前的**：

| key | 策略 | 可优化参数 |
|---|---|---|
| `ma_cross` | 均线金叉/死叉（默认） | ma_short, ma_long |
| `rsi` | RSI 超买超卖（默认） | rsi_period, rsi_oversold, rsi_overbought |
| `macd` | MACD 金叉/死叉（默认） | — |
| `bollinger` | 布林带均值回归 | bb_period, bb_std |
| `trend` | 趋势线穿越（收盘价上/下穿 MA_trend） | ma_trend |
| `donchian` | 唐奇安通道突破（N 日最高/最低价） | donchian_period |

默认策略集 `ma_cross rsi macd`：
- 买入 (1)：MA 金叉 | RSI < 30 | MACD 金叉
- 卖出 (-1)：MA 死叉 | RSI > 70 | MACD 死叉

每个信号都带 `Signal_Reason` 列说明触发原因（如 "MA5/MA10 金叉"、"跌破布林下轨 (20日,2σ)"），回测交易记录同样带原因和单笔盈亏。

优化器（`optimize`）的搜索网格 = 所选策略声明的参数子空间的并集，结果 JSON 记录 `strategies` 字段。

## 风险控制

**命令行参数**（单次生效，单位 %）：

```bash
python3 cli.py trade --mode paper --stop-loss 5 --take-profit 15 --max-positions 5 --position-size 20
```

**默认值**（改一次永久生效）：`src/config.py` 中的 `DEFAULT_TRADE`：

```python
DEFAULT_TRADE = {
    "mode": "paper",         # paper 模拟盘 / live 实盘
    "max_positions": 5,      # 最大持仓股票数
    "position_size": 0.2,    # 单仓占可用资金比例（20%）
    "stop_loss": 0.05,       # 止损 5%（小数，非百分比）
    "take_profit": 0.15,     # 止盈 15%
    "check_interval": 10,    # 交易循环间隔（秒）
    "order_cooldown": 300,   # 同标的同方向下单最小间隔（秒），防拒单后无限重试
    ...
}
```

交易循环每 `check_interval` 秒检查一次持仓浮动盈亏，触发止损/止盈自动卖出。内置安全机制：

- 止损/止盈与信号交易都受**交易时段门控**（港股 09:30–16:00 本地时间，美股 09:30–16:00 美东时间；`--outside-rth` 放开美股盘前盘后）
- **防重复下单**：同标的同方向已有在场订单不再下单；每次下单尝试后进入 `order_cooldown` 冷却
- 默认 paper 模拟盘，实盘需显式 `--mode live`

## 输出位置

| 产物 | 路径 |
|---|---|
| 股票分析 | `results/<SYM>_analysis.csv` |
| 运行参数（含策略集） | `results/<SYM>_analysis_params.json` |
| 回测结果 | `results/<SYM>_backtest_<ts>.csv` |
| 投资组合 | `results/portfolio_allocation.csv` |
| 交易状态 | `results/trading_state.json` |
| 优化结果 | `results/optimizations/<sym>/optimization_<ts>.json` |
| 汇总报告 | `results/summary_report_<ts>.md` |
| 优化图表 | `plots/optimizations/` |

## 项目结构

```
quantitative_trading/
├── cli.py                    # 唯一 CLI 入口（自动引导 venv）
├── src/
│   ├── config.py             # 默认参数、股票列表、路径约定
│   ├── data.py               # LongPort 数据拉取
│   ├── indicators.py         # 技术指标计算
│   ├── strategies.py         # 策略注册表
│   ├── signals.py            # 信号生成（Trade_Signal + Signal_Reason）
│   ├── backtest.py           # 回测引擎
│   ├── portfolio.py          # 投资组合分析
│   ├── optimizer.py          # 多进程网格搜索
│   ├── trader.py             # 自动交易（AutoTrader + 交易循环）
│   └── repl.py               # 交互模式
├── requirements.txt
├── results/                  # 分析结果（运行时生成）
└── plots/                    # 图表（运行时生成）
```

旧版脚本（`quant_trading.py`、`multi_stock_analysis.py`、`strategy_optimizer.py`、`auto_trader.py`、`run_all_analysis.py`）仍可独立运行，但不再维护，功能已全部并入 `cli.py`。

## 注意事项

- 本程序仅供学习和研究使用，不构成投资建议
- 交易决策请自行判断，使用真实资金前请在 paper 模拟盘充分测试策略
- API 密钥请妥善保管，`.env` 不要提交到版本库
- 默认为模拟交易模式，切换 `--mode live` 实盘前请务必谨慎评估风险
