import type { Page, Route } from "@playwright/test";

// Machine-readable declaration: every identity, title and payload below is
// synthetic and exists only to exercise the generic public interface.
export const SYNTHETIC_FIXTURE_ONLY = true as const;

export type FakeSessionMode = "eligible" | "token" | "noneligible" | "reverify" | "unavailable" | "error";

export interface FakeLearningState {
  accessDelayMs: number;
  attempts: Array<Record<string, unknown>>;
  externalRequests: string[];
  learningRequests: string[];
  progress: Map<string, Record<string, unknown>>;
  searchQueries: string[];
  synthetic: true;
}

const baseUrl = "http://127.0.0.1:4173";
const releaseId = "demo-fictive-release-001";
const catalogVersion = "demo-fictive-catalog-v1";
const csrfToken = "csrf-e2e-fictif";

const sessionByMode: Record<FakeSessionMode, Record<string, unknown>> = {
  eligible: {
    authenticated: true,
    role: "owner",
    auth_method: "imt",
    needs_security_setup: false,
    needs_sync_setup: false,
    account: { id: "account-e2e-fictif", display_name: "Étudiante fictive", imt_username: "demo.fictif" },
    learning: {
      available: true,
      audience_label: "FIP 2028 · DÉMO FICTIVE",
      level_label: "2A fictive",
      reverify_required: false,
      catalog_version: catalogVersion,
    },
  },
  token: {
    authenticated: true,
    role: "owner",
    auth_method: "token",
    needs_security_setup: false,
    needs_sync_setup: false,
    account: { id: "account-token-fictif", display_name: "Token fictif", imt_username: null },
    learning: {
      available: false,
      audience_label: null,
      level_label: null,
      reverify_required: false,
      catalog_version: null,
    },
  },
  noneligible: {
    authenticated: true,
    role: "owner",
    auth_method: "imt",
    needs_security_setup: false,
    needs_sync_setup: false,
    account: { id: "account-noneligible-fictif", display_name: "Compte fictif", imt_username: "demo.fictif" },
    learning: {
      available: false,
      audience_label: null,
      level_label: null,
      reverify_required: false,
      catalog_version: null,
    },
  },
  reverify: {
    authenticated: true,
    role: "owner",
    auth_method: "passkey",
    needs_security_setup: false,
    needs_sync_setup: false,
    account: { id: "account-reverify-fictif", display_name: "Étudiante fictive", imt_username: "demo.fictif" },
    learning: {
      available: false,
      audience_label: "FIP 2028 · DÉMO FICTIVE",
      level_label: "2A fictive",
      reverify_required: true,
      catalog_version: null,
    },
  },
  unavailable: {
    authenticated: true,
    role: "owner",
    auth_method: "imt",
    needs_security_setup: false,
    needs_sync_setup: false,
    account: { id: "account-unavailable-fictif", display_name: "Étudiante fictive", imt_username: "demo.fictif" },
    learning: {
      available: false,
      audience_label: "FIP 2028 · DÉMO FICTIVE",
      level_label: "2A fictive",
      reverify_required: false,
      catalog_version: null,
    },
  },
  error: {
    authenticated: true,
    role: "owner",
    auth_method: "imt",
    needs_security_setup: false,
    needs_sync_setup: false,
    account: { id: "account-error-fictif", display_name: "Étudiante fictive", imt_username: "demo.fictif" },
    learning: {
      available: false,
      audience_label: "FIP 2028 · DÉMO FICTIVE",
      level_label: "2A fictive",
      reverify_required: false,
      catalog_version: null,
    },
  },
};

const dashboard = {
  generated_at: "2026-01-01T00:00:00Z",
  latest_event_id: 0,
  account: {
    id: "account-e2e-fictif",
    display_name: "Étudiante fictive",
    imt_username: "demo.fictif",
    last_sync_at: null,
    last_sync_status: "never",
    last_sync_error: null,
    manual_sync: null,
    telegram_enabled: false,
  },
  summary: {
    average: null,
    average_credits: 0,
    gpa: null,
    gpa_credits: 0,
    validated_credits: 0,
    note_count: 0,
    ue_count: 0,
    missing_ects_count: 0,
  },
  years: [],
  semesters: [],
  ues: [],
  grade_distribution: [],
  grade_scale: [],
  notes: [],
  events: [],
};

