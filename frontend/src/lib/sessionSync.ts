const SESSION_CHANGE_KEY = "botnote:session-change";
const SESSION_CHANNEL = "botnote:session-security:v1";
const PROTOCOL_VERSION = 1 as const;
const MAX_SEEN_NONCES = 128;
const locallySentNonces = new Set<string>();

function rememberBounded(values: Set<string>, value: string): void {
  values.add(value);
  if (values.size > MAX_SEEN_NONCES) values.delete(values.values().next().value!);
}

export interface SessionTransitionMessage {
  version: typeof PROTOCOL_VERSION;
  type: "session-change";
  nonce: string;
}

function newMessage(): SessionTransitionMessage {
  return {
    version: PROTOCOL_VERSION,
    type: "session-change",
    nonce: window.crypto.randomUUID(),
  };
}

function parseMessage(value: unknown): SessionTransitionMessage | null {
  let candidate = value;
  if (typeof value === "string") {
    try {
      candidate = JSON.parse(value);
    } catch {
      return null;
    }
  }
  if (!candidate || typeof candidate !== "object") return null;
  const message = candidate as Partial<SessionTransitionMessage>;
  if (
    message.version !== PROTOCOL_VERSION ||
    message.type !== "session-change" ||
    typeof message.nonce !== "string" ||
    !/^[0-9a-f-]{16,64}$/i.test(message.nonce)
  ) {
    return null;
  }
  return message as SessionTransitionMessage;
}

export function broadcastSessionChange(): void {
  const message = newMessage();
  rememberBounded(locallySentNonces, message.nonce);
  try {
    window.localStorage.setItem(SESSION_CHANGE_KEY, JSON.stringify(message));
  } catch {
    // The current tab still updates synchronously when storage is unavailable.
  }
  try {
    const channel = new BroadcastChannel(SESSION_CHANNEL);
    channel.postMessage(message);
    channel.close();
  } catch {
    // localStorage remains the bounded fallback.
  }
}

export function subscribeToSessionChanges(callback: (message: SessionTransitionMessage) => void): () => void {
  const seen = new Set<string>();
  const remember = (message: SessionTransitionMessage) => {
    if (locallySentNonces.has(message.nonce)) return;
    if (seen.has(message.nonce)) return;
    rememberBounded(seen, message.nonce);
    callback(message);
  };
  const listener = (event: StorageEvent) => {
    if (event.key !== SESSION_CHANGE_KEY) return;
    const message = parseMessage(event.newValue);
    if (message) remember(message);
  };
  window.addEventListener("storage", listener);
  let channel: BroadcastChannel | null = null;
  try {
    channel = new BroadcastChannel(SESSION_CHANNEL);
    channel.addEventListener("message", (event) => {
      const message = parseMessage(event.data);
      if (message) remember(message);
    });
  } catch {
    channel = null;
  }
  return () => {
    window.removeEventListener("storage", listener);
    channel?.close();
  };
}
