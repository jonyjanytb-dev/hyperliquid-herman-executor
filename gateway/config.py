from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

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
class GatewayConfig:
    exchange: str
    network: str
    dex: str
    coin: str
    account_address: str

    okx_inst_id: str
    okx_api_key: str
    okx_secret_key: str
    okx_passphrase: str
    okx_demo: bool
    okx_base_url: str

    dry_run: bool
    order_notional_usdc: float
    leverage: int
    tp_mode: str
    sma_target_behaviour: str
    sl_mode: str
    sl_fixed_points: float
    state_path: str

    host: str
    port: int
    token: str
    refresh_seconds: float
    history_days: int
    request_timeout: float
    retry_attempts: int
    bot_stale_seconds: int

    @classmethod
    def load(cls) -> "GatewayConfig":
        load_dotenv()
        exchange = os.getenv("EXCHANGE", "hyperliquid").strip().lower()
        state_path = os.getenv("STATE_PATH", "").strip() or f"runtime/state-{exchange}.json"
        cfg = cls(
            exchange=exchange,
            network=os.getenv("NETWORK", "mainnet").strip().lower(),
            dex=os.getenv("DEX", "xyz").strip(),
            coin=os.getenv("COIN", "xyz:XYZ100").strip(),
            account_address=os.getenv("ACCOUNT_ADDRESS", "").strip(),
            okx_inst_id=os.getenv("OKX_INST_ID", "US100-USDT-SWAP").strip().upper(),
            okx_api_key=os.getenv("OKX_API_KEY", "").strip(),
            okx_secret_key=os.getenv("OKX_SECRET_KEY", "").strip(),
            okx_passphrase=os.getenv("OKX_PASSPHRASE", "").strip(),
            okx_demo=_bool("OKX_DEMO", True),
            okx_base_url=os.getenv("OKX_BASE_URL", "https://www.okx.com").strip(),
            dry_run=_bool("DRY_RUN", True),
            order_notional_usdc=_float("ORDER_NOTIONAL_USDC", 100.0),
            leverage=_int("LEVERAGE", 0),
            tp_mode=os.getenv("TP_MODE", "200 SMA").strip(),
            sma_target_behaviour=os.getenv("SMA_TARGET_BEHAVIOUR", "Dynamic").strip(),
            sl_mode=os.getenv("SL_MODE", "Fixed Points").strip(),
            sl_fixed_points=_float("SL_FIXED_POINTS", 125.0),
            state_path=state_path,
            host=os.getenv("GATEWAY_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_int("GATEWAY_PORT", 8787),
            token=os.getenv("GATEWAY_TOKEN", "").strip(),
            refresh_seconds=_float("GATEWAY_REFRESH_SECONDS", 15.0),
            history_days=_int("GATEWAY_HISTORY_DAYS", 30),
            request_timeout=_float("REQUEST_TIMEOUT", 15.0),
            retry_attempts=_int("REQUEST_RETRY_ATTEMPTS", 3),
            bot_stale_seconds=_int("GATEWAY_BOT_STALE_SECONDS", 150),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.exchange not in {"hyperliquid", "okx"}:
            raise ValueError("EXCHANGE must be hyperliquid or okx")
        if self.network not in {"mainnet", "testnet"}:
            raise ValueError("NETWORK must be mainnet or testnet")
        if not 1 <= self.port <= 65535:
            raise ValueError("GATEWAY_PORT must be between 1 and 65535")
        if self.refresh_seconds < 2:
            raise ValueError("GATEWAY_REFRESH_SECONDS must be >= 2")
        if self.history_days < 1 or self.history_days > 90:
            raise ValueError("GATEWAY_HISTORY_DAYS must be between 1 and 90")
        if self.request_timeout <= 0:
            raise ValueError("REQUEST_TIMEOUT must be > 0")
        if self.retry_attempts < 1 or self.retry_attempts > 5:
            raise ValueError("REQUEST_RETRY_ATTEMPTS must be between 1 and 5")
        if self.bot_stale_seconds < 60:
            raise ValueError("GATEWAY_BOT_STALE_SECONDS must be >= 60")

        loopback_hosts = {"127.0.0.1", "localhost", "::1"}
        if self.host not in loopback_hosts and len(self.token) < 16:
            raise ValueError(
                "GATEWAY_TOKEN must be at least 16 characters when GATEWAY_HOST is not loopback"
            )

        if self.exchange == "hyperliquid":
            if not self.account_address.startswith("0x") or len(self.account_address) != 42:
                raise ValueError(
                    "ACCOUNT_ADDRESS must be the 42-character Hyperliquid main/subaccount address"
                )
            if ":" not in self.coin or not self.coin.startswith(self.dex + ":"):
                raise ValueError("COIN must use HIP-3 prefixed form, e.g. xyz:XYZ100")
            return

        parsed = urlparse(self.okx_base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"www.okx.com", "my.okx.com", "app.okx.com"}
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OKX_BASE_URL must be an official OKX HTTPS origin")
        if not self.okx_inst_id.endswith("-SWAP"):
            raise ValueError("OKX_INST_ID must be a perpetual swap ending in -SWAP")
        if not all((self.okx_api_key, self.okx_secret_key, self.okx_passphrase)):
            raise ValueError(
                "OKX gateway requires OKX_API_KEY, OKX_SECRET_KEY and OKX_PASSPHRASE with Read permission"
            )

    @property
    def market_symbol(self) -> str:
        return self.okx_inst_id if self.exchange == "okx" else self.coin

    @property
    def quote_currency(self) -> str:
        if self.exchange == "hyperliquid":
            return "USDC"
        parts = self.okx_inst_id.split("-")
        return parts[-2] if len(parts) >= 3 else "USDT"

    @property
    def execution_mode(self) -> str:
        if self.dry_run:
            return "dry_run"
        if self.exchange == "okx" and self.okx_demo:
            return "demo"
        return "live"

    @property
    def state_file(self) -> Path:
        return Path(self.state_path)

    def public_config(self) -> dict:
        return {
            "exchange": self.exchange,
            "market": self.market_symbol,
            "mode": self.execution_mode,
            "order_notional": self.order_notional_usdc,
            "leverage": self.leverage,
            "tp": f"{self.tp_mode} / {self.sma_target_behaviour}",
            "sl": f"{self.sl_mode} / {self.sl_fixed_points:g}",
            "refresh_seconds": self.refresh_seconds,
        }
