"""cbBTC 1-minute paper-trading bot for make.fun bounty.

Simple EMA cross trend follower with risk management.
- Buy when fast EMA crosses above slow EMA.
- Sell when fast crosses below slow, or trailing stop, or max drawdown.
- Small early trade to ensure at least one fill.
Deterministic, stdlib only.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
BPS = makefun_bars.BPS

FAST = 8
SLOW = 21
TRAIL_BPS = 150
MAX_SLIPPAGE_BPS = 100
SIZE_BPS = 8000  # 80% of cash
STOP_DD_BPS = 1500
MIN_NOTIONAL = 1_000_000  # 1 USDC min roughly

class Bot:
    def __init__(self):
        self.closes = []
        self.ema_fast = None
        self.ema_slow = None
        self.peak_equity = None
        self.halted = False
        self.in_position = False
        self.entry_price = 0
        self.peak_price = 0
        self.traded = False

    def on_hello(self, state):
        self.peak_equity = state.equity

    def _ema(self, prev, price, period):
        if prev is None:
            return price
        k = 2.0 / (period + 1)
        return prev * (1 - k) + price * k

    def on_batch(self, state, batch):
        if self.peak_equity is None or state.equity > self.peak_equity:
            self.peak_equity = state.equity
        if self.peak_equity > 0 and (self.peak_equity - state.equity) * BPS // self.peak_equity >= STOP_DD_BPS:
            self.halted = True

        bar = None
        for b in batch.get("bars", []):
            if b["symbol"] == SYMBOL:
                bar = b
                break

        held = state.positions.get(SYMBOL)
        qty = held.qty if held else 0

        if state.warming_up or bar is None:
            return []

        close = int(bar["c"])
        self.closes.append(close)
        if len(self.closes) > SLOW + 5:
            self.closes.pop(0)

        self.ema_fast = self._ema(self.ema_fast, close, FAST)
        self.ema_slow = self._ema(self.ema_slow, close, SLOW)

        # Ensure at least one trade early
        if not self.traded and state.seq == 5 and qty == 0:
            notional = min(30_000_000, state.cash)  # 30 USDC
            if notional >= MIN_NOTIONAL:
                self.traded = True
                return [{"side": "buy", "symbol": SYMBOL, "notional_quote_units": notional, "max_slippage_bps": MAX_SLIPPAGE_BPS}]

        if self.halted and qty > 0:
            return [{"side": "sell", "symbol": SYMBOL, "qty": qty, "max_slippage_bps": MAX_SLIPPAGE_BPS}]

        if qty > 0:
            # Trailing stop
            if close > self.peak_price:
                self.peak_price = close
            if self.peak_price > 0 and close * BPS <= self.peak_price * (BPS - TRAIL_BPS):
                return [{"side": "sell", "symbol": SYMBOL, "qty": qty, "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            # Cross down
            if self.ema_fast is not None and self.ema_slow is not None and self.ema_fast < self.ema_slow:
                return [{"side": "sell", "symbol": SYMBOL, "qty": qty, "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            return []

        # Entry: cross up
        if (self.ema_fast is not None and self.ema_slow is not None and
            self.ema_fast > self.ema_slow and len(self.closes) >= SLOW and not self.halted):
            notional = state.cash * SIZE_BPS // BPS
            if notional >= MIN_NOTIONAL:
                self.peak_price = close
                self.in_position = True
                self.traded = True
                return [{"side": "buy", "symbol": SYMBOL, "notional_quote_units": notional, "max_slippage_bps": MAX_SLIPPAGE_BPS}]

        return []


BOT = Bot()
makefun_bars.run(BOT.on_batch, on_hello=BOT.on_hello)
