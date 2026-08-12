// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SimulationSaveState } from "../../components/simulations/SimulationSaveIndicator";
import { calculateDraftProjection, type SimulationDraftEntry } from "../../lib/simulations";
import type { SimulationEntry, SimulationScenarioSummary } from "../../types";
import { GpaSimulationFormula } from "./GpaSimulationFormula";
import { GpaSimulationHeader } from "./GpaSimulationHeader";
import { GpaSimulationScenarioSelector } from "./GpaSimulationScenarioSelector";
import { GpaSimulationSemesterFilter } from "./GpaSimulationSemesterFilter";
import { GpaSimulationSummary } from "./GpaSimulationSummary";
import { GpaSimulationUeEditor } from "./GpaSimulationUeEditor";
import { GpaSimulationUeList } from "./GpaSimulationUeList";
import {
  createGpaFocusIntentController,
  GPA_FOCUS_INVALIDATION_EVENT,
  restoreCurrentGpaFocus,
} from "./gpaSimulationFocus";

const NOW = "2026-07-20T10:00:00Z";
type SourceStatus = NonNullable<SimulationEntry["source"]>["status"];

function serverEntry(index: number, sourceStatus: SourceStatus = "current"): SimulationEntry {
  const semester = `S${5 + (index % 6)}` as SimulationEntry["semester"];
  const grade = (["A", "B", "C", "D", "E", "FX", "F"] as const)[index % 7]!;
  return {
    id: `ue-fictive-${index}`,
    lineage_key: `lineage-fictive-${index}`,
    semester,
    ue_code: `FIC${String(index + 1).padStart(3, "0")}`,
    title:
      index === 4
        ? "[FICTIF] Unité avec un intitulé volontairement très long pour éprouver la présentation"
        : `[FICTIF] Unité ${index + 1}`,
    credits_ects: index === 7 ? null : 1 + (index % 6),
    grade: index === 8 ? null : grade,
    gpa_points: null,
    status: grade === "FX" || grade === "F" ? "not_validated" : "validated",
    nature: index % 3 === 0 ? "modified" : "imported",
    source: {
      ue_code: `FIC${String(index + 1).padStart(3, "0")}`,
      status: sourceStatus,
      grade_source: "competences",
      observed_at: NOW,
    },
    baseline: {
      semester,
      ue_code: `FIC${String(index + 1).padStart(3, "0")}`,
      title: `[FICTIF] Unité ${index + 1}`,
      credits_ects: 1 + (index % 6),
      grade,
    },
    created_at: NOW,
    updated_at: NOW,
  };
}

function draftEntry(index: number, sourceStatus: SourceStatus = "current"): SimulationDraftEntry {
  const server = serverEntry(index, sourceStatus);
  return {
    clientKey: server.id,
    id: server.id,
    semester: server.semester,
    ue_code: server.ue_code ?? "",
    title: server.title,
    credits_ects: server.credits_ects === null ? "" : String(server.credits_ects),
    grade: server.grade,
    server,
  };
}

function scenario(index: number): SimulationScenarioSummary {
  return {
    id: `scenario-fictif-${index}`,
    name: `[FICTIF] Projection ${index + 1}`,
    created_from: index ? "blank" : "academic",
    formula_version: "fictive-v1",
    version: 2,
    source_revision: "revision-fictive",
    source_captured_at: NOW,
    rebase_available: false,
    created_at: NOW,
    updated_at: NOW,
    result: {
      status: "ready",
      gpa: 3.42 - index * 0.1,
      credits_entered: 60,
      credits_included: 60,
      ue_count: 24,
      graded_count: 22,
      pending_count: 2,
      missing_ects_count: 1,
      completion_rate: 92,
      semesters: [],
      warnings: [],
      formula: {
        version: "fictive-v1",
        label: "Formule fictive",
        scale: "0-4",
        rounding: "centième",
        scope: "simulation",
        expression: "somme pondérée",
        official: false,
      },
    },
  };
}

afterEach(() => cleanup());

