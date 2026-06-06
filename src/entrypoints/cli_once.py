from __future__ import annotations

import os
import sys
from typing import cast

from src.application.ports import AppLogger
from src.application.respond_on_reviews import respond_on_reviews
from src.infra.clients import YandexMarketClient
from src.infra.config.settings import Settings
from src.infra.llm.base import make_llm_client
from src.infra.logger import init_logger
from src.infra.prompt import PromptBuilder
from src.infra.reply_generator import LLMReplyGenerator


def _load_token_from_file(path: str, env_var: str) -> str | None:
    """Load a token from a file, fallback to env var."""
    try:
        with open(path) as f:
            token = f.read().strip()
        if token:
            return token
    except FileNotFoundError:
        pass
    return os.environ.get(env_var)


def run_once() -> None:
    settings = Settings()
    logger = cast(AppLogger, init_logger())

    # Load tokens from files (same pattern as WB responder)
    ym_token = _load_token_from_file("/tmp/ym_token.txt", "YANDEX_MARKET__API_TOKEN")
    or_key = _load_token_from_file("/tmp/openrouter_key.txt", "LLM__API_KEY")

    if ym_token:
        settings.yandex_market.api_token = ym_token
    if or_key:
        settings.llm.api_key = or_key

    # Validate
    if not settings.yandex_market.api_token:
        logger.warning("Yandex Market API token not found — set YANDEX_MARKET__API_TOKEN or create /tmp/ym_token.txt")
        sys.exit(1)
    if not settings.llm.api_key:
        logger.warning("LLM API key not found — set LLM__API_KEY or create /tmp/openrouter_key.txt")
        sys.exit(1)

    ym_client = YandexMarketClient(settings)
    llm_client = make_llm_client(settings)
    prompt_builder = PromptBuilder(template=settings.llm.prompt_template)
    reply_generator = LLMReplyGenerator(prompt_builder=prompt_builder, llm_client=llm_client)

    respond_on_reviews(
        review_fetcher=ym_client,
        reply_generator=reply_generator,
        review_publisher=ym_client,
        logger=logger,
    )


def main() -> None:
    run_once()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
