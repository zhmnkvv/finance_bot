import asyncio
import contextlib

from finance_bot.bot.app import main

if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
