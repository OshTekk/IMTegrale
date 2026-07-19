#!/usr/bin/env bash
set -euo pipefail
umask 077

BACKUP_DIR="${BOTNOTE_BACKUP_DIR:-/var/backups/botnote}"
RETENTION_DAYS="${BOTNOTE_BACKUP_RETENTION_DAYS:-30}"
RECIPIENT_FILE="${BOTNOTE_BACKUP_AGE_RECIPIENT_FILE:-/etc/botnote/backup-age-recipient}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="${BACKUP_DIR}/botnote-${STAMP}.dump.age"
TEMP="${TARGET}.tmp"
LATEST="${BACKUP_DIR}/latest.dump.age"
LATEST_TEMP="${BACKUP_DIR}/.latest.dump.age.${STAMP}.tmp"

cleanup() {
    rm -f "${TEMP}" "${LATEST_TEMP}"
}
trap cleanup EXIT

command -v age >/dev/null
test -r "${RECIPIENT_FILE}"
IFS= read -r RECIPIENT < "${RECIPIENT_FILE}"
case "${RECIPIENT}" in
    age1*) ;;
    *) echo "Invalid age recipient" >&2; exit 1 ;;
esac
mkdir -p "${BACKUP_DIR}"
pg_dump --format=custom --compress=9 --no-owner botnote \
    | age --recipient "${RECIPIENT}" --output "${TEMP}"
test -s "${TEMP}"
test "$(head -n 1 "${TEMP}")" = "age-encryption.org/v1"
mv "${TEMP}" "${TARGET}"
ln -s "$(basename "${TARGET}")" "${LATEST_TEMP}"
mv -Tf "${LATEST_TEMP}" "${LATEST}"
find "${BACKUP_DIR}" -type f -name 'botnote-*.dump.age' -mtime "+${RETENTION_DAYS}" -delete
trap - EXIT
