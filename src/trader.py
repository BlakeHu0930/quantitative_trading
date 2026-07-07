#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import asyncio
import time
import json
import traceback
import decimal
import enum
import logging
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from longport.openapi import (
    Config, TradeContext, QuoteContext,
    OrderSide, OrderType, TimeInForceType, OrderStatus,
)
from src.config import DEFAULT_TRADE, get_market, get_currency, analysis_csv_path, symbol_from_analysis_file
from src.data import get_longport_config, get_lot_size

# OutsideRTH 在旧版 SDK 可能不存在，按需导入
try:
    from longport.openapi import OutsideRTH
    _RTH_ANY_TIME = OutsideRTH.AnyTime
except ImportError:
    _RTH_ANY_TIME = None

load_dotenv()

logger = logging.getLogger("trader")


class _StateEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        if isinstance(o, (enum.Enum, OrderStatus)):
            return str(o)
        return super().default(o)


def _order_finished(status) -> bool:
    """订单是否已终结（拒单/取消/过期/全部成交）。部分成交仍在场内。"""
    s = str(status).upper()
    if any(t in s for t in ("REJECTED", "CANCELED", "CANCELLED", "EXPIRED")):
        return True
    return "FILLED" in s and "PARTIAL" not in s


class AutoTrader:
    """自动化交易（支持 paper / live 两种模式）。"""

    def __init__(self, trade_config: dict = None):
        self.cfg = {**DEFAULT_TRADE, **(trade_config or {})}
        self.positions: dict = {}
        self.orders: dict = {}
        self._lot_sizes: dict = {}  # 手数是静态的，按 symbol 缓存
        self._last_order_attempt: dict = {}  # (symbol, side) → time.time()，下单冷却
        self.trade_ctx = None
        self.quote_ctx = None
        self.is_trading = False
        os.makedirs("results", exist_ok=True)

    # ------------------------------------------------------------------ #
    #  初始化
    # ------------------------------------------------------------------ #

    async def initialize(self) -> bool:
        try:
            config = get_longport_config()
            self.trade_ctx = TradeContext(config)
            self.quote_ctx = QuoteContext(config)
            # 验证凭证
            resp = self.trade_ctx.account_balance()
            if not resp:
                logger.error("账户余额响应为空")
                return False
            logger.info("交易上下文初始化成功")
            return True
        except Exception as e:
            logger.error(f"初始化失败: {e}\n{traceback.format_exc()}")
            return False

    # ------------------------------------------------------------------ #
    #  持仓 & 行情
    # ------------------------------------------------------------------ #

    async def update_positions(self):
        try:
            resp = self.trade_ctx.stock_positions()
            positions_dict = {}
            positions = []
            if hasattr(resp, "channels"):
                for ch in (resp.channels or []):
                    positions.extend(getattr(ch, "positions", []) or [])
            elif hasattr(resp, "positions"):
                positions = resp.positions or []
            elif isinstance(resp, list):
                positions = resp

            for pos in positions:
                sym = getattr(pos, "symbol", None)
                if not sym:
                    continue
                qty = float(getattr(pos, "quantity", 0))
                cost = float(getattr(pos, "cost_price", 0) or getattr(pos, "avg_price", 0) or 0)
                positions_dict[sym] = {
                    "symbol": sym,
                    "quantity": qty,
                    "cost_price": cost,
                    "current_price": cost,
                    "market_value": qty * cost,
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            self.positions = positions_dict
            logger.info(f"持仓更新: {list(self.positions.keys())}")
            await self.save_state()
        except Exception as e:
            logger.error(f"更新持仓失败: {e}")

    async def update_market_data(self) -> bool:
        if not self.positions:
            return True
        symbols = list(self.positions.keys())
        try:
            resp = self.quote_ctx.quote(symbols)
            quotes = resp if isinstance(resp, list) else getattr(resp, "quotes", [])
            for q in quotes:
                sym = getattr(q, "symbol", None)
                price = getattr(q, "last_done", None)
                if sym and sym in self.positions and price:
                    self.positions[sym]["current_price"] = float(price)
            return True
        except Exception as e:
            logger.error(f"更新行情失败: {e}")
            return False

    # ------------------------------------------------------------------ #
    #  下单
    # ------------------------------------------------------------------ #

    def _get_lot_size(self, symbol: str) -> int:
        if symbol not in self._lot_sizes:
            self._lot_sizes[symbol] = get_lot_size(symbol, quote_ctx=self.quote_ctx)
        return self._lot_sizes[symbol]

    def _can_submit(self, symbol: str, side: str) -> bool:
        """防重复下单：同标的同方向已有在场订单、或距上次尝试不足 order_cooldown 秒时不下单。"""
        if any(
            o["symbol"] == symbol and o["side"] == side and not _order_finished(o["status"])
            for o in self.orders.values()
        ):
            logger.debug(f"{symbol} 已有在场{side}单，跳过")
            return False
        last = self._last_order_attempt.get((symbol, side))
        if last and time.time() - last < self.cfg.get("order_cooldown", 300):
            logger.debug(f"{symbol} {side}单冷却中，跳过")
            return False
        return True

    def _submit_order(self, symbol: str, side: OrderSide, price: float, quantity: int) -> str:
        """构造并提交限价单，记录到 self.orders，返回 order_id。"""
        side_name = "Buy" if side == OrderSide.Buy else "Sell"
        # 无论提交成功与否都进入冷却，避免异常/拒单时每个循环重试
        self._last_order_attempt[(symbol, side_name)] = time.time()
        order_kwargs = dict(
            symbol=symbol,
            order_type=OrderType.LO,
            side=side,
            submitted_price=decimal.Decimal(str(round(float(price), 4))),
            submitted_quantity=quantity,
            time_in_force=TimeInForceType.Day,
        )
        # 美股支持盘前/盘后交易
        if get_market(symbol) == "US" and self.cfg.get("outside_rth") and _RTH_ANY_TIME:
            order_kwargs["outside_rth"] = _RTH_ANY_TIME
        result = self.trade_ctx.submit_order(**order_kwargs)
        oid = result.order_id
        self.orders[oid] = {
            "symbol": symbol, "side": side_name,
            "quantity": quantity, "price": float(price), "status": "submitted",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        return oid

    async def place_buy_order(self, symbol: str, price: float) -> bool:
        if symbol in self.positions:
            logger.info(f"已持有 {symbol}，跳过买入")
            return False
        if len(self.positions) >= self.cfg["max_positions"]:
            logger.info("持仓数达上限，跳过买入")
            return False
        if not self._can_submit(symbol, "Buy"):
            return False

        # 查可用资金（按股票货币过滤）
        currency = get_currency(symbol)
        try:
            bal_resp = self.trade_ctx.account_balance(currency=currency)
        except TypeError:
            bal_resp = self.trade_ctx.account_balance()
        try:
            available_cash = 0.0
            accounts = bal_resp if isinstance(bal_resp, list) else getattr(bal_resp, "balances", [])
            if accounts:
                acct = accounts[0]
                cash_infos = getattr(acct, "cash_infos", []) or []
                for ci in cash_infos:
                    if getattr(ci, "currency", "") == currency:
                        available_cash = float(ci.available_cash) * self.cfg["position_size"]
                        break
                if available_cash == 0:
                    available_cash = float(acct.total_cash) * self.cfg["position_size"]
        except Exception as e:
            logger.error(f"获取账户余额失败: {e}")
            return False

        if available_cash <= 0:
            logger.info(f"可用资金不足 ({currency}): {available_cash:.2f}")
            return False

        # 计算股数：美股 lot_size=1，港股默认 100，API 查询确认
        lot_size = self._get_lot_size(symbol)
        quantity = int(available_cash / price / lot_size) * lot_size
        if quantity <= 0:
            logger.info(f"资金不足以买入一手 {symbol}（lot={lot_size}, 可用={available_cash:.2f} {currency}）")
            return False

        try:
            oid = self._submit_order(symbol, OrderSide.Buy, price, quantity)
            mode = "[模拟]" if self.cfg["mode"] == "paper" else ""
            logger.info(f"{mode} 买入: {symbol} {quantity} 股 @ {price:.4f} {currency} (id={oid})")
            return True
        except Exception as e:
            logger.error(f"买入下单失败: {e}")
            return False

    async def place_sell_order(self, symbol: str, price: float) -> bool:
        if symbol not in self.positions:
            logger.info(f"未持有 {symbol}，跳过卖出")
            return False
        if not self._can_submit(symbol, "Sell"):
            return False

        pos = self.positions[symbol]
        quantity = int(float(pos["quantity"]))

        try:
            oid = self._submit_order(symbol, OrderSide.Sell, price, quantity)
            cost = float(pos["cost_price"])
            pnl = (float(price) - cost) * quantity
            pnl_pct = (float(price) / cost - 1) * 100 if cost else 0
            currency = get_currency(symbol)
            mode = "[模拟]" if self.cfg["mode"] == "paper" else ""
            logger.info(f"{mode} 卖出: {symbol} {quantity} 股 @ {price:.4f} {currency}  盈亏 {pnl:+.2f} ({pnl_pct:+.2f}%) (id={oid})")
            return True
        except Exception as e:
            logger.error(f"卖出下单失败: {e}")
            return False

    # ------------------------------------------------------------------ #
    #  订单管理
    # ------------------------------------------------------------------ #

    async def update_orders(self):
        try:
            try:
                resp = self.trade_ctx.today_orders()
            except AttributeError:
                end = datetime.now()
                resp = self.trade_ctx.history_orders(start_at=end - timedelta(days=7), end_at=end)

            orders_list = resp if isinstance(resp, list) else getattr(resp, "orders", [])
            has_fills = False
            for order in orders_list:
                oid = getattr(order, "order_id", None) or order.get("order_id")
                if not oid or oid not in self.orders:
                    continue
                status = getattr(order, "status", None)
                if isinstance(status, (enum.Enum, OrderStatus)):
                    status = str(status)
                old = self.orders[oid]["status"]
                self.orders[oid]["status"] = status
                if old != status:
                    logger.info(f"订单 {oid} 状态: {old} → {status}")
                if any(s in str(status).upper() for s in ["FILLED", "PARTIALLY_FILLED"]):
                    has_fills = True
            if has_fills:
                await self.update_positions()

            # 取消超 24h 未成交订单
            now = datetime.now()
            for oid, order in list(self.orders.items()):
                if _order_finished(order["status"]):
                    continue
                try:
                    t = datetime.strptime(order["time"], "%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError):
                    continue
                if (now - t).total_seconds() > 86400:
                    try:
                        self.trade_ctx.cancel_order(order_id=oid)
                    except Exception:
                        pass  # 订单可能已在交易所侧终结，本地标记即可
                    self.orders[oid]["status"] = "CANCELED"
                    logger.info(f"取消超时订单 {oid}")
            await self.save_state()
        except Exception as e:
            logger.error(f"更新订单状态失败: {e}")

    # ------------------------------------------------------------------ #
    #  止损 / 止盈
    # ------------------------------------------------------------------ #

    async def check_stop_loss_take_profit(self):
        if not self.positions:
            return
        await self.update_market_data()
        sl = -abs(self.cfg["stop_loss"])
        tp = abs(self.cfg["take_profit"])
        outside_rth = self.cfg.get("outside_rth", False)
        for sym, pos in list(self.positions.items()):
            # 与信号交易同样受交易时段门控，休市时下单只会被拒
            if not _in_trading_hours(sym, self.cfg["trading_hours"], outside_rth):
                continue
            cost = float(pos.get("cost_price", 0))
            curr = float(pos.get("current_price", 0))
            if cost <= 0 or curr <= 0:
                continue
            ratio = (curr - cost) / cost
            if ratio <= sl and self._can_submit(sym, "Sell"):
                logger.warning(f"止损触发 {sym}: {ratio:.2%}  止损线 {sl:.2%}")
                await self.place_sell_order(sym, curr)
            elif ratio >= tp and self._can_submit(sym, "Sell"):
                logger.warning(f"止盈触发 {sym}: {ratio:.2%}  止盈线 {tp:.2%}")
                await self.place_sell_order(sym, curr)

    # ------------------------------------------------------------------ #
    #  信号处理
    # ------------------------------------------------------------------ #

    async def process_signals(self, signals_df: pd.DataFrame):
        if not self.is_trading or signals_df.empty:
            return
        outside_rth = self.cfg.get("outside_rth", False)
        for _, row in signals_df.iterrows():
            sym = row.get("symbol")
            signal = int(row.get("Trade_Signal", 0))
            price = float(row.get("close", 0))
            if not _in_trading_hours(sym, self.cfg["trading_hours"], outside_rth):
                continue
            if signal == 1:
                await self.place_buy_order(sym, price)
            elif signal == -1:
                await self.place_sell_order(sym, price)

    # ------------------------------------------------------------------ #
    #  状态持久化
    # ------------------------------------------------------------------ #

    async def save_state(self):
        with open("results/trading_state.json", "w") as f:
            json.dump(
                {"positions": self.positions, "orders": self.orders,
                 "is_trading": self.is_trading,
                 "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                f, indent=2, cls=_StateEncoder,
            )

    async def load_state(self) -> bool:
        path = "results/trading_state.json"
        if not os.path.exists(path):
            return False
        try:
            with open(path) as f:
                state = json.load(f)
            self.orders = state.get("orders", {})
            self.is_trading = state.get("is_trading", False)
            # 持仓在 start() 初始化交易上下文之后再拉取，此处 trade_ctx 尚未就绪
            return True
        except Exception as e:
            logger.error(f"加载状态失败: {e}")
            return False

    # ------------------------------------------------------------------ #
    #  启停
    # ------------------------------------------------------------------ #

    async def start(self) -> bool:
        logger.info(f"启动交易系统 (模式: {self.cfg['mode']}，止损: {self.cfg['stop_loss']:.0%}，止盈: {self.cfg['take_profit']:.0%})")
        if not await self.initialize():
            return False
        await self.update_positions()
        await self.update_orders()
        self.is_trading = True
        await self.save_state()
        return True

    async def stop(self):
        self.is_trading = False
        await self.save_state()
        logger.info("交易系统已停止")


# ------------------------------------------------------------------ #
#  主循环
# ------------------------------------------------------------------ #

async def trading_loop(target_symbol: str = None, trade_config: dict = None):
    """自动交易主循环。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )
    trader = AutoTrader(trade_config)
    await trader.load_state()

    retry = 0
    while retry < 3:
        if await trader.start():
            break
        retry += 1
        await asyncio.sleep(5 * retry)
    else:
        logger.error("交易系统启动失败，退出")
        return

    interval = trader.cfg["check_interval"]
    signal_cache = {}  # fp → (mtime, 单行信号 DataFrame)；文件未变时跳过重新解析

    try:
        cycle = 0
        while trader.is_trading:
            t0 = time.time()
            cycle += 1
            logger.info(f"─── 交易循环 #{cycle} ───")
            if cycle % 6 == 0:
                try:
                    if not trader.trade_ctx.account_balance():
                        await trader.initialize()
                except Exception:
                    await trader.initialize()

            # 收集信号
            all_signals = pd.DataFrame()
            result_dir = "results"
            files = (
                [analysis_csv_path(target_symbol)]
                if target_symbol
                else [os.path.join(result_dir, f) for f in os.listdir(result_dir) if f.endswith("_analysis.csv")]
            )
            for fp in files:
                if not os.path.exists(fp):
                    continue
                try:
                    mtime = os.path.getmtime(fp)
                    cached = signal_cache.get(fp)
                    if cached and cached[0] == mtime:
                        latest = cached[1]
                    else:
                        df = pd.read_csv(fp)
                        if df.empty:
                            continue
                        sig_col = "Trade_Signal" if "Trade_Signal" in df.columns else ("Signal" if "Signal" in df.columns else None)
                        if not sig_col:
                            continue
                        latest = df.tail(1)[["time", "close", sig_col]].copy()
                        if sig_col != "Trade_Signal":
                            latest = latest.rename(columns={sig_col: "Trade_Signal"})
                        latest["symbol"] = symbol_from_analysis_file(fp)
                        signal_cache[fp] = (mtime, latest)
                    all_signals = pd.concat([all_signals, latest], ignore_index=True)
                except Exception as e:
                    logger.error(f"读取信号文件失败 {fp}: {e}")

            await trader.process_signals(all_signals)
            await trader.update_orders()
            await trader.check_stop_loss_take_profit()
            await trader.save_state()

            elapsed = time.time() - t0
            await asyncio.sleep(max(0.1, interval - elapsed))

    except KeyboardInterrupt:
        logger.info("收到中断信号")
    finally:
        await trader.stop()


def _in_trading_hours(symbol: str, trading_hours: dict, outside_rth: bool = False) -> bool:
    """
    检查当前时间是否在交易时段内（时区由 trading_hours 配置决定，tz=None 用本地时间）。
    outside_rth=True 时跳过时间检查，允许美股盘前/盘后下单。
    """
    market = get_market(symbol)
    if market == "US" and outside_rth:
        return True  # 盘前/盘后模式不做时间限制
    hours = trading_hours.get(market)
    if not hours:
        return False
    tz = hours.get("tz")
    now = datetime.now(ZoneInfo(tz)) if tz else datetime.now()
    now = now.strftime("%H:%M")
    return hours["start"] <= now <= hours["end"]
