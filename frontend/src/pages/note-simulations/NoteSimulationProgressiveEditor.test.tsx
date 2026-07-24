// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { NoteSimulationAssessmentDraft, NoteSimulationUeDraft } from "../../lib/noteSimulations";
import { NoteSimulationAssessmentEditor } from "./NoteSimulationAssessmentEditor";
import { NoteSimulationUeEditor } from "./NoteSimulationUeEditor";
import { NoteSimulationUeList } from "./NoteSimulationUeList";
import { initialOpenUes } from "./noteSimulationPresentation";

function assessment(ueIndex: number, assessmentIndex: number): NoteSimulationAssessmentDraft {
  return {
    clientKey: `assessment-fictif-${ueIndex}-${assessmentIndex}`,
    id: `assessment-fictif-${ueIndex}-${assessmentIndex}`,
    label: `[FICTIF] Évaluation ${ueIndex + 1}.${assessmentIndex + 1}`,
    score: assessmentIndex === 2 ? "" : String(12 + assessmentIndex),
    coefficient: String(assessmentIndex + 1),
    is_resit: assessmentIndex === 2,
    server: null,
  };
}

function ue(index: number): NoteSimulationUeDraft {
  return {
    clientKey: `ue-fictive-${index}`,
    id: `ue-fictive-${index}`,
    semester: index % 2 === 0 ? "S5" : "S6",
    ue_code: `FIC${String(index + 1).padStart(3, "0")}`,
    title: `[FICTIF] Unité ${index + 1}`,
    credits_ects: "3",
    assessments: Array.from({ length: 3 }, (_, assessmentIndex) => assessment(index, assessmentIndex)),
    server: null,
  };
}

function renderList(ues: NoteSimulationUeDraft[], openUes = new Set<string>()) {
  return render(
    <NoteSimulationUeList
      ues={ues}
      openUes={openUes}
      compact
      disabled={false}
      emptyTitle="[FICTIF] Vide"
      emptyDetail="[FICTIF] Aucun élément"
      onToggle={vi.fn()}
      onCollapseAll={vi.fn()}
      onEditUe={vi.fn()}
      onEditAssessment={vi.fn()}
      onAddAssessment={vi.fn()}
      onResolveUe={vi.fn()}
      onResolveAssessment={vi.fn()}
    />,
  );
}

afterEach(() => cleanup());

describe("éditeur progressif de simulations de notes", () => {
  it("ne monte aucun formulaire d’évaluation dans l’état initial volumineux", () => {
    const ues = Array.from({ length: 20 }, (_, index) => ue(index));
    const { container } = renderList(ues);

    expect(screen.getAllByRole("button", { name: /Modifier \[FICTIF\] Unité/ })).toHaveLength(20);
    expect(container.querySelectorAll("input")).toHaveLength(0);
    expect(screen.queryByText("[FICTIF] Évaluation 1.1")).toBeNull();
    expect(container.querySelectorAll("button")).toHaveLength(20);
  });

  it("monte uniquement les résumés de l’UE ouverte", () => {
    const ues = Array.from({ length: 20 }, (_, index) => ue(index));
    const { container } = renderList(ues, new Set([ues[0]!.clientKey]));

    expect(screen.getByText("[FICTIF] Évaluation 1.1")).toBeTruthy();
    expect(screen.getByText("[FICTIF] Évaluation 1.3")).toBeTruthy();
    expect(screen.queryByText("[FICTIF] Évaluation 2.1")).toBeNull();
    expect(container.querySelectorAll("input")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Ajouter une évaluation" })).toBeTruthy();
  });

  it("n’ouvre automatiquement que le premier conflit en mode compact", () => {
    const ues = [ue(0), ue(1), ue(2)];
    ues[0]!.server = {
      id: ues[0]!.id!,
      lineage_key: "lineage-fictive-0",
      semester: "S5",
      ue_code: ues[0]!.ue_code,
      title: ues[0]!.title,
      credits_ects: 3,
      nature: "imported",
      projection: {
        average: 13,
        grade: "C",
        gpa_points: 3.5,
        used_resit: false,
        coefficient_total: 3,
        assessment_count: 3,
        scored_count: 2,
        pending_count: 1,
      },
      source: {
        ue_code: ues[0]!.ue_code,
        status: "conflict",
        observed_at: "2026-07-20T10:00:00Z",
      },
      baseline: null,
      assessments: [],
      created_at: "2026-07-20T10:00:00Z",
      updated_at: "2026-07-20T10:00:00Z",
    };
    ues[1]!.server = {
      ...ues[0]!.server,
      id: ues[1]!.id!,
      lineage_key: "lineage-fictive-1",
      ue_code: ues[1]!.ue_code,
      title: ues[1]!.title,
    };

    expect([...initialOpenUes(ues, true)]).toEqual([ues[0]!.clientKey]);
    expect([...initialOpenUes(ues, false)]).toEqual([ues[0]!.clientKey, ues[1]!.clientKey]);
  });

  it("annule un ajout d’évaluation sans créer de ligne", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    const onClose = vi.fn();
    render(
      <NoteSimulationAssessmentEditor
        open
        assessment={null}
        ueName="[FICTIF] Unité"
        onClose={onClose}
        onSave={onSave}
      />,
    );

    await user.type(screen.getByRole("textbox", { name: /^Nom de l’évaluation/ }), "Hypothèse fictive");
    await user.click(screen.getByRole("button", { name: "Annuler" }));

    expect(onClose).toHaveBeenCalledOnce();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("applique en une fois le formulaire temporaire d’une évaluation", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <NoteSimulationAssessmentEditor
        open
        assessment={null}
        ueName="[FICTIF] Unité"
        onClose={vi.fn()}
        onSave={onSave}
      />,
    );

    const label = screen.getByRole("textbox", { name: /^Nom de l’évaluation/ });
    const score = screen.getByRole("spinbutton", { name: "Note sur 20" });
    const coefficient = screen.getByRole("spinbutton", { name: "Coefficient" });
    await user.type(label, "Contrôle fictif");
    await user.type(score, "15");
    await user.click(screen.getByRole("switch", { name: /Rattrapage/ }));
    expect((label as HTMLInputElement).value).toBe("Contrôle fictif");
    expect((score as HTMLInputElement).value).toBe("15");
    expect((coefficient as HTMLInputElement).value).toBe("1");
    const submit = screen.getByRole("button", { name: "Ajouter" });
    expect((submit as HTMLButtonElement).disabled).toBe(false);
    fireEvent.submit(submit.closest("form")!);

    expect(onSave).toHaveBeenCalledOnce();
    expect(onSave.mock.calls[0]?.[0]).toMatchObject({
      label: "Contrôle fictif",
      score: "15",
      coefficient: "1",
      is_resit: true,
    });
  });

  it("annule la création d’une UE sans modifier le draft parent", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    const onClose = vi.fn();
    render(<NoteSimulationUeEditor open ue={null} defaultSemester="S7" onClose={onClose} onSave={onSave} />);

    await user.type(screen.getByRole("textbox", { name: "Intitulé de l’UE" }), "[FICTIF] Nouvelle UE");
    await user.click(screen.getByRole("button", { name: "Annuler" }));

    expect(onClose).toHaveBeenCalledOnce();
    expect(onSave).not.toHaveBeenCalled();
  });
});
