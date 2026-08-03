from __future__ import annotations

import asyncio
import os
import traceback
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

from agent_service.comps_client import (
    CompsToolError,
    CompsToolUnavailable,
    CompsToolValidationError,
    HttpCompsToolClient,
)
from comps_service.main import app as real_comps_app
from talk_to_your_stock_shared import (
    AnalysisPeriod,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    FailCalculatedRunRequest,
    FinalizeComparisonTakeawayRequest,
    GenerateCompsDraftResponse,
    GenerateCompsToolRequest,
    GenerateCompsToolResponse,
    PeerSelectionMode,
    RunStatus,
    RunResponse,
    RunTableDraftResponse,
)
from tests.live_service import running_service


class HttpCompsToolClientTest(unittest.TestCase):
    def test_calls_configured_comps_service_with_internal_contract(self) -> None:
        observed: dict[str, object] = {}
        tool_response = _tool_response()
        draft_response = GenerateCompsDraftResponse(
            run=tool_response.run.model_copy(
                update={"status": RunStatus.RUNNING, "completed_at": None}
            ),
            table=RunTableDraftResponse.model_validate(
                tool_response.table.model_dump(exclude={"comparison_takeaway"})
            ),
            trace=tool_response.trace,
        )
        comps_app = FastAPI()

        @comps_app.post("/v1/internal/tools/generate-comps-table")
        def generate_comps_table(
            request: GenerateCompsToolRequest,
            authorization: str = Header(),
        ) -> GenerateCompsDraftResponse:
            observed["request"] = request
            observed["authorization"] = authorization
            return draft_response

        @comps_app.post("/v1/internal/runs/{run_id}/finalize")
        def finalize_comps_run(
            run_id: UUID,
            request: FinalizeComparisonTakeawayRequest,
            authorization: str = Header(),
        ) -> GenerateCompsToolResponse:
            observed["finalize_run_id"] = run_id
            observed["finalize_request"] = request
            observed["finalize_authorization"] = authorization
            return tool_response.model_copy(
                update={
                    "table": tool_response.table.model_copy(
                        update={
                            "comparison_takeaway": request.comparison_takeaway
                        }
                    )
                }
            )

        @comps_app.post("/v1/internal/runs/{run_id}/fail")
        def fail_comps_run(
            run_id: UUID,
            request: FailCalculatedRunRequest,
            authorization: str = Header(),
        ) -> RunResponse:
            observed["fail_run_id"] = run_id
            observed["fail_request"] = request
            observed["fail_authorization"] = authorization
            return RunResponse(
                run=tool_response.run.model_copy(
                    update={
                        "status": RunStatus.FAILED,
                        "error_message": request.error_message,
                    }
                )
            )

        request = _tool_request(
            thread_id=tool_response.run.thread_id,
            trigger_message_id=tool_response.run.trigger_message_id,
        )
        with running_service(comps_app) as base_url:
            client = HttpCompsToolClient(
                base_url=base_url,
                internal_token="internal-token",
            )
            response = asyncio.run(client.generate_comps_table(request))
            finalized = asyncio.run(
                client.finalize_comps_run(
                    tool_response.run.id,
                    FinalizeComparisonTakeawayRequest(
                        comparison_takeaway=tool_response.table.comparison_takeaway
                    ),
                )
            )
            failed = asyncio.run(
                client.fail_comps_run(
                    tool_response.run.id,
                    FailCalculatedRunRequest(error_message="Agent failed."),
                )
            )

        self.assertEqual(response, draft_response)
        self.assertEqual(finalized, tool_response)
        self.assertEqual(observed["request"], request)
        self.assertEqual(observed["authorization"], "Bearer internal-token")
        self.assertEqual(observed["finalize_run_id"], tool_response.run.id)
        self.assertEqual(
            observed["finalize_authorization"],
            "Bearer internal-token",
        )
        self.assertEqual(failed.run.status, RunStatus.FAILED)
        self.assertEqual(observed["fail_run_id"], tool_response.run.id)
        self.assertEqual(observed["fail_authorization"], "Bearer internal-token")

    def test_preserves_structured_pre_run_validation_error(self) -> None:
        comps_app = FastAPI()

        @comps_app.post("/v1/internal/tools/generate-comps-table")
        def generate_comps_table() -> JSONResponse:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCode.VALIDATION_ERROR,
                        message="Unsupported ticker: AAPLL.",
                        details={"unsupported_tickers": ["AAPLL"]},
                    )
                ).model_dump(mode="json"),
            )

        request = _tool_request(thread_id=uuid4(), trigger_message_id=uuid4())
        with running_service(comps_app) as base_url:
            client = HttpCompsToolClient(
                base_url=base_url,
                internal_token="internal-token",
            )
            with self.assertRaises(CompsToolValidationError) as context:
                asyncio.run(client.generate_comps_table(request))

        self.assertEqual(context.exception.error.error.code, ErrorCode.VALIDATION_ERROR)
        self.assertEqual(
            context.exception.error.error.details,
            {"unsupported_tickers": ["AAPLL"]},
        )

    def test_malformed_error_response_uses_a_safe_generic_error(self) -> None:
        leaked_value = "FAKE_UPSTREAM_SECRET_123"
        comps_app = FastAPI()

        @comps_app.post("/v1/internal/tools/generate-comps-table")
        def generate_comps_table() -> JSONResponse:
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "code": leaked_value,
                        "message": "Malformed error.",
                    }
                },
            )

        request = _tool_request(thread_id=uuid4(), trigger_message_id=uuid4())
        with running_service(comps_app) as base_url:
            client = HttpCompsToolClient(
                base_url=base_url,
                internal_token="internal-token",
            )
            with self.assertRaises(CompsToolUnavailable) as context:
                asyncio.run(client.generate_comps_table(request))

        self.assertEqual(
            str(context.exception),
            "Comps Service returned an invalid error response.",
        )
        self.assertNotIn(
            leaked_value,
            "".join(traceback.format_exception(context.exception)),
        )

    def test_calls_real_comps_service_route_with_service_credential(self) -> None:
        ticker_validator = Mock()
        ticker_validator.is_supported.return_value = True
        request = _tool_request(thread_id=uuid4(), trigger_message_id=uuid4())

        with (
            patch.dict(
                os.environ,
                {"COMPS_SERVICE_INTERNAL_TOKEN": "internal-token"},
                clear=True,
            ),
            patch(
                "comps_service.tool_validation.AlphaVantageTickerValidator",
                return_value=ticker_validator,
            ),
            running_service(real_comps_app) as base_url,
        ):
            client = HttpCompsToolClient(
                base_url=base_url,
                internal_token="internal-token",
            )
            with self.assertRaises(CompsToolError) as context:
                asyncio.run(client.generate_comps_table(request))

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(
            context.exception.error.error.code,
            ErrorCode.INTERNAL_ERROR,
        )
        self.assertIsNone(context.exception.error.error.run_id)
        self.assertEqual(ticker_validator.is_supported.call_count, 2)


