from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass(frozen=True)
class Candle:
    t: int
    T: int
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass(frozen=True)
class Signal:
    side: str  # LONG | SHORT
    bar_time: int
    entry_reference: float
    initial_tp: float
    sl: float
    sma50: float
    sma200: float
    separation_points: float


@dataclass
class RuntimeState:
    last_processed_bar: Optional[int] = None
    last_entry_bar: Optional[int] = None
    last_exit_bar: Optional[int] = None
    active_side: int = 0  # 1 long, -1 short, 0 flat
    active_entry: Optional[float] = None
    active_sl: Optional[float] = None
    active_tp_at_entry: Optional[float] = None
    tp_order_oid: Optional[int] = None
    sl_order_oid: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, obj: dict) -> "RuntimeState":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: obj.get(k) for k in allowed})
