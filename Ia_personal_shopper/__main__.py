"""Entry point: `uv run shopper` oppure `python -m Ia_personal_shopper`"""

import asyncio

from dotenv import load_dotenv

load_dotenv()  # ANTHROPIC_API_KEY dal file .env, prima di ogni import che usa l'API

from Ia_personal_shopper.cli.loop import avvia  # noqa: E402


def main_sync() -> None:
    asyncio.run(avvia())


if __name__ == "__main__":
    main_sync()
