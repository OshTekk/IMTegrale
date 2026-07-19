export class ApiError extends Error {
  status: number;
  code: string | null;
  retryAfterSeconds: number | null;
  availableAt: string | null;

  constructor(
    message: string,
    status: number,
    options: { code?: string; retryAfterSeconds?: number; availableAt?: string } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = options.code ?? null;
    this.retryAfterSeconds = options.retryAfterSeconds ?? null;
    this.availableAt = options.availableAt ?? null;
  }
}

export interface ApiErrorDetails {
  message: string;
  code?: string;
  retryAfterSeconds?: number;
  availableAt?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function apiErrorDetails(payload: Record<string, unknown>, status: number): ApiErrorDetails {
  const rawDetail = payload.detail;
  const structured = isRecord(rawDetail) ? rawDetail : null;
  const metadata = isRecord(structured?.metadata) ? structured.metadata : null;
  const retryAfter = structured?.retry_after_seconds ?? metadata?.retry_after_seconds;
  const availableAt = structured?.available_at ?? metadata?.available_at;
  return {
    message:
      typeof rawDetail === "string"
        ? rawDetail
        : typeof structured?.message === "string"
          ? structured.message
          : `Erreur HTTP ${status}`,
    code: typeof structured?.code === "string" ? structured.code : undefined,
    retryAfterSeconds: typeof retryAfter === "number" ? retryAfter : undefined,
    availableAt: typeof availableAt === "string" ? availableAt : undefined,
  };
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

function downloadFilename(header: string | null): string {
  const match = header?.match(/filename="([^"]+)"/i);
  return match?.[1] ?? "document.pdf";
}

async function readFileResponse(response: Response): Promise<{ blob: Blob; filename: string }> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    const detail = apiErrorDetails(payload, response.status);
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent("botnote:unauthorized"));
    }
    if (detail.code === "STUDENT_REVERIFICATION_REQUIRED" && typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("botnote:learning-reverify"));
    }
    throw new ApiError(detail.message, response.status, {
      code: detail.code,
      retryAfterSeconds: detail.retryAfterSeconds,
      availableAt: detail.availableAt,
    });
  }
  return {
    blob: await response.blob(),
    filename: downloadFilename(response.headers.get("Content-Disposition")),
  };
}

export async function downloadFile(path: string): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(path, {
    method: "GET",
    headers: { Accept: "application/pdf" },
    credentials: "same-origin",
  });
  return readFileResponse(response);
}

export async function fetchLearningAsset(
  assetId: string,
  accept = "application/pdf,image/*",
  signal?: AbortSignal,
): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(`/api/v1/learning/assets/${encodeURIComponent(assetId)}`, {
    method: "GET",
    headers: { Accept: accept },
    credentials: "same-origin",
    signal,
  });
  return readFileResponse(response);
}
