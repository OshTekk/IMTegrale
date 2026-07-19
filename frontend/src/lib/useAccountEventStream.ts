import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { eventReconnectDelay } from "./events";
import { queryKeys } from "./queries";

export type AccountEventStreamState = "connected" | "connecting";

export function useAccountEventStream(accountId: string | undefined, latestEventId: number | undefined) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<AccountEventStreamState>("connecting");
  const cursor = useRef({ accountId: "", lastId: 0 });

  useEffect(() => {
    if (!accountId || latestEventId === undefined) return;
    if (cursor.current.accountId !== accountId) {
      cursor.current = { accountId, lastId: latestEventId };
      return;
    }
    cursor.current.lastId = Math.max(cursor.current.lastId, latestEventId);
  }, [accountId, latestEventId]);

  useEffect(() => {
    if (!accountId) return;
    let source: EventSource | null = null;
    let retryTimer: number | null = null;
    let retryAttempt = 0;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      source = new EventSource(`/api/v1/events?after=${cursor.current.lastId}`);
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
        const eventId = Number((event as MessageEvent).lastEventId);
        if (Number.isFinite(eventId) && eventId > 0) cursor.current.lastId = Math.max(cursor.current.lastId, eventId);
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
