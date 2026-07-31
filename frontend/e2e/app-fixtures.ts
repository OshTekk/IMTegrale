import type { Page, Route } from "@playwright/test";

export const SYNTHETIC_APP_FIXTURE_ONLY = true as const;

export type AppSessionMode = "anonymous" | "imt" | "passkey" | "token" | "viewer";

export interface FakeAppState {
  autonomousAvailable: boolean;
  autonomousConfigured: boolean;
  autonomousNeedsReenrollment: boolean;
  autonomousState: "active" | "invalid" | "revoked" | null;
  calendarConnectRequests: string[];
  calendarDisconnects: number;
  calendarEvents: Array<Record<string, unknown>>;
  calendarEventsError: boolean;
  calendarStatus: Record<string, unknown>;
  calendarStatusError: boolean;
  csrfHeaders: Array<string | undefined>;
  dashboard: Record<string, unknown>;
  dashboardError: boolean;
  dashboardRequests: number;
  credentialDeletes: number;
  credentialEnrollmentFields: string[][];
  credentialEnrollments: number;
  externalRequests: string[];
  loginError: boolean;
  loginRequests: Array<Record<string, unknown>>;
  passkeyCreates: number;
  passkeyDeletes: string[];
  session: Record<string, unknown>;
  syncMode: "manual" | "session_only" | "autonomous";
  syncModeUpdates: Array<Record<string, unknown>>;
  syncRequests: number;
  passAccessPurges: number;
  tokenCreates: Array<Record<string, unknown>>;
  trainingCalendar: Record<string, unknown>;
  trainingCalendarError: boolean;
  synthetic: true;
}

const baseUrl = "http://127.0.0.1:4173";
const csrfToken = "csrf-app-e2e-fictif";
const account = {
  id: "account-app-e2e-fictif",
  display_name: "Étudiante fictive",
  imt_username: "demo.fictif",
};

function session(mode: AppSessionMode): Record<string, unknown> {
  if (mode === "anonymous") return { authenticated: false };
  const authMethod = mode === "imt" ? "imt" : mode === "passkey" ? "passkey" : "token";
  const scopeMarker = mode === "imt" ? "a" : mode === "passkey" ? "b" : mode === "token" ? "c" : "d";
  return {
    authenticated: true,
    session_scope: `bss1_${scopeMarker.repeat(64)}`,
    session_expires_at: "2099-01-01T01:00:00.000Z",
    server_time: "2099-01-01T00:00:00.000Z",
    role: mode === "viewer" ? "viewer" : "owner",
    auth_method: authMethod,
    needs_security_setup: false,
    needs_sync_setup: false,
    account,
    private_comparisons: { available: false },
    learning: {
      available: false,
      audience_label: null,
      level_label: null,
      reverify_required: false,
      catalog_version: null,
    },
  };
}

const serviceSession = {
  state: "active",
  reauth_required: false,
  beta: true,
  retention_days: 30,
  established_at: "2026-01-01T08:00:00Z",
  expires_at: "2026-01-31T08:00:00Z",
  last_used_at: "2026-01-01T08:30:00Z",
  pass_last_success_at: "2026-01-01T08:30:00Z",
  hub_state: "ready",
  hub_last_attempt_at: "2026-01-01T08:30:00Z",
  hub_last_success_at: "2026-01-01T08:30:00Z",
};

const passAccess = {
  state: "available",
  available: true,
  available_at: "2026-01-01T08:00:00Z",
  retry_after_seconds: 0,
  circuit: { state: "closed", reason: null, next_probe_at: null },
  quota: {
    hour: { used: 0, limit: 3, remaining: 3 },
    day: { used: 0, limit: 8, remaining: 8 },
    available_at: "2026-01-01T08:00:00Z",
    retry_after_seconds: 0,
  },
  profile: { refreshed_at: "2026-01-01T08:30:00Z", refresh_due: false },
  service_session: serviceSession,
};

export const syntheticCalendarStatus = {
  configured: true,
  refresh_interval_minutes: 60,
  account_hint: "Compte agenda fictif",
  last_attempt_at: "2026-07-25T07:00:00Z",
  last_success_at: "2026-07-25T07:00:00Z",
  next_refresh_at: "2026-07-25T08:00:00Z",
  last_status: "success",
  last_error_code: null,
  event_count: 12,
  fip_training_available: true,
  promotion_year: 2028,
};

