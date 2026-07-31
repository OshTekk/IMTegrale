import { describe, expect, it } from "vitest";
import source from "../main.tsx?raw";
import authoritySource from "./sessionAuthority.tsx?raw";

describe("document session security topology", () => {
  it("initialise le fragment owner et l’autorité avant le premier rendu", () => {
    const createRootIndex = source.indexOf("createRoot(");

    expect(source.indexOf("initializeInvitationFragmentOwner()")).toBeGreaterThan(-1);
    expect(source.indexOf("initializeInvitationFragmentOwner()")).toBeLessThan(createRootIndex);
    expect(source.indexOf("new SessionAuthority()")).toBeLessThan(createRootIndex);
    expect(source.indexOf("sessionAuthority.start()")).toBeLessThan(createRootIndex);
  });

  it("maintient SessionAuthority au-dessus du QueryClient d’époque et du routeur", () => {
    const renderTree = source.slice(source.indexOf("createRoot("));
    const authorityIndex = renderTree.indexOf("<SessionAuthorityRoot");
    const queryClientIndex = renderTree.indexOf("<EpochQueryClientHost>");
    const routerIndex = renderTree.indexOf("<BrowserRouter>");
    const applicationIndex = renderTree.indexOf("<App />");

    expect(authorityIndex).toBeGreaterThan(-1);
    expect(authorityIndex).toBeLessThan(queryClientIndex);
    expect(queryClientIndex).toBeLessThan(routerIndex);
    expect(routerIndex).toBeLessThan(applicationIndex);
  });

  it("conserve la séquence comme condition explicite de publication", () => {
    expect(authoritySource).toContain("this.snapshot.currentRequestSequence === sequence");
    expect(authoritySource).toContain("this.snapshot.currentRequestSequence + 1");
  });
});
