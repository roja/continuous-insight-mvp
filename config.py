from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "sqlite:///./tech_audit.db"
    api_key: str = "key"
    openai_api_key: str = "your_openai_api_key_here"

    google_client_id: str = "your_google_client_id"
    google_client_secret: str = "your_google_client_secret"
    apple_client_id: str = "your_apple_client_id"
    apple_client_secret: str = "your_apple_client_secret"
    jwt_secret_key: str = "your_jwt_secret_key"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60  # 1 hour default
    jwt_refresh_token_expire_days: int = 7     # 7 days default

    model_config = SettingsConfigDict(env_file=".env")

# Create a single instance to be imported by other modules
settings = Settings()
