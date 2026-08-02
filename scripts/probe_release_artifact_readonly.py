#!/usr/bin/env python3
"""Prove that the post-seal build UID cannot mutate the published release tree."""

from __future__ import annotations

import argparse
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from verify_release_artifact import verify

Mutation = Callable[[], None]


def _attempt(mutation: Mutation) -> bool:
    try:
        mutation()
    except OSError:
        return False
    return True


def _write_first_byte(path: Path) -> None:
    with path.open("r+b", buffering=0) as handle:
        first = handle.read(1) or b"x"
        handle.seek(0)
        handle.write(first)


def _append_byte(path: Path) -> None:
    with path.open("ab", buffering=0) as handle:
        handle.write(b"x")


def probe(
    root: Path,
    *,
    expected_seal_digest: str,
    expected_source_commit: str,
    expected_owner: int = 0,
    expected_group: int = 0,
) -> None:
    root = root.absolute()
    wheel = next((root / "wheel").glob("*.whl"))
    manifest = root / "release-manifest.json"
    frontend = root / "frontend"
    frontend_file = next(
        path
        for path in sorted((frontend / "assets").iterdir())
        if path.is_file()
    )
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="imtegrale-seal-probe-") as temporary:
        temporary_root = Path(temporary)
        replacement = temporary_root / "replacement"
        replacement.write_bytes(b"synthetic replacement\n")
        symlink_replacement = temporary_root / "replacement-link"
        os.symlink(replacement, symlink_replacement)
        mutations: dict[str, Mutation] = {
            "write": lambda: _write_first_byte(frontend_file),
            "truncate": lambda: os.truncate(wheel, 0),
            "extend": lambda: _append_byte(manifest),
            "rename": lambda: os.rename(frontend_file, frontend_file.with_name("seal-probe-renamed")),
            "unlink": lambda: frontend_file.unlink(),
            "add": lambda: (root / "seal-probe-added").write_bytes(b"synthetic\n"),
            "symlink": lambda: os.symlink(frontend_file, root / "seal-probe-symlink"),
            "hardlink": lambda: os.link(frontend_file, root / "seal-probe-hardlink"),
            "symlink-substitution": lambda: os.replace(symlink_replacement, frontend_file),
            "directory-swap": lambda: os.rename(frontend, root / "seal-probe-frontend"),
        }
        for name, mutation in mutations.items():
            if _attempt(mutation):
                failures.append(name)

        concurrent_write_succeeded = threading.Event()

        def attack_during_verification() -> None:
            for _ in range(500):
                if _attempt(lambda: _append_byte(manifest)):
                    concurrent_write_succeeded.set()
                    return

        attacker = threading.Thread(target=attack_during_verification, daemon=True)
        attacker.start()
        verify(
            root,
            expected_seal_digest=expected_seal_digest,
            expected_source_commit=expected_source_commit,
            require_sealed=True,
            expected_owner=expected_owner,
            expected_group=expected_group,
        )
        attacker.join(timeout=10)
        if attacker.is_alive():
            raise RuntimeError("readonly-probe: attacker thread did not stop")
        if concurrent_write_succeeded.is_set():
            failures.append("concurrent-write")

    if failures:
        raise RuntimeError(f"readonly-probe: mutation succeeded count={len(failures)}")
    verify(
        root,
        expected_seal_digest=expected_seal_digest,
        expected_source_commit=expected_source_commit,
        require_sealed=True,
        expected_owner=expected_owner,
        expected_group=expected_group,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe one sealed IMTégrale release tree")
    parser.add_argument("artifact_root", type=Path)
    parser.add_argument("--expected-seal-digest", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-owner", type=int, default=0)
    parser.add_argument("--expected-group", type=int, default=0)
    args = parser.parse_args()
    try:
        probe(
            args.artifact_root,
            expected_seal_digest=args.expected_seal_digest,
            expected_source_commit=args.expected_source_commit,
            expected_owner=args.expected_owner,
            expected_group=args.expected_group,
        )
    except Exception:
        print("readonly-probe: denied", file=os.sys.stderr)
        return 1
    print("readonly-probe: ok mutations_denied=11 concurrent_attempts=500")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
