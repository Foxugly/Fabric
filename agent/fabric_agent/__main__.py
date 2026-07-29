from __future__ import annotations

import asyncio
import logging
import os

from fabric_agent.application.runner import run_agent


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("FABRIC_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        logging.getLogger("fabric_agent").info("Agent stopped")


if __name__ == "__main__":
    main()
