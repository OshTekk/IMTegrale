export type Role = "owner" | "editor" | "viewer";
export type AcademicSemester = "S5" | "S6" | "S7" | "S8" | "S9" | "S10";
export type SimulationSemester = AcademicSemester;

export interface Session {
  authenticated: boolean;
  role?: Role;
  auth_method?: "imt" | "token" | "passkey";
  needs_security_setup?: boolean;
  needs_sync_setup?: boolean;
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
  semester: AcademicSemester | null;
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
  state: "available" | "cooldown" | "in_progress" | "pass_unavailable" | "reauth_required";
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
  service_session: ServiceSessionStatus;
}

export interface ServiceSessionStatus {
  state: "active" | "reauth_required" | "owner_managed";
  reauth_required: boolean;
  beta: true;
  retention_days: number;
  established_at: string | null;
  expires_at: string | null;
  last_used_at: string | null;
  pass_last_success_at: string | null;
  hub_state: "unknown" | "ready" | "degraded";
  hub_last_attempt_at: string | null;
  hub_last_success_at: string | null;
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
    semester: AcademicSemester;
    label: AcademicSemester;
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

export interface CalendarStatus {
  configured: boolean;
  refresh_interval_minutes: 60;
  account_hint: string | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  next_refresh_at: string | null;
  last_status: "success" | "error" | "pending" | null;
  last_error_code: string | null;
  event_count: number;
  fip_training_available: boolean;
  promotion_year: number | null;
}

export interface CalendarEventItem {
  id: string;
  title: string;
  location: string | null;
  start: string;
  end: string;
  all_day: boolean;
}

export interface FipTrainingPeriod {
  kind: "school" | "company";
  start: string;
  end: string;
  weeks: number;
  campus: "Rennes" | "Brest" | null;
}

export interface FipTrainingMilestone {
  kind: "international_project" | "academic_mobility";
  title: string;
  start: string;
  end: string;
  detail: string;
}

export interface FipTrainingPromotion {
  promotion_year: number;
  level: "A1" | "A2" | "A3";
  semesters: Array<{
    semester: AcademicSemester;
    start: string;
    end: string;
  }>;
  totals: {
    school_weeks: number;
    company_weeks: number;
  };
  periods: FipTrainingPeriod[];
  milestones: FipTrainingMilestone[];
}

export interface FipTrainingCalendar {
  academic_year: string;
  title: string;
  speciality: string;
  source: {
    label: string;
    version_date: string;
  };
  promotions: FipTrainingPromotion[];
  default_promotion_year: number | null;
  campus_note: string;
}

export type SimulationGrade = "A" | "B" | "C" | "D" | "E" | "FX" | "F";

export interface SimulationFormula {
  version: string;
  label: string;
  scale: string;
  rounding: string;
  scope: string;
  expression: string;
  official: false;
}

export interface SimulationResult {
  status: "empty" | "partial" | "ready";
  gpa: number | null;
  credits_entered: number;
  credits_included: number;
  ue_count: number;
  graded_count: number;
  pending_count: number;
  missing_ects_count: number;
  completion_rate: number;
  semesters: Array<{
    semester: SimulationSemester;
    gpa: number | null;
    credits_included: number;
    ue_count: number;
  }>;
  warnings: Array<{ code: string; count: number; message: string }>;
  formula: SimulationFormula;
}

export interface SimulationSourceSummary {
  revision: string;
  captured_at: string;
  ue_count: number;
  graded_count: number;
}

export interface SimulationEntry {
  id: string;
  lineage_key: string;
  semester: SimulationSemester | null;
  ue_code: string | null;
  title: string;
  credits_ects: number | null;
  grade: SimulationGrade | null;
  gpa_points: number | null;
  status: "pending" | "validated" | "not_validated";
  nature: "imported" | "modified" | "simulated";
  source: {
    ue_code: string | null;
    status: "current" | "conflict" | "unavailable";
    grade_source: "competences" | "pass_calculated" | null;
    observed_at: string | null;
  } | null;
  baseline: {
    semester: SimulationSemester | null;
    ue_code: string | null;
    title: string | null;
    credits_ects: number | null;
    grade: SimulationGrade | null;
  } | null;
  created_at: string;
  updated_at: string;
}

export interface SimulationScenarioSummary {
  id: string;
  name: string;
  created_from: "blank" | "academic";
  formula_version: string;
  version: number;
  source_revision: string | null;
  source_captured_at: string | null;
  rebase_available: boolean;
  created_at: string;
  updated_at: string;
  result: SimulationResult;
}

export interface SimulationScenario extends SimulationScenarioSummary {
  entries: SimulationEntry[];
}

export interface SimulationList {
  limit: number;
  source: SimulationSourceSummary;
  scenarios: SimulationScenarioSummary[];
}

export interface SimulationComparisonDifference {
  lineage_key: string;
  kind: "changed" | "left_only" | "right_only";
  left: SimulationEntry | null;
  right: SimulationEntry | null;
  fields: Array<"presence" | "semester" | "ue" | "credits_ects" | "grade">;
}

export interface SimulationComparison {
  left: SimulationScenarioSummary;
  right: SimulationScenarioSummary;
  gpa_delta: number | null;
  differences: SimulationComparisonDifference[];
  formula: SimulationFormula;
}

export interface NoteSimulationFormula {
  version: string;
  label: string;
  scale: string;
  rounding: string;
  scope: string;
  ue_expression: string;
  average_expression: string;
  gpa_expression: string;
  official: false;
}

export interface NoteSimulationAssessment {
  id: string;
  lineage_key: string;
  label: string;
  score: number | null;
  coefficient: number;
  is_resit: boolean;
  nature: "imported" | "modified" | "simulated";
  source: {
    note_key: string | null;
    status: "current" | "conflict" | "unavailable";
    observed_at: string | null;
  } | null;
  baseline: {
    label: string | null;
    score: number | null;
    coefficient: number | null;
    is_resit: boolean | null;
  } | null;
  created_at: string;
  updated_at: string;
}

export interface NoteSimulationUeProjection {
  average: number | null;
  grade: SimulationGrade | null;
  gpa_points: number | null;
  used_resit: boolean;
  coefficient_total: number;
  assessment_count: number;
  scored_count: number;
  pending_count: number;
}

export interface NoteSimulationUe {
  id: string;
  lineage_key: string;
  semester: SimulationSemester | null;
  ue_code: string | null;
  title: string;
  credits_ects: number | null;
  nature: "imported" | "modified" | "simulated";
  projection: NoteSimulationUeProjection;
  source: {
    ue_code: string | null;
    status: "current" | "conflict" | "unavailable";
    observed_at: string | null;
  } | null;
  baseline: {
    semester: SimulationSemester | null;
    ue_code: string | null;
    title: string | null;
    credits_ects: number | null;
  } | null;
  assessments: NoteSimulationAssessment[];
  created_at: string;
  updated_at: string;
}

export interface NoteSimulationAggregate {
  status: "empty" | "partial" | "ready";
  average: number | null;
  gpa: number | null;
  credits_entered: number;
  credits_included: number;
  ue_count: number;
  calculated_ue_count: number;
  assessment_count: number;
  scored_count: number;
  pending_count: number;
  missing_ects_count: number;
  completion_rate: number;
  semesters: Array<{
    semester: SimulationSemester;
    average: number | null;
    gpa: number | null;
    credits_included: number;
    ue_count: number;
    calculated_ue_count: number;
    assessment_count: number;
    scored_count: number;
    pending_count: number;
  }>;
  warnings: Array<{ code: string; count: number; message: string }>;
  formula: NoteSimulationFormula;
}

export interface NoteSimulationSourceSummary {
  revision: string;
  captured_at: string;
  ue_count: number;
  assessment_count: number;
  scored_count: number;
}

export interface NoteSimulationScenarioSummary {
  id: string;
  name: string;
  created_from: "blank" | "academic";
  formula_version: string;
  version: number;
  source_revision: string | null;
  source_captured_at: string | null;
  rebase_available: boolean;
  created_at: string;
  updated_at: string;
  result: NoteSimulationAggregate;
}

export interface NoteSimulationScenario extends NoteSimulationScenarioSummary {
  ues: NoteSimulationUe[];
}

export interface NoteSimulationList {
  limit: number;
  source: NoteSimulationSourceSummary;
  scenarios: NoteSimulationScenarioSummary[];
}

export interface NoteSimulationComparisonDifference {
  lineage_key: string;
  kind: "changed" | "left_only" | "right_only";
  left: NoteSimulationUe | null;
  right: NoteSimulationUe | null;
  fields: Array<"presence" | "semester" | "ue" | "credits_ects" | "assessments">;
}

export interface NoteSimulationComparison {
  left: NoteSimulationScenarioSummary;
  right: NoteSimulationScenarioSummary;
  average_delta: number | null;
  gpa_delta: number | null;
  differences: NoteSimulationComparisonDifference[];
  formula: NoteSimulationFormula;
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
    paused_reason: "reauth_required" | null;
    paused_at: string | null;
    next_eligible_at: string | null;
    allowed_intervals: Array<2 | 4 | 6 | 8 | 12 | 24>;
    business_hours: {
      weekdays: "monday-friday";
      start: string;
      end: string;
      timezone: string;
    };
    pass_access: PassAccessStatus;
    service_session: ServiceSessionStatus;
  };
  access: {
    role: Role;
    auth_method: "imt" | "token" | "passkey";
    security_setup_completed: boolean;
    sync_setup_completed: boolean;
    passkey_count: number;
  };
}

