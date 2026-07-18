import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_public_identity_is_consistent_and_independent() -> None:
    index = read("frontend/index.html")
    brand = read("frontend/src/brand.ts")
    styles = read("frontend/src/styles.css")
    manifest = json.loads(read("frontend/public/site.webmanifest"))
    frontend_sources = "\n".join(
        path.read_text() for path in (ROOT / "frontend/src").rglob("*.tsx")
    )

    assert "IMTégrale" in index
    assert manifest["name"] == "IMTégrale"
    assert "Service étudiant indépendant" in brand
    assert "non affilié ni approuvé par IMT Atlantique" in brand
    assert "#00B8DE" not in styles.upper()
    assert "BotNote IMT" not in frontend_sources


def test_release_versions_remain_aligned() -> None:
    project = tomllib.loads(read("pyproject.toml"))
    frontend = json.loads(read("frontend/package.json"))
    backend = read("backend/app/__init__.py")
    match = re.search(r'^__version__ = "([^"]+)"$', backend, flags=re.MULTILINE)

    assert match is not None
    assert project["project"]["version"] == frontend["version"] == match.group(1)


def test_public_explanations_match_the_service_session_model() -> None:
    login = read("frontend/src/pages/LoginPage.tsx")
    trust = read("frontend/src/pages/TrustPage.tsx")
    demo = read("frontend/src/pages/DemoPage.tsx")
    security = read("SECURITY.md")

    assert "Mot de passe IMT jamais conservé" in login
    assert "il ne remplace pas les règles du SI" in trust
    assert "Accès & partage" in demo
    assert "Simulations" in demo
    assert "Agenda" in demo
    assert "Relevé PDF" in demo
    assert "chiffre les identifiants nécessaires" not in security
    assert "supprimés au plus tard après 30 jours" in security