def _tool_request(
    *,
    thread_id: UUID,
    trigger_message_id: UUID,
) -> GenerateCompsToolRequest:
    return GenerateCompsToolRequest(
        invocation_id=trigger_message_id,
        thread_id=thread_id,
        trigger_message_id=trigger_message_id,
        target_ticker="AAPL",
        peer_tickers=["MSFT"],
        peer_selection_mode=PeerSelectionMode.USER_SUPPLIED,
        analysis_period=AnalysisPeriod.LATEST,
    )


def _tool_response() -> GenerateCompsToolResponse:
    now = datetime.now(timezone.utc).isoformat()
    run_id = str(uuid4())
    thread_id = str(uuid4())
    trigger_message_id = str(uuid4())
    return GenerateCompsToolResponse.model_validate(
        {
            "run": {
                "id": run_id,
                "thread_id": thread_id,
                "trigger_message_id": trigger_message_id,
                "status": RunStatus.SUCCEEDED,
                "target_ticker": "AAPL",
                "peer_tickers": ["MSFT"],
                "currency": "USD",
                "as_of": now,
                "created_at": now,
            },
            "table": {
                "run_id": run_id,
                "target_ticker": "AAPL",
                "currency": "USD",
                "as_of": now,
                "rows": [],
                "summary": {
                    "stats": {
                        metric: {"min": None, "median": None, "max": None}
                        for metric in (
                            "ev_to_revenue",
                            "ev_to_ebit",
                            "ev_to_ebitda",
                            "pe",
                        )
                    }
                },
                "comparison_takeaway": {
                    "headline": "AAPL's relative valuation is inconclusive.",
                    "interpretation": "The Comps Table has insufficient evidence.",
                    "confidence": "limited",
                },
            },
            "trace": {"run_id": run_id, "formulas": []},
            "warnings": [],
        }
    )


if __name__ == "__main__":
    unittest.main()
