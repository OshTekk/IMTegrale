from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_IMPORTERS = (
    "backend/app/routers/auth.py",
    "backend/app/routers/settings.py",
    "backend/app/services/pass_sessions.py",
    "backend/app/services/pass_gateway.py",
    "backend/app/services/sync.py",
    "backend/app/services/sync_schedule.py",
    "backend/app/services/worker_runtime.py",
    "backend/app/services/jobs.py",
    "backend/app/models.py",
    "backend/app/config.py",
)

FORBIDDEN_CRYPTO_DEPENDENCIES = (
    "app.config",
    "app.database",
    "app.models",
    "app.security",
    "app.services",
    "fastapi",
    "sqlalchemy",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_application_paths_do_not_import_the_hpke_package() -> None:
    for relative_path in FORBIDDEN_IMPORTERS:
        modules = _imported_modules(PROJECT_ROOT / relative_path)
        assert all(not module.startswith("app.crypto") for module in modules), relative_path

    for path in (PROJECT_ROOT / "frontend").rglob("*"):
        if path.is_file() and path.suffix in {".ts", ".tsx", ".js", ".mjs"}:
            assert "app.crypto" not in path.read_text(encoding="utf-8"), path


def test_hpke_package_has_no_application_or_persistence_dependency() -> None:
    for path in (PROJECT_ROOT / "backend/app/crypto").glob("*.py"):
        modules = _imported_modules(path)
        assert all(
            not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in FORBIDDEN_CRYPTO_DEPENDENCIES
            )
            for module in modules
        ), path


def test_hpke_package_does_not_read_configuration_or_filesystem_keys() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "backend/app/crypto").glob("*.py")
    )
    forbidden_fragments = (
        "BOTNOTE_",
        "CREDENTIALS_DIRECTORY",
        "cipher_for",
        "encrypted_imt_password",
        "imt_sync_credentials",
        "open(",
        "Path(",
        "os.environ",
        "getenv(",
    )
    assert all(fragment not in source for fragment in forbidden_fragments)
