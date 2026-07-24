import type { Page, Route } from "@playwright/test";
import type { FakeAppState } from "./app-fixtures";

type JsonRecord = Record<string, any>;
type Grade = "A" | "B" | "C" | "D" | "E" | "FX" | "F";

export interface FakeGpaSimulationState {
  csrfHeaders: Array<string | undefined>;
  scenarios: JsonRecord[];
  saveRequests: JsonRecord[];
  failNextSave: boolean;
  conflictNextSave: boolean;
  synthetic: true;
}

const now = "2026-02-10T14:00:00Z";
const semesters = ["S5", "S6", "S7", "S8", "S9", "S10"];
const grades: Grade[] = ["A", "B", "C", "D", "E", "FX", "F"];
const points: Record<Grade, number> = { A: 4, B: 3.8, C: 3.5, D: 3, E: 2.5, FX: 0, F: 0 };
const formula = {
  version: "fictive-v1",
  label: "Règle fictive de démonstration",
  scale: "A à F",
  rounding: "Au centième",
  scope: "Simulation privée fictive",
  expression: "Somme pondérée fictive",
  official: false,
};

function sourceStatus(index: number): "current" | "conflict" | "unavailable" {
  if (index === 1 || index === 11) return "conflict";
  if (index === 3 || index === 17) return "unavailable";
  return "current";
}

function entry(index: number): JsonRecord {
  const id = `ue-gpa-e2e-fictive-${index}`;
  const grade = index === 7 || index === 13 ? null : grades[index % grades.length]!;
  const credits = index === 9 ? null : 1 + (index % 6);
  const semester = semesters[index % semesters.length]!;
  const title =
    index === 4
      ? "[FICTIF] Unité dont l’intitulé volontairement très long vérifie la troncature et la lecture mobile"
      : `[FICTIF] Unité GPA ${index + 1}`;
  const baselineGrade = grade === "B" ? "C" : grade;
  return {
    id,
    lineage_key: `lineage-${id}`,
    semester,
    ue_code: `FGP${String(index + 1).padStart(3, "0")}`,
    title,
    credits_ects: credits,
    grade,
    gpa_points: grade ? points[grade] : null,
    status: grade === "FX" || grade === "F" ? "not_validated" : grade ? "validated" : "pending",
    nature: index % 4 === 0 ? "modified" : "imported",
    source: {
      ue_code: `FGP${String(index + 1).padStart(3, "0")}`,
      status: sourceStatus(index),
      grade_source: index % 2 ? "pass_calculated" : "competences",
      observed_at: now,
    },
    baseline: {
      semester,
      ue_code: `FGP${String(index + 1).padStart(3, "0")}`,
      title: `[FICTIF] Unité GPA ${index + 1}`,
      credits_ects: credits,
      grade: baselineGrade,
    },
    created_at: now,
    updated_at: now,
  };
}

function scenarioResult(entries: JsonRecord[]): JsonRecord {
  const included = entries.filter(
    (item) =>
      item.grade &&
      typeof item.credits_ects === "number" &&
      Number.isFinite(item.credits_ects) &&
      item.credits_ects > 0,
  );
  const creditsIncluded = included.reduce((total, item) => total + Number(item.credits_ects), 0);
  const gpa = creditsIncluded
    ? included.reduce((total, item) => total + points[item.grade as Grade] * Number(item.credits_ects), 0) /
      creditsIncluded
    : null;
  const semesterResults = semesters
    .filter((semester) => entries.some((item) => item.semester === semester))
    .map((semester) => {
      const scoped = entries.filter((item) => item.semester === semester);
      const scopedResult = scenarioResultWithoutSemesters(scoped);
      return { semester, ...scopedResult };
    });
  const gradedCount = entries.filter((item) => item.grade).length;
  const pendingCount = entries.length - gradedCount;
  return {
    status: !entries.length ? "empty" : pendingCount ? "partial" : "ready",
    gpa: gpa === null ? null : Math.round(gpa * 100) / 100,
    credits_entered: entries.reduce(
      (total, item) => total + (typeof item.credits_ects === "number" ? item.credits_ects : 0),
      0,
    ),
    credits_included: creditsIncluded,
    ue_count: entries.length,
    graded_count: gradedCount,
    pending_count: pendingCount,
    missing_ects_count: entries.filter((item) => item.grade && item.credits_ects === null).length,
    completion_rate: entries.length ? Math.round((gradedCount / entries.length) * 100) : 0,
    semesters: semesterResults,
    warnings: pendingCount
      ? [{ code: "pending_grades", count: pendingCount, message: "Des grades fictifs sont en attente." }]
      : [],
    formula,
  };
}

