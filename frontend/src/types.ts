export type Role = "owner" | "editor" | "viewer";

export interface Session {
  authenticated: boolean;
  role?: Role;
  auth_method?: "imt" | "token" | "passkey";
  needs_security_setup?: boolean;
  account?: {
    id: string;
    display_name: string;
    imt_username: string | null;
  };
}

export interface NoteItem {
  id: string;
  source: "pass" | "manual";
  ue_code: string;
  label: string;
  score: number;
  coefficient: number;
  is_resit: boolean;
  has_override: boolean;
  editable: boolean;
  detected_at: string;
  updated_at: string;
}

export interface UeItem {
  code: string;
  title: string;
  year: string;
  semester: string | null;
  official_code: string | null;
  credits_ects: number | null;
  earned_credits_ects: number | null;
  metadata_source: "manual" | "competences";
  metadata_refreshed_at: string | null;
  average: number | null;
  grade: string | null;
  grade_description: string | null;
  grade_source: "competences" | "pass_calculated" | "manual_calculated";
  gpa: number | null;
  validated: boolean;
  used_resit: boolean;
  note_count: number;
}

export interface EventItem {
  id: number;
  kind: string;
  payload: Record<string, unknown>;
  actor: string;
  created_at: string;
}

export interface ManualSyncStatus {
  state: "available" | "cooldown" | "in_progress" | "pass_unavailable";
  can_start: boolean;
  cooldown_seconds: number;
  retry_after_seconds: number;
  cooldown_until: string | null;
  active_until: string | null;
  server_time: string;
  last_request: {
    request_id: string;
    status: "queued" | "running" | "succeeded" | "failed" | "skipped";
    actor: string;
    accepted_at: string;
    completed_at: string | null;
    error_code: string | null;
  } | null;
  pass_access: PassAccessStatus;
}

export interface PassAccessStatus {
  state: "available" | "busy" | "resting" | "circuit_open";
  available: boolean;
  available_at: string;
  retry_after_seconds: number;
  circuit: {
    state: "closed" | "open" | "half_open";
    reason: string | null;
    next_probe_at: string | null;
  };
  quota: {
    hour: { used: number; limit: number; remaining: number };
    day: { used: number; limit: number; remaining: number };
    available_at: string;
    retry_after_seconds: number;
  };
  profile?: { refreshed_at: string | null; refresh_due: boolean };
}

export interface SyncStartResponse {
  ok: boolean;
  request_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "skipped";
  idempotent_replay: boolean;
  accepted_at: string;
  cooldown_until: string;
  retry_after_seconds: number;
  server_time: string;
  error_code: string | null;
}

export interface Dashboard {
  generated_at: string;
  latest_event_id: number;
  account: {
    id: string;
    display_name: string;
    imt_username: string | null;
    last_sync_at: string | null;
    last_sync_status: "never" | "running" | "success" | "error";
    last_sync_error: string | null;
    manual_sync: ManualSyncStatus | null;
    telegram_enabled: boolean;
  };
  summary: {
    average: number | null;
    average_credits: number;
    gpa: number | null;
    gpa_credits: number;
    validated_credits: number;
    note_count: number;
    ue_count: number;
    missing_ects_count: number;
  };
  years: Array<{
    year: string;
    label: string;
    average: number | null;
    average_credits: number;
    gpa: number | null;
    gpa_credits: number;
    validated_credits: number;
    ue_count: number;
  }>;
  semesters: Array<{
    semester: string;
    label: string;
    average: number | null;
    average_credits: number;
    gpa: number | null;
    gpa_credits: number;
    validated_credits: number;
    ue_count: number;
  }>;
  ues: UeItem[];
  notes: NoteItem[];
  grade_distribution: Array<{ grade: string; count: number }>;
  grade_scale: Array<{ grade: string; description: string; gpa: number }>;
  events: EventItem[];
}

export interface ShareToken {
  id: string;
  name: string;
  prefix: string;
  role: "owner" | "viewer" | "editor";
  expires_at: string | null;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  token?: string;
}

export interface SettingsView {
  account: {
    display_name: string;
    imt_username: string | null;
    timezone: string;
    credentials_updated_at: string | null;
    campus: Campus;
    campus_source: string;
    profile_refreshed_at: string | null;
    program: string;
    promotion_year: number | null;
    academic_source: string;
    academic_verified_at: string | null;
    official_first_name: string | null;
    official_last_name: string | null;
    official_name: string | null;
    official_identity_at: string | null;
  };
  telegram: {
    configured: boolean;
    enabled: boolean;
    last_test_at: string | null;
    last_test_status: "pending" | "success" | "failed" | null;
  };
  sync: {
    enabled: boolean;
    interval_hours: 2 | 4 | 6 | 8 | 12 | 24;
    adaptive: boolean;
    current_interval_hours: 2 | 4 | 6 | 8 | 12 | 24;
    no_change_streak: number;
    consented_at: string | null;
    next_eligible_at: string | null;
    allowed_intervals: Array<2 | 4 | 6 | 8 | 12 | 24>;
    business_hours: {
      weekdays: "monday-friday";
      start: string;
      end: string;
      timezone: string;
    };
    pass_access: PassAccessStatus;
  };
  access: {
    role: Role;
    auth_method: "imt" | "token" | "passkey";
    security_setup_completed: boolean;
    passkey_count: number;
  };
}

