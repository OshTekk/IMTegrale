"""Verify the recorded immutable W1 bytes without fetching its history."""

import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "W1-IDENTITY.json").read_text())
for name, digest in manifest["sha256"].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
print(f"W1 byte identity: {len(manifest['sha256'])} original files verified")
