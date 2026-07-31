from __future__ import annotations

import json

from app.main import app
from fastapi.testclient import TestClient

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
ERROR_STATUSES = {"400", "401", "403", "404", "409", "413", "422", "429", "503"}


def operations(document: dict):  # noqa: ANN201
    for path, path_item in document["paths"].items():
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def test_operation_ids_are_stable_and_unique() -> None:
    document = app.openapi()
    operation_ids = [operation["operationId"] for _, _, operation in operations(document)]

    assert len(operation_ids) == len(set(operation_ids))
    assert "auth_login_imt" in operation_ids
    assert "tokens_create_token" in operation_ids
    assert "sync_start_sync" in operation_ids
    assert all("api_v1" not in operation_id for operation_id in operation_ids)


def test_every_json_success_response_has_an_explicit_schema() -> None:
    document = app.openapi()
    checked = 0
    for path, method, operation in operations(document):
        for status_code, response in operation["responses"].items():
            if not status_code.startswith("2"):
                continue
            json_content = response.get("content", {}).get("application/json")
            if json_content is None:
                continue
            schema = json_content.get("schema")
            assert schema, f"{method.upper()} {path} has no JSON response schema"
            assert not (
                schema.get("type") == "object"
                and schema.get("additionalProperties") is True
                and not schema.get("properties")
            ), f"{method.upper()} {path} exposes an unrestricted JSON object"
            checked += 1
    assert checked >= 80


def test_every_api_operation_documents_the_stable_error_envelope() -> None:
    document = app.openapi()
    expected_ref = "#/components/schemas/ApiErrorEnvelope"
    for path, method, operation in operations(document):
        assert ERROR_STATUSES.issubset(operation["responses"]), (
            f"{method.upper()} {path} is missing common errors"
        )
        for status_code in ERROR_STATUSES:
            schema = operation["responses"][status_code]["content"]["application/json"]["schema"]
            assert schema == {"$ref": expected_ref}, (
                f"{method.upper()} {path} has an inconsistent {status_code} envelope"
            )


def test_validation_errors_do_not_echo_rejected_credentials(client: TestClient) -> None:
    sentinel = "credential-value-that-must-never-be-echoed"
    response = client.post(
        "/api/v1/auth/login/imt",
        json={"username": {"private": sentinel}, "password": {"private": sentinel}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"
    assert sentinel not in response.text


def test_body_limit_uses_the_same_stable_error_contract(client: TestClient) -> None:
    sentinel = "oversized-private-body-marker"
    response = client.post(
        "/api/v1/auth/login/token",
        content=(sentinel.encode() + b"x" * 1_100_000),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": {
            "code": "HTTP_413",
            "message": "Requête trop volumineuse",
        }
    }
    assert sentinel not in response.text


def test_private_comparison_token_is_limited_to_one_shot_and_post_bodies() -> None:
    document = app.openapi()
    schemas = document["components"]["schemas"]
    created = schemas["PrivateComparisonInvitationCreatedResponse"]
    invitation_list = schemas["PrivateComparisonInvitationResponse"]
    active_comparison = schemas["ActivePrivateComparisonListItem"]
    terminal_comparison = schemas["TerminalPrivateComparisonHistoryItem"]
    detail = schemas["PrivateComparisonDetailResponse"]

    assert "token" in created["required"]
    assert created["properties"]["token"]["pattern"] == r"^pcinv1_[A-Za-z0-9_-]{43}$"
    assert "writeOnly" not in created["properties"]["token"]
    assert "token" not in invitation_list["properties"]
    assert "token" not in active_comparison["properties"]
    assert "token" not in terminal_comparison["properties"]
    assert "token" not in detail["properties"]
    assert "token_digest" not in json.dumps(document)

    for request_schema in (
        "PrivateComparisonInvitationAccept",
        "PrivateComparisonInvitationTokenRequest",
    ):
        token = schemas[request_schema]["properties"]["token"]
        assert token["writeOnly"] is True
        assert token["pattern"] == r"^pcinv1_[A-Za-z0-9_-]{43}$"

    assert "token" not in schemas["PrivateComparisonInvitationCreate"]["properties"]


def test_unavailable_private_comparison_schemas_are_structurally_minimal() -> None:
    document = app.openapi()
    schemas = document["components"]["schemas"]
    terminal = schemas["TerminalPrivateComparisonHistoryItem"]
    suspended = schemas["SuspendedPrivateComparisonListItem"]

    assert set(terminal["properties"]) == {"public_id", "status", "ended_at"}
    assert set(terminal["required"]) == {"public_id", "status", "ended_at"}
    assert set(terminal["properties"]["status"]["enum"]) == {"expired", "revoked"}
    assert set(suspended["properties"]) == {"public_id", "status", "label"}
    assert set(suspended["required"]) == {"public_id", "status", "label"}
    assert suspended["properties"]["status"]["const"] == "suspended"
    assert (
        suspended["properties"]["label"]["const"]
        == "Comparaison temporairement indisponible"
    )

    forbidden_fragments = {
        "identity",
        "display_name",
        "freshness",
        "summary",
        "common_ues",
        "average",
        "gpa",
        "ects",
        "grade",
        "participant",
        "sync",
    }
    for schema in (terminal, suspended):
        serialized = json.dumps(schema).casefold()
        assert all(fragment not in serialized for fragment in forbidden_fragments)

    list_items = schemas["PrivateComparisonListResponse"]["properties"]["comparisons"]["items"]
    assert list_items["discriminator"]["propertyName"] == "status"
    assert {
        branch["$ref"].rsplit("/", 1)[-1]
        for branch in list_items["oneOf"]
    } == {
        "ActivePrivateComparisonListItem",
        "SuspendedPrivateComparisonListItem",
        "TerminalPrivateComparisonHistoryItem",
    }


def test_private_comparison_session_and_one_shot_contract_are_scope_bound() -> None:
    schemas = app.openapi()["components"]["schemas"]
    authenticated = schemas["AuthenticatedSessionResponse"]
    created = schemas["PrivateComparisonInvitationCreatedResponse"]

    assert {"session_scope", "session_expires_at", "server_time"} <= set(
        authenticated["required"]
    )
    assert authenticated["properties"]["session_scope"]["pattern"] == r"^bss1_[0-9a-f]{64}$"
    assert {"session_scope", "token"} <= set(created["required"])
    assert created["properties"]["session_scope"]["pattern"] == r"^bss1_[0-9a-f]{64}$"


def test_event_contract_exposes_only_opaque_non_ordered_cursors() -> None:
    document = app.openapi()
    schemas = document["components"]["schemas"]
    event = schemas["EventResponse"]
    dashboard = schemas["DashboardResponse"]

    assert "id" not in event["properties"]
    assert event["properties"]["cursor"]["pattern"] == r"^evc1_[A-Za-z0-9_-]{32}$"
    assert (
        dashboard["properties"]["latest_event_cursor"]["anyOf"][0]["pattern"]
        == r"^evc1_[A-Za-z0-9_-]{32}$"
    )
    event_stream_parameters = document["paths"]["/api/v1/events"]["get"]["parameters"]
    after = next(parameter for parameter in event_stream_parameters if parameter["name"] == "after")
    assert after["schema"]["anyOf"][0]["type"] == "string"
    assert all(branch.get("type") != "integer" for branch in after["schema"]["anyOf"])
