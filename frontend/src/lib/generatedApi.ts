import { client } from "../generated/api/client.gen";
import { ApiError, apiErrorDetails } from "./api";

type GeneratedResult<T> = {
  data: T;
  request: Request;
  response: Response;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readCookie(names: readonly string[]): string | null {
  const cookieHeader = typeof document === "undefined" ? "" : document.cookie;
  const cookies = new Map(
    cookieHeader.split(";").map((part) => {
      const item = part.trim();
      const separator = item.indexOf("=");
      return separator < 0 ? [item, ""] : [item.slice(0, separator), item.slice(separator + 1)];
    }),
  );
  for (const name of names) {
    const value = cookies.get(name);
    if (value === undefined) continue;
    try {
      return decodeURIComponent(value);
    } catch {
      return null;
    }
  }
  return null;
}

function requestPath(request: Request | undefined): string {
  if (!request) return "";
  try {
    return new URL(request.url).pathname;
  } catch {
    return request.url;
  }
}

client.setConfig({
  baseUrl: typeof window === "undefined" ? "http://localhost" : window.location.origin,
  credentials: "same-origin",
  headers: { Accept: "application/json" },
  throwOnError: true,
});

client.interceptors.request.use((request) => {
  if (["GET", "HEAD", "OPTIONS"].includes(request.method.toUpperCase())) return request;
  const path = requestPath(request);
  const names = path.startsWith("/api/v1/admin/")
    ? ["__Host-botnote_admin_csrf", "botnote_admin_csrf"]
    : ["__Host-botnote_csrf", "botnote_csrf"];
  const csrf = readCookie(names);
  if (!csrf) return request;
  const headers = new Headers(request.headers);
  headers.set("X-CSRF-Token", csrf);
  return new Request(request, { headers });
});

client.interceptors.error.use((error, response, request) => {
  if (error instanceof ApiError) return error;
  const status = response?.status ?? 0;
  const path = requestPath(request);
  const payload = isRecord(error) ? error : {};
  const detail = apiErrorDetails(payload, status);
  if (
    status === 401 &&
    !path.includes("/auth/login") &&
    path !== "/api/v1/admin/auth/passkey" &&
    path !== "/api/v1/auth/pass/reconnect" &&
    typeof window !== "undefined"
  ) {
    window.dispatchEvent(
      new CustomEvent(path.startsWith("/api/v1/admin/") ? "botnote:admin-unauthorized" : "botnote:unauthorized"),
    );
  }
  if (detail.code === "STUDENT_REVERIFICATION_REQUIRED" && typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("botnote:learning-reverify"));
  }
  const fallbackMessage = error instanceof Error && error.message ? error.message : detail.message;
  return new ApiError(fallbackMessage, status, {
    code: detail.code,
    retryAfterSeconds: detail.retryAfterSeconds,
    availableAt: detail.availableAt,
  });
});

export async function apiData<T>(request: Promise<GeneratedResult<T>>): Promise<T> {
  return (await request).data;
}

export const throwOnApiError = true as const;
