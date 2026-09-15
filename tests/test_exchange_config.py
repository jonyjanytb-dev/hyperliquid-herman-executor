from dataclasses import replace

import pytest

from tests.test_strategy import cfg as base_cfg


def test_okx_dry_run_config_accepts_public_market_data_without_credentials():
    config = replace(base_cfg(), exchange="okx", okx_inst_id="BTC-USDT-SWAP")

    config.validate()


def test_okx_default_instrument_matches_the_us100_strategy_target():
    config = replace(base_cfg(), exchange="okx")

    assert config.okx_inst_id == "US100-USDT-SWAP"
    config.validate()


def test_okx_live_config_requires_all_three_api_credentials():
    config = replace(
        base_cfg(),
        exchange="okx",
        dry_run=False,
        okx_inst_id="BTC-USDT-SWAP",
        okx_api_key="key",
        okx_secret_key="",
        okx_passphrase="pass",
    )

    with pytest.raises(ValueError, match="OKX_API_KEY, OKX_SECRET_KEY and OKX_PASSPHRASE"):
        config.validate()


def test_okx_adapter_is_limited_to_perpetual_swaps():
    config = replace(base_cfg(), exchange="okx", okx_inst_id="BTC-USDT")

    with pytest.raises(ValueError, match="OKX_INST_ID must be a perpetual swap"):
        config.validate()


def test_okx_credentials_can_only_be_sent_to_an_official_okx_origin():
    config = replace(
        base_cfg(),
        exchange="okx",
        okx_inst_id="BTC-USDT-SWAP",
        okx_base_url="https://www.okx.com.attacker.example",
    )

    with pytest.raises(ValueError, match="official OKX HTTPS origin"):
        config.validate()