export const syntheticCalendarEvents = [
  {
    id: "event-calendar-fictif-1",
    title: "Atelier de conception entièrement fictif avec un intitulé volontairement long",
    location: "Salle fictive A-101, bâtiment de démonstration",
    start: "2026-07-27T08:00:00+02:00",
    end: "2026-07-27T10:00:00+02:00",
    all_day: false,
  },
  {
    id: "event-calendar-fictif-2",
    title: "Travaux dirigés synthétiques",
    location: "Amphithéâtre fictif",
    start: "2026-07-27T13:30:00+02:00",
    end: "2026-07-27T15:00:00+02:00",
    all_day: false,
  },
  {
    id: "event-calendar-fictif-3",
    title: "Projet collectif de démonstration",
    location: null,
    start: "2026-07-28T09:15:00+02:00",
    end: "2026-07-28T12:15:00+02:00",
    all_day: false,
  },
  {
    id: "event-calendar-fictif-4",
    title: "Cours fictif de systèmes",
    location: "Salle fictive B-204",
    start: "2026-07-29T10:00:00+02:00",
    end: "2026-07-29T12:00:00+02:00",
    all_day: false,
  },
  {
    id: "event-calendar-fictif-5",
    title: "Séminaire synthétique",
    location: "Espace fictif de travail",
    start: "2026-07-30T14:00:00+02:00",
    end: "2026-07-30T17:00:00+02:00",
    all_day: false,
  },
  {
    id: "event-calendar-fictif-6",
    title: "Journée de démonstration",
    location: "Campus fictif",
    start: "2026-07-31",
    end: "2026-08-01",
    all_day: true,
  },
];

function syntheticTrainingPromotion(promotionYear: number, level: "A1" | "A2" | "A3", firstSemester: string) {
  const secondSemester = `S${Number(firstSemester.slice(1)) + 1}`;
  return {
    promotion_year: promotionYear,
    level,
    semesters: [
      { semester: firstSemester, start: "2026-09-01", end: "2027-01-31" },
      { semester: secondSemester, start: "2027-02-01", end: "2027-07-15" },
    ],
    totals: { school_weeks: 18, company_weeks: 27 },
    periods: [
      { kind: "school", start: "2026-09-01", end: "2026-10-09", weeks: 6, campus: "Site fictif Alpha" },
      { kind: "company", start: "2026-10-10", end: "2027-01-03", weeks: 12, campus: null },
      { kind: "school", start: "2027-01-04", end: "2027-02-12", weeks: 6, campus: "Site fictif Bêta" },
      { kind: "company", start: "2027-02-13", end: "2027-05-28", weeks: 15, campus: null },
      { kind: "school", start: "2027-05-29", end: "2027-07-15", weeks: 6, campus: "Site fictif Alpha" },
    ],
    milestones: [
      {
        kind: "international_project",
        title: "Projet international fictif",
        start: "2027-03-01",
        end: "2027-04-30",
        detail: "Période synthétique utilisée uniquement par les tests.",
      },
    ],
  };
}

export const syntheticTrainingCalendar = {
  academic_year: "2026-2027",
  title: "Calendrier de formation fictif",
  speciality: "Formation ingénieur partenaire fictive",
  source: { label: "Calendrier synthétique de test", version_date: "2026-07-01" },
  promotions: [
    syntheticTrainingPromotion(2027, "A3", "S9"),
    syntheticTrainingPromotion(2028, "A2", "S7"),
    syntheticTrainingPromotion(2029, "A1", "S5"),
  ],
  default_promotion_year: 2028,
  campus_note: "Les campus affichés sont entièrement fictifs.",
};

