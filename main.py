import logging
from app.bot import TradingBot
from app.config import Config


def main():
    cfg = Config.load()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    TradingBot(cfg).run()


if __name__ == "__main__":
    main()
