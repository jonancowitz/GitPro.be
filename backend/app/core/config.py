from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/gitprobe"
    redis_url: str = "redis://localhost:6379/0"
    manapool_api_key: str = ""
    manapool_email: str = ""
    manapool_affiliate_tag: str = ""
    cardkingdom_affiliate_tag: str = ""
    next_public_api_base_url: str = "http://localhost:8000"


settings = Settings()
