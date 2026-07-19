export interface InspectedFetchRequest {
  url: string;
  pathname: string;
  method: string;
  credentials: RequestCredentials | undefined;
}

export function inspectFetchRequest(input: RequestInfo | URL, init?: RequestInit): InspectedFetchRequest {
  const request = input instanceof Request ? input : null;
  const url = request?.url ?? (input instanceof URL ? input.href : String(input));
  let pathname = url;
  try {
    pathname = new URL(url, "http://localhost").pathname;
  } catch {
    // Keep the raw value so an assertion fails without hiding the malformed URL.
  }
  return {
    url,
    pathname,
    method: (request?.method ?? init?.method ?? "GET").toUpperCase(),
    credentials: request?.credentials ?? init?.credentials,
  };
}

export async function readFetchJson(input: RequestInfo | URL, init?: RequestInit): Promise<unknown> {
  if (input instanceof Request) return input.clone().json();
  if (typeof init?.body !== "string") throw new Error("Expected a JSON string request body");
  return JSON.parse(init.body) as unknown;
}
