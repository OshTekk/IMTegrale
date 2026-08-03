from __future__ import annotations

import os
import runpy
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SYNC_UNIT = ROOT / "deploy" / "botnote-sync-worker.service"
GENERIC_UNIT = ROOT / "deploy" / "botnote-job-worker@.service"
WEB_UNIT = ROOT / "deploy" / "botnote-web.service"
SCHEDULER_UNIT = ROOT / "deploy" / "botnote-scheduler.service"
PROVISIONER = ROOT / "deploy" / "security" / "provision-sync-hpke-keys"
PG_HBA_RULES = ROOT / "deploy" / "security" / "botnote-sync.pg-hba.conf"
POSTGRES_ROLE = ROOT / "deploy" / "security" / "provision-sync-postgres-role.sql"


def test_dedicated_unit_has_fixed_credentials_and_hardening() -> None:
    unit = SYNC_UNIT.read_text()
    assert "User=botnote-sync" in unit
    assert "Group=botnote-sync" in unit
    assert "ExecStart=/opt/botnote/runtime/bin/botnote sync-worker" in unit
    assert "EnvironmentFile=/etc/botnote/botnote.env" not in unit
    assert unit.count("LoadCredential=") == 6
    for logical_name in (
        "imt-sync-credential-private",
        "imt-sync-credential-public",
        "pass-service-session-private",
        "pass-service-session-public",
        "autonomous-sync-canary-account-ids",
        "owner-imt-username",
    ):
        assert f"LoadCredential={logical_name}" in unit
        assert f"Environment={logical_name}" not in unit
    for logical_name in (
        "autonomous-sync-canary-account-ids",
        "owner-imt-username",
    ):
        assert f"LoadCredential={logical_name}\n" in unit
    assert "-v2." in unit
    assert "-v1." not in unit
    for directive in (
        "PrivateMounts=true",
        "ProtectProc=invisible",
        "ProcSubset=pid",
        "MemorySwapMax=0",
        "LimitCORE=0",
        "CapabilityBoundingSet=",
        "InaccessiblePaths=-/etc/botnote/sync-hpke",
        "ReadWritePaths=/run/botnote-sync-locks",
    ):
        assert directive in unit
    assert "ExecCondition=/usr/bin/test %i != sync" in GENERIC_UNIT.read_text()


def test_web_can_share_only_the_account_lock_boundary() -> None:
    unit = WEB_UNIT.read_text()
    assert "SupplementaryGroups=botnote-sync-lock" in unit
    assert "ReadWritePaths=/run/botnote-sync-locks" in unit
    assert unit.count("LoadCredential=") == 3
    assert (
        "LoadCredential=pass-service-session-public:/etc/botnote/sync-hpke/pass-service-session-v2.public.raw"
    ) in unit
    assert "LoadCredential=autonomous-sync-canary-account-ids" in unit
    assert "LoadCredential=learning-allowed-imt-usernames" in unit
    for forbidden_name in (
        "pass-service-session-private",
        "imt-sync-credential-private",
        "imt-sync-credential-public",
    ):
        assert forbidden_name not in unit
    assert "InaccessiblePaths=-/etc/botnote/sync-hpke" in unit


def test_other_runtime_units_receive_only_their_identifier_consumers() -> None:
    generic = GENERIC_UNIT.read_text()
    scheduler = SCHEDULER_UNIT.read_text()

    assert "InaccessiblePaths=-/etc/botnote/sync-hpke" in generic
    assert "InaccessiblePaths=-/etc/credstore" in generic
    assert "LoadCredential=" not in generic

    assert "InaccessiblePaths=-/etc/botnote/sync-hpke" in scheduler
    assert "InaccessiblePaths=-/etc/credstore" in scheduler
    assert scheduler.count("LoadCredential=") == 1
    assert "LoadCredential=autonomous-sync-canary-account-ids" in scheduler


def test_postgres_peer_identity_is_limited_to_the_application_database() -> None:
    rules = [
        line.split() for line in PG_HBA_RULES.read_text().splitlines() if line and not line.startswith("#")
    ]
    assert rules == [
        ["local", "botnote", "botnote-sync", "peer"],
        ["local", "all", "botnote-sync", "reject"],
    ]


def test_sync_role_receives_only_bounded_credential_table_permissions() -> None:
    source = POSTGRES_ROLE.read_text()
    assert 'GRANT SELECT ON TABLE imt_sync_credentials TO "botnote-sync";' in source
    update_grant = source.split("GRANT UPDATE (", 1)[1].split(
        ") ON TABLE imt_sync_credentials",
        1,
    )[0]
    for allowed in (
        "encrypted_envelope",
        "envelope_version",
        "key_id",
        "credential_generation",
        "state",
        "last_used_at",
        "last_success_at",
        "last_failure_at",
        "failure_count",
        "revoked_at",
        "revoked_reason",
        "updated_at",
    ):
        assert allowed in update_grant
    for forbidden in (
        "account_id",
        "consent_version",
        "consented_at",
        "verified_at",
    ):
        assert forbidden not in update_grant
    assert "INSERT ON TABLE imt_sync_credentials" not in source
    assert "DELETE ON TABLE imt_sync_credentials" not in source


