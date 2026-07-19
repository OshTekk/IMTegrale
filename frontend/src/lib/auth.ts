import type { Session } from "../types";

export function isPrimaryOwnerSession(session: Pick<Session, "role" | "auth_method">): boolean {
  return session.role === "owner" && (session.auth_method === "imt" || session.auth_method === "passkey");
}
