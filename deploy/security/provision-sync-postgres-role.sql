\set ON_ERROR_STOP on

DO $provision$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'botnote-sync') THEN
        CREATE ROLE "botnote-sync"
            LOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOREPLICATION
            NOBYPASSRLS;
    ELSE
        ALTER ROLE "botnote-sync"
            LOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOREPLICATION
            NOBYPASSRLS;
    END IF;
END
$provision$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM "botnote-sync";
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM "botnote-sync";
DO $grant_connect$
BEGIN
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO "botnote-sync"',
        current_database()
    );
END
$grant_connect$;
GRANT USAGE ON SCHEMA public TO "botnote-sync";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    accounts,
    notes,
    ue_settings,
    events,
    sync_requests,
    pass_system_state,
    pass_operations,
    pass_service_sessions,
    pass_denials,
    auth_attempts,
    auth_throttle_states,
    cohort_pulses,
    leaderboard_profiles,
    durable_jobs,
    notification_outbox,
    runtime_heartbeats
TO "botnote-sync";
GRANT SELECT ON TABLE imt_sync_credentials TO "botnote-sync";
GRANT UPDATE (
    encrypted_envelope,
    envelope_version,
    key_id,
    credential_generation,
    state,
    last_used_at,
    last_success_at,
    last_failure_at,
    failure_count,
    revoked_at,
    revoked_reason,
    updated_at
) ON TABLE imt_sync_credentials TO "botnote-sync";
GRANT USAGE, SELECT ON SEQUENCE
    auth_attempts_id_seq,
    events_id_seq,
    pass_denials_id_seq
TO "botnote-sync";
