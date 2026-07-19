#!/usr/bin/env python3
"""Generate a deterministic CycloneDX inventory from production lock files."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path
from urllib.parse import quote

PYTHON_REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")
PNPM_PACKAGE = re.compile(r"^  ['\"]?(.+?@[^:'\"]+)['\"]?:$")


def _component(ecosystem: str, name: str, version: str) -> dict[str, object]:
    purl_type = "pypi" if ecosystem == "python" else "npm"
    purl_name = quote(name, safe="@/")
    return {
        "type": "library",
        "bom-ref": f"pkg:{purl_type}/{purl_name}@{version}",
        "name": name,
        "version": version,
        "purl": f"pkg:{purl_type}/{purl_name}@{version}",
        "properties": [{"name": "imtegrale:ecosystem", "value": ecosystem}],
    }


def python_components(path: Path) -> list[dict[str, object]]:
    components = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = PYTHON_REQUIREMENT.fullmatch(raw_line.strip())
        if match:
            components.append(_component("python", match.group(1), match.group(2)))
    return components


def node_components(path: Path) -> list[dict[str, object]]:
    components = []
    in_packages = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line == "packages:":
            in_packages = True
            continue
        if raw_line == "snapshots:":
            break
        if not in_packages:
            continue
        match = PNPM_PACKAGE.fullmatch(raw_line)
        if not match:
            continue
        package_and_version = match.group(1).split("(", 1)[0]
        name, separator, version = package_and_version.rpartition("@")
        if separator and name and version:
            components.append(_component("node", name, version))
    unique = {str(component["bom-ref"]): component for component in components}
    return list(unique.values())


def generate(repo_root: Path) -> dict[str, object]:
    project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    components = python_components(repo_root / "deploy" / "requirements.lock")
    components.extend(node_components(repo_root / "frontend" / "pnpm-lock.yaml"))
    components.sort(key=lambda item: str(item["bom-ref"]))
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "IMTegrale",
                "version": project["version"],
            },
            "properties": [
                {"name": "imtegrale:source", "value": "production-locks"},
            ],
        },
        "components": components,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = generate(args.repo_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"sbom: {len(payload['components'])} components")


if __name__ == "__main__":
    main()
