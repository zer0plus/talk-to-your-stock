from __future__ import annotations

import os


class Settings:
    service_name = "agent-service"
    app_name = "talk-to-your-stock"
    comps_service_url = os.getenv("COMPS_SERVICE_URL", "http://localhost:8002").rstrip("/")
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    google_genai_use_vertexai = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "FALSE").strip().upper()
    google_api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    google_cloud_project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    google_cloud_location = os.getenv("GOOGLE_CLOUD_LOCATION", "").strip()

    @property
    def use_vertexai(self) -> bool:
        return self.google_genai_use_vertexai == "TRUE"

    def validate_gemini_credentials(self) -> None:
        if self.use_vertexai:
            if self.google_api_key:
                return
            if self.google_cloud_project and self.google_cloud_location:
                return
            raise RuntimeError(
                "Google ADK/Gemini is configured for Vertex AI, but credentials are missing. "
                "Set GOOGLE_API_KEY for Vertex Express Mode, or set GOOGLE_CLOUD_PROJECT "
                "and GOOGLE_CLOUD_LOCATION with application-default credentials."
            )
        if not self.google_api_key:
            raise RuntimeError(
                "Google ADK/Gemini credentials are missing. Set GOOGLE_GENAI_USE_VERTEXAI=FALSE "
                "and GOOGLE_API_KEY before starting agent-service."
            )


settings = Settings()