export const syntheticDashboard = {
  generated_at: "2026-01-01T08:30:00Z",
  latest_event_cursor: `evc1_${"a".repeat(32)}`,
  account: {
    ...account,
    last_sync_at: "2026-01-01T08:30:00Z",
    last_sync_status: "success",
    last_sync_error: null,
    manual_sync: {
      state: "available",
      can_start: true,
      cooldown_seconds: 0,
      retry_after_seconds: 0,
      cooldown_until: null,
      active_until: null,
      server_time: "2026-01-01T08:30:00Z",
      last_request: null,
      pass_access: passAccess,
    },
    telegram_enabled: false,
  },
  summary: {
    average: 12.74,
    average_credits: 11,
    gpa: 3.21,
    gpa_credits: 11,
    validated_credits: 11,
    note_count: 5,
    ue_count: 4,
    missing_ects_count: 1,
  },
  years: [
    {
      year: "1",
      label: "1re année",
      average: 12.74,
      average_credits: 11,
      gpa: 3.21,
      gpa_credits: 11,
      validated_credits: 11,
      ue_count: 2,
    },
    {
      year: "2",
      label: "2e année",
      average: null,
      average_credits: 0,
      gpa: null,
      gpa_credits: 0,
      validated_credits: 0,
      ue_count: 2,
    },
  ],
  semesters: [
    {
      semester: "S5",
      label: "S5",
      average: 14.2,
      average_credits: 6,
      gpa: 3.5,
      gpa_credits: 6,
      validated_credits: 6,
      ue_count: 1,
    },
    {
      semester: "S6",
      label: "S6",
      average: 10.5,
      average_credits: 5,
      gpa: 2.5,
      gpa_credits: 5,
      validated_credits: 5,
      ue_count: 1,
    },
    {
      semester: "S7",
      label: "S7",
      average: null,
      average_credits: 0,
      gpa: null,
      gpa_credits: 0,
      validated_credits: 0,
      ue_count: 1,
    },
  ],
  ues: [
    {
      code: "UE-DEMO",
      title: "Analyse numérique entièrement fictive",
      year: "1",
      semester: "S5",
      official_code: "DEMO-S5",
      credits_ects: 6,
      earned_credits_ects: 6,
      metadata_source: "competences",
      metadata_refreshed_at: "2026-01-01T08:30:00Z",
      average: 14.2,
      grade: "B",
      grade_description: "[14-17[",
      grade_source: "competences",
      gpa: 3.8,
      validated: true,
      used_resit: false,
      note_count: 2,
    },
    {
      code: "RES-FICTIF",
      title: "Réseaux entièrement imaginaires",
      year: "1",
      semester: "S6",
      official_code: "FICTIF-S6-RES",
      credits_ects: 5,
      earned_credits_ects: 5,
      metadata_source: "competences",
      metadata_refreshed_at: "2026-01-05T08:30:00Z",
      average: 10.5,
      grade: "E",
      grade_description: "Rattrapage",
      grade_source: "pass_calculated",
      gpa: 2.5,
      validated: true,
      used_resit: true,
      note_count: 2,
    },
    {
      code: "ART-FICTIF",
      title: "Création synthétique",
      year: "2",
      semester: "S7",
      official_code: null,
      credits_ects: null,
      earned_credits_ects: null,
      metadata_source: "manual",
      metadata_refreshed_at: null,
      average: null,
      grade: null,
      grade_description: null,
      grade_source: "manual_calculated",
      gpa: null,
      validated: false,
      used_resit: false,
      note_count: 0,
    },
    {
      code: "LIBRE-FICTIF",
      title: "Projet fictif sans semestre",
      year: "2",
      semester: null,
      official_code: null,
      credits_ects: 2,
      earned_credits_ects: 0,
      metadata_source: "manual",
      metadata_refreshed_at: null,
      average: 8,
      grade: "FX",
      grade_description: "[5-10[",
      grade_source: "pass_calculated",
      gpa: 0,
      validated: false,
      used_resit: false,
      note_count: 1,
    },
  ],
  grade_distribution: [
    { grade: "B", count: 1 },
    { grade: "E", count: 1 },
    { grade: "FX", count: 1 },
  ],
  grade_scale: [
    { grade: "A", description: "[17-20]", gpa: 4 },
    { grade: "B", description: "[14-17[", gpa: 3.8 },
    { grade: "C", description: "[12-14[", gpa: 3.5 },
    { grade: "D", description: "[10-12[", gpa: 3 },
    { grade: "E", description: "Rattrapage", gpa: 2.5 },
    { grade: "FX", description: "[5-10[", gpa: 0 },
    { grade: "F", description: "[0-5[", gpa: 0 },
  ],
  notes: [
    {
      id: "note-demo-projet-fictif",
      source: "pass",
      ue_code: "UE-DEMO",
      label: "Projet fictif",
      score: 15,
      coefficient: 3,
      is_resit: false,
      has_override: false,
      editable: false,
      detected_at: "2026-01-03T08:30:00Z",
      updated_at: "2026-01-03T08:30:00Z",
    },
    {
      id: "note-demo-controle-fictif",
      source: "pass",
      ue_code: "UE-DEMO",
      label: "Contrôle synthétique",
      score: 13,
      coefficient: 1,
      is_resit: false,
      has_override: false,
      editable: false,
      detected_at: "2026-01-02T08:30:00Z",
      updated_at: "2026-01-02T08:30:00Z",
    },
    {
      id: "note-reseau-classique-fictif",
      source: "pass",
      ue_code: "RES-FICTIF",
      label: "Évaluation réseau fictive",
      score: 8,
      coefficient: 2,
      is_resit: false,
      has_override: false,
      editable: false,
      detected_at: "2026-01-01T08:30:00Z",
      updated_at: "2026-01-01T08:30:00Z",
    },
    {
      id: "note-reseau-rattrapage-fictif",
      source: "pass",
      ue_code: "RES-FICTIF",
      label: "Session de rattrapage fictive",
      score: 11,
      coefficient: 1,
      is_resit: true,
      has_override: false,
      editable: false,
      detected_at: "2026-01-05T08:30:00Z",
      updated_at: "2026-01-05T08:30:00Z",
    },
    {
      id: "note-libre-fictive",
      source: "pass",
      ue_code: "LIBRE-FICTIF",
      label: "Présentation fictive",
      score: 8,
      coefficient: 1,
      is_resit: false,
      has_override: false,
      editable: false,
      detected_at: "2026-01-04T08:30:00Z",
      updated_at: "2026-01-04T08:30:00Z",
    },
  ],
  events: [],
};

