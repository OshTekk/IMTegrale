import type { Page, Route } from "@playwright/test";
import type { FakeAppState } from "./app-fixtures";

type JsonRecord = Record<string, unknown>;

export interface FakeNoteSimulationState {
  csrfHeaders: Array<string | undefined>;
  scenarios: JsonRecord[];
  saveRequests: JsonRecord[];
  failNextSave: boolean;
  conflictNextSave: boolean;
  synthetic: true;
}

const now = "2026-02-10T14:00:00Z";
const formula = {
  version: "fictive-v1",
  label: "Règle fictive de démonstration",
  scale: "0–20 puis 0–4",
  rounding: "Au centième",
  scope: "Simulation privée fictive",
  ue_expression: "Somme pondérée fictive",
  average_expression: "Moyenne pondérée fictive",
  gpa_expression: "GPA pondéré fictif",
  official: false,
};

function gradeForAverage(average: number | null, usedResit: boolean) {
  if (average === null) return null;
  if (usedResit && average >= 10) return "E";
  if (average >= 17) return "A";
  if (average >= 14) return "B";
  if (average >= 12) return "C";
  if (average >= 10) return "D";
  if (average >= 5) return "FX";
  return "F";
}

function gpaForGrade(grade: string | null) {
  if (grade === "A") return 4;
  if (grade === "B") return 3.8;
  if (grade === "C") return 3.5;
  if (grade === "D") return 3;
  if (grade === "E") return 2.5;
  return grade ? 0 : null;
}

function ueProjection(ue: JsonRecord) {
  const assessments = ue.assessments as JsonRecord[];
  const scored = assessments.filter((assessment) => assessment.score !== null);
  const resits = scored.filter((assessment) => assessment.is_resit);
  const normal = scored.filter((assessment) => !assessment.is_resit);
  let average: number | null = null;
  let coefficientTotal = 0;
  if (resits.length) {
    const latest = resits.at(-1)!;
    average = Number(latest.score);
    coefficientTotal = Number(latest.coefficient);
  } else if (normal.length) {
    const weighted = normal.reduce(
      (total, assessment) => total + Number(assessment.score) * Number(assessment.coefficient),
      0,
    );
    coefficientTotal = normal.reduce((total, assessment) => total + Number(assessment.coefficient), 0);
    average = coefficientTotal ? weighted / coefficientTotal : null;
  }
  const roundedAverage = average === null ? null : Math.round((average + Number.EPSILON) * 100) / 100;
  const grade = gradeForAverage(roundedAverage, Boolean(resits.length));
  return {
    average: roundedAverage,
    grade,
    gpa_points: gpaForGrade(grade),
    used_resit: Boolean(resits.length),
    coefficient_total: coefficientTotal,
    assessment_count: assessments.length,
    scored_count: scored.length,
    pending_count: assessments.length - scored.length,
  };
}

function scenarioResult(ues: JsonRecord[]) {
  const projected = ues.map((ue) => ({ ue, projection: ueProjection(ue) }));
  const included = projected.filter(
    ({ ue, projection }) =>
      projection.average !== null && typeof ue.credits_ects === "number" && Number(ue.credits_ects) > 0,
  );
  const creditsIncluded = included.reduce((total, { ue }) => total + Number(ue.credits_ects), 0);
  const average = creditsIncluded
    ? included.reduce((total, { ue, projection }) => total + Number(projection.average) * Number(ue.credits_ects), 0) /
      creditsIncluded
    : null;
  const gpa = creditsIncluded
    ? included.reduce(
        (total, { ue, projection }) => total + Number(projection.gpa_points ?? 0) * Number(ue.credits_ects),
        0,
      ) / creditsIncluded
    : null;
  const semesters = ["S5", "S6", "S7", "S8", "S9", "S10"]
    .filter((semester) => ues.some((ue) => ue.semester === semester))
    .map((semester) => {
      const scoped = ues.filter((ue) => ue.semester === semester);
      const scopedResult = scenarioResultWithoutSemesters(scoped);
      return { semester, ...scopedResult };
    });
  const assessmentCount = projected.reduce((total, item) => total + item.projection.assessment_count, 0);
  const scoredCount = projected.reduce((total, item) => total + item.projection.scored_count, 0);
  const pendingCount = assessmentCount - scoredCount;
  const missingEctsCount = projected.filter(
    ({ ue, projection }) => projection.average !== null && typeof ue.credits_ects !== "number",
  ).length;
  return {
    status: !ues.length ? "empty" : pendingCount ? "partial" : "ready",
    average: average === null ? null : Math.round(average * 100) / 100,
    gpa: gpa === null ? null : Math.round(gpa * 100) / 100,
    credits_entered: ues.reduce(
      (total, ue) => total + (typeof ue.credits_ects === "number" ? Number(ue.credits_ects) : 0),
      0,
    ),
    credits_included: creditsIncluded,
    ue_count: ues.length,
    calculated_ue_count: projected.filter((item) => item.projection.average !== null).length,
    assessment_count: assessmentCount,
    scored_count: scoredCount,
    pending_count: pendingCount,
    missing_ects_count: missingEctsCount,
    completion_rate: assessmentCount ? Math.round((scoredCount / assessmentCount) * 100) : 0,
    semesters,
    warnings: [
      ...(pendingCount
        ? [
            {
              code: "pending_assessments",
              count: pendingCount,
              message: "Des notes fictives restent en attente.",
            },
          ]
        : []),
      ...(missingEctsCount
        ? [
            {
              code: "missing_ects",
              count: missingEctsCount,
              message: "Des ECTS fictifs sont manquants.",
            },
          ]
        : []),
    ],
    formula,
  };
}

