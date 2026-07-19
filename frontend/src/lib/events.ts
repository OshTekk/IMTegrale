export function eventReconnectDelay(attempt: number, jitter = Math.random()): number {
  const safeAttempt = Math.max(0, Math.floor(attempt));
  const base = Math.min(30_000, 3_000 * 2 ** Math.min(safeAttempt, 4));
  const spread = Math.floor(Math.max(0, Math.min(0.999, jitter)) * 500);
  return base + spread;
}