describe("éditeur progressif de simulation GPA", () => {
  it("présente 24 UE sans monter leurs formulaires dans l’état initial", () => {
    const entries = Array.from({ length: 24 }, (_, index) => draftEntry(index));
    const { container } = render(
      <GpaSimulationUeList
        entries={entries}
        selectedKey={null}
        compact
        disabled={false}
        emptyTitle="[FICTIF] Vide"
        emptyDetail="[FICTIF] Aucun élément"
        onOpen={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("button", { name: /Modifier \[FICTIF\] Unité/ })).toHaveLength(24);
    expect(container.querySelectorAll("input, select")).toHaveLength(0);
    expect(container.querySelectorAll("button, input, select, textarea, a[href]")).toHaveLength(24);
  });

  it("ouvre une seule copie temporaire et annule sans modifier le parent", async () => {
    const user = userEvent.setup();
    const entry = draftEntry(0);
    const onApply = vi.fn();
    const onClose = vi.fn();
    render(
      <GpaSimulationUeEditor
        open
        compact
        entry={entry}
        defaultSemester="S5"
        disabled={false}
        conflictPending={false}
        onClose={onClose}
        onApply={onApply}
        onDelete={vi.fn()}
        onResolve={vi.fn()}
      />,
    );

    const title = screen.getByRole("textbox", { name: "Intitulé de l’UE" });
    await user.clear(title);
    await user.type(title, "[FICTIF] Hypothèse temporaire");
    await user.click(screen.getByRole("button", { name: "Annuler" }));

    expect(onClose).toHaveBeenCalledOnce();
    expect(onApply).not.toHaveBeenCalled();
    expect(entry.title).toBe("[FICTIF] Unité 1");
  });

  it("applique code, ECTS et grade en une seule fois", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(
      <GpaSimulationUeEditor
        open
        compact
        entry={null}
        defaultSemester="S7"
        disabled={false}
        conflictPending={false}
        onClose={vi.fn()}
        onApply={onApply}
        onResolve={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "Code UE" }), { target: { value: "fic999" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Crédits ECTS" }), { target: { value: "6" } });
    await user.selectOptions(screen.getByRole("combobox", { name: "Grade potentiel" }), "B");
    fireEvent.submit(screen.getByRole("button", { name: "Ajouter l’UE" }).closest("form")!);

    expect(onApply).toHaveBeenCalledOnce();
    expect(onApply.mock.calls[0]?.[0]).toMatchObject({
      semester: "S7",
      ue_code: "FIC999",
      credits_ects: "6",
      grade: "B",
    });
  });

  it("garde l’éditeur ouvert lorsque les ECTS sont invalides", async () => {
    render(
      <GpaSimulationUeEditor
        open
        compact
        entry={draftEntry(1)}
        defaultSemester={null}
        disabled={false}
        conflictPending={false}
        onClose={vi.fn()}
        onApply={vi.fn()}
        onDelete={vi.fn()}
        onResolve={vi.fn()}
      />,
    );
    const credits = screen.getByRole("spinbutton", { name: "Crédits ECTS" });
    fireEvent.change(credits, { target: { value: "61" } });

    expect(credits.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText("Les crédits doivent être compris entre 0,01 et 60.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Appliquer" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("confirme explicitement la suppression sans toucher à la source", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    const entry = draftEntry(2);
    render(
      <GpaSimulationUeEditor
        open
        compact
        entry={entry}
        defaultSemester={null}
        disabled={false}
        conflictPending={false}
        onClose={vi.fn()}
        onApply={vi.fn()}
        onDelete={onDelete}
        onResolve={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Supprimer cette UE" }));
    expect(screen.getByText(/Les données officielles restent inchangées/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Confirmer la suppression" }));
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ clientKey: entry.clientKey }));
  });

  it("rend les conflits et les sources indisponibles visibles depuis les cartes fermées", () => {
    render(
      <GpaSimulationUeList
        entries={[draftEntry(0, "conflict"), draftEntry(1, "unavailable")]}
        selectedKey={null}
        compact
        disabled={false}
        emptyTitle="[FICTIF] Vide"
        emptyDetail="[FICTIF] Aucun élément"
        onOpen={vi.fn()}
      />,
    );
    expect(screen.getByText("Conflit à résoudre")).toBeTruthy();
    expect(screen.getByText("Source indisponible")).toBeTruthy();
  });

  it("met la projection locale à jour sans attendre le réseau", () => {
    const entries = [draftEntry(0), draftEntry(1)];
    const projection = calculateDraftProjection(entries);
    const { rerender } = render(<GpaSimulationSummary global={projection} selected={projection} semester="all" />);
    expect(screen.getByText("GPA global simulé")).toBeTruthy();

    const modified = [{ ...entries[0]!, grade: "F" as const }, entries[1]!];
    const next = calculateDraftProjection(modified);
    rerender(<GpaSimulationSummary global={next} selected={next} semester="all" />);
    expect(screen.getAllByText(formatFrench(next.gpa)).length).toBeGreaterThan(0);
  });

  it("filtre les semestres avec des cibles tactiles et propose l’ajout", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onAdd = vi.fn();
    render(
      <GpaSimulationSemesterFilter
        semester="all"
        semesters={["S5", "S6", "S7"]}
        compact
        visibleCount={24}
        disabled={false}
        limitReached={false}
        onChange={onChange}
        onAdd={onAdd}
      />,
    );
    await user.selectOptions(screen.getByRole("combobox", { name: "Semestre" }), "S7");
    await user.click(screen.getByRole("button", { name: "Ajouter une UE" }));
    expect(onChange).toHaveBeenCalledWith("S7");
    expect(onAdd).toHaveBeenCalledOnce();
  });

  it("ferme le menu d’actions avec Échap et restaure son déclencheur", async () => {
    const user = userEvent.setup();
    const header = (index: number) => (
      <StrictMode>
        <GpaSimulationHeader
          scenario={scenario(index)}
          name="[FICTIF] Projection"
          saveState={"saved" satisfies SimulationSaveState}
          valid
          canCompare
          actionPending={false}
          onNameChange={vi.fn()}
          onCompare={vi.fn()}
          onDuplicate={vi.fn()}
          onConfirm={vi.fn()}
        />
      </StrictMode>
    );
    const { rerender } = render(header(0));
    const trigger = screen.getByRole("button", { name: "Actions sur la simulation" });
    const focusAfterMenuUnmount: boolean[] = [];
    const invalidations = vi.fn();
    document.addEventListener(GPA_FOCUS_INVALIDATION_EVENT, invalidations);
    vi.spyOn(trigger, "focus").mockImplementation((options) => {
      focusAfterMenuUnmount.push(screen.queryByRole("menu") === null);
      HTMLElement.prototype.focus.call(trigger, options);
    });
    for (let cycle = 0; cycle < 2; cycle += 1) {
      trigger.focus();
      await user.keyboard("{Enter}");
      expect(screen.getByRole("menu")).toBeTruthy();
      expect(trigger.getAttribute("aria-expanded")).toBe("true");
      screen.getAllByRole("menuitem")[0]!.focus();
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("menu")).toBeNull();
      expect(trigger.getAttribute("aria-expanded")).toBe("false");
      expect(trigger.isConnected).toBe(true);
      expect(document.activeElement).toBe(trigger);
      expect(document.activeElement).not.toBe(document.body);
    }
    expect(focusAfterMenuUnmount.slice(-2)).toEqual([true, true]);
    expect(invalidations).toHaveBeenCalledTimes(2);
    document.removeEventListener(GPA_FOCUS_INVALIDATION_EVENT, invalidations);

    rerender(header(1));
    expect(screen.getByRole("button", { name: "Actions sur la simulation" })).toBe(trigger);
    expect(document.activeElement).toBe(trigger);
  });

  it("résout le trigger courant connecté après son remplacement et reste idempotent sous StrictMode", () => {
    const targetId = "gpa-action-trigger-fictif";
    const view = (version: number) => (
      <StrictMode>
        <button id={targetId} key={version} type="button">
          Actions sur la simulation
        </button>
      </StrictMode>
    );
    const { rerender } = render(view(1));
    const previous = screen.getByRole("button", { name: "Actions sur la simulation" });
    expect(restoreCurrentGpaFocus(targetId)).toBe(true);
    expect(document.activeElement).toBe(previous);
    rerender(view(2));
    const current = screen.getByRole("button", { name: "Actions sur la simulation" });
    const focus = vi.spyOn(current, "focus");

    expect(previous.isConnected).toBe(false);
    expect(current).not.toBe(previous);
    expect(restoreCurrentGpaFocus(targetId)).toBe(true);
    expect(current.isConnected).toBe(true);
    expect(document.activeElement).toBe(current);
    expect(document.activeElement).not.toBe(document.body);
    expect(focus).toHaveBeenCalledOnce();

    expect(restoreCurrentGpaFocus(targetId)).toBe(true);
    expect(focus).toHaveBeenCalledOnce();

    const detached = document.createElement("button");
    const detachedFocus = vi.spyOn(detached, "focus");
    vi.spyOn(document, "getElementById").mockReturnValueOnce(detached);
    expect(restoreCurrentGpaFocus(targetId)).toBe(false);
    expect(detachedFocus).not.toHaveBeenCalled();
  });

  it("invalide une intention GPA ancienne avant qu’elle puisse reprendre le focus", () => {
    const controller = createGpaFocusIntentController();
    render(
      <>
        <button id="gpa-entry-fictif" type="button">
          Saisie GPA
        </button>
        <button type="button">Actions sur la simulation</button>
      </>,
    );
    const entry = screen.getByRole("button", { name: "Saisie GPA" });
    const trigger = screen.getByRole("button", { name: "Actions sur la simulation" });
    const staleIntent = controller.create(entry.id);
    controller.invalidate();
    trigger.focus();

    expect(controller.restore(staleIntent)).toBe(false);
    expect(document.activeElement).toBe(trigger);
    expect(document.activeElement).not.toBe(entry);
    expect(document.activeElement).not.toBe(document.body);
  });

  it("verrouille le changement de scénario pendant une modification locale", () => {
    render(
      <GpaSimulationScenarioSelector
        scenarios={[scenario(0), scenario(1)]}
        activeId="scenario-fictif-0"
        compact={false}
        saveState="dirty"
        limit={5}
        activeGpa={3.42}
        activeUeCount={24}
        onSelect={vi.fn()}
        onCreate={vi.fn()}
      />,
    );
    expect((screen.getByRole("tab", { name: /\[FICTIF\] Projection 2/ }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Nouveau scénario" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("présente le barème dans une section repliable", async () => {
    const user = userEvent.setup();
    const { container } = render(<GpaSimulationFormula version="fictive-v1" />);
    expect(container.querySelector("details")?.open).toBe(false);
    await user.click(screen.getByText("Barème et formule"));
    expect(container.querySelector("details")?.open).toBe(true);
    expect(screen.getByText(/GPA = somme/)).toBeTruthy();
    expect(screen.getByText(/Règle IMTégrale fictive-v1/)).toBeTruthy();
  });
});

function formatFrench(value: number | null): string {
  if (value === null) return "—";
  return new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 }).format(value);
}