export type Campus = "rennes" | "brest" | "nantes" | "other" | "unknown";
export type Cohort = "1a" | "2a" | "3a" | "higher" | "atypical" | "unknown";
export type LeaderboardMetric = "gpa" | "average";

export interface LeaderboardEntry {
  rank: number | null;
  official_name: string;
  score: number;
  verified_at: string | null;
  freshness: "current" | "recommended" | "stale";
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
    freshness: "current" | "recommended" | "stale";
    verified_at: string | null;
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
  auto_sync_paused_reason: "reauth_required" | null;
  auto_sync_paused_at: string | null;
  created_at: string;
  session_count: number;
  active_token_count: number;
  passkey_count: number;
  pass_session: ServiceSessionStatus;
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
  service_sessions: {
    window_hours: number;
    active: number;
    reauth_required: number;
    hub_ready: number;
    established: number;
    completed: number;
    completed_duration_hours: { median: number | null; longest: number | null };
    survival: Record<"24h" | "3d" | "7d" | "30d", {
      eligible: number;
      survived: number;
      rate: number | null;
    }>;
    end_reasons: Record<string, number>;
  };
}

export interface AdminPassSession extends ServiceSessionStatus {
  account_id: string;
  display_name: string;
  imt_username: string;
  auto_sync_enabled: boolean;
  auto_sync_paused_reason: "reauth_required" | null;
}

export interface AdminAccountsView {
  stats: { accounts: number; disabled: number; participants: number; reviews: number };
  accounts: AdminAccount[];
}
