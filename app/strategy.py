from __future__ import annotations

from statistics import fmean
from typing import Optional
from .config import Config
from .models import Candle, RuntimeState, Signal


class HermanTrendRebalanceStrategy:
    """1:1 execution translation of Trend Rebalance Map [Herman] v1.1 signal/level logic.

    Deliberately does not optimize or reinterpret the Pine logic.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _sma(self, closes: list[float], length: int) -> float:
        return fmean(closes[-length:])

    def evaluate(self, candles: list[Candle], state: RuntimeState, flat: bool) -> Optional[Signal]:
        need = max(self.cfg.sma200_length, self.cfg.sma50_length) + 1
        if len(candles) < need:
            return None

        current = candles[-1]
        closes = [x.c for x in candles]

        sma50 = self._sma(closes, self.cfg.sma50_length)
        sma50_prev = fmean(closes[-self.cfg.sma50_length - 1:-1])
        sma200 = self._sma(closes, self.cfg.sma200_length)

        # Pine ta.crossover(close, sma50) / ta.crossunder(close, sma50)
        bullish_cross = closes[-1] > sma50 and closes[-2] <= sma50_prev
        bearish_cross = closes[-1] < sma50 and closes[-2] >= sma50_prev

        long_direction = sma50 < sma200
        short_direction = sma50 > sma200
        separation_points = abs(sma50 - sma200) / self.cfg.point_size
        separation_pass = separation_points > self.cfg.min_separation

        long_signal = bullish_cross and long_direction and separation_pass
        short_signal = bearish_cross and short_direction and separation_pass

        can_enter = (
            flat
            and (state.last_entry_bar is None or current.t != state.last_entry_bar)
            and (state.last_exit_bar is None or current.t != state.last_exit_bar)
        )

        if self.cfg.enable_longs and long_signal and can_enter:
            entry = current.c
            initial_tp = (
                sma200 if self.cfg.tp_mode == "200 SMA"
                else entry + self.cfg.tp_fixed_points * self.cfg.point_size
            )
            target_distance = abs(initial_tp - entry)
            sl = (
                entry - target_distance if self.cfg.sl_mode == "1R to TP"
                else entry - self.cfg.sl_fixed_points * self.cfg.point_size
            )
            return Signal("LONG", current.t, entry, initial_tp, sl, sma50, sma200, separation_points)

        if self.cfg.enable_shorts and short_signal and can_enter:
            entry = current.c
            initial_tp = (
                sma200 if self.cfg.tp_mode == "200 SMA"
                else entry - self.cfg.tp_fixed_points * self.cfg.point_size
            )
            target_distance = abs(entry - initial_tp)
            sl = (
                entry + target_distance if self.cfg.sl_mode == "1R to TP"
                else entry + self.cfg.sl_fixed_points * self.cfg.point_size
            )
            return Signal("SHORT", current.t, entry, initial_tp, sl, sma50, sma200, separation_points)

        return None

    def current_dynamic_tp(self, candles: list[Candle], state: RuntimeState) -> Optional[float]:
        if state.active_side == 0:
            return None
        if self.cfg.tp_mode != "200 SMA":
            return state.active_tp_at_entry
        if self.cfg.sma_target_behaviour != "Dynamic":
            return state.active_tp_at_entry
        if len(candles) < self.cfg.sma200_length:
            return None
        closes = [x.c for x in candles]
        return fmean(closes[-self.cfg.sma200_length:])
