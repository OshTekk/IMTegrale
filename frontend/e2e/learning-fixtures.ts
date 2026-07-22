import type { Page, Route } from "@playwright/test";

// Machine-readable declaration: every identity, title and payload below is
// synthetic and exists only to exercise the generic public interface.
export const SYNTHETIC_FIXTURE_ONLY = true as const;

export type FakeSessionMode = "eligible" | "token" | "noneligible" | "reverify" | "unavailable" | "error";

export interface FakeLearningState {
  accessDelayMs: number;
  assetRanges: string[];
  assetRequestIds: string[];
  assetRequests: number;
  attempts: Array<Record<string, unknown>>;
  externalRequests: string[];
  learningRequests: string[];
  progress: Map<string, Record<string, unknown>>;
  searchQueries: string[];
  synthetic: true;
}

const baseUrl = "http://127.0.0.1:4173";
const releaseId = "synthetic-personal-library-001";
const catalogVersion = "synthetic-catalog-v3";
const csrfToken = "csrf-e2e-synthetic";
const inlineMathFixture = "q = \\alpha + 1";
const blockMathFixture = "\\sum_{k=1}^{n} k = \\frac{n(n+1)}{2}";

const sessionByMode: Record<FakeSessionMode, Record<string, unknown>> = {
  eligible: {
    authenticated: true,
    role: "owner",
    auth_method: "imt",
    needs_security_setup: false,
    needs_sync_setup: false,
    account: { id: "account-e2e-synthetic", display_name: "Étudiante fictive", imt_username: "synthetic.user" },
    learning: {
      available: true,
      audience_label: "Cursus fictif 2099",
      level_label: "Niveau 2A fictif",
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
    account: { id: "account-token-synthetic", display_name: "Token fictif", imt_username: null },
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
    account: { id: "account-noneligible-synthetic", display_name: "Compte fictif", imt_username: "synthetic.user" },
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
    account: { id: "account-reverify-synthetic", display_name: "Étudiante fictive", imt_username: "synthetic.user" },
    learning: {
      available: false,
      audience_label: "Bibliothèque fictive",
      level_label: "Niveau 2A fictif",
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
    account: { id: "account-unavailable-synthetic", display_name: "Étudiante fictive", imt_username: "synthetic.user" },
    learning: {
      available: false,
      audience_label: "Cursus fictif 2099",
      level_label: "Niveau 2A fictif",
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
    account: { id: "account-error-synthetic", display_name: "Étudiante fictive", imt_username: "synthetic.user" },
    learning: {
      available: false,
      audience_label: "Cursus fictif 2099",
      level_label: "Niveau 2A fictif",
      reverify_required: false,
      catalog_version: null,
    },
  },
};

const dashboard = {
  generated_at: "2099-01-01T00:00:00Z",
  latest_event_id: 0,
  account: {
    id: "account-e2e-synthetic",
    display_name: "Étudiante fictive",
    imt_username: "synthetic.user",
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

type FixtureSection = "course" | "practice" | "exam" | "summary" | "glossary" | "sources";
type FixtureVisibility = "primary" | "secondary" | "hidden";

interface NodeOptions {
  code?: string;
  contentId?: string;
  description?: string;
  difficulty?: "introductory" | "standard" | "advanced";
  documentType?: "pdf" | "image" | "download";
  downloadAllowed?: boolean;
  minutes?: number;
  pageCount?: number;
  prerequisites?: string[];
  section?: FixtureSection;
  sourceId?: string;
  visibility?: FixtureVisibility;
}

const node = (
  id: string,
  kind: string,
  title: string,
  parentId: string | null,
  position: number,
  options: NodeOptions = {},
) => ({
  id,
  kind,
  title,
  code: options.code ?? null,
  description: options.description ?? null,
  parent_id: parentId,
  content_id: options.contentId ?? null,
  source_id: options.sourceId ?? null,
  prerequisite_ids: options.prerequisites ?? [],
  difficulty: options.difficulty ?? (options.contentId ? "standard" : null),
  estimated_minutes: options.minutes ?? null,
  section: options.section ?? null,
  reader_visibility: options.visibility ?? "primary",
  document_type: options.documentType ?? null,
  page_count: options.pageCount ?? null,
  download_allowed: options.downloadAllowed ?? false,
  review_status: "reviewed",
  revision: "synthetic-review-r1",
  position,
});

const catalog = {
  schema_version: 3,
  release_mode: "personal_library",
  release_id: releaseId,
  catalog_version: catalogVersion,
  audience: "personal:synthetic-owner",
  nodes: [
    node("audience-synthetic", "audience", "Espace pédagogique fictif", null, 1),
    node("curriculum-synthetic", "curriculum", "Cursus fictif 2099", "audience-synthetic", 1),
    node("promotion-synthetic", "promotion", "Promotion fictive", "curriculum-synthetic", 1),
    node("level-synthetic", "level", "Niveau 2A fictif", "promotion-synthetic", 1),
    node("semester-synthetic", "semester", "Semestre fictif", "level-synthetic", 1, { code: "S-DEMO" }),
    node("ue-fictive", "ue", "Sciences imaginaires", "semester-synthetic", 1, {
      code: "UE-FIC100",
      description: "Une unité entièrement synthétique conçue pour vérifier l'expérience de lecture publique.",
    }),
    node("module-fictif", "module", "Raisonnement symbolique", "ue-fictive", 1, {
      code: "MOD-FIC",
      description: "Un parcours fictif pour explorer des symboles, pratiquer puis réviser dans un ordre clair.",
    }),
    node("chapter-fictif", "chapter", "Construire ses repères", "module-fictif", 1, {
      section: "course",
    }),
    node("lesson-node-fictif", "lesson", "Lire une relation", "chapter-fictif", 1, {
      contentId: "lesson-fictive",
      difficulty: "introductory",
      minutes: 8,
      section: "course",
    }),
    node("lesson-node-beta", "lesson", "Relier les symboles", "chapter-fictif", 2, {
      contentId: "lesson-beta",
      difficulty: "standard",
      minutes: 11,
      section: "course",
    }),
    node("chapter-beta", "chapter", "Comparer des modèles fictifs", "module-fictif", 2, {
      section: "course",
    }),
    node("lesson-node-gamma", "lesson", "Choisir une représentation", "chapter-beta", 1, {
      contentId: "lesson-gamma",
      difficulty: "advanced",
      minutes: 14,
      section: "course",
    }),
    node("concept-node-alpha", "concept", "Symbole alpha", "module-fictif", 1, {
      contentId: "concept-alpha",
      section: "glossary",
      visibility: "secondary",
    }),
    node("concept-node-zorbion", "concept", "Zorbion", "module-fictif", 2, {
      contentId: "concept-zorbion",
      section: "glossary",
      visibility: "secondary",
    }),
    node("exercise-node-fictif", "exercise", "Manipuler un zorbion", "chapter-fictif", 1, {
      contentId: "exercise-fictif",
      difficulty: "introductory",
      minutes: 12,
      prerequisites: ["concept-node-alpha", "concept-node-zorbion"],
      section: "practice",
    }),
    node("pc-node-fictif", "pc_td", "Atelier de relations fictives", "chapter-beta", 2, {
      contentId: "pc-fictif",
      difficulty: "standard",
      minutes: 25,
      prerequisites: ["concept-node-zorbion"],
      section: "practice",
    }),
    node("exam-node-fictif", "past_exam", "Sujet blanc synthétique", "module-fictif", 1, {
      contentId: "exam-fictif",
      difficulty: "advanced",
      minutes: 45,
      section: "exam",
    }),
    node("summary-node-fictif", "lesson", "Fiche de synthèse fictive", "module-fictif", 1, {
      contentId: "summary-fictif",
      minutes: 6,
      section: "summary",
    }),
    node("source-node-fictif", "source", "Carnet synthétique", "module-fictif", 1, {
      documentType: "pdf",
      downloadAllowed: true,
      pageCount: 2,
      section: "sources",
      sourceId: "source-fictive",
      visibility: "secondary",
    }),
    node("source-node-memo", "source", "Mémo visuel fictif", "module-fictif", 2, {
      documentType: "pdf",
      downloadAllowed: false,
      pageCount: 1,
      section: "sources",
      sourceId: "source-memo-fictif",
      visibility: "secondary",
    }),
  ],
};

const text = (value: string) => ({ type: "text", text: value, marks: [] });

function content(
  id: string,
  catalogNodeId: string,
  kind: "concept" | "lesson" | "exercise" | "pc_td" | "past_exam",
  title: string,
  blocks: Array<Record<string, unknown>>,
  minutes = 10,
) {
  return {
    release_id: releaseId,
    id,
    kind,
    frontmatter: {
      catalog_node_id: catalogNodeId,
      title,
      review_status: "reviewed",
      revision: "synthetic-review-r1",
      prerequisite_ids: [],
      difficulty: "standard",
      estimated_minutes: minutes,
    },
    blocks,
  };
}

const contents: Record<string, Record<string, unknown>> = {
  "lesson-fictive": content(
    "lesson-fictive",
    "lesson-node-fictif",
    "lesson",
    "Lire une relation",
    [
      { type: "heading", id: "notion-alpha", level: 2, inlines: [text("Une idée entièrement fictive")] },
      {
        type: "paragraph",
        inlines: [
          text("Le symbole "),
          { type: "math", latex: inlineMathFixture },
          text(" sert uniquement à vérifier un rendu mathématique accessible."),
        ],
      },
      { type: "math", latex: blockMathFixture },
      {
        type: "paragraph",
        inlines: [{ type: "exercise_ref", exercise_id: "exercise-fictif", label: "Essayer l'exercice fictif" }],
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
    8,
  ),
  "lesson-beta": content("lesson-beta", "lesson-node-beta", "lesson", "Relier les symboles", [
    { type: "heading", id: "relations-fictives", level: 2, inlines: [text("Relations fictives")] },
    { type: "paragraph", inlines: [text("Cette leçon de démonstration ne reprend aucun support réel.")] },
  ]),
  "lesson-gamma": content("lesson-gamma", "lesson-node-gamma", "lesson", "Choisir une représentation", [
    { type: "heading", id: "representation-fictive", level: 2, inlines: [text("Représentation fictive")] },
    { type: "paragraph", inlines: [text("Le contenu reste volontairement générique et synthétique.")] },
  ]),
  "concept-alpha": content("concept-alpha", "concept-node-alpha", "concept", "Symbole alpha", [
    { type: "paragraph", inlines: [text("Définition fictive utilisée uniquement par les tests publics.")] },
  ]),
  "concept-zorbion": content("concept-zorbion", "concept-node-zorbion", "concept", "Zorbion", [
    { type: "paragraph", inlines: [text("Terme inventé sans équivalent dans un enseignement réel.")] },
  ]),
  "exercise-fictif": content(
    "exercise-fictif",
    "exercise-node-fictif",
    "exercise",
    "Manipuler un zorbion",
    [
      { type: "heading", id: "enonce-fictif", level: 2, inlines: [text("Énoncé entièrement fictif")] },
      {
        type: "paragraph",
        inlines: [text("Trouver une valeur symbolique sans utiliser de donnée pédagogique réelle.")],
      },
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
        inlines: [text("La réponse de démonstration est le symbole alpha.")],
      },
    ],
    12,
  ),
  "pc-fictif": content("pc-fictif", "pc-node-fictif", "pc_td", "Atelier de relations fictives", [
    { type: "paragraph", inlines: [text("Activité pratique entièrement synthétique.")] },
  ]),
  "exam-fictif": content("exam-fictif", "exam-node-fictif", "past_exam", "Sujet blanc synthétique", [
    { type: "paragraph", inlines: [text("Sujet inventé pour démontrer la section Annales.")] },
  ]),
  "summary-fictif": content("summary-fictif", "summary-node-fictif", "lesson", "Fiche de synthèse fictive", [
    { type: "paragraph", inlines: [text("Résumé fictif, formulaire fictif et erreurs fréquentes fictives.")] },
  ]),
};

function createSyntheticPdf(): string {
  const pageOne = "BT\n/F1 20 Tf\n72 720 Td\n(DEMO FICTIVE PAGE 1) Tj\n0 -32 Td\n(SYMBOLIC ALPHA) Tj\nET";
  const pageTwo = "BT\n/F1 20 Tf\n72 720 Td\n(DEMO FICTIVE PAGE 2) Tj\n0 -32 Td\n(ZORBION REFERENCE) Tj\nET";
  const padding = "SYNTHETIC-ONLY\n".repeat(9_000);
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>",
    `<< /Length ${pageOne.length} >>\nstream\n${pageOne}\nendstream`,
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>",
    `<< /Length ${pageTwo.length} >>\nstream\n${pageTwo}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${padding.length} >>\nstream\n${padding}endstream`,
  ];
  let pdf = "%PDF-1.7\n% SYNTHETIC FIXTURE ONLY\n";
  const offsets = [0];
  objects.forEach((body, index) => {
    offsets.push(pdf.length);
    pdf += `${index + 1} 0 obj\n${body}\nendobj\n`;
  });
  const xrefOffset = pdf.length;
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  offsets.slice(1).forEach((offset) => {
    pdf += `${String(offset).padStart(10, "0")} 00000 n \n`;
  });
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
  return pdf;
}

const syntheticPdf = createSyntheticPdf();

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
    created_at: "2099-01-01T00:00:00Z",
    updated_at: "2099-01-01T00:00:00Z",
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

async function serveSyntheticPdf(route: Route, state: FakeLearningState, download: boolean, assetId: string) {
  state.assetRequests += 1;
  state.assetRequestIds.push(assetId);
  const range = route.request().headers()["range"];
  const commonHeaders = {
    ...privateHeaders(),
    "Accept-Ranges": "bytes",
    "Content-Type": "application/pdf",
    "Content-Disposition": `${download ? "attachment" : "inline"}; filename="synthetic-reader-fixture.pdf"`,
  };
  if (!range) {
    await route.fulfill({
      status: 200,
      headers: { ...commonHeaders, "Content-Length": String(syntheticPdf.length) },
      body: syntheticPdf,
    });
    return;
  }
  state.assetRanges.push(range);
  const match = /^bytes=(\d+)-(\d*)$/.exec(range);
  if (!match) {
    await route.fulfill({
      status: 416,
      headers: { ...commonHeaders, "Content-Range": `bytes */${syntheticPdf.length}` },
    });
    return;
  }
  const start = Number(match[1]);
  const requestedEnd = match[2] ? Number(match[2]) : syntheticPdf.length - 1;
  if (
    !Number.isSafeInteger(start) ||
    !Number.isSafeInteger(requestedEnd) ||
    start >= syntheticPdf.length ||
    requestedEnd < start
  ) {
    await route.fulfill({
      status: 416,
      headers: { ...commonHeaders, "Content-Range": `bytes */${syntheticPdf.length}` },
    });
    return;
  }
  const end = Math.min(requestedEnd, syntheticPdf.length - 1);
  const body = syntheticPdf.slice(start, end + 1);
  await route.fulfill({
    status: 206,
    headers: {
      ...commonHeaders,
      "Content-Length": String(body.length),
      "Content-Range": `bytes ${start}-${end}/${syntheticPdf.length}`,
    },
    body,
  });
}

export async function installFakeLearningApi(
  page: Page,
  mode: FakeSessionMode = "eligible",
): Promise<FakeLearningState> {
  const state: FakeLearningState = {
    accessDelayMs: 0,
    assetRanges: [],
    assetRequestIds: [],
    assetRequests: 0,
    attempts: [],
    externalRequests: [],
    learningRequests: [],
    progress: new Map([
      [
        "lesson-fictive",
        progressItem("lesson-fictive", {
          favorite: true,
          last_section_id: "notion-alpha",
          self_assessment: 2,
          updated_at: "2099-01-03T12:00:00Z",
        }),
      ],
      ["lesson-beta", progressItem("lesson-beta", { completed: true, updated_at: "2099-01-02T12:00:00Z" })],
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
          audience: "personal:synthetic-owner",
          audience_label: "Cursus fictif 2099",
          level_label: "Niveau 2A fictif",
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
      const item = contents[contentId];
      await json(route, item ?? { detail: { message: "Introuvable." } }, item ? 200 : 404);
      return;
    }
    if (url.pathname === "/api/v1/learning/references/lesson-fictive/source-ref-fictif") {
      await json(route, {
        release_id: releaseId,
        id: "source-ref-fictif",
        content_id: "lesson-fictive",
        source_id: "source-fictive",
        source_title: "Carnet synthétique",
        page: 2,
        end_page: null,
        label: "Consulter la page fictive 2",
        source_url: "/api/v1/learning/sources/source-fictive",
        asset_url: "/api/v1/learning/assets/asset-pdf-fictif",
        source_serving_allowed: true,
        download_allowed: true,
        download_url: "/api/v1/learning/assets/asset-pdf-fictif/download",
      });
      return;
    }
    if (url.pathname === "/api/v1/learning/sources/source-fictive") {
      await json(route, {
        release_id: releaseId,
        id: "source-fictive",
        title: "Carnet synthétique",
        asset_id: "asset-pdf-fictif",
        kind: "pdf",
        mime_type: "application/pdf",
        filename: "synthetic-reader-fixture.pdf",
        revision: "synthetic-review-r1",
        pages: [
          { page: 1, label: "Page fictive 1" },
          { page: 2, label: "Page fictive 2" },
        ],
        page_count: 2,
        rights_label: "Document fictif réservé aux tests publics",
        asset_url: "/api/v1/learning/assets/asset-pdf-fictif",
        source_serving_allowed: true,
        download_allowed: true,
        download_url: "/api/v1/learning/assets/asset-pdf-fictif/download",
      });
      return;
    }
    if (url.pathname === "/api/v1/learning/sources/source-memo-fictif") {
      await json(route, {
        release_id: releaseId,
        id: "source-memo-fictif",
        title: "Mémo visuel fictif",
        asset_id: "asset-pdf-inline-fictif",
        kind: "pdf",
        mime_type: "application/pdf",
        filename: "synthetic-inline-fixture.pdf",
        revision: "synthetic-review-r1",
        pages: [{ page: 1, label: null }],
        page_count: 1,
        rights_label: "Usage personnel fictif",
        asset_url: "/api/v1/learning/assets/asset-pdf-inline-fictif",
        source_serving_allowed: true,
        download_allowed: false,
        download_url: null,
      });
      return;
    }
    if (url.pathname === "/api/v1/learning/assets/asset-pdf-fictif") {
      await serveSyntheticPdf(route, state, false, "asset-pdf-fictif");
      return;
    }
    if (url.pathname === "/api/v1/learning/assets/asset-pdf-fictif/download") {
      await serveSyntheticPdf(route, state, true, "asset-pdf-fictif");
      return;
    }
    if (url.pathname === "/api/v1/learning/assets/asset-pdf-inline-fictif") {
      await serveSyntheticPdf(route, state, false, "asset-pdf-inline-fictif");
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
      const updated = { ...current, ...request.postDataJSON(), updated_at: "2099-01-03T12:01:00Z" };
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
      const update: Record<string, unknown> = { exercise_viewed: true, updated_at: "2099-01-03T12:02:00Z" };
      if (input.attempt_kind === "hint_opened") update.opened_hint_ids = [String(input.hint_id)];
      if (input.attempt_kind === "self_assessed") update.self_assessment = input.self_assessment;
      if (input.attempt_kind === "completed") update.completed = true;
      state.progress.set(exerciseId, { ...current, ...update });
      const attempt = {
        id: `attempt-synthetic-${state.attempts.length + 1}`,
        exercise_id: exerciseId,
        attempt_kind: input.attempt_kind,
        hint_id: input.hint_id ?? null,
        self_assessment: input.self_assessment ?? null,
        attempted_at: "2099-01-03T12:02:00Z",
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
                title: "Manipuler un zorbion",
                excerpt: "Résultat synthétique sans extrait de document réel.",
                ue_id: "ue-fictive",
                module_id: "module-fictif",
                semester: "S-DEMO",
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