function settings(state: FakeAppState) {
  const role = state.session.role === "viewer" ? "viewer" : "owner";
  const authMethod = typeof state.session.auth_method === "string" ? state.session.auth_method : "imt";
  const primaryOwner = role === "owner" && (authMethod === "imt" || authMethod === "passkey");
  const autonomousAvailable = primaryOwner && state.autonomousAvailable;
  const serviceSessionView =
    state.passAccessPurges > 0
      ? {
          ...serviceSession,
          state: "reauth_required",
          reauth_required: true,
          established_at: null,
          expires_at: null,
          last_used_at: null,
          pass_last_success_at: null,
          hub_state: "unavailable",
          hub_last_attempt_at: null,
          hub_last_success_at: null,
        }
      : serviceSession;
  const passAccessView = {
    ...passAccess,
    service_session: serviceSessionView,
  };
  return {
    account: {
      display_name: account.display_name,
      imt_username: account.imt_username,
      timezone: "Europe/Paris",
      campus: "rennes",
      campus_source: "pass",
      profile_refreshed_at: "2026-01-01T08:30:00Z",
      program: "FIP",
      promotion_year: 2028,
      academic_source: "pass",
      academic_verified_at: "2026-01-01T08:30:00Z",
      official_first_name: "Étudiante",
      official_last_name: "FICTIVE",
      official_name: "Étudiante FICTIVE",
      official_identity_at: "2026-01-01T08:30:00Z",
    },
    telegram: { configured: false, enabled: false, last_test_at: null, last_test_status: null },
    sync: {
      enabled: state.syncMode !== "manual",
      mode: state.syncMode,
      available_modes: autonomousAvailable ? ["manual", "session_only", "autonomous"] : ["manual", "session_only"],
      autonomous: primaryOwner
        ? {
            available: autonomousAvailable,
            enrollment_available: autonomousAvailable,
            runtime_ready: autonomousAvailable,
            unavailable_reason: autonomousAvailable ? null : "unavailable",
            configured: state.autonomousConfigured,
            state: state.autonomousState,
            activation_pending: state.autonomousConfigured && state.syncMode !== "autonomous",
            consent_version: state.autonomousConfigured ? 1 : null,
            consented_at: state.autonomousConfigured ? "2026-07-27T10:00:00Z" : null,
            verified_at: state.autonomousConfigured ? "2026-07-27T10:00:00Z" : null,
            last_used_at: state.syncMode === "autonomous" && state.autonomousConfigured ? "2026-07-27T10:15:00Z" : null,
            last_success_at:
              state.syncMode === "autonomous" && state.autonomousConfigured ? "2026-07-27T10:16:00Z" : null,
            last_failure_at: state.autonomousNeedsReenrollment ? "2026-07-27T10:20:00Z" : null,
            needs_reenrollment: state.autonomousNeedsReenrollment,
          }
        : {
            available: false,
            enrollment_available: false,
            runtime_ready: false,
            unavailable_reason: "unavailable",
            configured: false,
            state: null,
            activation_pending: false,
            consent_version: null,
            consented_at: null,
            verified_at: null,
            last_used_at: null,
            last_success_at: null,
            last_failure_at: null,
            needs_reenrollment: false,
          },
      interval_hours: 2,
      adaptive: true,
      current_interval_hours: 2,
      no_change_streak: 0,
      consented_at: null,
      paused_reason: null,
      paused_at: null,
      next_eligible_at: null,
      allowed_intervals: [2, 4, 6, 8, 12, 24],
      business_hours: { weekdays: "monday-friday", start: "08:00", end: "20:00", timezone: "Europe/Paris" },
      pass_access: passAccessView,
      service_session: role === "owner" ? serviceSessionView : null,
    },
    access: {
      role,
      auth_method: authMethod,
      security_setup_completed: true,
      sync_setup_completed: true,
      passkey_count: 1,
    },
  };
}

