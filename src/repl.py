#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""交互模式（REPL）：python cli.py 无参数进入。

- 子命令/选项/股票代码/策略键自动补全（需 prompt_toolkit，缺失时回退 input()）
- use <代码> 设置当前股票，后续命令自动注入 --symbol
- argparse 报错 / Ctrl+C 不会退出循环
"""

import argparse
import glob
import os
import shlex
import sys
import traceback

from src.config import DEFAULT_STOCKS, symbol_from_analysis_file
from src.strategies import strategy_choices

HISTORY_FILE = os.path.expanduser("~/.quant_cli_history")
BUILTINS = ("use", "help", "exit", "quit")


def _subcommand_map(parser: argparse.ArgumentParser) -> dict:
    """内省 argparse，返回 {子命令: [option_strings...]}。"""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return {
                name: [s for a in sub._actions for s in a.option_strings]
                for name, sub in action.choices.items()
            }
    return {}


def _known_symbols() -> list:
    """默认股票 + results/ 下已有分析文件推断出的代码。"""
    symbols = list(DEFAULT_STOCKS)
    for fp in glob.glob("results/*_analysis.csv"):
        sym = symbol_from_analysis_file(fp)
        if sym not in symbols:
            symbols.append(sym)
    return symbols


def _make_completer(parser):
    from prompt_toolkit.completion import Completer, Completion

    sub_map = _subcommand_map(parser)
    symbols = _known_symbols()
    strategies = strategy_choices()

    class QuantCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            words = text.split()
            # 正在输入的词（行尾无空格时为最后一个词）
            current = "" if (not words or text.endswith(" ")) else words[-1]
            done = words[: len(words) - (0 if text.endswith(" ") or not words else 1)]

            if not done:  # 第一个词：子命令 + 内建命令
                candidates = list(sub_map) + list(BUILTINS)
            elif done[0] == "use":
                candidates = symbols
            elif done[0] == "help":
                candidates = list(sub_map)
            elif done[0] in sub_map:
                prev = done[-1]
                opts = sub_map[done[0]]
                if prev in ("--symbol", "--symbols"):
                    candidates = symbols
                elif prev == "--strategies":
                    candidates = strategies
                elif prev in strategies and "--strategies" in done:
                    candidates = strategies + opts  # nargs 连续输入中，也可直接接下一个选项
                else:
                    candidates = opts
            else:
                candidates = []

            for c in candidates:
                if c.startswith(current):
                    yield Completion(c, start_position=-len(current))

    return QuantCompleter()


def _make_prompt(parser):
    """交互终端且装了 prompt_toolkit → 带补全/历史；否则回退 input()。"""
    if not sys.stdin.isatty():
        return input
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory

        session = PromptSession(
            history=FileHistory(HISTORY_FILE),
            completer=_make_completer(parser),
            complete_while_typing=True,
        )
        return session.prompt
    except ImportError:
        return input


def _symbol_subcommands(parser) -> set:
    """拥有 --symbol 选项的子命令（use 注入的目标）。"""
    return {
        name for name, opts in _subcommand_map(parser).items()
        if "--symbol" in opts
    }


def _inject_symbol(tokens: list, symbol: str, symbol_cmds: set) -> list:
    if (
        symbol
        and tokens[0] in symbol_cmds
        and not any(t in ("--symbol", "--symbols", "--file") for t in tokens)
    ):
        return tokens + ["--symbol", symbol]
    return tokens


def run_repl(parser: argparse.ArgumentParser) -> None:
    current_symbol = None
    prompt = _make_prompt(parser)
    symbol_cmds = _symbol_subcommands(parser)

    print("量化交易 REPL — help 查看命令, use <代码> 设置当前股票, exit 退出")
    while True:
        ps = f"quant[{current_symbol}]> " if current_symbol else "quant> "
        try:
            line = prompt(ps).strip()
        except KeyboardInterrupt:
            continue  # Ctrl+C 清行
        except EOFError:
            break  # Ctrl+D / 管道结束
        if not line:
            continue

        try:
            tokens = shlex.split(line)
        except ValueError as e:
            print(f"解析失败: {e}")
            continue

        cmd = tokens[0]
        if cmd in ("exit", "quit"):
            break
        if cmd == "use":
            current_symbol = tokens[1] if len(tokens) > 1 else None
            print(f"当前股票: {current_symbol}" if current_symbol else "已清除当前股票")
            continue
        if cmd == "help":
            tokens = ([tokens[1], "-h"] if len(tokens) > 1 else ["-h"])
        if cmd == "repl":
            print("已在 REPL 中")
            continue

        tokens = _inject_symbol(tokens, current_symbol, symbol_cmds)
        try:
            args = parser.parse_args(tokens)
            args.func(args)
        except SystemExit:
            pass  # argparse 已打印 error/usage/help
        except KeyboardInterrupt:
            print("\n(已中断)")
        except Exception:
            traceback.print_exc()
    print("再见")
