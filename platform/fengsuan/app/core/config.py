"""从环境变量与可选 config.toml [platform] 读取平台配置。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None


def _load_toml_platform() -> dict:
    # platform/fengsuan/app/core/config.py → repo root is parents[4]
    root = Path(__file__).resolve().parents[4]
    cfg = root / "config.toml"
    loader = toml
    if loader is None:
        try:
            import tomllib as loader  # Python 3.11+
        except ImportError:
            return {}
    if not cfg.exists():
        return {}
    try:
        if hasattr(loader, "load") and loader is toml:
            data = loader.load(cfg)
        else:
            with cfg.open("rb") as fh:
                data = loader.load(fh)
        return data.get("platform") or {}
    except Exception:
        return {}


_TOML = _load_toml_platform()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_file_encoding="utf-8")

    database_url: str = Field(default=_TOML.get("database_url", "sqlite+pysqlite:///./data/fengsuan.db"))
    secret_key: str = Field(default="dev-only-change-SECRET_KEY-in-env")
    jwt_expire_minutes: int = Field(default=int(_TOML.get("jwt_expire_minutes", 30)))
    jwt_refresh_days: int = Field(default=int(_TOML.get("jwt_refresh_days", 7)))
    file_root: str = Field(default=_TOML.get("file_root", "data"))
    embedding_model: str = Field(default=_TOML.get("embedding_model", "bge-m3"))
    embedding_dim: int = Field(default=int(_TOML.get("embedding_dim", 1024)))
    heavy_job_per_user: int = Field(default=int(_TOML.get("heavy_job_per_user", 1)))
    heavy_job_per_lab: int = Field(default=int(_TOML.get("heavy_job_per_lab", 2)))
    cors_origins: str = Field(default="http://localhost:5173")
    gpu_mode: str = Field(default=_TOML.get("gpu_mode", "off"))
    gpu_external_endpoint: str = Field(default=_TOML.get("gpu_external_endpoint", ""))
    # 须为合法邮箱（登录体校验 EmailStr）；默认与 DEPLOY 示例一致。
    admin_email: str = Field(default="admin@example.com")
    admin_password: str = Field(default="changeme1")
    lab_monthly_token_budget: int = Field(default=2_000_000)
    guest_monthly_token_budget: int = Field(default=2_000_000)
    frontend_origin: str = Field(default="http://localhost:5173")
    email_host: str = Field(default="")
    grobid_url: str = Field(default=_TOML.get("grobid_url", "") or "")
    email_port: int = Field(default=587)
    email_user: str = Field(default="")
    email_password: str = Field(default="")
    email_from: str = Field(default="")
    redis_url: str = Field(default=_TOML.get("redis_url", "") or "")

    def cors_origin_list(self) -> List[str]:
        raw = _TOML.get("cors_origins")
        if isinstance(raw, list) and raw:
            return [str(x) for x in raw]
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
