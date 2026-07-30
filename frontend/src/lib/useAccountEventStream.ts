import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { eventReconnectDelay } from "./events";
import { queryKeys } from "./queries";

export type AccountEventStreamState = "connected" | "connecting";

const EVENT_CURSOR_PATTERN = /^evc1_[A-Za-z0-9_-]{32}$/;

export function useAccountEventStream(accountId: string | undefined, latestEventCursor: string | null | undefined) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<AccountEventStreamState>("connecting");
  const cursor = useRef<{ accountId: string; value: string | null }>({
    accountId: "",
    value: null,
  });

  useEffect(() => {
    if (!accountId || latestEventCursor === undefined) return;
    if (cursor.current.accountId !== accountId) {
      cursor.current = {
        accountId,
        value: latestEventCursor && EVENT_CURSOR_PATTERN.test(latestEventCursor) ? latestEventCursor : null,
      };
      return;
    }
    if (cursor.current.value === null && latestEventCursor && EVENT_CURSOR_PATTERN.test(latestEventCursor)) {
      cursor.current.value = latestEventCursor;
    }
  }, [accountId, latestEventCursor]);

  useEffect(() => {
    if (!accountId) return;
    let source: EventSource | null = null;
    let retryTimer: number | null = null;
    let retryAttempt = 0;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      const after = cursor.current.value ? `?after=${encodeURIComponent(cursor.current.value)}` : "";
      source = new EventSource(`/api/v1/events${after}`);
      source.onopen = () => {
        retryAttempt = 0;
        setState("connected");
      };
      source.onerror = () => {
        source?.close();
        setState("connecting");
        if (stopped) return;
        retryTimer = window.setTimeout(connect, eventReconnectDelay(retryAttempt));
        retryAttempt += 1;
      };
      source.addEventListener("update", (event) => {
        const eventCursor = (event as MessageEvent).lastEventId;
        if (EVENT_CURSOR_PATTERN.test(eventCursor)) {
          cursor.current.value = eventCursor;
        }
        void queryClient.invalidateQueries({ queryKey: queryKeys.account });
      });
      source.addEventListener("unauthorized", () => {
        stopped = true;
        source?.close();
        window.dispatchEvent(new CustomEvent("botnote:unauthorized"));
      });
    };

    connect();
    return () => {
      stopped = true;
      source?.close();
      if (retryTimer !== null) window.clearTimeout(retryTimer);
    };
  }, [accountId, queryClient]);

  return state;
}
