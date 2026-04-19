from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "HABLE_YA_"}

    db_path: str = "./hable_ya.db"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    llama_cpp_url: str = "http://localhost:8080"
    llm_model_name: str = "gemma-4-e4b-finetuned"
    llm_max_tokens: int = 150

    whisper_model: str = "small"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"

    piper_voice: str = "es_ES-carlfm-x_low"
    piper_model_dir: Path = Path.home() / "piper_models"

    smart_turn_stop_secs: float = 4.0
    vad_stop_secs: float = 0.5

    audio_sample_rate: int = 16000


settings = Settings()