const node = (
  id: string,
  kind: string,
  title: string,
  parentId: string | null,
  position: number,
  options: { contentId?: string; sourceId?: string; minutes?: number } = {},
) => ({
  id,
  kind,
  title,
  parent_id: parentId,
  content_id: options.contentId ?? null,
  source_id: options.sourceId ?? null,
  prerequisite_ids: [],
  difficulty: options.contentId ? "introductory" : null,
  estimated_minutes: options.minutes ?? null,
  review_status: "published",
  revision: "demo-fictive-r1",
  position,
});

const catalog = {
  release_id: releaseId,
  catalog_version: catalogVersion,
  audience: "fip:2028",
  nodes: [
    node("audience-fictive", "audience", "DÉMO FICTIVE — audience", null, 1),
    node("curriculum-fictif", "curriculum", "DÉMO FICTIVE — cursus", "audience-fictive", 1),
    node("promotion-fictive", "promotion", "DÉMO FICTIVE — promotion 2028", "curriculum-fictif", 1),
    node("level-fictif", "level", "DÉMO FICTIVE — niveau 2A", "promotion-fictive", 1),
    node("semester-fictif", "semester", "DÉMO FICTIVE — semestre", "level-fictif", 1),
    node("ue-fictive", "ue", "DÉMO FICTIVE — UE Zorbion", "semester-fictif", 1),
    node("module-fictif", "module", "DÉMO FICTIVE — module Alpha", "ue-fictive", 1),
    node("chapter-fictif", "chapter", "DÉMO FICTIVE — chapitre Un", "module-fictif", 1),
    node("lesson-node-fictif", "lesson", "DÉMO FICTIVE — leçon Alpha", "chapter-fictif", 1, {
      contentId: "lesson-fictive",
      minutes: 8,
    }),
    node("exercise-node-fictif", "exercise", "DÉMO FICTIVE — exercice Zorbion", "chapter-fictif", 2, {
      contentId: "exercise-fictif",
      minutes: 12,
    }),
    node("source-node-fictif", "source", "DÉMO FICTIVE — source blanche", "chapter-fictif", 3, {
      sourceId: "source-fictive",
    }),
  ],
};

const text = (value: string) => ({ type: "text", text: value, marks: [] });

const contents: Record<string, Record<string, unknown>> = {
  "lesson-fictive": {
    release_id: releaseId,
    id: "lesson-fictive",
    kind: "lesson",
    frontmatter: {
      catalog_node_id: "lesson-node-fictif",
      title: "DÉMO FICTIVE — leçon Alpha",
      review_status: "published",
      revision: "demo-fictive-r1",
      prerequisite_ids: [],
      difficulty: "introductory",
      estimated_minutes: 8,
    },
    blocks: [
      { type: "heading", id: "notion-alpha", level: 2, inlines: [text("Notion Alpha fictive")] },
      {
        type: "paragraph",
        inlines: [text("Ce texte synthétique vérifie le renderer sans reproduire de contenu pédagogique réel.")],
      },
      { type: "math", latex: "z = alpha + 1" },
      {
        type: "paragraph",
        inlines: [{ type: "exercise_ref", exercise_id: "exercise-fictif", label: "Essayer l’exercice fictif" }],
      },
      {
        type: "paragraph",
        inlines: [
          {
            type: "source_ref",
            id: "source-ref-fictif",
            source_id: "source-fictive",
            page: 2,
            end_page: null,
            label: "Consulter la page fictive 2",
          },
        ],
      },
    ],
  },
  "exercise-fictif": {
    release_id: releaseId,
    id: "exercise-fictif",
    kind: "exercise",
    frontmatter: {
      catalog_node_id: "exercise-node-fictif",
      title: "DÉMO FICTIVE — exercice Zorbion",
      review_status: "published",
      revision: "demo-fictive-r1",
      prerequisite_ids: [],
      difficulty: "introductory",
      estimated_minutes: 12,
    },
    blocks: [
      { type: "heading", id: "enonce-fictif", level: 2, inlines: [text("Énoncé entièrement fictif")] },
      { type: "paragraph", inlines: [text("Trouver la valeur symbolique du zorbion alpha.")] },
      {
        type: "directive",
        id: "hint-fictif-1",
        name: "hint",
        title: "Premier indice fictif",
        inlines: [text("Commencer par isoler le symbole alpha.")],
      },
      {
        type: "directive",
        id: "solution-fictive",
        name: "solution",
        title: "Correction fictive",
        inlines: [text("La réponse de démonstration est alpha.")],
      },
    ],
  },
};

