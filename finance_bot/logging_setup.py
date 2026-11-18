import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # «Update id=… is handled / is not handled» на INFO — сразу видно, доходят ли апдейты и отвечает ли бот.
    # Токен в этих строках не печатается.
    logging.getLogger("aiogram.event").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
