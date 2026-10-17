"""Verify unchanged W1 bytes and explicitly identify the corrected configuration."""

import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "W1-IDENTITY.json").read_text())
candidate = manifest["corrected_candidate"]
assert candidate["file"] == "backend/app/config.py"
assert candidate["correction_commit"] == "c383700b9fcaeef1c5cb98a306be9c73ca12d8ad"
for name, digest in manifest["sha256"].items():
    expected = candidate["sha256"] if name == candidate["file"] else digest
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
assert candidate["sha256"] != manifest["sha256"][candidate["file"]]
print(f"W1 byte identity: {len(manifest['sha256']) - 1} unchanged original files verified")
print(f"Corrected W2.2 candidate: {candidate['file']} sha256={candidate['sha256']}")
