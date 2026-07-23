import type { Page, Route } from "@playwright/test";

export const SYNTHETIC_APP_FIXTURE_ONLY = true as const;

export type AppSessionMode = "anonymous" | "imt" | "passkey" | "token" | "viewer";

export interface FakeAppState {
  csrfHeaders: Array<string | undefined>;
  dashboard: Record<string, unknown>;
  dashboardError: boolean;
  dashboardRequests: number;
  externalRequests: string[];
  loginError: boolean;
  loginRequests: Array<Record<string, unknown>>;
  passkeyCreates: number;
  passkeyDeletes: string[];
  session: Record<string, unknown>;
  syncRequests: number;
  tokenCreates: Array<Record<string, unknown>>;
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
  return {
    authenticated: true,
    role: mode === "viewer" ? "viewer" : "owner",
    auth_method: authMethod,
    needs_security_setup: false,
    needs_sync_setup: false,
    account,
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

export const syntheticDashboard = {
  generated_at: "2026-01-01T08:30:00Z",
  latest_event_id: 1,
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
      enabled: false,
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
      pass_access: passAccess,
      service_session: serviceSession,
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
    csrfHeaders: [],
    dashboard: structuredClone(syntheticDashboard),
    dashboardError: false,
    dashboardRequests: 0,
    externalRequests: [],
    loginError: false,
    loginRequests: [],
    passkeyCreates: 0,
    passkeyDeletes: [],
    session: session(mode),
    syncRequests: 0,
    tokenCreates: [],
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
    if (url.pathname === "/api/v1/settings" && request.method() === "GET") {
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
      value: () => {
        for (const source of FakeEventSource.instances) {
          source.dispatchEvent(new MessageEvent("update", { data: "{}", lastEventId: "2" }));
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