function scenarioResultWithoutSemesters(entries: JsonRecord[]) {
  const included = entries.filter(
    (item) => item.grade && typeof item.credits_ects === "number" && item.credits_ects > 0,
  );
  const creditsIncluded = included.reduce((total, item) => total + Number(item.credits_ects), 0);
  const gradedCount = entries.filter((item) => item.grade).length;
  return {
    gpa: creditsIncluded
      ? Math.round(
          (included.reduce((total, item) => total + points[item.grade as Grade] * Number(item.credits_ects), 0) /
            creditsIncluded) *
            100,
        ) / 100
      : null,
    credits_included: creditsIncluded,
    ue_count: entries.length,
    graded_count: gradedCount,
    pending_count: entries.length - gradedCount,
  };
}

function makeScenario(id: string, name: string, entries: JsonRecord[], overrides: JsonRecord = {}) {
  return {
    id,
    name,
    created_from: "academic",
    formula_version: formula.version,
    version: 1,
    source_revision: "source-fictive-v1",
    source_captured_at: now,
    rebase_available: false,
    created_at: now,
    updated_at: now,
    result: scenarioResult(entries),
    entries,
    ...overrides,
  };
}

function summary(scenario: JsonRecord): JsonRecord {
  const { entries: _entries, ...value } = scenario;
  return value;
}

function refreshed(scenario: JsonRecord): JsonRecord {
  const entries = scenario.entries.map((item: JsonRecord) => ({
    ...item,
    gpa_points: item.grade ? points[item.grade as Grade] : null,
    status: item.grade === "FX" || item.grade === "F" ? "not_validated" : item.grade ? "validated" : "pending",
  }));
  return { ...scenario, entries, result: scenarioResult(entries) };
}

function fromPayload(current: JsonRecord, payload: JsonRecord): JsonRecord {
  const previous = new Map<string, JsonRecord>(current.entries.map((item: JsonRecord) => [String(item.id), item]));
  const entries = payload.entries.map((input: JsonRecord, index: number) => {
    const existing = input.id ? previous.get(input.id) : undefined;
    const id = String(input.id ?? `ue-gpa-e2e-ajoutee-${Date.now()}-${index}`);
    return {
      id,
      lineage_key: existing?.lineage_key ?? `lineage-${id}`,
      semester: input.semester,
      ue_code: input.ue_code,
      title: input.title ?? input.ue_code ?? "[FICTIF] UE libre",
      credits_ects: input.credits_ects,
      grade: input.grade,
      gpa_points: input.grade ? points[input.grade as Grade] : null,
      status: input.grade === "FX" || input.grade === "F" ? "not_validated" : input.grade ? "validated" : "pending",
      nature: existing ? "modified" : "simulated",
      source: existing?.source ?? null,
      baseline: existing?.baseline ?? null,
      created_at: existing?.created_at ?? now,
      updated_at: now,
    };
  });
  return refreshed({
    ...current,
    name: payload.name,
    version: Number(current.version) + 1,
    updated_at: now,
    entries,
  });
}

