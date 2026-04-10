from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CORTEXFLOW_UI_")

    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173"]

    mlflow_port: int = 5000
    ray_port: int = 8265
    minio_port: int = 9001


settings = Settings()
