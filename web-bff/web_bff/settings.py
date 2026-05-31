from __future__ import annotations

import os
from pathlib import Path


class Settings:
    service_name = "web-bff"
    repo_root = Path(__file__).resolve().parents[2]
    openapi_path = repo_root / "api" / "openapi.yaml"
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql://talk_to_your_stock:talk_to_your_stock@localhost:5432/talk_to_your_stock",
    )
    agent_service_url = os.getenv("AGENT_SERVICE_URL", "http://localhost:8001").rstrip("/")
    comps_service_url = os.getenv("COMPS_SERVICE_URL", "http://localhost:8002").rstrip("/")
    demo_user_email = os.getenv("DEMO_USER_EMAIL", "demo@talktoyourstock.com")


settings = Settings()