function privateHeaders() {
  return {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
  };
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json; charset=utf-8",
    headers: privateHeaders(),
    body: JSON.stringify(body),
  });
}

function recordCsrf(route: Route, state: FakeAppState) {
  state.csrfHeaders.push(route.request().headers()["x-csrf-token"]);
}

export async function installFakeAppApi(page: Page, mode: AppSessionMode = "imt"): Promise<FakeAppState> {
  const state: FakeAppState = {
    autonomousAvailable: false,
    autonomousConfigured: false,
    autonomousNeedsReenrollment: false,
    autonomousState: null,
    calendarConnectRequests: [],
    calendarDisconnects: 0,
    calendarEvents: structuredClone(syntheticCalendarEvents),
    calendarEventsError: false,
    calendarStatus: structuredClone(syntheticCalendarStatus),
    calendarStatusError: false,
    csrfHeaders: [],
    dashboard: structuredClone(syntheticDashboard),
    dashboardError: false,
    dashboardRequests: 0,
    credentialDeletes: 0,
    credentialEnrollmentFields: [],
    credentialEnrollments: 0,
    externalRequests: [],
    loginError: false,
    loginRequests: [],
    passkeyCreates: 0,
    passkeyDeletes: [],
    passAccessPurges: 0,
    session: session(mode),
    syncMode: "manual",
    syncModeUpdates: [],
    syncRequests: 0,
    tokenCreates: [],
    trainingCalendar: structuredClone(syntheticTrainingCalendar),
    trainingCalendarError: false,
    synthetic: SYNTHETIC_APP_FIXTURE_ONLY,
  };
  await page.context().addCookies([{ name: "botnote_csrf", value: csrfToken, url: baseUrl, sameSite: "Lax" }]);

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if ((url.protocol === "http:" || url.protocol === "https:") && url.origin !== baseUrl) {
      state.externalRequests.push(request.url());
      await route.abort("blockedbyclient");
      return;
    }
    if (!url.pathname.startsWith("/api/v1/")) {
      await route.continue();
      return;
    }
    if (url.pathname === "/api/v1/events") {
      await route.fulfill({ status: 204, headers: { "Cache-Control": "no-store" } });
      return;
    }
    if (url.pathname === "/api/v1/auth/session") {
      await json(route, state.session);
      return;
    }
    if (url.pathname === "/api/v1/auth/login/imt" && request.method() === "POST") {
      recordCsrf(route, state);
      state.loginRequests.push(request.postDataJSON() as Record<string, unknown>);
      if (state.loginError) {
        await json(
          route,
          { detail: { code: "IMT_AUTHENTICATION_FAILED", message: "Identifiants fictifs refusés." } },
          401,
        );
      } else {
        state.session = session("imt");
        await json(route, state.session);
      }
      return;
    }
    if (url.pathname === "/api/v1/auth/login/token" && request.method() === "POST") {
      recordCsrf(route, state);
      state.loginRequests.push(request.postDataJSON() as Record<string, unknown>);
      state.session = session("token");
      await json(route, state.session);
      return;
    }
    if (url.pathname === "/api/v1/auth/logout" && request.method() === "POST") {
      recordCsrf(route, state);
      state.session = session("anonymous");
      await json(route, { ok: true });
      return;
    }
    if (url.pathname === "/api/v1/dashboard") {
      state.dashboardRequests += 1;
      if (state.dashboardError) {
        await json(route, { detail: { code: "SERVICE_UNAVAILABLE", message: "Service fictif indisponible." } }, 503);
      } else {
        await json(route, state.dashboard);
      }
      return;
    }
    if (url.pathname === "/api/v1/calendar/status" && request.method() === "GET") {
      if (state.calendarStatusError) {
        await json(route, { detail: { code: "SERVICE_UNAVAILABLE", message: "Agenda fictif indisponible." } }, 503);
      } else {
        await json(route, state.calendarStatus);
      }
      return;
    }
    if (url.pathname === "/api/v1/calendar/events" && request.method() === "GET") {
      if (state.calendarEventsError) {
        await json(route, { detail: { code: "SERVICE_UNAVAILABLE", message: "Cours fictifs indisponibles." } }, 503);
      } else {
        await json(route, state.calendarEvents);
      }
      return;
    }
    if (url.pathname === "/api/v1/calendar/training" && request.method() === "GET") {
      if (state.trainingCalendarError) {
        await json(route, { detail: { code: "SERVICE_UNAVAILABLE", message: "Formation fictive indisponible." } }, 503);
      } else {
        await json(route, state.trainingCalendar);
      }
      return;
    }
    if (url.pathname === "/api/v1/calendar/subscription" && request.method() === "PUT") {
      recordCsrf(route, state);
      const body = request.postDataJSON() as { url?: unknown };
      state.calendarConnectRequests.push(String(body.url ?? ""));
      state.calendarStatus = { ...state.calendarStatus, configured: true, event_count: state.calendarEvents.length };
      await json(route, state.calendarStatus);
      return;
    }
    if (url.pathname === "/api/v1/calendar/subscription" && request.method() === "DELETE") {
      recordCsrf(route, state);
      state.calendarDisconnects += 1;
      state.calendarEvents = [];
      state.calendarStatus = { ...state.calendarStatus, configured: false, event_count: 0 };
      await route.fulfill({ status: 204, headers: privateHeaders() });
      return;
    }
    if (url.pathname === "/api/v1/settings" && request.method() === "GET") {
      await json(route, settings(state));
      return;
    }
    if (url.pathname === "/api/v1/settings/sync-credential/enroll" && request.method() === "POST") {
      recordCsrf(route, state);
      if (!state.autonomousAvailable) {
        await json(
          route,
          {
            detail: {
              code: "AUTONOMOUS_SYNC_ENROLLMENT_UNAVAILABLE",
              message: "L'enrôlement autonome n'est pas disponible.",
            },
          },
          409,
        );
        return;
      }
      const body = request.postDataJSON() as Record<string, unknown>;
      state.credentialEnrollments += 1;
      state.credentialEnrollmentFields.push(Object.keys(body).sort());
      state.autonomousConfigured = true;
      state.autonomousNeedsReenrollment = false;
      state.autonomousState = "active";
      await json(route, settings(state));
      return;
    }
    if (url.pathname === "/api/v1/settings/sync-mode" && request.method() === "PATCH") {
      recordCsrf(route, state);
      const body = request.postDataJSON() as Record<string, unknown>;
      state.syncModeUpdates.push(structuredClone(body));
      const requestedMode = body.mode;
      if (requestedMode === "autonomous" && !state.autonomousAvailable) {
        await json(
          route,
          {
            detail: {
              code: "AUTONOMOUS_SYNC_UNAVAILABLE",
              message: "La synchronisation autonome n'est pas encore disponible.",
            },
          },
          409,
        );
        return;
      }
      if (requestedMode === "autonomous" && !state.autonomousConfigured) {
        await json(
          route,
          {
            detail: {
              code: "SYNC_CREDENTIAL_REQUIRED",
              message: "Un mot de passe IMT protégé est requis pour ce mode.",
            },
          },
          409,
        );
        return;
      }
      if (requestedMode === "autonomous" && state.autonomousNeedsReenrollment) {
        await json(
          route,
          {
            detail: {
              code: "SYNC_CREDENTIAL_REENROLLMENT_REQUIRED",
              message: "Le mot de passe IMT protégé doit être renouvelé.",
            },
          },
          409,
        );
        return;
      }
      if (requestedMode === "manual" || requestedMode === "session_only" || requestedMode === "autonomous") {
        if (state.syncMode === "autonomous" && requestedMode !== "autonomous") {
          state.autonomousConfigured = false;
          state.autonomousState = "revoked";
        }
        state.syncMode = requestedMode;
      }
      await json(route, settings(state));
      return;
    }
    if (url.pathname === "/api/v1/settings/sync-credential" && request.method() === "DELETE") {
      recordCsrf(route, state);
      state.credentialDeletes += 1;
      state.autonomousConfigured = false;
      state.autonomousNeedsReenrollment = false;
      state.autonomousState = "revoked";
      if (state.syncMode === "autonomous") state.syncMode = "session_only";
      await json(route, settings(state));
      return;
    }
    if (url.pathname === "/api/v1/settings/pass-access/purge" && request.method() === "POST") {
      recordCsrf(route, state);
      state.passAccessPurges += 1;
      state.syncMode = "manual";
      state.autonomousConfigured = false;
      state.autonomousNeedsReenrollment = false;
      state.autonomousState = "revoked";
      await json(route, settings(state));
      return;
    }
    if (url.pathname === "/api/v1/settings/auto-sync" && request.method() === "PATCH") {
      recordCsrf(route, state);
      const body = request.postDataJSON() as Record<string, unknown>;
      state.syncMode = body.enabled ? "session_only" : "manual";
      state.autonomousConfigured = false;
      state.autonomousState = "revoked";
      await json(route, settings(state));
      return;
    }
    if (url.pathname === "/api/v1/settings/sync-setup" && request.method() === "PUT") {
      recordCsrf(route, state);
      const body = request.postDataJSON() as Record<string, unknown>;
      state.syncMode = body.enabled ? "session_only" : "manual";
      state.autonomousConfigured = false;
      state.autonomousState = "revoked";
      await json(route, settings(state));
      return;
    }
    if (url.pathname === "/api/v1/tokens" && request.method() === "GET") {
      await json(route, []);
      return;
    }
    if (url.pathname === "/api/v1/tokens" && request.method() === "POST") {
      recordCsrf(route, state);
      const body = request.postDataJSON() as Record<string, unknown>;
      state.tokenCreates.push(body);
      if (body.role === "owner" && state.session.auth_method === "token") {
        await json(
          route,
          {
            detail: {
              code: "PRIMARY_AUTH_REQUIRED",
              message: "Une authentification IMT ou passkey est requise pour cette opération.",
            },
          },
          403,
        );
      } else {
        await json(route, {
          id: "token-cree-fictif",
          name: String(body.name),
          prefix: "demofictif",
          role: body.role,
          expires_at: "2026-02-01T08:00:00Z",
          created_at: "2026-01-01T08:00:00Z",
          last_used_at: null,
          revoked_at: null,
          token: "bn1_secret-entierement-fictif",
        });
      }
      return;
    }
    if (url.pathname === "/api/v1/auth/passkeys" && request.method() === "GET") {
      await json(route, [
        {
          id: "passkey-fictive",
          name: "Appareil fictif",
          device_type: "single_device",
          backed_up: false,
          transports: ["internal"],
          created_at: "2026-01-01T08:00:00Z",
          last_used_at: null,
        },
      ]);
      return;
    }
    if (url.pathname === "/api/v1/auth/passkeys/registration/options" && request.method() === "POST") {
      recordCsrf(route, state);
      await json(route, {
        challenge_id: "challenge-passkey-fictif",
        publicKey: {
          challenge: "AQ",
          rp: { name: "IMTégrale", id: "127.0.0.1" },
          user: { id: "AQ", name: account.imt_username, displayName: account.display_name },
          pubKeyCredParams: [{ type: "public-key", alg: -7 }],
          timeout: 60_000,
          attestation: "none",
          excludeCredentials: [],
        },
      });
      return;
    }
    if (url.pathname === "/api/v1/auth/passkeys" && request.method() === "POST") {
      recordCsrf(route, state);
      state.passkeyCreates += 1;
      await json(route, {
        id: "passkey-creee-fictive",
        name: String((request.postDataJSON() as { name?: unknown }).name),
        device_type: "single_device",
        backed_up: false,
        transports: ["internal"],
        created_at: "2026-01-01T08:00:00Z",
        last_used_at: null,
      });
      return;
    }
    if (url.pathname.startsWith("/api/v1/auth/passkeys/") && request.method() === "DELETE") {
      recordCsrf(route, state);
      state.passkeyDeletes.push(decodeURIComponent(url.pathname.slice("/api/v1/auth/passkeys/".length)));
      await json(route, { ok: true });
      return;
    }
    if (url.pathname === "/api/v1/sync" && request.method() === "POST") {
      recordCsrf(route, state);
      state.syncRequests += 1;
      await json(route, {
        ok: true,
        request_id: "sync-fictive-1",
        status: "queued",
        idempotent_replay: false,
        accepted_at: "2026-01-01T08:31:00Z",
        cooldown_until: "2026-01-01T10:31:00Z",
        retry_after_seconds: 7200,
        server_time: "2026-01-01T08:31:00Z",
        error_code: null,
      });
      return;
    }
    if (url.pathname.startsWith("/api/v1/settings/") && ["PUT", "POST"].includes(request.method())) {
      recordCsrf(route, state);
      await json(route, settings(state));
      return;
    }
    await json(route, { detail: { code: "RESOURCE_NOT_FOUND", message: "Route fictive non configurée." } }, 404);
  });
  return state;
}

