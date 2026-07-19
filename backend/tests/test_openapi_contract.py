from __future__ import annotations

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
