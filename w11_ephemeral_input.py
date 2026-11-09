"""Pinned, ephemeral test inputs; never print payloads or private exceptions."""
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest

HASHES = {
    'manager.py': '046a3d7b002a33192b95e529397fd95d5b0be1bee77685fb3be855dc4fff44e6',
    'tests/helpers.py': '717bfad2b4652019b617ffdcf3c95becd63931632b8b912c254c7e8251caf180',
    'tests/__init__.py': '01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b',
}


def verify(root):
    for name, expected in HASHES.items():
        path = root / 'credstore' / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('test input identity mismatch')


def unpack(root):
    # Fixed filenames, new private directory, bounded decoding, no tar extraction.
    encoded = os.environ.pop('W11_EPHEMERAL_INPUT', '')
    if not 0 < len(encoded) <= 48000:
        raise RuntimeError('missing or oversized test input')
    compressed = base64.b64decode(encoded, validate=True)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw = stream.read(150001)
    if len(raw) > 150000:
        raise RuntimeError('oversized decoded test input')
    values = json.loads(raw)
    if set(values) != set(HASHES):
        raise RuntimeError('unexpected test input files')
    payloads = {name: base64.b64decode(value, validate=True) for name, value in values.items()}
    if any(hashlib.sha256(data).hexdigest() != HASHES[name] for name, data in payloads.items()):
        raise RuntimeError('test input identity mismatch')
    root.mkdir(mode=0o700)  # Existing paths fail closed.
    (root / 'credstore').mkdir(mode=0o700)
    (root / 'credstore/tests').mkdir(mode=0o700)
    for name, data in payloads.items():
        with (root / 'credstore' / name).open('xb') as stream:
            os.chmod(stream.fileno(), 0o600)
            stream.write(data)
    verify(root)


def regressions(root):
    verify(root)
    os.environ['W11_CREDSTORE_SOURCE'] = str(root)
    suite = unittest.defaultTestLoader.loadTestsFromNames(
        ['test_w10_diagnostics', 'test_w104_quiescence', 'test_w11_credstore'])
    # A failed private frame must not publish private source lines in public logs.
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    print(json.dumps({'w11_local_regressions': result.testsRun,
                      'failures': len(result.failures), 'errors': len(result.errors),
                      'skips': len(result.skipped)}), flush=True)
    if not result.wasSuccessful() or result.testsRun != 37 or result.skipped:
        raise RuntimeError('bounded private-input regression failure; no private traceback emitted')


def cleanup(root):
    # Never recursively remove an arbitrary directory.
    if not root.exists():
        return
    if root.is_symlink():
        raise RuntimeError('unsafe cleanup root')
    for name in HASHES:
        (root / 'credstore' / name).unlink(missing_ok=True)
    (root / 'credstore/tests').rmdir()
    (root / 'credstore').rmdir()
    root.rmdir()


if __name__ == '__main__':
    try:
        action, folder = sys.argv[1:]
        root = Path(folder)
        if not root.is_absolute() or root.name != 'w11-ephemeral-input':
            raise RuntimeError('invalid test input directory')
        {'unpack': unpack, 'regressions': regressions, 'cleanup': cleanup}[action](root)
    except BaseException:
        print('W11_INPUT_OPERATION_FAILED; details withheld to protect private source', flush=True)
        raise SystemExit(1) from None
