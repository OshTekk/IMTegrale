import re
from pathlib import Path

import pytest
from app.database import engine
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_NGINX_HEADERS = {
    'add_header Cache-Control "private, no-store" always;',
    'add_header Referrer-Policy "no-referrer" always;',
    'add_header Vary "Cookie" always;',
    'add_header X-Content-Type-Options "nosniff" always;',
    'add_header X-Robots-Tag "noindex, nofollow, noarchive" always;',
}


def read(path: str) -> str:
    return (ROOT / path).read_text()


def nginx_location_block(nginx: str, marker: str) -> str:
    assert marker in nginx
    return nginx.split(marker, 1)[1].split("\n    location ", 1)[0]


def test_learning_runtime_defaults_and_optional_read_only_mount() -> None:
    runtime_env = read("deploy/botnote-runtime.env")
    service = read("deploy/botnote-web.service")

    assert "BOTNOTE_LEARNING_CONTENT_ROOT=/opt/botnote-learning" in runtime_env
    assert "BOTNOTE_LEARNING_STUDENT_STATUS_MAX_AGE_DAYS=30" in runtime_env
    assert "BOTNOTE_LEARNING_ACCESS_MODE=cohort" in runtime_env
    assert "BOTNOTE_LEARNING_AUDIENCE_ID=fip:2028" in runtime_env
    assert "BOTNOTE_LEARNING_ALLOWED_IMT_USERNAMES='[]'" in runtime_env
    assert "BOTNOTE_LEARNING_ALLOWED_IDENTITIES='[]'" in runtime_env
    assert "User=botnote" in service
    assert "Group=botnote" in service
    assert "ProtectSystem=strict" in service
    assert "ReadOnlyPaths=-/opt/botnote-learning" in service
    assert "ConditionPathExists=/opt/botnote-learning" not in service
    assert "validate_release" not in service


def test_personal_learning_deployment_is_explicit_and_keeps_real_identity_private() -> None:
    example = read(".env.example")
    deployment = read("deploy/README.md")
    web_service = read("deploy/botnote-web.service")
    worker_service = read("deploy/botnote-worker.service")
    cli = read("deploy/botnote-cli")

    assert "BOTNOTE_LEARNING_ACCESS_MODE=personal" in example
    assert "BOTNOTE_LEARNING_AUDIENCE_ID=personal:owner" in example
    assert "BOTNOTE_LEARNING_ALLOWED_IMT_USERNAMES" in example
    assert "BOTNOTE_LEARNING_ALLOWED_IDENTITIES" in example
    assert "une liste absente ou vide" in deployment
    assert "l'audience générale `fip:2028`" in deployment
    assert "fait échouer la configuration" in deployment
    assert "exactement un login IMT stable" in deployment
    assert "un grant temporaire ne peut donc pas ajouter un autre compte" in deployment
    assert "internet:…" in deployment
    assert "peer:…" in deployment
    for unit in (web_service, worker_service):
        assert unit.index("EnvironmentFile=/etc/botnote/botnote-runtime.env") < unit.index(
            "EnvironmentFile=/etc/botnote/botnote.env"
        )
    assert cli.index(". /etc/botnote/botnote-runtime.env") < cli.index(
        ". /etc/botnote/botnote.env"
    )


def test_learning_proxy_has_no_static_content_or_disk_buffering() -> None:
    nginx = read("deploy/pve/botnote-nginx.conf")
    marker = "location ~ ^/api/v1/learning(?:/|$) {"

    assert nginx.index(marker) < nginx.index("location /api/ {")
    block = nginx_location_block(nginx, marker)

    assert "proxy_pass https://botnote_backend;" in block
    assert "if ($botnote_private_ingress = 0) { return 404; }" in block
    assert "X-BotNote-Client-Identity $botnote_client_identity" in block
    assert "proxy_cache off;" in block
    assert "proxy_buffering off;" in block
    assert "proxy_request_buffering off;" in block
    assert "proxy_max_temp_file_size 0;" in block
    assert "access_log /var/log/nginx/botnote-learning.access.log botnote_learning_no_uri;" in block
    assert "error_log /dev/null crit;" in block
    for header in PRIVATE_NGINX_HEADERS:
        assert header in block
    assert re.search(r"(?m)^\s*(?:root|alias)\s+", nginx) is None

    parcours_block = nginx_location_block(nginx, "location ~ ^/parcours(?:/|$) {")
    assert "if ($botnote_private_ingress = 0) { return 404; }" in parcours_block


