import { afterAll, afterEach, beforeAll } from "vitest";
import { apiMockServer } from "./server";

beforeAll(() => apiMockServer.listen({ onUnhandledRequest: "error" }));
afterEach(() => apiMockServer.resetHandlers());
afterAll(() => apiMockServer.close());
