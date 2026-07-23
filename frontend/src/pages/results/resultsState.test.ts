import { describe, expect, it } from "vitest";
import {
  legacyResultsSearch,
  parseResultsSearch,
  resultsSearchForState,
  sanitizeResultsSearch,
  updateResultsSearch,
} from "./resultsState";

describe("resultsState", () => {
  it("uses the UE view and stable defaults", () => {
    expect(parseResultsSearch(new URLSearchParams()).state).toEqual({
      view: "ues",
      year: null,
      semester: null,
      ue: null,
      type: "all",
      sort: "ue-coefficient",
      q: "",
    });
  });

  it("falls back cleanly from invalid enum values", () => {
    const parsed = parseResultsSearch(new URLSearchParams("view=unknown&type=other&sort=random&semester=S7"));
    expect(parsed.invalidKeys).toEqual(["view", "type", "sort"]);
    expect(parsed.state).toMatchObject({
      view: "ues",
      type: "all",
      sort: "ue-coefficient",
      semester: "S7",
    });
  });

  it("keeps only parameters relevant to the active view", () => {
    const params = resultsSearchForState({
      view: "ues",
      year: "2",
      semester: "S7",
      ue: "UE-FICTIVE",
      type: "resit",
      sort: "recent",
      q: "texte",
    });
    expect(params.toString()).toBe("view=ues&year=2&semester=S7&ue=UE-FICTIVE");
  });

  it("updates a shared URL state without mutating the source", () => {
    const source = new URLSearchParams("view=evaluations&semester=S5&q=analyse");
    const updated = updateResultsSearch(source, { sort: "coefficient", type: "resit" });
    expect(source.toString()).toBe("view=evaluations&semester=S5&q=analyse");
    expect(updated.toString()).toBe("view=evaluations&semester=S5&type=resit&sort=coefficient&q=analyse");
  });

  it("preserves useful legacy parameters while replacing the view", () => {
    expect(legacyResultsSearch("?semester=S6&q=r%C3%A9seau&view=recent", "evaluations")).toBe(
      "?semester=S6&q=r%C3%A9seau&view=evaluations",
    );
  });

  it("canonicalizes unknown filters and strips unrelated parameters", () => {
    const sanitized = sanitizeResultsSearch(
      new URLSearchParams("view=evaluations&year=9&semester=S7&ue=INCONNUE&q=test&secret=non"),
      {
        years: new Set(["1", "2"]),
        semesters: new Set(["S5", "S6"]),
        ues: new Set(["UE-FICTIVE"]),
      },
    );
    expect(sanitized.toString()).toBe("view=evaluations&q=test");
  });
});