def test_learning_access_log_omits_uri_query_and_referer() -> None:
    nginx = read("deploy/pve/botnote-nginx.conf")
    match = re.search(
        r"log_format\s+botnote_learning_no_uri\s+(?P<format>.*?);",
        nginx,
        flags=re.DOTALL,
    )

    assert match is not None
    variables = set(re.findall(r"\$[a-z0-9_]+", match.group("format")))
    assert variables.isdisjoint(
        {
            "$args",
            "$http_referer",
            "$query_string",
            "$request",
            "$request_body",
            "$request_uri",
            "$uri",
        }
    )


def test_server_level_logs_are_safe_before_location_selection() -> None:
    nginx = read("deploy/pve/botnote-nginx.conf")

    assert "access_log /var/log/nginx/botnote.access.log botnote_learning_no_uri;" in nginx
    assert "access_log /var/log/nginx/botnote.access.log;" not in nginx
    assert "error_log /var/log/nginx/botnote.error.log" not in nginx
    server_prefix = nginx.split("location = /health/", 1)[0]
    assert "error_log /dev/null crit;" in server_prefix


@pytest.mark.parametrize(
    ("marker", "access_log"),
    [
        (
            "location ~ ^/api/v1/learning(?:/|$) {",
            "access_log /var/log/nginx/botnote-learning.access.log botnote_learning_no_uri;",
        ),
        (
            "location ~ ^/parcours(?:/|$) {",
            "access_log /var/log/nginx/botnote-parcours.access.log botnote_learning_no_uri;",
        ),
    ],
)
def test_private_learning_locations_use_uri_free_logs_and_always_private_headers(
    marker: str,
    access_log: str,
) -> None:
    nginx = read("deploy/pve/botnote-nginx.conf")
    block = nginx_location_block(nginx, marker)

    assert access_log in block
    assert "error_log /dev/null crit;" in block
    assert "proxy_pass https://botnote_backend;" in block
    for forbidden_variable in (
        "$args",
        "$http_referer",
        "$query_string",
        "$request",
        "$request_body",
        "$request_uri",
        "$uri",
    ):
        assert forbidden_variable not in block
    for header in PRIVATE_NGINX_HEADERS:
        assert header in block


def test_parcours_deep_links_do_not_fall_through_to_the_global_uri_log() -> None:
    nginx = read("deploy/pve/botnote-nginx.conf")
    parcours_marker = "location ~ ^/parcours(?:/|$) {"
    block = nginx_location_block(nginx, parcours_marker)

    assert nginx.index(parcours_marker) < nginx.index("location / {")
    assert "root " not in block
    assert "alias " not in block
    assert "botnote_learning_no_uri" in block


def test_ci_builds_and_scans_one_backend_wheel_outside_dist() -> None:
    workflow = read(".github/workflows/ci.yml")

    assert 'wheel_out="$(mktemp -d)"' in workflow
    assert '--wheel-dir "$wheel_out"' in workflow
    assert "python scripts/check_content_boundary.py --wheel" in workflow
    assert "--wheel-dir dist" not in workflow


def test_frontend_ci_scans_dist_from_its_working_directory() -> None:
    workflow = read(".github/workflows/ci.yml")

    assert "working-directory: frontend" in workflow
    assert (
        "python ../scripts/check_content_boundary.py --repo-root .. --dist frontend/dist"
        in workflow
    )
    assert "python ../scripts/check_content_boundary.py --repo-root .. --dist dist" not in workflow


def test_private_release_process_is_atomic_and_kept_out_of_public_artifacts() -> None:
    deployment = read("deploy/README.md")
    contributing = read("CONTRIBUTING.md")

    assert "python tools/validate_release.py <release_dir>" in deployment
    assert "/opt/botnote-learning/releases/RELEASE_ID" in deployment
    assert "mv -Tf" in deployment
    assert "root:botnote" in deployment
    assert "compilateur" in deployment
    assert "Aucun véritable compte étudiant" in deployment
    for public_path in ("frontend/public", "frontend/dist", "backend/app/static"):
        assert public_path in deployment
        assert public_path in contributing


def test_database_errors_hide_private_bound_parameters() -> None:
    canary = "private-learning-id-must-not-reach-logs"

    assert engine.hide_parameters is True
    with engine.connect() as connection, pytest.raises(SQLAlchemyError) as captured:
        connection.execute(
            text("SELECT * FROM deliberately_missing_table WHERE content_id = :content_id"),
            {"content_id": canary},
        )

    assert canary not in str(captured.value)
