// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ToastProvider } from "../components/Toast";
import { queryKeys } from "../lib/queries";
import { inspectFetchRequest, readFetchJson } from "../test/fetchRequest";
import { expectNoSeriousLearningViolations } from "../test/learningTestA11y";
import type { Session } from "../types";
import { LearningPage } from "./LearningPage";

const FICTIVE_ACCOUNT = {
  id: "account-fictif",
  display_name: "[FICTIF] Étudiante",
  imt_username: "etudiante.fictive",
};
const createObjectURL = vi.fn(() => "blob:https://imt.test/FICTIF-source");

function session(overrides: Partial<Session> = {}): Session {
  return {
    authenticated: true,
    role: "owner",
    auth_method: "imt",
    account: FICTIVE_ACCOUNT,
    learning: {
      available: false,
      audience_label: null,
      level_label: null,
      reverify_required: false,
      catalog_version: null,
    },
    ...overrides,
  };
}

function renderDirectRoute(value: Session, initialEntry = "/parcours") {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  client.setQueryData(queryKeys.session, value);
  const result = render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/" element={<h1>[FICTIF] Accueil principal</h1>} />
            <Route path="/parcours/*" element={<LearningPage session={value} />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { ...result, client };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const eligibleLearning = {
  available: true,
  audience: "fip:2028",
  audience_label: "[FICTIF] FIP 2028",
  level_label: "[FICTIF] 2A",
  reverify_required: false,
  catalog_version: "catalogue-fictif-v1",
  release_id: "release-fictive-v1",
};

function catalogNode(overrides: Record<string, unknown>) {
  return {
    id: "node.fictif",
    kind: "lesson",
    title: "[FICTIF] Contenu",
    code: null,
    description: null,
    parent_id: null,
    content_id: null,
    source_id: null,
    prerequisite_ids: [],
    difficulty: null,
    estimated_minutes: null,
    section: "course",
    reader_visibility: "primary",
    document_type: null,
    page_count: null,
    download_allowed: false,
    review_status: "published",
    revision: "fictive-r1",
    position: 0,
    ...overrides,
  };
}

function catalogResponse(nodes: unknown[], releaseMode: "published" | "private_preview" = "published") {
  return {
    schema_version: 2,
    release_mode: releaseMode,
    release_id: eligibleLearning.release_id,
    catalog_version: eligibleLearning.catalog_version,
    audience: eligibleLearning.audience,
    nodes,
  };
}

beforeEach(() => {
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    queueMicrotask(() => callback(0));
    return 1;
  });
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: createObjectURL,
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("LearningPage access states", () => {
  it("redirects a non-eligible direct visit without making a learning request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderDirectRoute(session());

    expect(await screen.findByRole("heading", { name: "[FICTIF] Accueil principal" })).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Navigation Parcours")).toBeNull();
  });

  it("fails closed for a token even if its learning payload is inconsistent", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderDirectRoute(
      session({
        role: "owner",
        auth_method: "token",
        learning: {
          available: true,
          audience_label: "[FICTIF] Ne doit pas être affiché",
          level_label: "[FICTIF] Ne doit pas être affiché",
          reverify_required: false,
          catalog_version: "FICTIF-inconsistent-token-catalog",
        },
      }),
    );

    expect(await screen.findByRole("heading", { name: "[FICTIF] Accueil principal" })).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByText("Réussir ma 2A")).toBeNull();
  });

  it("shows a clear reverification gate and keeps keyboard focus inside its modal", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderDirectRoute(
      session({
        auth_method: "passkey",
        learning: {
          available: false,
          audience_label: "[FICTIF] FIP 2028",
          level_label: "[FICTIF] 2A",
          reverify_required: true,
          catalog_version: null,
        },
      }),
    );

    expect(screen.getByRole("heading", { name: "Confirme ton statut étudiant" })).toBeTruthy();
    expect(screen.getByText(/ne lance pas de synchronisation des notes/)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
    const gate = screen.getByRole("heading", { name: "Confirme ton statut étudiant" }).closest(".learning-gate");
    expect(gate).toBeTruthy();
    await expectNoSeriousLearningViolations(gate!);

    const trigger = screen.getByRole("button", { name: "Vérifier avec mon compte IMT" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Vérifier mon statut étudiant" });
    await waitFor(() => expect(document.activeElement).toBe(dialog));
    await expectNoSeriousLearningViolations(dialog);

    const close = screen.getByRole("button", { name: "Fermer" });
    const cancel = screen.getByRole("button", { name: "Annuler" });
    close.focus();
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(cancel);

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("renders a stable catalog-unavailable error without backend detail", async () => {
    const privateCanary = "[FICTIF] PRIVATE_INTERNAL_CATALOG_CANARY";
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: "LEARNING_CATALOG_UNAVAILABLE",
            message: privateCanary,
          },
        }),
        {
          status: 503,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderDirectRoute(
      session({
        learning: {
          available: false,
          audience_label: "[FICTIF] FIP 2028",
          level_label: "[FICTIF] 2A",
          reverify_required: false,
          catalog_version: null,
        },
      }),
    );

    expect(await screen.findByRole("heading", { name: "Parcours temporairement indisponible" })).toBeTruthy();
    expect(screen.getByRole("alert").textContent).not.toContain(privateCanary);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [input, init] = fetchMock.mock.calls[0]!;
    const request = inspectFetchRequest(input, init);
    expect(request.pathname).toBe("/api/v1/learning/access");
    expect(request.credentials).toBe("same-origin");
    await expectNoSeriousLearningViolations(screen.getByRole("alert"));
  });
});

describe("LearningPage eligible catalog and content", () => {
  it("uses generic copy when the current release has no published UE", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const { pathname: path } = inspectFetchRequest(input, init);
      if (path.endsWith("/access")) return jsonResponse(eligibleLearning);
      if (path.endsWith("/catalog")) return jsonResponse(catalogResponse([]));
      if (path.endsWith("/progress"))
        return jsonResponse({
          catalog_version: eligibleLearning.catalog_version,
          items: [],
          summary: { started_count: 0, completed_lessons: 0, viewed_exercises: 0, favorite_count: 0 },
        });
      throw new Error(`Unexpected fictive request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderDirectRoute(session({ learning: eligibleLearning }));

    expect(await screen.findByText("Catalogue vide")).toBeTruthy();
    expect(screen.getByText("Aucune UE n'est disponible pour le moment.")).toBeTruthy();
    expect(screen.queryByText(/release fictive/i)).toBeNull();
  });

  it("renders an eligible catalog and keeps direct UE content discoverable", async () => {
    const user = userEvent.setup();
    const nodes = [
      catalogNode({ id: "ue.fictive", kind: "ue", title: "[FICTIF] UE alpha" }),
      catalogNode({
        id: "annale.fictive",
        kind: "past_exam",
        title: "[FICTIF] Annale directe",
        parent_id: "ue.fictive",
        content_id: "annale-contenu.fictif",
        estimated_minutes: 25,
        position: 1,
      }),
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const { pathname: path } = inspectFetchRequest(input, init);
      if (path.endsWith("/access")) return jsonResponse(eligibleLearning);
      if (path.endsWith("/catalog")) return jsonResponse(catalogResponse(nodes));
      if (path.endsWith("/progress"))
        return jsonResponse({
          catalog_version: eligibleLearning.catalog_version,
          items: [],
          summary: { started_count: 0, completed_lessons: 0, viewed_exercises: 0, favorite_count: 0 },
        });
      throw new Error(`Unexpected fictive request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const value = session({
      learning: {
        available: true,
        audience_label: eligibleLearning.audience_label,
        level_label: eligibleLearning.level_label,
        reverify_required: false,
        catalog_version: eligibleLearning.catalog_version,
      },
    });
    const { container } = renderDirectRoute(value);

    const ueLink = await screen.findByRole("link", { name: /\[FICTIF\] UE alpha/ });
    await user.click(ueLink);
    expect(await screen.findByRole("heading", { name: "[FICTIF] Annale directe" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /\[FICTIF\] Annale directe/ }).getAttribute("href")).toBe(
      "/parcours/exercices/annale-contenu.fictif",
    );
    await expectNoSeriousLearningViolations(container.querySelector(".learning-workspace")!);
  });

  it("shows an exact citation without loading a metadata-only source asset", async () => {
    const sourceId = "source.fictive-metadata-only";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const { pathname: path, method } = inspectFetchRequest(input, init);
      if (path.endsWith("/access")) return jsonResponse(eligibleLearning);
      if (path.endsWith(`/sources/${sourceId}`))
        return jsonResponse({
          release_id: eligibleLearning.release_id,
          id: sourceId,
          title: "[FICTIF] Document cité",
          asset_id: null,
          kind: null,
          mime_type: null,
          filename: null,
          revision: "fictive-r1",
          pages: Array.from({ length: 20 }, (_, index) => ({ page: index + 1, label: null })),
          page_count: 20,
          rights_label: "[FICTIF] Consultation locale uniquement",
          asset_url: null,
          source_serving_allowed: false,
        });
      if (path.endsWith(`/progress/${sourceId}`) && method === "PUT")
        return jsonResponse({
          content_id: sourceId,
          last_section_id: null,
          last_page: 12,
          completed: false,
          exercise_viewed: false,
          opened_hint_ids: [],
          self_assessment: null,
          favorite: false,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        });
      throw new Error(`Unexpected fictive request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const value = session({
      learning: {
        available: true,
        audience_label: eligibleLearning.audience_label,
        level_label: eligibleLearning.level_label,
        reverify_required: false,
        catalog_version: eligibleLearning.catalog_version,
      },
    });
    const { container } = renderDirectRoute(value, `/parcours/sources/${sourceId}?page=12`);

    const citation = await screen.findByRole("region", { name: "Citation disponible" });
    expect(citation.textContent).toContain("[FICTIF] Document cité — page 12");
    expect(citation.textContent).toContain("Aucun fichier n'est chargé");
    expect(screen.queryByRole("link", { name: /Télécharger/ })).toBeNull();
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input, init]) =>
          inspectFetchRequest(input, init).pathname.includes("/learning/assets/"),
        ),
      ).toBe(false),
    );
    expect(createObjectURL).not.toHaveBeenCalled();
    await expectNoSeriousLearningViolations(container.querySelector(".learning-workspace")!);
  });

  it("redirects an exercise opened under the lesson prefix before writing route-derived progress", async () => {
    const user = userEvent.setup();
    const exerciseId = "exercise-contenu.fictif";
    const node = catalogNode({
      id: "exercise.fictif",
      kind: "exercise",
      title: "[FICTIF] Exercice alpha",
      content_id: exerciseId,
      estimated_minutes: 10,
      review_status: "private_preview",
    });
    const progressItem = {
      content_id: exerciseId,
      last_section_id: "section.fictive",
      last_page: null,
      completed: false,
      exercise_viewed: true,
      opened_hint_ids: [],
      self_assessment: null,
      favorite: false,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const { pathname: path, method } = inspectFetchRequest(input, init);
      if (path.endsWith("/access")) return jsonResponse(eligibleLearning);
      if (path.endsWith("/catalog")) return jsonResponse(catalogResponse([node], "private_preview"));
      if (path.includes(`/content/${exerciseId}`))
        return jsonResponse({
          release_id: eligibleLearning.release_id,
          id: exerciseId,
          kind: "exercise",
          frontmatter: {
            catalog_node_id: "exercise.fictif",
            title: "[FICTIF] Exercice alpha",
            review_status: "private_preview",
            revision: "fictive-r1",
            prerequisite_ids: [],
            difficulty: "introductory",
            estimated_minutes: 10,
          },
          blocks: [
            {
              type: "heading",
              id: "section.fictive",
              level: 2,
              inlines: [{ type: "text", text: "[FICTIF] Énoncé", marks: [] }],
            },
            {
              type: "directive",
              id: "hint.fictif",
              name: "hint",
              title: null,
              inlines: [{ type: "text", text: "[FICTIF] Indice caché", marks: [] }],
            },
            {
              type: "directive",
              id: "solution.fictive",
              name: "solution",
              title: null,
              inlines: [{ type: "text", text: "[FICTIF] Correction", marks: [] }],
            },
          ],
        });
      if (path.endsWith("/progress") && method === "GET")
        return jsonResponse({
          catalog_version: eligibleLearning.catalog_version,
          items: [],
          summary: { started_count: 0, completed_lessons: 0, viewed_exercises: 0, favorite_count: 0 },
        });
      if (path.includes("/progress/") && method === "PUT") return jsonResponse(progressItem);
      if (path.endsWith("/attempts") && method === "POST") {
        const body = (await readFetchJson(input, init)) as { attempt_kind: string; hint_id?: string };
        return jsonResponse(
          {
            id: "attempt.fictif",
            exercise_id: exerciseId,
            attempt_kind: body.attempt_kind,
            hint_id: body.hint_id ?? null,
            self_assessment: null,
            attempted_at: "2026-01-01T00:00:00Z",
          },
          201,
        );
      }
      throw new Error(`Unexpected fictive request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const value = session({
      learning: {
        available: true,
        audience_label: eligibleLearning.audience_label,
        level_label: eligibleLearning.level_label,
        reverify_required: false,
        catalog_version: eligibleLearning.catalog_version,
      },
    });
    renderDirectRoute(value, `/parcours/lecons/${exerciseId}`);

    const openHint = await screen.findByRole("button", { name: "Ouvrir l'indice 1" });
    expect(screen.queryByText("Brouillon privé")).toBeNull();
    expect(screen.queryByText("Version de travail")).toBeNull();
    expect(screen.queryByText("fictive-r1")).toBeNull();
    expect(screen.queryByText(eligibleLearning.release_id)).toBeNull();
    expect(screen.queryByText("Publié")).toBeNull();
    await user.click(screen.getByLabelText("Options Parcours"));
    await user.click(screen.getByRole("button", { name: "Informations de vérification" }));
    const reviewPanel = screen.getByRole("complementary", { name: "Métadonnées de revue" });
    expect(reviewPanel.textContent).toContain("Version de travail");
    expect(reviewPanel.textContent).toContain("fictive-r1");
    expect(screen.getAllByText("Version de travail")).toHaveLength(1);
    const progressWrites = await Promise.all(
      fetchMock.mock.calls.map(async ([input, init]) => {
        const request = inspectFetchRequest(input, init);
        if (!request.pathname.includes("/progress/") || request.method !== "PUT") return null;
        return readFetchJson(input, init) as Promise<Record<string, unknown>>;
      }),
    );
    expect(progressWrites.some((body) => body?.exercise_viewed === true)).toBe(false);
    expect(screen.queryByText("[FICTIF] Indice caché")).toBeNull();

    await user.click(openHint);
    expect(await screen.findByText("[FICTIF] Indice caché")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("Indice 1 ouvert");
  });
});