export type Campus = "rennes" | "brest" | "nantes" | "other" | "unknown";
export type Cohort = "1a" | "2a" | "3a" | "higher" | "atypical" | "unknown";
export type LeaderboardMetric = "gpa" | "average";

export interface LeaderboardEntry {
  rank: number;
  official_name: string;
  score: number;
  is_self: boolean;
}

export interface LeaderboardView {
  state: "not_joined" | "pending" | "active" | "suspended";
  profile: {
    official_first_name: string | null;
    official_last_name: string | null;
    official_name: string | null;
    official_identity_at: string | null;
    campus: Campus;
    campus_source: string;
    campus_confirmed_at: string | null;
    detected_campus: Campus;
    detected_campus_at: string | null;
    cohort: Cohort;
    cohort_source: string;
    cohort_confirmed_at: string | null;
    program: string;
    promotion_year: number | null;
    academic_source: string;
    academic_verified_at: string | null;
    segment: string | null;
    classification_review_required: boolean;
    joined_at: string | null;
    ranking_visible_at: string | null;
    withdraw_available_at: string | null;
    left_at: string | null;
    rejoin_after: string | null;
    verification_status: "standard" | "review" | "suspended";
  };
  eligibility: {
    eligible: boolean;
    missing: Array<"identity" | "campus" | "promotion" | "pass_notes" | "ects">;
    score: {
      average: number | null;
      gpa: number | null;
      credits: number;
      ue_count: number;
      note_count: number;
      missing_ects_count: number;
    };
  };
  can_withdraw: boolean;
  can_delete_data: boolean;
  consent_version: string;
  publication: {
    wait_complete: boolean;
    score_ready: boolean;
  };
  rules: {
    version: string;
    updated_at: string;
    wait_hours: number;
    withdrawal_lock_hours: number;
    rejoin_cooldown_hours: number;
    source: string;
    weighting: string;
    segment: string;
    excluded: string[];
    ties: string;
    freshness: string;
    public_fields: string[];
  };
  board: {
    metric: LeaderboardMetric;
    campus_filter: "all" | Exclude<Campus, "unknown">;
    cohort_filter: "official";
    segment: string | null;
    calculated_at: string;
    participant_count: number;
    entries: LeaderboardEntry[];
  } | null;
}

export interface AdminSession {
  authenticated: boolean;
  username?: string;
  must_change_password?: boolean;
  expires_at?: string;
}

export interface AdminAccount {
  id: string;
  display_name: string;
  imt_username: string;
  is_disabled: boolean;
  disabled_at: string | null;
  disabled_reason: string | null;
  last_login_at: string | null;
  last_sync_at: string | null;
  last_sync_status: "never" | "running" | "success" | "error";
  auto_sync_enabled: boolean;
  auto_sync_interval_hours: 2 | 4 | 6 | 8 | 12 | 24;
  auto_sync_adaptive: boolean;
  auto_sync_current_interval_hours: 2 | 4 | 6 | 8 | 12 | 24;
  created_at: string;
  session_count: number;
  active_token_count: number;
  passkey_count: number;
  tokens: AdminToken[];
  leaderboard: {
    state: LeaderboardView["state"];
    official_first_name: string | null;
    official_last_name: string | null;
    official_identity_at: string | null;
    has_leaderboard_data: boolean;
    campus: Campus;
    detected_campus: Campus;
    cohort: Cohort;
    program: string;
    promotion_year: number | null;
    academic_source: string;
    academic_verified_at: string | null;
    profile_refreshed_at: string | null;
    classification_review_required: boolean;
    verification_status: "standard" | "review" | "suspended";
    score_ects_basis: Record<string, number> | null;
    score_basis_updated_at: string | null;
    ranking_visible_at: string | null;
    rejoin_after: string | null;
    suspended_at: string | null;
    suspended_reason: string | null;
  };
}

export interface AdminToken {
  id: string;
  name: string;
  prefix: string;
  role: "owner" | "viewer" | "editor";
  expires_at: string | null;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface PasskeyItem {
  id: string;
  name: string;
  device_type: "single_device" | "multi_device" | null;
  backed_up: boolean;
  transports: string[];
  created_at: string;
  last_used_at: string | null;
}

export interface AdminPassMetrics {
  window_hours: number;
  from: string;
  to: string;
  operations: number;
  real_requests: number;
  duration_ms: { mean: number | null; p95: number | null; worst: number | null };
  session_reuse: {
    hits: number;
    successful_operations: number;
    hit_rate: number;
    full_sso_performed: number;
    full_sso_avoided: number;
  };
  profiles: { fetched: number; skipped: number };
  by_kind: Record<string, number>;
  errors: Record<string, number>;
  denials: Record<string, number>;
  circuit: PassAccessStatus["circuit"];
}

export interface AdminAccountsView {
  stats: { accounts: number; disabled: number; participants: number; reviews: number };
  accounts: AdminAccount[];
}