function scenarioResultWithoutSemesters(ues: JsonRecord[]) {
  const result = scenarioResultBase(ues);
  return {
    average: result.average,
    gpa: result.gpa,
    credits_included: result.credits_included,
    ue_count: result.ue_count,
    calculated_ue_count: result.calculated_ue_count,
    assessment_count: result.assessment_count,
    scored_count: result.scored_count,
    pending_count: result.pending_count,
  };
}

function scenarioResultBase(ues: JsonRecord[]) {
  const projected = ues.map((ue) => ({ ue, projection: ueProjection(ue) }));
  const included = projected.filter(
    ({ ue, projection }) =>
      projection.average !== null && typeof ue.credits_ects === "number" && Number(ue.credits_ects) > 0,
  );
  const creditsIncluded = included.reduce((total, { ue }) => total + Number(ue.credits_ects), 0);
  return {
    average: creditsIncluded
      ? Math.round(
          (included.reduce(
            (total, { ue, projection }) => total + Number(projection.average) * Number(ue.credits_ects),
            0,
          ) /
            creditsIncluded) *
            100,
        ) / 100
      : null,
    gpa: creditsIncluded
      ? Math.round(
          (included.reduce(
            (total, { ue, projection }) => total + Number(projection.gpa_points ?? 0) * Number(ue.credits_ects),
            0,
          ) /
            creditsIncluded) *
            100,
        ) / 100
      : null,
    credits_included: creditsIncluded,
    ue_count: ues.length,
    calculated_ue_count: projected.filter((item) => item.projection.average !== null).length,
    assessment_count: projected.reduce((total, item) => total + item.projection.assessment_count, 0),
    scored_count: projected.reduce((total, item) => total + item.projection.scored_count, 0),
    pending_count: projected.reduce((total, item) => total + item.projection.pending_count, 0),
  };
}

function assessment(ueIndex: number, index: number, status: "current" | "conflict" | "unavailable" = "current") {
  const id = `assessment-e2e-fictif-${ueIndex}-${index}`;
  const score = index === 2 ? null : 11 + ((ueIndex + index) % 7);
  return {
    id,
    lineage_key: `lineage-${id}`,
    label: `[FICTIF] Évaluation ${ueIndex + 1}.${index + 1}`,
    score,
    coefficient: index + 1,
    is_resit: false,
    nature: index === 1 ? "modified" : "imported",
    source: {
      note_key: `note-fictive-${ueIndex}-${index}`,
      status,
      observed_at: now,
    },
    baseline: {
      label: `[FICTIF] Évaluation ${ueIndex + 1}.${index + 1}`,
      score: index === 1 && score !== null ? score - 1 : score,
      coefficient: index + 1,
      is_resit: false,
    },
    created_at: now,
    updated_at: now,
  };
}

function ue(
  index: number,
  options: {
    ueStatus?: "current" | "conflict" | "unavailable";
    assessmentConflict?: boolean;
  } = {},
) {
  const id = `ue-e2e-fictive-${index}`;
  const assessments = Array.from({ length: 3 }, (_, assessmentIndex) =>
    assessment(index, assessmentIndex, options.assessmentConflict && assessmentIndex === 1 ? "conflict" : "current"),
  );
  const value: JsonRecord = {
    id,
    lineage_key: `lineage-${id}`,
    semester: ["S5", "S6", "S7", "S8"][index % 4],
    ue_code: `FIC${String(index + 1).padStart(3, "0")}`,
    title: `[FICTIF] Unité d’enseignement ${index + 1}`,
    credits_ects: 3,
    nature: index % 3 === 0 ? "modified" : "imported",
    source: {
      ue_code: `FIC${String(index + 1).padStart(3, "0")}`,
      status: options.ueStatus ?? "current",
      observed_at: now,
    },
    baseline: {
      semester: ["S5", "S6", "S7", "S8"][index % 4],
      ue_code: `FIC${String(index + 1).padStart(3, "0")}`,
      title: `[FICTIF] Unité d’enseignement ${index + 1}`,
      credits_ects: 3,
    },
    assessments,
    created_at: now,
    updated_at: now,
  };
  value.projection = ueProjection(value);
  return value;
}

