#!/usr/bin/env bash
set -euo pipefail
umask 077

BACKUP_FILE="${BOTNOTE_RESTORE_BACKUP_FILE:-/var/backups/botnote/latest.dump.age}"
IDENTITY_FILE="${BOTNOTE_RESTORE_AGE_IDENTITY_FILE:-/etc/botnote-restore/age-identity}"
TARGET_URL="${BOTNOTE_RESTORE_DATABASE_URL:?BOTNOTE_RESTORE_DATABASE_URL is required}"
EXPECTED_DATABASE="${BOTNOTE_RESTORE_DATABASE_NAME:-botnote_restore_test}"
STATE_DIR="${BOTNOTE_RESTORE_STATE_DIR:-/var/lib/botnote-restore}"

command -v age >/dev/null
command -v pg_restore >/dev/null
command -v psql >/dev/null
test -r "${IDENTITY_FILE}"
test -r "${BACKUP_FILE}"

ACTUAL_DATABASE="$(psql "${TARGET_URL}" -v ON_ERROR_STOP=1 -Atqc 'SELECT current_database()')"
if [ "${ACTUAL_DATABASE}" != "${EXPECTED_DATABASE}" ] || [ "${ACTUAL_DATABASE}" != "botnote_restore_test" ]; then
    echo '{"ok":false,"alert":"RESTORE_TARGET_NOT_ISOLATED"}' >&2
    exit 1
fi

age --decrypt --identity "${IDENTITY_FILE}" "${BACKUP_FILE}" \
    | pg_restore --clean --if-exists --exit-on-error --no-owner --no-privileges \
        --dbname "${TARGET_URL}"

REVISION="$(psql "${TARGET_URL}" -v ON_ERROR_STOP=1 -Atqc \
    'SELECT version_num FROM alembic_version LIMIT 1')"
test -n "${REVISION}"
BOTNOTE_DATABASE_URL="${TARGET_URL}" \
    /opt/botnote/runtime/bin/alembic -c /opt/botnote/current/alembic.ini current --check-heads \
    >/dev/null
BOTNOTE_ENVIRONMENT=test BOTNOTE_DATABASE_URL="${TARGET_URL}" \
    /opt/botnote/runtime/bin/botnote pass-sessions-revoke-all \
        --reason database_restored --dry-run >/dev/null
BOTNOTE_ENVIRONMENT=test BOTNOTE_DATABASE_URL="${TARGET_URL}" \
    /opt/botnote/runtime/bin/botnote pass-sessions-revoke-all \
        --reason database_restored \
        --confirm REVOKE-ALL-PASS-SESSIONS >/dev/null
BOTNOTE_ENVIRONMENT=test BOTNOTE_DATABASE_URL="${TARGET_URL}" \
    /opt/botnote/runtime/bin/botnote sync-credentials-revoke-all \
        --reason database_restored --dry-run >/dev/null
BOTNOTE_ENVIRONMENT=test BOTNOTE_DATABASE_URL="${TARGET_URL}" \
    /opt/botnote/runtime/bin/botnote sync-credentials-revoke-all \
        --reason database_restored \
        --confirm REVOKE-ALL-SYNC-CREDENTIALS >/dev/null
REMAINING_SERVICE_SESSIONS="$(psql "${TARGET_URL}" -v ON_ERROR_STOP=1 -Atqc \
    'SELECT count(*) FROM pass_service_sessions
     WHERE encrypted_cookie_jar IS NOT NULL OR hpke_envelope IS NOT NULL')"
test "${REMAINING_SERVICE_SESSIONS}" = "0"
REMAINING_SYNC_CREDENTIALS="$(psql "${TARGET_URL}" -v ON_ERROR_STOP=1 -Atqc \
    'SELECT count(*) FROM imt_sync_credentials
     WHERE encrypted_envelope IS NOT NULL')"
test "${REMAINING_SYNC_CREDENTIALS}" = "0"

install -d -m 0700 "${STATE_DIR}"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "${STATE_DIR}/last-success.tmp"
mv "${STATE_DIR}/last-success.tmp" "${STATE_DIR}/last-success"
printf '{"ok":true,"revision":"%s"}\n' "${REVISION}"
