# config.py
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./test.db"
    api_key: str = "your_api_key_here"

    class Config:
        env_file = ".env"


settings = Settings()
