from dataclasses import replace

from app.bot import build_executor, build_market_data
from app.executor import DryRunExecutor
from app.market_data import HyperliquidMarketData, OKXMarketData
from tests.test_strategy import cfg as base_cfg


def test_bot_factories_keep_hyperliquid_as_the_default_adapter():
    config = base_cfg()

    assert isinstance(build_market_data(config), HyperliquidMarketData)
    assert isinstance(build_executor(config), DryRunExecutor)


def test_bot_factories_select_okx_market_data_without_sending_orders_in_dry_run():
    config = replace(base_cfg(), exchange="okx", okx_inst_id="BTC-USDT-SWAP")

    market = build_market_data(config)
    executor = build_executor(config)

    assert isinstance(market, OKXMarketData)
    assert isinstance(executor, DryRunExecutor)