function compare(left: JsonRecord, right: JsonRecord) {
  const leftByLineage = new Map<string, JsonRecord>(
    left.entries.map((item: JsonRecord) => [String(item.lineage_key), item]),
  );
  const rightByLineage = new Map<string, JsonRecord>(
    right.entries.map((item: JsonRecord) => [String(item.lineage_key), item]),
  );
  const keys = new Set([...leftByLineage.keys(), ...rightByLineage.keys()]);
  const differences = [...keys]
    .map((key) => {
      const leftEntry = leftByLineage.get(key) ?? null;
      const rightEntry = rightByLineage.get(key) ?? null;
      if (!leftEntry)
        return { lineage_key: key, kind: "right_only", left: null, right: rightEntry, fields: ["presence"] };
      if (!rightEntry)
        return { lineage_key: key, kind: "left_only", left: leftEntry, right: null, fields: ["presence"] };
      const fields = ["semester", "ue", "credits_ects", "grade"].filter((field) => {
        if (field === "ue") return leftEntry.ue_code !== rightEntry.ue_code || leftEntry.title !== rightEntry.title;
        return leftEntry[field] !== rightEntry[field];
      });
      return fields.length ? { lineage_key: key, kind: "changed", left: leftEntry, right: rightEntry, fields } : null;
    })
    .filter(Boolean)
    .slice(0, 8);
  return {
    left: summary(left),
    right: summary(right),
    gpa_delta:
      left.result.gpa === null || right.result.gpa === null
        ? null
        : Math.round((right.result.gpa - left.result.gpa) * 100) / 100,
    differences,
    formula,
  };
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json; charset=utf-8",
    headers: {
      "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff",
    },
    body: JSON.stringify(body),
  });
}

