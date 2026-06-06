from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict, YamlConfigSettingsSource


class YandexMarketSettings(BaseModel):
    api_token: str = ""
    base_url: str = "https://api.partner.market.yandex.ru"
    business_id: int = 0
    request_timeout: int = 30
    batch_size: int = 25
    check_every_minutes: int = 30
    reviews_max_age_days: int = 90
    max_pages: int = 10


class LLMRuntimeSettings(BaseModel):
    api_key: str | None = None
    model: str = ""
    base_url: str = "https://openrouter.ai/api/v1"
    temperature: float = 0.3
    max_tokens: int = 600
    instructions: str = "You are a polite support agent replying to Yandex Market reviews."
    timeout: int = 10
    prompt_template: str = "Reply to the Yandex Market review in a polite and concise tone."


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    yandex_market: YandexMarketSettings = Field(default_factory=YandexMarketSettings)
    llm: LLMRuntimeSettings = Field(default_factory=LLMRuntimeSettings)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        project_root = Path(__file__).resolve().parents[3]
        yaml_path = project_root / "settings.yaml"
        yaml_settings = YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path)

        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            yaml_settings,
        )
