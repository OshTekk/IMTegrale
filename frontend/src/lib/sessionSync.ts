const SESSION_CHANGE_KEY = "botnote:session-change";

export function broadcastSessionChange(): void {
  try {
    window.localStorage.setItem(SESSION_CHANGE_KEY, `${Date.now()}:${window.crypto.randomUUID()}`);
  } catch {
    // The current tab still updates synchronously when storage is unavailable.
  }
}

export function subscribeToSessionChanges(callback: () => void): () => void {
  const listener = (event: StorageEvent) => {
    if (event.key === SESSION_CHANGE_KEY) callback();
  };
  window.addEventListener("storage", listener);
  return () => window.removeEventListener("storage", listener);
}
