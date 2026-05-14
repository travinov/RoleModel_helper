from __future__ import annotations

import os
from dataclasses import dataclass

from rolemodel_etl.config import DBConfig


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class AppConfig:
    db: DBConfig
    embedding_dim: int = 128
    tesseract_cmd: str = "tesseract"
    tesseract_langs: str = "rus+eng"
    rag_top_k: int = 5
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    gigachat_auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    gigachat_base_url: str = "https://gigachat.devices.sberbank.ru/api/v1"
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_auth_key: str | None = None
    gigachat_client_id: str | None = None
    gigachat_client_secret: str | None = None
    gigachat_access_token: str | None = None
    gigachat_chat_model: str = "GigaChat-2-Pro"
    gigachat_chunk_model: str = "GigaChat-2-Pro"
    gigachat_timeout_sec: float = 45.0
    gigachat_verify_ssl: bool = True
    gigachat_ca_bundle: str | None = None
    gigachat_use_for_intent: bool = True
    gigachat_use_for_chunking: bool = True
    gigachat_use_for_rag_answer: bool = True

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            db=DBConfig(
                host=os.getenv("RM_DB_HOST", "127.0.0.1"),
                port=int(os.getenv("RM_DB_PORT", "5432")),
                dbname=os.getenv("RM_DB_NAME", "rolemodel"),
                user=os.getenv("RM_DB_USER", "rolemodel"),
                password=os.getenv("RM_DB_PASSWORD", "rolemodel"),
                schema=os.getenv("RM_DB_SCHEMA", "public"),
            ),
            embedding_dim=int(os.getenv("RM_EMBEDDING_DIM", "128")),
            tesseract_cmd=os.getenv("RM_TESSERACT_CMD", "tesseract"),
            tesseract_langs=os.getenv("RM_TESSERACT_LANGS", "rus+eng"),
            rag_top_k=int(os.getenv("RM_RAG_TOP_K", "5")),
            app_host=os.getenv("RM_APP_HOST", "127.0.0.1"),
            app_port=int(os.getenv("RM_APP_PORT", "8000")),
            gigachat_auth_url=os.getenv(
                "RM_GIGACHAT_AUTH_URL",
                "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
            ),
            gigachat_base_url=os.getenv(
                "RM_GIGACHAT_BASE_URL",
                "https://gigachat.devices.sberbank.ru/api/v1",
            ),
            gigachat_scope=os.getenv("RM_GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
            gigachat_auth_key=os.getenv("RM_GIGACHAT_AUTH_KEY"),
            gigachat_client_id=os.getenv("RM_GIGACHAT_CLIENT_ID"),
            gigachat_client_secret=os.getenv("RM_GIGACHAT_CLIENT_SECRET"),
            gigachat_access_token=os.getenv("RM_GIGACHAT_ACCESS_TOKEN"),
            gigachat_chat_model=os.getenv("RM_GIGACHAT_CHAT_MODEL", "GigaChat-2-Pro"),
            gigachat_chunk_model=os.getenv("RM_GIGACHAT_CHUNK_MODEL", "GigaChat-2-Pro"),
            gigachat_timeout_sec=float(os.getenv("RM_GIGACHAT_TIMEOUT_SEC", "45")),
            gigachat_verify_ssl=_env_bool("RM_GIGACHAT_VERIFY_SSL", True),
            gigachat_ca_bundle=os.getenv("RM_GIGACHAT_CA_BUNDLE"),
            gigachat_use_for_intent=_env_bool("RM_GIGACHAT_USE_FOR_INTENT", True),
            gigachat_use_for_chunking=_env_bool("RM_GIGACHAT_USE_FOR_CHUNKING", True),
            gigachat_use_for_rag_answer=_env_bool("RM_GIGACHAT_USE_FOR_RAG_ANSWER", True),
        )

    @property
    def gigachat_enabled(self) -> bool:
        if self.gigachat_access_token:
            return True
        if self.gigachat_auth_key:
            return True
        return bool(self.gigachat_client_id and self.gigachat_client_secret)
