import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import {
  isSafeLearningId,
  learningAssetUrl,
  learningContentHref,
  learningContentMode,
  learningDocumentTitle,
  learningEntryVisible,
  learningErrorCopy,
  learningErrorState,
  learningResumeHref,
  learningRouteState,
} from "./learning";
import type { LearningSessionAccess, Session } from "../types";

function access(overrides: Partial<LearningSessionAccess> = {}): LearningSessionAccess {
  return {
    available: false,
    audience_label: null,
    level_label: null,
    reverify_required: false,
    catalog_version: null,
    ...overrides,
  };
}

function session(overrides: Partial<Session> = {}, learningOverrides: Partial<LearningSessionAccess> = {}): Session {
  return {
    authenticated: true,
    role: "owner",
    auth_method: "imt",
    account: {
      id: "account-fictional",
      display_name: "Compte fictif",
      imt_username: "fictional",
    },
    learning: access(learningOverrides),
    ...overrides,
  };
}

describe("learning access UX", () => {
  it("shows every entry point only to a primary owner with explicit learning availability", () => {
    expect(learningEntryVisible(session({}, { available: true }))).toBe(true);
    expect(learningEntryVisible(session({ auth_method: "passkey" }, { available: true }))).toBe(true);
    expect(learningEntryVisible(session({}, { available: false }))).toBe(false);
    expect(learningEntryVisible(session({ role: "viewer" }, { available: true }))).toBe(false);
    expect(learningEntryVisible(session({ auth_method: "token" }, { available: true }))).toBe(false);
    expect(learningEntryVisible(session({ authenticated: false }, { available: true }))).toBe(false);
    expect(learningEntryVisible(undefined)).toBe(false);
    expect(learningEntryVisible(session({}, { available: 1 as unknown as boolean }))).toBe(false);
  });

  it("hides direct routes for non-eligible, viewer and token sessions", () => {
    expect(learningRouteState(session())).toBe("hidden");
    expect(learningRouteState(undefined)).toBe("hidden");
    expect(learningRouteState(session({ role: "viewer" }, { available: true }))).toBe("hidden");
    expect(learningRouteState(session({ auth_method: "token" }, { available: true }))).toBe("hidden");
    expect(learningRouteState(session({}, { available: true, catalog_version: "fiction-v1" }))).toBe("allowed");
    expect(learningRouteState(session({ auth_method: "passkey" }, { available: true }))).toBe("allowed");
    expect(learningRouteState(session({}, { audience_label: "FIP 2028", level_label: "2A" }))).toBe("probe");
  });

  it("isolates reverification to primary sessions and gives it fail-closed priority", () => {
    expect(learningRouteState(session({}, { reverify_required: true, audience_label: "FIP 2028" }))).toBe("reverify");
    expect(learningRouteState(session({ auth_method: "passkey" }, { reverify_required: true }))).toBe("reverify");
    expect(learningRouteState(session({ auth_method: "token" }, { reverify_required: true }))).toBe("hidden");
    expect(learningRouteState(session({}, { available: true, reverify_required: true }))).toBe("reverify");
  });
});

describe("learning API error states", () => {
  it("maps stable codes without exposing backend messages or paths", () => {
    const reverify = new ApiError("private /srv/catalog/person.pdf", 403, { code: "STUDENT_REVERIFICATION_REQUIRED" });
    const unavailable = new ApiError("checksum /srv/releases/secret.json", 503, {
      code: "LEARNING_CATALOG_UNAVAILABLE",
    });

    expect(learningErrorState(reverify)).toBe("reverify");
    expect(learningErrorState(unavailable)).toBe("catalog-unavailable");
    expect(learningErrorCopy[learningErrorState(unavailable)].message).not.toContain("/srv/");
    expect(learningErrorCopy[learningErrorState(reverify)].message).not.toContain("person.pdf");
  });

  it("keeps 404 hidden and other failures generic", () => {
    expect(learningErrorState(new ApiError("not found", 404))).toBe("hidden");
    expect(learningErrorState(new Error("secret"))).toBe("error");
  });
});

describe("learning route builders", () => {
  it("accepts stable IDs and rejects paths, encodings and traversal", () => {
    expect(isSafeLearningId("lesson.analysis-1")).toBe(true);
    for (const invalid of ["../secret", "a/b", "a\\b", "%2e%2e", "", ".hidden", "a..b", "UPPER", "trailing.", "a::b"]) {
      expect(isSafeLearningId(invalid)).toBe(false);
    }
  });

  it("builds only fixed internal content and protected asset routes", () => {
    expect(learningContentHref("lesson", "lesson.one")).toBe("/parcours/lecons/lesson.one");
    expect(learningContentHref("exercise", "exercise:one")).toBe("/parcours/exercices/exercise%3Aone");
    expect(learningContentHref("chapter", "chapter.one")).toBeNull();
    expect(learningAssetUrl("asset.figure-1")).toBe("/api/v1/learning/assets/asset.figure-1");
    expect(learningAssetUrl("../outside.pdf")).toBeNull();
  });

  it("derives canonical content views and resume positions from server kinds", () => {
    expect(learningContentMode("lesson")).toBe("lesson");
    expect(learningContentMode("chapter")).toBeNull();
    expect(learningContentMode("past_exam")).toBe("exercise");
    expect(learningContentMode("source")).toBeNull();
    expect(learningResumeHref("lesson", "lesson.one", { last_section_id: "section.two", last_page: null })).toBe(
      "/parcours/lecons/lesson.one#section.two",
    );
    expect(learningResumeHref("source", "source.one", { last_section_id: null, last_page: 12 })).toBe(
      "/parcours/sources/source.one?page=12",
    );
  });

  it("uses stable titles for every Parcours subroute", () => {
    expect(learningDocumentTitle("/parcours")).toBe("Parcours");
    expect(learningDocumentTitle("/parcours/recherche")).toBe("Recherche Parcours");
    expect(learningDocumentTitle("/notes")).toBeUndefined();
  });
});
