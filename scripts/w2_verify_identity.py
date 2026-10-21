"""Verify unchanged W1 bytes and explicitly identify the corrected configuration."""

import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "W1-IDENTITY.json").read_text())
candidate = manifest["corrected_candidate"]
assert candidate["file"] == "backend/app/config.py"
assert candidate["correction_commit"] == "c383700b9fcaeef1c5cb98a306be9c73ca12d8ad"
assert manifest["w3_candidate"]["source_commit"] == "464e9fca0abc024e8b62d2db9100a606d87a7f5f"
w3 = manifest["w3_candidate"]["sha256"]
assert set(w3) == {
    "backend/app/services/worker_mode_lock.py",
    "backend/app/services/worker_runtime.py",
    "backend/tests/test_worker_mode_lock.py",
    "backend/postgres_tests/test_worker_mode_exclusion.py",
    "backend/postgres_tests/test_general_worker.py",
}
for name, digest in manifest["sha256"].items():
    expected = candidate["sha256"] if name == candidate["file"] else w3.get(name, digest)
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
assert candidate["sha256"] != manifest["sha256"][candidate["file"]]
for name, digest in w3.items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
print("W1 evidence retained: 87 original files unchanged; corrected config and W3 overrides explicit")
print(f"Corrected W2.2 candidate: {candidate['file']} sha256={candidate['sha256']}")
