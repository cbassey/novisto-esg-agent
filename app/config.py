"""Runtime configuration, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    var_dir: Path
    company_api_url: str
    openai_model: str
    openai_base_url: str | None
    llm_timeout_s: float
    max_agent_steps: int
    # Cost estimate only: USD per 1M tokens. Override per model in .env.
    price_in_per_1m: float
    price_out_per_1m: float

    @property
    def log_file(self) -> Path:
        return self.var_dir / "log" / "app.jsonl"


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "data")),
        var_dir=Path(os.environ.get("VAR_DIR", "var")),
        company_api_url=os.environ.get("COMPANY_API_URL", "http://localhost:8001"),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        openai_base_url=os.environ.get("OPENAI_BASE_URL") or None,
        llm_timeout_s=float(os.environ.get("LLM_TIMEOUT_S", "60")),
        max_agent_steps=int(os.environ.get("MAX_AGENT_STEPS", "6")),
        price_in_per_1m=float(os.environ.get("PRICE_IN_PER_1M", "0.15")),
        price_out_per_1m=float(os.environ.get("PRICE_OUT_PER_1M", "0.60")),
    )


settings = load_settings()
