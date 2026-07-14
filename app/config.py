import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "bedrock"  # "anthropic" | "bedrock"
    anthropic_api_key: str = ""
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "anthropic.claude-sonnet-4-6"
    anthropic_model_id: str = "claude-sonnet-4-6"

    simulate: int = 1
    log_file: str = str(ROOT / "sample_logs" / "app.log")
    db_path: str = str(ROOT / "data" / "incidents.db")
    chroma_dir: str = str(ROOT / "data" / "chroma")
    kb_seed_path: str = str(ROOT / "data" / "kb_seed.json")
    confidence_threshold: float = 0.85

    allowed_commands: list[str] = [
        "echo",
        "date",
        "whoami",
        "hostname",
        "printenv",
        "uptime",
        "df",
        "free",
        "true",
    ]


settings = Settings()

Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
Path(settings.chroma_dir).mkdir(parents=True, exist_ok=True)
Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
if not Path(settings.log_file).exists():
    Path(settings.log_file).touch()
