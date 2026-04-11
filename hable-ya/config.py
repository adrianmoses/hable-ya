from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "HABLE_YA_"}

    db_path: str = "./hable_ya.db"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    llama_cpp_url: str = "http://localhost:8080"


settings = Settings()
