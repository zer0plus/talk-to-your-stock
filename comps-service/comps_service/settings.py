from __future__ import annotations

import os


class Settings:
    service_name = "comps-service"
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql://talk_to_your_stock:talk_to_your_stock@localhost:5432/talk_to_your_stock",
    )
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    alpha_vantage_api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
    alpha_vantage_base_url = os.getenv("ALPHA_VANTAGE_BASE_URL", "https://www.alphavantage.co/query")
    alpha_vantage_timeout_seconds = float(os.getenv("ALPHA_VANTAGE_TIMEOUT_SECONDS", "20"))
    alpha_vantage_min_request_interval_seconds = float(os.getenv("ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS", "1.1"))
    alpha_vantage_quote_entitlement = os.getenv("ALPHA_VANTAGE_QUOTE_ENTITLEMENT", "").strip()
    alpha_vantage_earnings_horizon = os.getenv("ALPHA_VANTAGE_EARNINGS_HORIZON", "3month")
    cache_refresh_lead_days = int(os.getenv("FUNDAMENTAL_CACHE_REFRESH_LEAD_DAYS", "7"))
    cache_refresh_backoff_days = int(os.getenv("FUNDAMENTAL_CACHE_REFRESH_BACKOFF_DAYS", "1"))
    cache_refresh_lock_seconds = int(os.getenv("FUNDAMENTAL_CACHE_REFRESH_LOCK_SECONDS", "60"))
    estimated_quarterly_report_lag_days = int(os.getenv("FUNDAMENTAL_CACHE_ESTIMATED_QUARTERLY_REPORT_LAG_DAYS", "45"))


settings = Settings()
