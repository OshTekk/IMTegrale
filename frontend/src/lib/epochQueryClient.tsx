import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useLayoutEffect, useState } from "react";
import type { Session } from "../types";
import { queryKeys } from "./queries";
import { type SessionAuthority, useSessionAuthority, useSessionAuthoritySnapshot } from "./sessionAuthority";

export function createEpochQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: 1, refetchOnWindowFocus: true },
      mutations: { retry: false },
    },
  });
}

function EpochQueryClientInstance({
  authority,
  authEpoch,
  initialSession,
  children,
}: {
  authority: SessionAuthority;
  authEpoch: number;
  initialSession: Session | undefined;
  children: ReactNode;
}) {
  const [client] = useState(() => {
    const created = createEpochQueryClient();
    if (initialSession) created.setQueryData(queryKeys.session, initialSession);
    return created;
  });

  useLayoutEffect(
    () =>
      authority.registerEpochResource({
        authEpoch,
        purge: () => {
          void client.cancelQueries();
          client.clear();
        },
        projectSession: (session) => {
          if (session) client.setQueryData(queryKeys.session, session);
          else client.removeQueries({ queryKey: queryKeys.session, exact: true });
        },
      }),
    [authority, authEpoch, client],
  );

  useLayoutEffect(() => {
    if (initialSession) client.setQueryData(queryKeys.session, initialSession);
    else client.removeQueries({ queryKey: queryKeys.session, exact: true });
  }, [client, initialSession]);

  return (
    <QueryClientProvider client={client}>
      <div data-query-client-epoch={authEpoch}>{children}</div>
    </QueryClientProvider>
  );
}

export function EpochQueryClientHost({ children }: { children: ReactNode }) {
  const authority = useSessionAuthority();
  const snapshot = useSessionAuthoritySnapshot(authority);
  return (
    <EpochQueryClientInstance
      key={snapshot.authEpoch}
      authority={authority}
      authEpoch={snapshot.authEpoch}
      initialSession={snapshot.session}
    >
      {children}
    </EpochQueryClientInstance>
  );
}