function makeScenario(id: string, name: string, ues: JsonRecord[], overrides: JsonRecord = {}) {
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
    result: scenarioResult(ues),
    ues,
    ...overrides,
  };
}

function summary(scenario: JsonRecord) {
  const { ues: _ues, ...value } = scenario;
  return value;
}

function refreshScenario(scenario: JsonRecord) {
  const ues = (scenario.ues as JsonRecord[]).map((item) => ({
    ...item,
    projection: ueProjection(item),
  }));
  return { ...scenario, ues, result: scenarioResult(ues) };
}

function scenarioFromPayload(current: JsonRecord, payload: JsonRecord): JsonRecord {
  const previousUes = new Map((current.ues as JsonRecord[]).map((item) => [item.id, item]));
  const ues = (payload.ues as JsonRecord[]).map((input, ueIndex) => {
    const existing = input.id ? previousUes.get(input.id) : undefined;
    const id = String(input.id ?? `ue-e2e-ajoutee-${Date.now()}-${ueIndex}`);
    const previousAssessments = new Map(
      ((existing?.assessments as JsonRecord[] | undefined) ?? []).map((item) => [item.id, item]),
    );
    const assessments = (input.assessments as JsonRecord[]).map((assessmentInput, assessmentIndex) => {
      const previous = assessmentInput.id ? previousAssessments.get(assessmentInput.id) : undefined;
      const assessmentId = String(
        assessmentInput.id ?? `assessment-e2e-ajoutee-${Date.now()}-${ueIndex}-${assessmentIndex}`,
      );
      return {
        id: assessmentId,
        lineage_key: previous?.lineage_key ?? `lineage-${assessmentId}`,
        label: assessmentInput.label,
        score: assessmentInput.score,
        coefficient: assessmentInput.coefficient,
        is_resit: assessmentInput.is_resit,
        nature: previous ? "modified" : "simulated",
        source: previous?.source ?? null,
        baseline: previous?.baseline ?? null,
        created_at: previous?.created_at ?? now,
        updated_at: now,
      };
    });
    const value: JsonRecord = {
      id,
      lineage_key: existing?.lineage_key ?? `lineage-${id}`,
      semester: input.semester,
      ue_code: input.ue_code,
      title: input.title ?? input.ue_code ?? "[FICTIF] UE sans titre",
      credits_ects: input.credits_ects,
      nature: existing ? "modified" : "simulated",
      source: existing?.source ?? null,
      baseline: existing?.baseline ?? null,
      assessments,
      created_at: existing?.created_at ?? now,
      updated_at: now,
    };
    value.projection = ueProjection(value);
    return value;
  });
  return refreshScenario({
    ...current,
    name: payload.name,
    version: Number(current.version) + 1,
    updated_at: now,
    ues,
  });
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

export async function installFakeNoteSimulationsApi(
  page: Page,
  appState?: FakeAppState,
): Promise<FakeNoteSimulationState> {
  const primary = makeScenario(
    "scenario-notes-fictif-principal",
    "[FICTIF] Projection volumineuse",
    Array.from({ length: 20 }, (_, index) =>
      ue(index, {
        ueStatus: index === 3 ? "unavailable" : "current",
      }),
    ),
  );
  const conflict = makeScenario("scenario-notes-fictif-conflit", "[FICTIF] Projection avec conflits", [
    ue(30, { ueStatus: "conflict", assessmentConflict: true }),
    ue(31),
  ]);
  const state: FakeNoteSimulationState = {
    csrfHeaders: [],
    scenarios: [primary, conflict],
    saveRequests: [],
    failNextSave: false,
    conflictNextSave: false,
    synthetic: true,
  };
  const originals = new Map(state.scenarios.map((scenario) => [String(scenario.id), structuredClone(scenario)]));

  const recordCsrf = (route: Route) => {
    const value = route.request().headers()["x-csrf-token"];
    state.csrfHeaders.push(value);
    appState?.csrfHeaders.push(value);
  };

  await page.route("**/api/v1/note-simulations**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const base = "/api/v1/note-simulations";

    if (url.pathname === base && method === "GET") {
      await json(route, {
        limit: 5,
        source: {
          revision: "source-fictive-v1",
          captured_at: now,
          ue_count: 20,
          assessment_count: 60,
          scored_count: 40,
        },
        scenarios: state.scenarios.map(summary),
      });
      return;
    }

    if (url.pathname === `${base}/compare` && method === "GET") {
      const left = state.scenarios.find((item) => item.id === url.searchParams.get("left_id"));
      const right = state.scenarios.find((item) => item.id === url.searchParams.get("right_id"));
      if (!left || !right) {
        await json(
          route,
          {
            detail: {
              code: "RESOURCE_NOT_FOUND",
              message: "Scénario fictif introuvable.",
            },
          },
          404,
        );
        return;
      }
      await json(route, {
        left: summary(left),
        right: summary(right),
        average_delta: Number((right.result as JsonRecord).average) - Number((left.result as JsonRecord).average),
        gpa_delta: Number((right.result as JsonRecord).gpa) - Number((left.result as JsonRecord).gpa),
        differences: [
          {
            lineage_key: "difference-fictive",
            kind: "changed",
            left: (left.ues as JsonRecord[])[0] ?? null,
            right: (right.ues as JsonRecord[])[0] ?? null,
            fields: ["assessments"],
          },
        ],
        formula,
      });
      return;
    }

    if (url.pathname === base && method === "POST") {
      recordCsrf(route);
      const body = request.postDataJSON() as JsonRecord;
      const id = `scenario-notes-fictif-cree-${state.scenarios.length + 1}`;
      const imported = Boolean(body.import_current);
      const created = makeScenario(
        id,
        String(body.name),
        imported ? structuredClone(primary.ues as JsonRecord[]) : [],
        {
          created_from: imported ? "academic" : "blank",
          source_revision: imported ? "source-fictive-v1" : null,
          source_captured_at: imported ? now : null,
        },
      );
      state.scenarios.unshift(created);
      originals.set(id, structuredClone(created));
      await json(route, created, 201);
      return;
    }

    const relative = url.pathname.slice(`${base}/`.length);
    const segments = relative.split("/").map(decodeURIComponent);
    const scenarioId = segments[0] ?? "";
    const index = state.scenarios.findIndex((item) => item.id === scenarioId);
    const current = index >= 0 ? state.scenarios[index] : undefined;
    if (!current) {
      await json(
        route,
        {
          detail: {
            code: "RESOURCE_NOT_FOUND",
            message: "Scénario fictif introuvable.",
          },
        },
        404,
      );
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
          {
            detail: {
              code: "SERVICE_UNAVAILABLE",
              message: "Enregistrement fictif indisponible.",
            },
          },
          503,
        );
        return;
      }
      if (state.conflictNextSave || Number(body.version) !== Number(current.version)) {
        state.conflictNextSave = false;
        await json(
          route,
          {
            detail: {
              code: "simulation_version_conflict",
              message: "Version fictive concurrente.",
            },
          },
          409,
        );
        return;
      }
      const saved = scenarioFromPayload(current, body);
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
      const duplicated = refreshScenario({
        ...structuredClone(current),
        id: `scenario-notes-fictif-copie-${Date.now()}`,
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
      const rebased = refreshScenario({
        ...current,
        version: Number(current.version) + 1,
        rebase_available: false,
        updated_at: now,
      });
      state.scenarios[index] = rebased;
      await json(route, rebased);
      return;
    }

    if (segments[1] === "ues" && segments[3] === "resolve" && method === "POST") {
      recordCsrf(route);
      const ues = (current.ues as JsonRecord[]).map((item) =>
        item.id === segments[2]
          ? {
              ...item,
              source: {
                ...(item.source as JsonRecord),
                status: "current",
              },
            }
          : item,
      );
      const resolved = refreshScenario({
        ...current,
        version: Number(current.version) + 1,
        ues,
      });
      state.scenarios[index] = resolved;
      await json(route, resolved);
      return;
    }

    if (segments[1] === "assessments" && segments[3] === "resolve" && method === "POST") {
      recordCsrf(route);
      const ues = (current.ues as JsonRecord[]).map((item) => ({
        ...item,
        assessments: (item.assessments as JsonRecord[]).map((assessment) =>
          assessment.id === segments[2]
            ? {
                ...assessment,
                source: {
                  ...(assessment.source as JsonRecord),
                  status: "current",
                },
              }
            : assessment,
        ),
      }));
      const resolved = refreshScenario({
        ...current,
        version: Number(current.version) + 1,
        ues,
      });
      state.scenarios[index] = resolved;
      await json(route, resolved);
      return;
    }

    await json(
      route,
      {
        detail: {
          code: "RESOURCE_NOT_FOUND",
          message: "Route de simulation fictive non configurée.",
        },
      },
      404,
    );
  });

  return state;
}
