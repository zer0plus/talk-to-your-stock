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
        "/v1/runs/{run_id}/source-snapshot",
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
        "/v1/runs/{run_id}/source-snapshot",
    ):
        response_schema = contract["paths"][path]["get"]["responses"]["400"][
            "content"
        ]["application/json"]["schema"]
        assert response_schema["$ref"] == "#/components/schemas/ErrorResponse"


def test_source_snapshot_contracts_forbid_unknown_response_fields() -> None:
    source_contract = yaml.safe_load(
        (REPO_ROOT / "api" / "openapi.yaml").read_text()
    )
    source_schema = source_contract["components"]["schemas"][
        "SourceSnapshotResponse"
    ]
    assert source_schema["additionalProperties"] is False

    generated_contract = TestClient(app).get("/openapi.json").json()
    generated_schema = generated_contract["components"]["schemas"][
        "SourceSnapshotResponse"
    ]
    assert generated_schema["additionalProperties"] is False


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


def test_thread_run_history_contract_matches_implemented_behavior() -> None:
    source_contract = yaml.safe_load(
        (REPO_ROOT / "api" / "openapi.yaml").read_text()
    )
    source_operation = source_contract["paths"][
        "/v1/threads/{thread_id}/runs"
    ]["get"]
    assert "newest first" in source_operation["description"]
    assert source_operation["security"] == []
    source_parameters = {}
    for parameter in source_operation["parameters"]:
        if "$ref" in parameter:
            parameter = source_contract["components"]["parameters"][
                parameter["$ref"].rsplit("/", maxsplit=1)[-1]
            ]
        source_parameters[parameter["name"]] = parameter
    assert set(source_parameters) == {
        "thread_id",
        "status",
        "limit",
        "cursor",
        "authorization",
    }
    assert source_parameters["authorization"].get("required", False) is False
    assert source_parameters["authorization"]["schema"] == {
        "type": ["string", "null"]
    }
    assert source_parameters["status"]["schema"]["enum"] == [
        "queued",
        "running",
        "succeeded",
        "failed",
    ]
    assert source_parameters["limit"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 200,
        "default": 20,
    }
    assert set(source_operation["responses"]) == {
        "200",
        "400",
        "401",
        "404",
        "503",
    }

    generated_contract = TestClient(app).get("/openapi.json").json()
    generated_operation = generated_contract["paths"][
        "/v1/threads/{thread_id}/runs"
    ]["get"]
    assert "newest first" in generated_operation["description"]
    assert generated_operation.get("security", []) == source_operation["security"]
    generated_parameters = {
        parameter["name"]: parameter
        for parameter in generated_operation["parameters"]
    }
    assert set(generated_parameters) == {
        "thread_id",
        "status",
        "limit",
        "cursor",
        "authorization",
    }
    assert generated_parameters["authorization"]["required"] is False
    assert generated_parameters["authorization"]["schema"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert generated_contract["components"]["schemas"]["RunStatus"]["enum"] == (
        source_parameters["status"]["schema"]["enum"]
    )
    generated_limit = generated_parameters["limit"]["schema"]
    assert {
        key: generated_limit[key]
        for key in ("type", "minimum", "maximum", "default")
    } == source_parameters["limit"]["schema"]
    assert set(generated_operation["responses"]) == {
        "200",
        "400",
        "401",
        "404",
        "503",
    }
    assert generated_operation["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/RunListResponse"}
    assert generated_contract["components"]["schemas"]["RunListResponse"][
        "required"
    ] == ["runs", "page"]
    source_pagination = source_contract["components"]["schemas"]["PaginationMeta"]
    generated_pagination = generated_contract["components"]["schemas"][
        "PaginationMeta"
    ]
    assert generated_pagination["required"] == source_pagination["required"]
    assert set(generated_pagination["properties"]) == set(
        source_pagination["properties"]
    )
    source_run = source_contract["components"]["schemas"]["Run"]
    generated_run = generated_contract["components"]["schemas"]["Run"]
    assert generated_run["required"] == source_run["required"]
    assert set(generated_run["properties"]) == set(source_run["properties"])
    source_error_response = source_contract["components"]["schemas"][
        "ErrorResponse"
    ]
    source_error_detail = source_error_response["properties"]["error"]
    generated_error_response = generated_contract["components"]["schemas"][
        "ErrorResponse"
    ]
    generated_error_detail = generated_contract["components"]["schemas"][
        "ErrorDetail"
    ]
    assert generated_error_response["required"] == source_error_response["required"]
    assert generated_error_detail["required"] == source_error_detail["required"]
    assert set(generated_error_detail["properties"]) == set(
        source_error_detail["properties"]
    )
    assert generated_contract["components"]["schemas"]["ErrorCode"]["enum"] == (
        source_error_detail["properties"]["code"]["enum"]
    )
    assert generated_error_detail["properties"]["message"]["type"] == (
        source_error_detail["properties"]["message"]["type"]
    )
    for field in ("details", "run_id", "request_id"):
        generated_types = {
            schema["type"]
            for schema in generated_error_detail["properties"][field]["anyOf"]
        }
        assert generated_types == set(
            source_error_detail["properties"][field]["type"]
        )
    for response_code in ("400", "401", "404", "503"):
        source_response_ref = source_operation["responses"][response_code]["$ref"]
        source_response = source_contract["components"]["responses"][
            source_response_ref.rsplit("/", maxsplit=1)[-1]
        ]
        source_schema = source_response["content"]["application/json"]["schema"]
        generated_schema = generated_operation["responses"][response_code]["content"][
            "application/json"
        ]["schema"]
        assert source_schema == generated_schema == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
