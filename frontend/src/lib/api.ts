export class ApiError extends Error {
  status: number;
  code: string | null;
  retryAfterSeconds: number | null;
  availableAt: string | null;

  constructor(
    message: string,
    status: number,
    options: { code?: string; retryAfterSeconds?: number; availableAt?: string } = {}
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = options.code ?? null;
    this.retryAfterSeconds = options.retryAfterSeconds ?? null;
    this.availableAt = options.availableAt ?? null;
  }
}

export function readCsrfCookie(cookieHeader = document.cookie): string | null {
  return readNamedCookie(["__Host-botnote_csrf", "botnote_csrf"], cookieHeader);
}

function readNamedCookie(names: string[], cookieHeader = document.cookie): string | null {
  const cookies = new Map(
    cookieHeader.split(";").map((part) => {
      const item = part.trim();
      const separator = item.indexOf("=");
      return separator < 0 ? [item, ""] : [item.slice(0, separator), item.slice(separator + 1)];
    })
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

async function request<T>(
  path: string,
  options: RequestInit,
  csrfNames: string[],
  unauthorizedEvent: string | null
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = readNamedCookie(csrfNames);
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }

  const response = await fetch(path, {
    ...options,
    method,
    headers,
    credentials: "same-origin"
  });
  const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) {
    const rawDetail = payload.detail;
    const structured = rawDetail && typeof rawDetail === "object"
      ? rawDetail as Record<string, unknown>
      : null;
    const detail = typeof rawDetail === "string"
      ? rawDetail
      : typeof structured?.message === "string"
        ? structured.message
        : `Erreur HTTP ${response.status}`;
    if (
      response.status === 401
      && !path.includes("/auth/login")
      && path !== "/api/v1/auth/pass/reconnect"
      && unauthorizedEvent
    ) {
      window.dispatchEvent(new CustomEvent(unauthorizedEvent));
    }
    throw new ApiError(detail, response.status, {
      code: typeof structured?.code === "string" ? structured.code : undefined,
      retryAfterSeconds: typeof structured?.retry_after_seconds === "number" ? structured.retry_after_seconds : undefined,
      availableAt: typeof structured?.available_at === "string" ? structured.available_at : undefined
    });
  }
  return payload as T;
}

export function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  return request<T>(
    path,
    options,
    ["__Host-botnote_csrf", "botnote_csrf"],
    "botnote:unauthorized"
  );
}

export function adminApi<T>(path: string, options: RequestInit = {}): Promise<T> {
  return request<T>(
    path,
    options,
    ["__Host-botnote_admin_csrf", "botnote_admin_csrf"],
    "botnote:admin-unauthorized"
  );
}

function downloadFilename(header: string | null): string {
  const match = header?.match(/filename="([^"]+)"/i);
  return match?.[1] ?? "document.pdf";
}

export async function downloadFile(path: string): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(path, {
    method: "GET",
    headers: { Accept: "application/pdf" },
    credentials: "same-origin"
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    const detail = typeof payload.detail === "string"
      ? payload.detail
      : `Erreur HTTP ${response.status}`;
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent("botnote:unauthorized"));
    }
    throw new ApiError(detail, response.status);
  }
  return {
    blob: await response.blob(),
    filename: downloadFilename(response.headers.get("Content-Disposition"))
  };
}