export async function installFakeGpaSimulationsApi(
  page: Page,
  appState?: FakeAppState,
): Promise<FakeGpaSimulationState> {
  const primary = makeScenario(
    "scenario-gpa-fictif-principal",
    "[FICTIF] Projection GPA volumineuse",
    Array.from({ length: 24 }, (_, index) => entry(index)),
    { rebase_available: true },
  );
  const alternativeEntries = Array.from({ length: 8 }, (_, index) => {
    const value = entry(index);
    return index === 0 ? { ...value, grade: "F", gpa_points: 0, nature: "modified" } : value;
  });
  const alternative = makeScenario(
    "scenario-gpa-fictif-alternatif",
    "[FICTIF] Hypothèses alternatives",
    alternativeEntries,
  );
  const state: FakeGpaSimulationState = {
    csrfHeaders: [],
    scenarios: [primary, alternative],
    saveRequests: [],
    failNextSave: false,
    conflictNextSave: false,
    synthetic: true,
  };
  const originals = new Map(state.scenarios.map((item) => [String(item.id), structuredClone(item)]));

  const recordCsrf = (route: Route) => {
    const value = route.request().headers()["x-csrf-token"];
    state.csrfHeaders.push(value);
    appState?.csrfHeaders.push(value);
  };

  await page.route("**/api/v1/simulations**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const base = "/api/v1/simulations";

    if (url.pathname === base && method === "GET") {
      await json(route, {
        limit: 5,
        source: {
          revision: "source-fictive-v1",
          captured_at: now,
          ue_count: 24,
          graded_count: 22,
        },
        scenarios: state.scenarios.map(summary),
      });
      return;
    }

    if (url.pathname === `${base}/compare` && method === "GET") {
      const left = state.scenarios.find((item) => item.id === url.searchParams.get("left_id"));
      const right = state.scenarios.find((item) => item.id === url.searchParams.get("right_id"));
      if (!left || !right) {
        await json(route, { detail: { code: "RESOURCE_NOT_FOUND", message: "Scénario fictif introuvable." } }, 404);
        return;
      }
      await json(route, compare(left, right));
      return;
    }

    if (url.pathname === base && method === "POST") {
      recordCsrf(route);
      const body = request.postDataJSON() as JsonRecord;
      const imported = Boolean(body.import_current);
      const id = `scenario-gpa-fictif-cree-${state.scenarios.length + 1}`;
      const created = makeScenario(id, String(body.name), imported ? structuredClone(primary.entries) : [], {
        created_from: imported ? "academic" : "blank",
        source_revision: imported ? "source-fictive-v1" : null,
        source_captured_at: imported ? now : null,
      });
      state.scenarios.unshift(created);
      originals.set(id, structuredClone(created));
      await json(route, created, 201);
      return;
    }

    const segments = url.pathname.slice(`${base}/`.length).split("/").map(decodeURIComponent);
    const scenarioId = segments[0] ?? "";
    const index = state.scenarios.findIndex((item) => item.id === scenarioId);
    const current = index >= 0 ? state.scenarios[index] : undefined;
    if (!current) {
      await json(route, { detail: { code: "RESOURCE_NOT_FOUND", message: "Scénario fictif introuvable." } }, 404);
      return;
    }

    if (segments.length === 1 && method === "GET") {
      await json(route, current);
      return;
    }

    if (segments.length === 1 && method === "PUT") {
      recordCsrf(route);
      const body = request.postDataJSON() as JsonRecord;
      state.saveRequests.push(structuredClone(body));
      if (state.failNextSave) {
        state.failNextSave = false;
        await json(
          route,
          { detail: { code: "SERVICE_UNAVAILABLE", message: "Enregistrement fictif indisponible." } },
          503,
        );
        return;
      }
      if (state.conflictNextSave || Number(body.version) !== Number(current.version)) {
        state.conflictNextSave = false;
        await json(
          route,
          { detail: { code: "simulation_version_conflict", message: "Version fictive concurrente." } },
          409,
        );
        return;
      }
      const saved = fromPayload(current, body);
      state.scenarios[index] = saved;
      await json(route, saved);
      return;
    }

    if (segments.length === 1 && method === "DELETE") {
      recordCsrf(route);
      state.scenarios.splice(index, 1);
      await json(route, { ok: true });
      return;
    }

    if (segments[1] === "duplicate" && method === "POST") {
      recordCsrf(route);
      const body = request.postDataJSON() as JsonRecord;
      const duplicated = refreshed({
        ...structuredClone(current),
        id: `scenario-gpa-fictif-copie-${Date.now()}`,
        name: body.name,
        version: 1,
        created_at: now,
        updated_at: now,
      });
      state.scenarios.unshift(duplicated);
      await json(route, duplicated, 201);
      return;
    }

    if (segments[1] === "reset" && method === "POST") {
      recordCsrf(route);
      const reset = {
        ...structuredClone(originals.get(scenarioId) ?? current),
        version: Number(current.version) + 1,
        updated_at: now,
      };
      state.scenarios[index] = reset;
      await json(route, reset);
      return;
    }

    if (segments[1] === "rebase" && method === "POST") {
      recordCsrf(route);
      const rebased = refreshed({
        ...current,
        version: Number(current.version) + 1,
        rebase_available: false,
        updated_at: now,
      });
      state.scenarios[index] = rebased;
      await json(route, rebased);
      return;
    }

    if (segments[1] === "entries" && segments[3] === "resolve" && method === "POST") {
      recordCsrf(route);
      const body = request.postDataJSON() as JsonRecord;
      const entries = current.entries.map((item: JsonRecord) => {
        if (item.id !== segments[2]) return item;
        const useSource = body.resolution === "source" && item.baseline;
        return {
          ...item,
          ...(useSource
            ? {
                semester: item.baseline.semester,
                ue_code: item.baseline.ue_code,
                title: item.baseline.title,
                credits_ects: item.baseline.credits_ects,
                grade: item.baseline.grade,
              }
            : {}),
          source: { ...item.source, status: "current" },
        };
      });
      const resolved = refreshed({ ...current, version: Number(current.version) + 1, entries });
      state.scenarios[index] = resolved;
      await json(route, resolved);
      return;
    }

    await json(
      route,
      { detail: { code: "RESOURCE_NOT_FOUND", message: "Route de simulation fictive non configurée." } },
      404,
    );
  });

  return state;
}
