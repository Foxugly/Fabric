from __future__ import annotations

import asyncio

from fabric_agent.application.runner import run_agent


def main() -> None:
    asyncio.run(run_agent())


if __name__ == "__main__":
    main()
