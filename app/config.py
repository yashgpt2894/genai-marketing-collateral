"""
Configuration (pydantic-settings). Everything is environment-overridable; no
secrets live in code. Copy .env.example -> .env and fill in your key.

MODEL IDS: the defaults below are the Gemini 3.x family this prototype targets.
Model identifiers move fast — confirm the exact strings in the Google Cloud /
AI Studio console the day you run this, and override via .env if they differ.
A wrong id surfaces as a clear 4xx from the API call, not a silent failure.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    from pydantic import Field
except Exception:  # pragma: no cover - lets the module import for tooling without the dep
    BaseSettings = object  # type: ignore
    SettingsConfigDict = dict  # type: ignore

    def Field(default=None, **_):  # type: ignore
        return default


_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):  # type: ignore[misc]
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- Auth: pick ONE path ---------------------------------------------------
    # (a) Gemini Developer API: set GEMINI_API_KEY.
    # (b) Vertex AI: set use_vertex=true + project/location, auth via ADC.
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    use_vertex: bool = Field(default=False, alias="GOOGLE_GENAI_USE_VERTEXAI")
    google_cloud_project: str | None = Field(default=None, alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(default="europe-west1", alias="GOOGLE_CLOUD_LOCATION")

    # --- Models (override in .env to match the console) ------------------------
    model_writer: str = Field(default="gemini-3.1-pro", alias="MODEL_WRITER")        # article quality
    model_parser: str = Field(default="gemini-3-flash", alias="MODEL_PARSER")        # multimodal PDF
    model_cheap: str = Field(default="gemini-3.1-flash-lite", alias="MODEL_CHEAP")   # verify / repair / extract

    # --- Generation knobs ------------------------------------------------------
    writer_temperature: float = Field(default=0.5, alias="WRITER_TEMPERATURE")
    max_output_tokens: int = Field(default=8192, alias="MAX_OUTPUT_TOKENS")  # room for 2.5 "thinking" + JSON
    max_repair_iters: int = Field(default=3, alias="MAX_REPAIR_ITERS")
    request_timeout_s: float = Field(default=60.0, alias="REQUEST_TIMEOUT_S")
    llm_max_retries: int = Field(default=3, alias="LLM_MAX_RETRIES")

    # --- Storage (local filesystem OR GCS+Firestore, by flag) -----------------
    store_backend: str = Field(default="local", alias="STORE_BACKEND")   # "local" | "cloud"
    data_dir: Path = Field(default=_ROOT / "data", alias="DATA_DIR")     # used by the local backend
    gcs_bucket: str | None = Field(default=None, alias="GCS_BUCKET")     # cloud: PDFs + assets
    firestore_database: str = Field(default="(default)", alias="FIRESTORE_DATABASE")
    default_tenant: str = Field(default="default", alias="DEFAULT_TENANT")  # until auth supplies tenant_id

    # --- Async parsing (in-process BackgroundTask OR Pub/Sub worker, by flag) --
    parse_backend: str = Field(default="local", alias="PARSE_BACKEND")   # "local" | "pubsub"
    pubsub_parse_topic: str | None = Field(default=None, alias="PUBSUB_PARSE_TOPIC")

    # --- Auth (app-level Google ID-token verification) ------------------------
    auth_mode: str = Field(default="none", alias="AUTH_MODE")               # "none" | "google"
    auth_audience: str | None = Field(default=None, alias="AUTH_AUDIENCE")  # expected token audience (service URL)

    # --- Upload limits --------------------------------------------------------
    max_upload_mb: int = Field(default=10, alias="MAX_UPLOAD_MB")

    # --- Versioning stamped on every output (reproducibility) -----------------
    prompt_version: str = Field(default="p1", alias="PROMPT_VERSION")

    @property
    def llm_configured(self) -> bool:
        return bool(self.gemini_api_key) or (self.use_vertex and bool(self.google_cloud_project))


@lru_cache
def get_settings() -> "Settings":
    return Settings()
