from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "api" / "openapi.yaml"


@lru_cache(maxsize=1)
def load_spec() -> dict[str, Any]:
    with SPEC_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


app = FastAPI(
    title="TalkToYourStock API",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


@app.get("/healthz", tags=["Health"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def custom_openapi() -> dict[str, Any]:
    return load_spec()


app.openapi = custom_openapi
