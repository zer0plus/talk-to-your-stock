from __future__ import annotations

from fastapi.encoders import jsonable_encoder
import httpx

from agent_service.settings import settings
from talk_to_your_stock_shared import GenerateCompsToolRequest, GenerateCompsToolResponse


class CompsToolClient:
    def generate_comps_table(self, request: GenerateCompsToolRequest) -> GenerateCompsToolResponse:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{settings.comps_service_url}/v1/internal/tools/generate-comps-table",
                json=jsonable_encoder(request),
            )
            response.raise_for_status()
            return GenerateCompsToolResponse.model_validate(response.json())
