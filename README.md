# Hyperliquid Herman Executor

Cloud/local executor for **Trend Rebalance Map [Herman] v1.1** on Hyperliquid HIP-3 `xyz:XYZ100` using 1-minute closed candles.

## Architecture

`Hyperliquid XYZ100 1m candles -> Pine logic translated 1:1 -> Hyperliquid official Python SDK -> entry + native TP/SL`

No TradingView subscription or webhook is required.

## Strategy logic preserved

The implementation intentionally preserves the uploaded Pine defaults and rules:

- SMA50 / SMA200
- long: close crosses above SMA50, SMA50 < SMA200, separation > 30 points
- short: close crosses below SMA50, SMA50 > SMA200, separation > 30 points
- closed bars only
- one position at a time
- opposite signals ignored while a trade is open
- default TP: 200 SMA, Dynamic
- default SL: 125 points
- same-bar re-entry blocked after an exit

The original Pine source is included under `reference/`.

## Safety default

`DRY_RUN=true` is the default. The program will not place live orders until you explicitly set `DRY_RUN=false` and provide credentials.

## Run locally

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Or Docker:

```bash
cp .env.example .env
docker build -t herman-hl .
docker run --env-file .env herman-hl
```

## Hyperliquid credentials

Use an **API Wallet** authorized for trading. Put:

- `ACCOUNT_ADDRESS`: your main Hyperliquid account address
- `API_PRIVATE_KEY`: the API Wallet private key

Do not use a withdrawal-capable secret in the bot.

For HIP-3 the SDK is initialized with `perp_dexs=["xyz"]` and the coin is `xyz:XYZ100`.

## Switch to live trading

After dry-run logs look correct:

```env
DRY_RUN=false
NETWORK=mainnet
DEX=xyz
COIN=xyz:XYZ100
ACCOUNT_ADDRESS=0x...
API_PRIVATE_KEY=0x...
ORDER_NOTIONAL_USDC=100
```

## Railway deployment

1. Put this folder in a GitHub repo.
2. Create a Railway service from the repo.
3. Add the `.env` values as Railway Variables. Do **not** upload `.env` to GitHub.
4. Deploy. `railway.toml` and `Dockerfile` are included.
5. Keep `DRY_RUN=true` for the first deployment; only switch it off after checking logs.

If you want state to persist across container replacement, attach a Railway volume and set `STATE_PATH` to a path on that volume (for example `/data/state.json`). The bot also attempts to recover an existing live position and its trigger orders from Hyperliquid after a restart.

## Notes on execution parity

Pine models the entry at the signal-bar close (`process_orders_on_close=true`). A live API market order is sent immediately after the closed candle is observed, so the actual fill can differ from that close. The strategy's TP/SL reference remains based on the signal-bar close to preserve the Pine calculation.
