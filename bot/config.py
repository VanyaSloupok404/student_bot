from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    gemini_api_key: str
    openweather_api_key: str | None = None
    database_url: str = "sqlite+aiosqlite:///./bot.db"
    log_level: str = "INFO"
    admin_ids_raw: str = Field(default="", validation_alias="ADMIN_IDS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def admin_ids(self) -> list[int]:
        if not self.admin_ids_raw:
            return []
        ids = []
        for x in self.admin_ids_raw.replace("[", "").replace("]", "").split(","):
            val = x.strip()
            if val.isdigit():
                ids.append(int(val))
        return ids


settings = Settings()  # type: ignore
