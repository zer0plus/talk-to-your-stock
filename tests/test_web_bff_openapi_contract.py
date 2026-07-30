from pathlib import Path

import yaml


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
