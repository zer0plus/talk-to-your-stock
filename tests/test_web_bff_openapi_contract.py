from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from web_bff.main import app


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_message_list_documents_invalid_cursor_response() -> None:
    contract = yaml.safe_load((REPO_ROOT / "api" / "openapi.yaml").read_text())

    responses = contract["paths"]["/v1/threads/{thread_id}/messages"]["get"]["responses"]

    assert responses["400"] == {"$ref": "#/components/responses/ValidationError"}


def test_run_readback_documents_comps_service_unavailability() -> None:
    contract = yaml.safe_load((REPO_ROOT / "api" / "openapi.yaml").read_text())

    for path in (
        "/v1/runs/{run_id}",
        "/v1/runs/{run_id}/table",
        "/v1/runs/{run_id}/trace",
    ):
        responses = contract["paths"][path]["get"]["responses"]
        assert responses["503"] == {
            "$ref": "#/components/responses/ServiceUnavailable"
        }


def test_generated_run_readback_contract_documents_validation_errors() -> None:
    contract = TestClient(app).get("/openapi.json").json()

    for path in (
        "/v1/runs/{run_id}",
        "/v1/runs/{run_id}/table",
        "/v1/runs/{run_id}/trace",
    ):
        response_schema = contract["paths"][path]["get"]["responses"]["400"][
            "content"
        ]["application/json"]["schema"]
        assert response_schema["$ref"] == "#/components/schemas/ErrorResponse"


def test_failed_run_errors_are_declared_in_source_and_generated_contracts() -> None:
    source_contract = yaml.safe_load(
        (REPO_ROOT / "api" / "openapi.yaml").read_text()
    )
    source_error = source_contract["components"]["schemas"]["ErrorResponse"][
        "properties"
    ]["error"]["properties"]
    assert source_error["run_id"] == {
        "type": ["string", "null"],
        "format": "uuid",
    }
    source_responses = source_contract["paths"][
        "/v1/threads/{thread_id}/messages"
    ]["post"]["responses"]
    assert source_responses["503"] == {
        "$ref": "#/components/responses/ServiceUnavailable"
    }

    generated_contract = TestClient(app).get("/openapi.json").json()
    generated_error = generated_contract["components"]["schemas"]["ErrorDetail"][
        "properties"
    ]
    assert generated_error["run_id"]["anyOf"] == [
        {"type": "string", "format": "uuid"},
        {"type": "null"},
    ]
    generated_responses = generated_contract["paths"][
        "/v1/threads/{thread_id}/messages"
    ]["post"]["responses"]
    assert generated_responses["503"]["content"]["application/json"]["schema"][
        "$ref"
    ] == "#/components/schemas/ErrorResponse"


def test_run_as_of_is_nullable_in_source_and_generated_contracts() -> None:
    source_contract = yaml.safe_load(
        (REPO_ROOT / "api" / "openapi.yaml").read_text()
    )
    source_as_of = source_contract["components"]["schemas"]["Run"]["properties"][
        "as_of"
    ]
    assert source_as_of == {
        "type": ["string", "null"],
        "format": "date-time",
    }

    generated_contract = TestClient(app).get("/openapi.json").json()
    generated_as_of = generated_contract["components"]["schemas"]["Run"][
        "properties"
    ]["as_of"]
    assert generated_as_of["anyOf"] == [
        {"type": "string", "format": "date-time"},
        {"type": "null"},
    ]