export async function installFakeEventSource(page: Page) {
  await page.addInitScript(() => {
    class FakeEventSource extends EventTarget {
      static instances: FakeEventSource[] = [];
      onopen: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      readonly url: string;

      constructor(url: string | URL) {
        super();
        this.url = String(url);
        FakeEventSource.instances.push(this);
        queueMicrotask(() => this.onopen?.(new Event("open")));
      }

      close() {}
    }

    Object.defineProperty(window, "EventSource", { configurable: true, value: FakeEventSource });
    Object.defineProperty(window, "__emitSyntheticUpdate", {
      configurable: true,
      value: (payload: Record<string, unknown> = {}) => {
        for (const source of FakeEventSource.instances) {
          source.dispatchEvent(
            new MessageEvent("update", {
              data: JSON.stringify(payload),
              lastEventId: `evc1_${"b".repeat(32)}`,
            }),
          );
        }
      },
    });
  });
}

export async function installFakeWebAuthn(page: Page) {
  await page.addInitScript(() => {
    class FakePublicKeyCredential {
      id = "credential-fictive";
      rawId = new Uint8Array([1]).buffer;
      type = "public-key";
      authenticatorAttachment = "platform";
      response = {
        clientDataJSON: new Uint8Array([1]).buffer,
        attestationObject: new Uint8Array([2]).buffer,
        getTransports: () => ["internal"],
      };

      getClientExtensionResults() {
        return {};
      }
    }

    Object.defineProperty(window, "PublicKeyCredential", { configurable: true, value: FakePublicKeyCredential });
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: { create: async () => new FakePublicKeyCredential(), get: async () => new FakePublicKeyCredential() },
    });
  });
}
