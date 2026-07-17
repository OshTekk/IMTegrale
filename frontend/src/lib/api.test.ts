import { describe, expect, it } from "vitest";
import { readCsrfCookie } from "./api";

describe("readCsrfCookie", () => {
  it("matches only the exact host cookie and ignores suffix collisions", () => {
    const cookies = "evilbotnote_csrf=attacker; __Host-botnote_csrf=valid%2Dtoken";

    expect(readCsrfCookie(cookies)).toBe("valid-token");
  });

  it("fails closed instead of throwing on malformed encoding", () => {
    expect(readCsrfCookie("__Host-botnote_csrf=%")).toBeNull();
  });
});
