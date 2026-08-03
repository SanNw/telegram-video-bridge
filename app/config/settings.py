"""Configuração centralizada da aplicação via Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Todas as configurações da aplicação, carregadas de variáveis de ambiente ou `.env`.

    Nenhum valor sensível ou operacional deve ser hardcoded fora desta classe.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Telegram / MTProto ---
    api_id: int
    api_hash: SecretStr
    session_string: SecretStr
    chat_id: int

    # --- Autorização do bot ---
    authorized_user_ids: list[int] = Field(
        default_factory=list,
        description="Whitelist de user_id do Telegram autorizados a controlar o bot.",
    )

    # --- Logging ---
    log_level: Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_dir: Path = Path("logs")
    log_rotation: str = Field(
        default="10 MB", description="Política de rotação do Loguru (tamanho ou tempo)."
    )
    log_retention: str = Field(
        default="7 days", description="Política de retenção de logs antigos do Loguru."
    )

    # --- FFmpeg / mídia ---
    ffmpeg_path: str = "ffmpeg"
    media_path: Path = Path("media")
    ffmpeg_max_concurrent: int = Field(
        default=1, gt=0, description="Máximo de processos FFmpeg simultâneos."
    )
    ffmpeg_healthcheck_interval_seconds: float = Field(default=5.0, gt=0)

    # --- Fila (player) ---
    queue_max_items: int = Field(default=50, gt=0, description="Limite máximo de itens na fila.")
    queue_data_path: Path = Path("data/queue.json")

    # --- Retry / resiliência (chamada e FFmpeg) ---
    retry_base_delay_seconds: float = Field(default=2.0, gt=0)
    retry_max_delay_seconds: float = Field(default=60.0, gt=0)
    retry_max_attempts: int = Field(
        default=8, gt=0, description="Tentativas antes de marcar falha permanente e alertar."
    )
    retry_jitter_seconds: float = Field(default=1.0, ge=0)

    @field_validator("authorized_user_ids", mode="before")
    @classmethod
    def _split_authorized_user_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(item) for item in value.split(",") if item.strip()]
        return value

    @field_validator("retry_max_delay_seconds")
    @classmethod
    def _max_delay_not_below_base(cls, value: float, info: object) -> float:
        base = getattr(info, "data", {}).get("retry_base_delay_seconds")
        if base is not None and value < base:
            raise ValueError("retry_max_delay_seconds deve ser >= retry_base_delay_seconds")
        return value

    def masked_dict(self) -> dict[str, object]:
        """Retorna a configuração em dict com segredos mascarados, seguro para log/debug."""
        data = self.model_dump(mode="json")
        data["api_hash"] = "***MASKED***"
        data["session_string"] = "***MASKED***"
        return data


@lru_cache
def get_settings() -> Settings:
    """Retorna a instância cacheada de `Settings` (singleton por processo)."""
    return Settings()