function progressItem(contentId: string, update: Record<string, unknown> = {}) {
  return {
    content_id: contentId,
    last_section_id: null,
    last_page: null,
    completed: false,
    exercise_viewed: false,
    opened_hint_ids: [],
    self_assessment: null,
    favorite: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...update,
  };
}

function privateHeaders() {
  return {
    "Cache-Control": "private, no-store",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    Vary: "Cookie",
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

function progressResponse(state: FakeLearningState) {
  const items = [...state.progress.values()];
  return {
    catalog_version: catalogVersion,
    items,
    summary: {
      started_count: items.length,
      completed_lessons: items.filter((item) => item.completed).length,
      viewed_exercises: items.filter((item) => item.exercise_viewed).length,
      favorite_count: items.filter((item) => item.favorite).length,
    },
  };
}

export async function installFakeLearningApi(
  page: Page,
  mode: FakeSessionMode = "eligible",
): Promise<FakeLearningState> {
  const state: FakeLearningState = {
    accessDelayMs: 0,
    attempts: [],
    externalRequests: [],
    learningRequests: [],
    progress: new Map([
      ["lesson-fictive", progressItem("lesson-fictive", { favorite: true, last_section_id: "notion-alpha" })],
    ]),
    searchQueries: [],
    synthetic: SYNTHETIC_FIXTURE_ONLY,
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
    if (url.pathname.startsWith("/api/v1/learning/")) state.learningRequests.push(url.pathname);
    if (url.pathname === "/api/v1/events") {
      await route.fulfill({ status: 204, headers: { "Cache-Control": "no-store" } });
      return;
    }
    if (url.pathname === "/api/v1/auth/session") {
      await json(route, sessionByMode[mode]);
      return;
    }
    if (url.pathname === "/api/v1/dashboard") {
      await json(route, dashboard);
      return;
    }
    if (url.pathname === "/api/v1/learning/access") {
      if (state.accessDelayMs > 0) await new Promise((resolve) => setTimeout(resolve, state.accessDelayMs));
      if (mode === "unavailable") {
        await json(
          route,
          { detail: { code: "LEARNING_CATALOG_UNAVAILABLE", message: "Catalogue indisponible." } },
          503,
        );
      } else if (mode === "error") {
        await json(route, { detail: { message: "Erreur fictive générique." } }, 500);
      } else {
        await json(route, {
          available: true,
          audience: "fip:2028",
          audience_label: "FIP 2028 · DÉMO FICTIVE",
          level_label: "2A fictive",
          reverify_required: false,
          catalog_version: catalogVersion,
          release_id: releaseId,
        });
      }
      return;
    }
    if (url.pathname === "/api/v1/learning/catalog") {
      await json(route, catalog);
      return;
    }
    if (url.pathname.startsWith("/api/v1/learning/content/")) {
      const contentId = decodeURIComponent(url.pathname.slice("/api/v1/learning/content/".length));
      const content = contents[contentId];
      await json(route, content ?? { detail: { message: "Introuvable." } }, content ? 200 : 404);
      return;
    }
    if (url.pathname === "/api/v1/learning/references/lesson-fictive/source-ref-fictif") {
      await json(route, {
        release_id: releaseId,
        id: "source-ref-fictif",
        content_id: "lesson-fictive",
        source_id: "source-fictive",
        source_title: "DÉMO FICTIVE — source blanche",
        page: 2,
        end_page: null,
        label: "Consulter la page fictive 2",
        source_url: "/api/v1/learning/sources/source-fictive",
        asset_url: "/api/v1/learning/assets/asset-pdf-fictif",
      });
      return;
    }
    if (url.pathname === "/api/v1/learning/sources/source-fictive") {
      await json(route, {
        release_id: releaseId,
        id: "source-fictive",
        title: "DÉMO FICTIVE — source blanche",
        asset_id: "asset-pdf-fictif",
        kind: "pdf",
        mime_type: "application/pdf",
        filename: "demo-fictive-source.pdf",
        revision: "demo-fictive-r1",
        pages: [
          { page: 1, label: null },
          { page: 2, label: "Page fictive 2" },
        ],
        page_count: 2,
        rights_label: "Droits fictifs — démonstration technique",
        asset_url: "/api/v1/learning/assets/asset-pdf-fictif",
      });
      return;
    }
    if (url.pathname === "/api/v1/learning/assets/asset-pdf-fictif") {
      await route.fulfill({
        status: 200,
        headers: {
          ...privateHeaders(),
          "Content-Type": "application/pdf",
          "Content-Disposition": 'inline; filename="demo-fictive-source.pdf"',
        },
        body: "%PDF-1.4\n%DÉMO FICTIVE\n%%EOF\n",
      });
      return;
    }
    if (url.pathname === "/api/v1/learning/progress" && request.method() === "GET") {
      await json(route, progressResponse(state));
      return;
    }
    if (url.pathname.startsWith("/api/v1/learning/progress/") && request.method() === "PUT") {
      if (request.headers()["x-csrf-token"] !== csrfToken) {
        await json(route, { detail: { message: "CSRF fictif refusé." } }, 403);
        return;
      }
      const contentId = decodeURIComponent(url.pathname.slice("/api/v1/learning/progress/".length));
      const current = state.progress.get(contentId) ?? progressItem(contentId);
      const updated = { ...current, ...request.postDataJSON(), updated_at: "2026-01-01T00:01:00Z" };
      state.progress.set(contentId, updated);
      await json(route, updated);
      return;
    }
    if (url.pathname === "/api/v1/learning/progress" && request.method() === "DELETE") {
      if (request.headers()["x-csrf-token"] !== csrfToken) {
        await json(route, { detail: { message: "CSRF fictif refusé." } }, 403);
        return;
      }
      const deletedAttempts = state.attempts.length;
      const deletedProgress = state.progress.size;
      state.progress.clear();
      state.attempts = [];
      await json(route, { deleted: { progress: deletedProgress, attempts: deletedAttempts } });
      return;
    }
    if (url.pathname === "/api/v1/learning/attempts" && request.method() === "GET") {
      await json(route, { items: state.attempts });
      return;
    }
    if (url.pathname === "/api/v1/learning/attempts" && request.method() === "POST") {
      if (request.headers()["x-csrf-token"] !== csrfToken) {
        await json(route, { detail: { message: "CSRF fictif refusé." } }, 403);
        return;
      }
      const input = request.postDataJSON() as Record<string, unknown>;
      const exerciseId = String(input.exercise_id);
      const current = state.progress.get(exerciseId) ?? progressItem(exerciseId);
      const update: Record<string, unknown> = { exercise_viewed: true, updated_at: "2026-01-01T00:02:00Z" };
      if (input.attempt_kind === "hint_opened") update.opened_hint_ids = [String(input.hint_id)];
      if (input.attempt_kind === "self_assessed") update.self_assessment = input.self_assessment;
      if (input.attempt_kind === "completed") update.completed = true;
      state.progress.set(exerciseId, { ...current, ...update });
      const attempt = {
        id: `attempt-fictif-${state.attempts.length + 1}`,
        exercise_id: exerciseId,
        attempt_kind: input.attempt_kind,
        hint_id: input.hint_id ?? null,
        self_assessment: input.self_assessment ?? null,
        attempted_at: "2026-01-01T00:02:00Z",
      };
      state.attempts.push(attempt);
      await json(route, attempt, 201);
      return;
    }
    if (url.pathname === "/api/v1/learning/search" && request.method() === "POST") {
      if (request.headers()["x-csrf-token"] !== csrfToken) {
        await json(route, { detail: { message: "CSRF fictif refusé." } }, 403);
        return;
      }
      const query = String((request.postDataJSON() as { query?: unknown }).query ?? "");
      state.searchQueries.push(query);
      await json(route, {
        release_id: releaseId,
        items: query.toLocaleLowerCase("fr").includes("zorbion")
          ? [
              {
                entity_id: "exercise-fictif",
                catalog_node_id: "exercise-node-fictif",
                entity_type: "exercise",
                title: "DÉMO FICTIVE — exercice Zorbion",
                excerpt: "Résultat synthétique, sans extrait de document réel.",
                ue_id: "ue-fictive",
                module_id: "module-fictif",
                semester: "S6",
                difficulty: "introductory",
                estimated_minutes: 12,
              },
            ]
          : [],
        has_more: false,
        next_offset: null,
      });
      return;
    }
    await json(route, { detail: { message: "Route fictive non configurée." } }, 404);
  });

  return state;
}
