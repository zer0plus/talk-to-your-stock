from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_message_list_documents_invalid_cursor_response() -> None:
    contract = yaml.safe_load((REPO_ROOT / "api" / "openapi.yaml").read_text())

    responses = contract["paths"]["/v1/threads/{thread_id}/messages"]["get"]["responses"]

    assert responses["400"] == {"$ref": "#/components/responses/ValidationError"}
