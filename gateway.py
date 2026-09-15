from __future__ import annotations

import logging

import uvicorn

from gateway.app import create_app
from gateway.config import GatewayConfig


def main() -> None:
    cfg = GatewayConfig.load()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    app = create_app(cfg)
    uvicorn.run(
        app,
        host=cfg.host,
        port=cfg.port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
