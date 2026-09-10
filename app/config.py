from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Config:
    dry_run: bool
    network: str
    dex: str
    coin: str
    interval: str
    account_address: str
    api_private_key: str
    order_notional_usdc: float
    leverage: int
    max_slippage: float

    sma50_length: int
    sma200_length: int
    point_size: float
    min_separation: float
    enable_longs: bool
    enable_shorts: bool
    tp_mode: str
    sma_target_behaviour: str
    tp_fixed_points: float
    sl_mode: str
    sl_fixed_points: float

    poll_seconds: float
    lookback_candles: int
    state_path: str
    log_level: str

    @classmethod
    def load(cls) -> "Config":
        load_dotenv()
        cfg = cls(
            dry_run=_bool("DRY_RUN", True),
            network=os.getenv("NETWORK", "mainnet").strip().lower(),
            dex=os.getenv("DEX", "xyz").strip(),
            coin=os.getenv("COIN", "xyz:XYZ100").strip(),
            interval=os.getenv("INTERVAL", "1m").strip(),
            account_address=os.getenv("ACCOUNT_ADDRESS", "").strip(),
            api_private_key=os.getenv("API_PRIVATE_KEY", "").strip(),
            order_notional_usdc=_float("ORDER_NOTIONAL_USDC", 100.0),
            leverage=_int("LEVERAGE", 0),
            max_slippage=_float("MAX_SLIPPAGE", 0.005),
            sma50_length=_int("SMA50_LENGTH", 50),
            sma200_length=_int("SMA200_LENGTH", 200),
            point_size=_float("POINT_SIZE", 1.0),
            min_separation=_float("MIN_SEPARATION", 30.0),
            enable_longs=_bool("ENABLE_LONGS", True),
            enable_shorts=_bool("ENABLE_SHORTS", True),
            tp_mode=os.getenv("TP_MODE", "200 SMA").strip(),
            sma_target_behaviour=os.getenv("SMA_TARGET_BEHAVIOUR", "Dynamic").strip(),
            tp_fixed_points=_float("TP_FIXED_POINTS", 100.0),
            sl_mode=os.getenv("SL_MODE", "Fixed Points").strip(),
            sl_fixed_points=_float("SL_FIXED_POINTS", 125.0),
            poll_seconds=_float("POLL_SECONDS", 3.0),
            lookback_candles=_int("LOOKBACK_CANDLES", 260),
            state_path=os.getenv("STATE_PATH", "runtime/state.json").strip(),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.network not in {"mainnet", "testnet"}:
            raise ValueError("NETWORK must be mainnet or testnet")
        if self.interval != "1m":
            raise ValueError("This build is intentionally fixed to INTERVAL=1m")
        if self.point_size <= 0:
            raise ValueError("POINT_SIZE must be > 0")
        if self.order_notional_usdc <= 0:
            raise ValueError("ORDER_NOTIONAL_USDC must be > 0")
        if self.max_slippage <= 0 or self.max_slippage > 0.05:
            raise ValueError("MAX_SLIPPAGE must be > 0 and <= 0.05")
        if self.tp_mode not in {"200 SMA", "Fixed Points"}:
            raise ValueError("TP_MODE must be '200 SMA' or 'Fixed Points'")
        if self.sma_target_behaviour not in {"Locked at Entry", "Dynamic"}:
            raise ValueError("SMA_TARGET_BEHAVIOUR invalid")
        if self.sl_mode not in {"1R to TP", "Fixed Points"}:
            raise ValueError("SL_MODE must be '1R to TP' or 'Fixed Points'")
        if ":" not in self.coin or not self.coin.startswith(self.dex + ":"):
            raise ValueError("COIN must use HIP-3 prefixed form, e.g. xyz:XYZ100")
        if not self.dry_run and (not self.account_address or not self.api_private_key):
            raise ValueError("ACCOUNT_ADDRESS and API_PRIVATE_KEY are required when DRY_RUN=false")
