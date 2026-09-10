from __future__ import annotations

import logging
import signal
import time
from datetime import datetime
from statistics import fmean
from typing import Optional

from .config import Config
from .executor import BaseExecutor, DryRunExecutor, HyperliquidExecutor
from .market_data import HyperliquidMarketData
from .models import RuntimeState
from .state import StateStore
from .strategy import HermanTrendRebalanceStrategy

log = logging.getLogger(__name__)


class TradingBot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.market = HyperliquidMarketData(cfg.network, cfg.coin, cfg.interval)
        self.strategy = HermanTrendRebalanceStrategy(cfg)
        self.store = StateStore(cfg.state_path)
        self.state = self.store.load()
        self.executor: BaseExecutor = DryRunExecutor(cfg) if cfg.dry_run else HyperliquidExecutor(cfg)
        self.running = True

    def stop(self, *_):
        self.running = False

    def _sync_position_state(self, bar_time: int) -> tuple[bool, float]:
        pos = self.executor.position()
        flat = pos.flat

        # A position previously managed by us has closed via TP/SL.
        if self.state.active_side != 0 and flat:
            log.info("Position closed; clearing remaining protective orders")
            self.executor.cancel_all_protection()
            self.state.last_exit_bar = bar_time
            self.state.active_side = 0
            self.state.active_entry = None
            self.state.active_sl = None
            self.state.active_tp_at_entry = None
            self.state.tp_order_oid = None
            self.state.sl_order_oid = None
            self.store.save(self.state)

        # Restart recovery: live position exists while local state is empty.
        elif self.state.active_side == 0 and not flat:
            tp_oid, tp_px, sl_oid, sl_px = self.executor.recover_protection()
            if tp_px is None or sl_px is None:
                raise RuntimeError(
                    "Existing position detected but its TP/SL could not be recovered. "
                    "Refusing to issue new trades; restore/correct protective orders first."
                )
            self.state.active_side = 1 if pos.size > 0 else -1
            self.state.active_entry = pos.entry_px
            self.state.active_tp_at_entry = tp_px
            self.state.active_sl = sl_px
            self.state.tp_order_oid = tp_oid
            self.state.sl_order_oid = sl_oid
            self.store.save(self.state)
            log.info("Recovered existing position size=%s TP=%s SL=%s", pos.size, tp_px, sl_px)

        return flat, pos.size

    def _log_heartbeat(self, candles, position_size: float, has_signal: bool) -> None:
        """Print one compact status line for every newly closed 1m candle."""
        if not candles:
            return
        bar = candles[-1]
        bar_time = datetime.fromtimestamp(bar.t / 1000).astimezone().strftime("%H:%M")
        closes = [x.c for x in candles]

        if len(closes) < self.cfg.sma200_length:
            log.info(
                "[%s] XYZ100=%.2f | SMA warmup %s/%s | 等待数据",
                bar_time,
                bar.c,
                len(closes),
                self.cfg.sma200_length,
            )
            return

        sma50 = fmean(closes[-self.cfg.sma50_length:])
        sma200 = fmean(closes[-self.cfg.sma200_length:])
        separation = abs(sma50 - sma200) / self.cfg.point_size

        if position_size > 0:
            status = "持有 LONG"
        elif position_size < 0:
            status = "持有 SHORT"
        elif has_signal:
            status = "触发信号"
        else:
            status = "等待信号"

        log.info(
            "[%s] XYZ100=%.2f | SMA50=%.2f | SMA200=%.2f | Sep=%.2f | %s",
            bar_time,
            bar.c,
            sma50,
            sma200,
            separation,
            status,
        )

    def process_once(self) -> bool:
        candles = self.market.fetch_recent(self.cfg.lookback_candles)
        if not candles:
            return False
        bar = candles[-1]
        if self.state.last_processed_bar == bar.t:
            return False

        if isinstance(self.executor, DryRunExecutor):
            self.executor.set_mid(bar.c)

        flat, position_size = self._sync_position_state(bar.t)

        # Dynamic 200-SMA target follows the current closed-bar SMA200 while trade is open.
        if not flat and self.state.active_side != 0:
            dynamic_tp = self.strategy.current_dynamic_tp(candles, self.state)
            if dynamic_tp is not None and self.cfg.tp_mode == "200 SMA" and self.cfg.sma_target_behaviour == "Dynamic":
                self.state.tp_order_oid = self.executor.update_tp(
                    position_size,
                    self.state.tp_order_oid,
                    dynamic_tp,
                )
                self.store.save(self.state)
                log.info("Dynamic TP updated to %.4f", dynamic_tp)

        # Refresh flat after synchronization. Opposite signals are ignored while open.
        pos = self.executor.position()
        flat = pos.flat
        decision = self.strategy.evaluate(candles, self.state, flat)

        # One heartbeat per newly closed candle, even when there is no trade signal.
        self._log_heartbeat(candles, pos.size, decision is not None)

        if decision is not None:
            is_buy = decision.side == "LONG"
            log.info(
                "Signal %s bar=%s close=%.4f SMA50=%.4f SMA200=%.4f sep=%.2f",
                decision.side, decision.bar_time, decision.entry_reference,
                decision.sma50, decision.sma200, decision.separation_points,
            )
            resp = self.executor.open_market(is_buy, self.cfg.order_notional_usdc)
            log.info("Entry response: %s", resp)

            # Confirm actual position exists before placing protection.
            pos = self.executor.position()
            if pos.flat:
                raise RuntimeError("Entry response returned but no live position was found")

            tp_oid, sl_oid = self.executor.place_protection(pos.size, decision.initial_tp, decision.sl)
            self.state.active_side = 1 if pos.size > 0 else -1
            self.state.active_entry = decision.entry_reference
            self.state.active_tp_at_entry = decision.initial_tp
            self.state.active_sl = decision.sl
            self.state.last_entry_bar = decision.bar_time
            self.state.tp_order_oid = tp_oid
            self.state.sl_order_oid = sl_oid
            self.store.save(self.state)

        self.state.last_processed_bar = bar.t
        self.store.save(self.state)
        return True

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info(
            "Starting Herman executor: network=%s coin=%s dry_run=%s",
            self.cfg.network, self.cfg.coin, self.cfg.dry_run,
        )
        failures = 0
        while self.running:
            try:
                changed = self.process_once()
                failures = 0
                time.sleep(self.cfg.poll_seconds if not changed else 0.5)
            except Exception:
                failures += 1
                delay = min(60, 2 ** min(failures, 5))
                log.exception("Bot iteration failed; retrying in %ss", delay)
                time.sleep(delay)