def test_sync_role_receives_only_the_sequences_used_by_its_write_paths() -> None:
    source = POSTGRES_ROLE.read_text()
    sequence_grant = source.split("GRANT USAGE, SELECT ON SEQUENCE", 1)[1].split(
        'TO "botnote-sync";',
        1,
    )[0]
    for required in (
        "auth_attempts_id_seq",
        "events_id_seq",
        "pass_denials_id_seq",
    ):
        assert required in sequence_grant
    for forbidden in (
        "admin_audit_logs_id_seq",
        "calendar_fetch_attempts_id_seq",
        "pass_system_state_id_seq",
    ):
        assert forbidden not in sequence_grant


def test_sync_environment_example_contains_no_hpke_or_unrelated_secrets() -> None:
    example = (ROOT / "deploy" / "botnote-sync.env.example").read_text()
    assert "BOTNOTE_AUTONOMOUS_SYNC_ENABLED=false" in example
    assert "BOTNOTE_AUTONOMOUS_SYNC_ENROLLMENT_ENABLED=false" in example
    assert "BOTNOTE_AUTONOMOUS_SYNC_ROLLOUT=off" in example
    assert "BOTNOTE_AUTONOMOUS_SYNC_CANARY_ACCOUNT_IDS" not in example
    assert "BOTNOTE_OWNER_IMT_USERNAME" not in example
    assert "BOTNOTE_CREDENTIAL_KEY" not in example
    assert "BOTNOTE_CREDENTIAL_PREVIOUS_KEYS" not in example
    assert "PRIVATE" not in example
    assert "TELEGRAM" not in example
    assert "ADMIN_" not in example
    assert "LEARNING_" not in example


def test_hpke_runtime_context_is_limited_to_the_explicit_sync_pipeline() -> None:
    application = ROOT / "backend" / "app"
    forbidden_import = "sync_worker_credentials"
    for path in (
        *sorted((application / "routers").glob("*.py")),
        application / "services" / "pass_sessions.py",
    ):
        assert forbidden_import not in path.read_text()
    for relative_path in (
        "services/pass_gateway.py",
        "services/sync.py",
        "services/jobs.py",
        "services/worker_runtime.py",
    ):
        source = (application / relative_path).read_text()
        assert "SyncRuntimeContext" in source
    assert "app.crypto" not in (application / "services" / "pass_sessions.py").read_text()
    pass_sessions = (application / "services" / "pass_sessions.py").read_text()
    runtime_context = (application / "services" / "sync_worker_credentials.py").read_text()
    pass_gateway = (application / "services" / "pass_gateway.py").read_text()
    assert "legacy_pass_session_migration" not in pass_sessions
    assert "LegacySessionCipher" not in pass_sessions
    assert "legacy_session_cipher" not in runtime_context
    assert "CredentialCipher" not in runtime_context
    assert "cipher_for" not in runtime_context
    assert "legacy_session_cipher" not in pass_gateway
    for relative_path in (
        "services/pass_gateway.py",
        "services/sync.py",
        "services/jobs.py",
        "services/worker_runtime.py",
        "services/sync_worker_credentials.py",
    ):
        source = (application / relative_path).read_text()
        assert "from app.models import ImtSyncCredential" not in source
        assert "imt_sync_credentials" not in source
    tracked = subprocess.check_output(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    assert not any(Path(path).suffix in {".raw", ".key", ".pem"} for path in tracked)


def test_provisioner_is_atomic_idempotent_and_verifiable(tmp_path: Path) -> None:
    namespace = runpy.run_path(str(PROVISIONER))
    provision = namespace["provision"]
    verify = namespace["verify"]
    provision_error = namespace["ProvisionError"]
    target = tmp_path / "sync-hpke"

    provision(target, require_root=False)
    verify(target, require_root=False)

    assert target.stat().st_mode & 0o777 == 0o700
    assert {path.name for path in target.iterdir()} == namespace["EXPECTED_FILES"]
    assert all(path.stat().st_mode & 0o777 == 0o400 for path in target.iterdir())
    with pytest.raises(provision_error, match="SYNC_HPKE_TARGET_EXISTS"):
        provision(target, require_root=False)


def test_provisioner_detects_tampering_without_printing_keys(
    tmp_path: Path,
    capsys,
) -> None:
    namespace = runpy.run_path(str(PROVISIONER))
    target = tmp_path / "sync-hpke"
    namespace["provision"](target, require_root=False)
    private_path = target / "imt-sync-credential-v1.private.raw"
    private_path.chmod(0o440)

    with pytest.raises(namespace["ProvisionError"], match="SYNC_HPKE_VERIFY_FAILED"):
        namespace["verify"](target, require_root=False)
    assert capsys.readouterr().out == ""


def test_provisioner_rejects_hardlinked_or_replaced_key_material(tmp_path: Path) -> None:
    namespace = runpy.run_path(str(PROVISIONER))

    hardlink_target = tmp_path / "hardlink-keyset"
    namespace["provision"](hardlink_target, require_root=False)
    source = hardlink_target / "pass-service-session-v1.public.raw"
    source.chmod(0o600)
    os.link(source, tmp_path / "public-copy")
    source.chmod(0o400)
    with pytest.raises(namespace["ProvisionError"], match="SYNC_HPKE_VERIFY_FAILED"):
        namespace["verify"](hardlink_target, require_root=False)

    symlink_target = tmp_path / "symlink-keyset"
    namespace["provision"](symlink_target, require_root=False)
    public_path = symlink_target / "imt-sync-credential-v1.public.raw"
    public_path.chmod(0o600)
    public_path.unlink()
    public_path.symlink_to(tmp_path / "public-copy")
    with pytest.raises(namespace["ProvisionError"], match="SYNC_HPKE_VERIFY_FAILED"):
        namespace["verify"](symlink_target, require_root=False)
