// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { StrictMode, useState, type ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  PrivateComparisonConsentManifestResponse,
  PrivateComparisonDetailResponse,
  PrivateComparisonInvitationCreatedResponse,
  PrivateComparisonInvitationListResponse,
  PrivateComparisonInvitationPreviewResponse,
  PrivateComparisonListResponse,
} from "../../generated/api/types.gen";
import { PrivateComparisonAcceptGate } from "../../App";
import { ToastProvider } from "../../components/Toast";
import { resetInvitationFragmentOwnerForTests } from "../../lib/invitationFragmentOwner";
import { resetPrivateComparisonLeasesForTests } from "../../lib/privateComparisonLease";
import { clearAccountStateOnCapabilityChange, queryKeys, useSession } from "../../lib/queries";
import { primarySessionScope } from "../../lib/securityScope";
import { fetchSecuritySession, SessionSecurityBoundary } from "../../lib/sessionSecurity";
import { apiMockServer } from "../../test/server";
import type { Session } from "../../types";
import { PrivateComparisonAcceptPage } from "./PrivateComparisonAcceptPage";
import { PrivateComparisonDetailPage } from "./PrivateComparisonDetailPage";
import { PrivateComparisonInvitationModal } from "./PrivateComparisonInvitationModal";
import { PrivateComparisonsPage } from "./PrivateComparisonsPage";
import {
  invitationFromFragment,
  invitationUrl,
  sortCommonUes,
  validInvitationToken,
  validPrivateComparisonPublicId,
} from "./privateComparisonPresentation";

const TOKEN = `pcinv1_${"A".repeat(43)}`;
const RELATION_ID = `pc_${"b".repeat(24)}`;
const INVITATION_ID = `pci_${"c".repeat(24)}`;
const OTHER_RELATION_ID = `pc_${"d".repeat(24)}`;

const consentManifest: PrivateComparisonConsentManifestResponse = {
  consent_version: 2,
  included_sections: [
    {
      key: "official_identity",
      title: "Identité",
      fields: [
        {
          response_path: "participant.identity.official_name",
          label: "Identité officielle de chaque participant",
        },
      ],
    },
    {
      key: "general_summary",
      title: "Résumé général",
      fields: [
        { response_path: "participant.summary.average", label: "Moyenne générale" },
        { response_path: "participant.summary.gpa", label: "GPA général" },
        { response_path: "participant.summary.validated_ects", label: "ECTS validés" },
        { response_path: "participant.summary.grade_distribution", label: "Répartition des grades" },
        {
          response_path: "participant.summary.academic_verified_at",
          label: "Date de dernière vérification académique",
        },
        { response_path: "participant.summary.freshness", label: "État de fraîcheur des données" },
        { response_path: "participant.summary.ue_count", label: "Nombre d’UE prises en compte" },
      ],
    },
    {
      key: "common_ues",
      title: "UE communes",
      fields: [
        { response_path: "common_ues.official_code", label: "Code officiel" },
        { response_path: "common_ues.participant.title", label: "Intitulé" },
        { response_path: "common_ues.participant.year", label: "Année" },
        { response_path: "common_ues.participant.semester", label: "Semestre" },
        { response_path: "common_ues.participant.average", label: "Moyenne" },
        { response_path: "common_ues.participant.grade", label: "Grade" },
        { response_path: "common_ues.participant.gpa", label: "GPA" },
        { response_path: "common_ues.participant.earned_ects", label: "ECTS obtenus" },
        { response_path: "common_ues.participant.allocated_ects", label: "ECTS alloués" },
        { response_path: "common_ues.participant.validated", label: "État de validation" },
        { response_path: "common_ues.participant.freshness", label: "État de fraîcheur" },
        { response_path: "common_ues.participant.verified_at", label: "Date de dernière vérification" },
      ],
    },
    {
      key: "relation_metadata",
      title: "Relation privée",
      fields: [
        { response_path: "relation.public_id", label: "Identifiant opaque de la comparaison" },
        { response_path: "relation.status", label: "Statut" },
        { response_path: "relation.activated_at", label: "Date d’activation" },
        { response_path: "relation.expires_at", label: "Date d’expiration" },
        { response_path: "relation.consent_version", label: "Version du consentement" },
        { response_path: "relation.calculated_at", label: "Date de calcul de la vue" },
      ],
    },
  ],
  excluded_sections: [
    { key: "detailed_assessments", label: "Détail des évaluations" },
    { key: "assessment_labels", label: "Libellés des évaluations" },
    { key: "assessment_coefficients", label: "Coefficients des évaluations" },
    { key: "non_common_results", label: "Notes qui ne sont pas communes" },
    { key: "non_common_ues", label: "UE qui ne sont pas communes" },
    { key: "simulations", label: "Simulations" },
    { key: "agenda", label: "Agenda" },
    { key: "learning", label: "Parcours" },
    { key: "leaderboard_rank", label: "Rang dans le classement" },
    { key: "competition_score", label: "Score de compétition" },
    { key: "personal_comments", label: "Commentaires personnels" },
    { key: "third_party_data", label: "Données d’un troisième étudiant" },
    { key: "public_sharing", label: "Publication ou partage public" },
  ],
  duration_and_revocation: {
    duration: "La durée choisie commence après l’acceptation et ne dépasse jamais 90 jours.",
    expiration: "La consultation cesse automatiquement à l’expiration.",
    immediate_revocation: "Chaque participant peut révoquer immédiatement la comparaison.",
    minimal_history: "L’historique conserve seulement le statut et les dates, sans résultat académique.",
    private_only: "La comparaison reste privée aux deux participants.",
  },
  copy_risk: "L’autre participant peut recopier ou capturer les informations visibles avant une révocation.",
};

const ownerSession: Session = {
  authenticated: true,
  session_scope: `bss1_${"a".repeat(64)}`,
  session_expires_at: "2099-07-30T12:30:00.000Z",
  server_time: "2099-07-30T12:00:00.000Z",
  role: "owner",
  auth_method: "imt",
  account: {
    id: "account-fictif-alice",
    display_name: "Alice Exemple",
    imt_username: "alice.exemple",
  },
  private_comparisons: { available: true },
  needs_security_setup: false,
  needs_sync_setup: false,
};

const replacementOwnerSession: Session = {
  ...ownerSession,
  session_scope: `bss1_${"b".repeat(64)}`,
  auth_method: "passkey",
  account: {
    id: "account-fictif-basile",
    display_name: "Basile Exemple",
    imt_username: "basile.exemple",
  },
};

const invitationCreated: PrivateComparisonInvitationCreatedResponse = {
  public_id: INVITATION_ID,
  token: TOKEN,
  session_scope: ownerSession.session_scope!,
  consent_version: 2,
  expires_at: "2099-08-05T12:00:00Z",
  relationship_duration_days: 30,
  consent_manifest: consentManifest,
};

const invitationPreview: PrivateComparisonInvitationPreviewResponse = {
  creator: { official_name: "Camille Exemple" },
  expires_at: "2099-08-05T12:00:00Z",
  relationship_duration_days: 30,
  consent_version: 2,
  consent_manifest: consentManifest,
};

const relations: PrivateComparisonListResponse = {
  comparisons: [
    {
      public_id: RELATION_ID,
      other_participant: { official_name: "Camille Exemple" },
      status: "active",
      activated_at: "2099-07-29T12:00:00Z",
      expires_at: "2099-08-28T12:00:00Z",
      academic_verified_at: "2099-07-29T10:00:00Z",
      freshness: "current",
    },
    {
      public_id: OTHER_RELATION_ID,
      status: "revoked",
      ended_at: "2099-06-20T12:00:00Z",
    },
  ],
};

const invitations: PrivateComparisonInvitationListResponse = {
  invitations: [
    {
      public_id: INVITATION_ID,
      created_at: "2099-07-29T12:00:00Z",
      expires_at: "2099-08-05T12:00:00Z",
      relationship_duration_days: 30,
      status: "active",
    },
    {
      public_id: `pci_${"e".repeat(24)}`,
      created_at: "2099-06-01T12:00:00Z",
      expires_at: "2099-06-08T12:00:00Z",
      relationship_duration_days: 7,
      status: "consumed",
    },
    {
      public_id: `pci_${"f".repeat(24)}`,
      created_at: "2099-05-01T12:00:00Z",
      expires_at: "2099-05-08T12:00:00Z",
      relationship_duration_days: 60,
      status: "expired",
    },
    {
      public_id: `pci_${"g".repeat(24)}`,
      created_at: "2099-04-01T12:00:00Z",
      expires_at: "2099-04-08T12:00:00Z",
      relationship_duration_days: 90,
      status: "revoked",
    },
  ],
};

const detail: PrivateComparisonDetailResponse = {
  public_id: RELATION_ID,
  status: "active",
  consent_version: 2,
  activated_at: "2099-07-29T12:00:00Z",
  expires_at: "2099-08-28T12:00:00Z",
  calculated_at: "2099-07-29T12:30:00Z",
  current: {
    identity: { official_name: "Alice Exemple" },
    summary: {
      average: 13.4,
      gpa: 3.1,
      validated_ects: 54,
      ue_count: 12,
      freshness: "current",
      academic_verified_at: "2099-07-29T10:00:00Z",
      grade_distribution: { A: 1, B: 3, C: 4, D: 2, E: 1, FX: 1, F: 0 },
    },
  },
  other: {
    identity: { official_name: "Camille Exemple" },
    summary: {
      average: 15.2,
      gpa: 3.5,
      validated_ects: 57,
      ue_count: 13,
      freshness: "recommended",
      academic_verified_at: "2099-07-28T10:00:00Z",
      grade_distribution: { A: 3, B: 4, C: 3, D: 1, E: 1, FX: 0, F: 1 },
    },
  },
  common_ues: [
    {
      official_code: "UE-FICTIVE-201",
      current: {
        title: "Analyse de systèmes fictifs",
        year: "2",
        semester: "S7",
        average: 12.8,
        grade: "C",
        gpa: 3,
        earned_ects: 5,
        allocated_ects: 5,
        validated: true,
        freshness: "current",
        verified_at: "2099-07-29T10:00:00Z",
      },
      other: {
        title: "Analyse de systèmes fictifs",
        year: "2",
        semester: "S7",
        average: 14.7,
        grade: "B",
        gpa: 3.5,
        earned_ects: 5,
        allocated_ects: 5,
        validated: true,
        freshness: "recommended",
        verified_at: "2099-07-28T10:00:00Z",
      },
    },
  ],
};

function testClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  client.setQueryData(queryKeys.session, ownerSession);
  return client;
}

function installTestSession(client: QueryClient, next: Session) {
  const previous = client.getQueryData<Session>(queryKeys.session);
  clearAccountStateOnCapabilityChange(client, previous, next);
  client.setQueryData(queryKeys.session, next);
}

function Providers({
  children,
  client,
  strict = false,
}: {
  children: ReactNode;
  client: QueryClient;
  strict?: boolean;
}) {
  function SecurityBoundary({ children: securedChildren }: { children: ReactNode }) {
    const currentSession = useSession();
    return (
      <SessionSecurityBoundary
        session={currentSession.data}
        sessionPending={currentSession.isPending}
        refetchSession={fetchSecuritySession}
      >
        {securedChildren}
      </SessionSecurityBoundary>
    );
  }
  const content = (
    <QueryClientProvider client={client}>
      <SecurityBoundary>
        <ToastProvider>{children}</ToastProvider>
      </SecurityBoundary>
    </QueryClientProvider>
  );
  return strict ? <StrictMode>{content}</StrictMode> : content;
}

function renderRoute(element: ReactNode, initialEntry: string, client = testClient(), strict = false) {
  const accepting = initialEntry === "/comparisons/accept";
  const result = render(
    <Providers client={client} strict={strict}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/comparisons" element={<div data-testid="comparisons-home">Accueil Comparaisons</div>} />
          <Route
            path="/comparisons/accept"
            element={accepting ? element : <div data-testid="comparison-accept-destination" />}
          />
          <Route
            path="/comparisons/:publicId"
            element={accepting ? <div data-testid="comparison-detail-destination" /> : element}
          />
        </Routes>
      </MemoryRouter>
    </Providers>,
  );
  return { ...result, client };
}

function installEmptyPrivateComparisonLists() {
  apiMockServer.use(
    http.get("*/api/v1/private-comparisons/consent-manifest", () => HttpResponse.json(consentManifest)),
    http.get("*/api/v1/private-comparisons", () => HttpResponse.json({ comparisons: [] })),
    http.get("*/api/v1/private-comparisons/invitations", () => HttpResponse.json({ invitations: [] })),
  );
}

async function renderAndCreateInvitation(client: QueryClient, strict = false) {
  render(
    <Providers client={client} strict={strict}>
      <MemoryRouter>
        <PrivateComparisonsPage />
      </MemoryRouter>
    </Providers>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Créer une invitation" }));
  for (const checkbox of screen.getAllByRole("checkbox")) await user.click(checkbox);
  await user.click(screen.getByRole("button", { name: "Créer le lien" }));
  return user;
}

function persistedPageTransition(type: "pagehide" | "pageshow"): Event {
  const event = new Event(type);
  Object.defineProperty(event, "persisted", { value: true });
  return event;
}

afterEach(() => {
  cleanup();
  resetInvitationFragmentOwnerForTests();
  resetPrivateComparisonLeasesForTests();
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
  Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

beforeEach(() => {
  apiMockServer.use(http.get("*/api/v1/auth/session", () => HttpResponse.json(ownerSession)));
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});

describe("présentation et validation locales", () => {
  it("accepte uniquement le format exact du secret et des identifiants publics", () => {
    expect(validInvitationToken(TOKEN)).toBe(true);
    expect(validInvitationToken(`pcinv1_${"A".repeat(42)}`)).toBe(false);
    expect(invitationFromFragment(`#invite=${TOKEN}`)).toEqual({ state: "valid", token: TOKEN });
    expect(invitationFromFragment(`#invite=${TOKEN}&invite=${TOKEN}`)).toEqual({ state: "invalid", token: null });
    expect(invitationUrl(TOKEN, "https://app.example.test")).toBe(
      `https://app.example.test/comparisons/accept#invite=${TOKEN}`,
    );
    expect(validPrivateComparisonPublicId(RELATION_ID)).toBe(true);
    expect(validPrivateComparisonPublicId("pc_123")).toBe(false);
  });

  it("trie les UE par semestre puis code sans calculer d’écart", () => {
    const second = structuredClone(detail.common_ues[0]!);
    second.official_code = "UE-FICTIVE-101";
    second.current.semester = "S6";
    expect(sortCommonUes([detail.common_ues[0]!, second]).map((value) => value.official_code)).toEqual([
      "UE-FICTIVE-101",
      "UE-FICTIVE-201",
    ]);
  });
});

describe("invitation one-shot", () => {
  it("efface le bearer avant de rendre un remplacement direct de compte", async () => {
    apiMockServer.use(
      http.get("*/api/v1/private-comparisons/consent-manifest", () => HttpResponse.json(consentManifest)),
      http.get("*/api/v1/private-comparisons", () => HttpResponse.json({ comparisons: [] })),
      http.get("*/api/v1/private-comparisons/invitations", () => HttpResponse.json({ invitations: [] })),
      http.post("*/api/v1/private-comparisons/invitations", () =>
        HttpResponse.json(invitationCreated, { status: 201 }),
      ),
    );
    const client = testClient();
    render(
      <Providers client={client}>
        <MemoryRouter>
          <PrivateComparisonsPage />
        </MemoryRouter>
      </Providers>,
    );
    const user = userEvent.setup();
    const clipboardWrite = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    await user.click(await screen.findByRole("button", { name: "Créer une invitation" }));
    for (const checkbox of screen.getAllByRole("checkbox")) await user.click(checkbox);
    await user.click(screen.getByRole("button", { name: "Créer le lien" }));
    expect(((await screen.findByLabelText("Lien d’invitation")) as HTMLInputElement).value).toBe(
      `${window.location.origin}/comparisons/accept#invite=${TOKEN}`,
    );

    act(() => installTestSession(client, replacementOwnerSession));

    await waitFor(() => expect(client.getQueryData(queryKeys.session)).toEqual(replacementOwnerSession));
    expect(screen.queryByLabelText("Lien d’invitation")).toBeNull();
    expect(screen.queryByRole("button", { name: "Copier le lien" })).toBeNull();
    expect(document.documentElement.outerHTML).not.toContain(TOKEN);
    expect(clipboardWrite).not.toHaveBeenCalled();
  });

  it("jette une réponse one-shot arrivée après le passage direct de A à B", async () => {
    installEmptyPrivateComparisonLists();
    let releaseResponse: (() => void) | undefined;
    let requestStarted = false;
    const responseGate = new Promise<void>((resolve) => {
      releaseResponse = resolve;
    });
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations", async () => {
        requestStarted = true;
        await responseGate;
        return HttpResponse.json(invitationCreated, { status: 201 });
      }),
    );
    const client = testClient();
    const creation = renderAndCreateInvitation(client);
    await waitFor(() => expect(requestStarted).toBe(true));
    expect(releaseResponse).toBeTypeOf("function");
    const clipboardWrite = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);

    act(() => installTestSession(client, replacementOwnerSession));
    await waitFor(() => expect(client.getQueryData(queryKeys.session)).toEqual(replacementOwnerSession));
    act(() => releaseResponse?.());
    await creation;

    await waitFor(() => expect(screen.queryByLabelText("Lien d’invitation")).toBeNull());
    expect(document.documentElement.outerHTML).not.toContain(TOKEN);
    expect(screen.queryByText("Lien d’invitation créé")).toBeNull();
    expect(clipboardWrite).not.toHaveBeenCalled();
  });

  it.each([
    [
      "owner délégué par token",
      {
        ...ownerSession,
        session_scope: `bss1_${"c".repeat(64)}`,
        auth_method: "token" as const,
        private_comparisons: { available: false },
      },
    ],
    [
      "viewer",
      {
        ...ownerSession,
        session_scope: `bss1_${"d".repeat(64)}`,
        role: "viewer" as const,
        private_comparisons: { available: false },
      },
    ],
    ["session expirée", { authenticated: false, private_comparisons: { available: false } }],
    [
      "compte désactivé ou capacité retirée",
      {
        ...ownerSession,
        session_scope: `bss1_${"e".repeat(64)}`,
        private_comparisons: { available: false },
      },
    ],
  ] satisfies Array<[string, Session]>)("purge le bearer lors du downgrade vers %s", async (_label, nextSession) => {
    installEmptyPrivateComparisonLists();
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations", () =>
        HttpResponse.json(invitationCreated, { status: 201 }),
      ),
    );
    const client = testClient();
    await renderAndCreateInvitation(client);
    expect(await screen.findByLabelText("Lien d’invitation")).toBeTruthy();

    act(() => installTestSession(client, nextSession));

    await waitFor(() => expect(screen.queryByLabelText("Lien d’invitation")).toBeNull());
    expect(screen.queryByRole("button", { name: "Copier le lien" })).toBeNull();
    expect(document.documentElement.outerHTML).not.toContain(TOKEN);
  });

  it("ne restaure pas le bearer sous StrictMode après un remplacement de principal", async () => {
    installEmptyPrivateComparisonLists();
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations", () =>
        HttpResponse.json(invitationCreated, { status: 201 }),
      ),
    );
    const client = testClient();
    await renderAndCreateInvitation(client, true);
    expect(await screen.findAllByLabelText("Lien d’invitation")).toHaveLength(1);

    act(() => installTestSession(client, replacementOwnerSession));

    await waitFor(() => expect(screen.queryByLabelText("Lien d’invitation")).toBeNull());
    expect(document.documentElement.outerHTML).not.toContain(TOKEN);
  });

  it("purge le bearer avant une mise en BFCache et ne le restaure pas au pageshow", async () => {
    installEmptyPrivateComparisonLists();
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations", () =>
        HttpResponse.json(invitationCreated, { status: 201 }),
      ),
    );
    const client = testClient();
    await renderAndCreateInvitation(client);
    expect(await screen.findByLabelText("Lien d’invitation")).toBeTruthy();

    act(() => window.dispatchEvent(persistedPageTransition("pagehide")));
    await waitFor(() => expect(screen.queryByLabelText("Lien d’invitation")).toBeNull());
    act(() => window.dispatchEvent(persistedPageTransition("pageshow")));

    expect(screen.queryByLabelText("Lien d’invitation")).toBeNull();
    expect(document.documentElement.outerHTML).not.toContain(TOKEN);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Créer une invitation" }));
    expect(screen.queryByLabelText("Lien d’invitation")).toBeNull();
  });

  it("interdit la copie si un signal inter-onglet arrive pendant la revalidation serveur", async () => {
    installEmptyPrivateComparisonLists();
    let authCalls = 0;
    let releaseRevalidation: (() => void) | undefined;
    const revalidationGate = new Promise<void>((resolve) => {
      releaseRevalidation = resolve;
    });
    apiMockServer.use(
      http.get("*/api/v1/auth/session", async () => {
        authCalls += 1;
        if (authCalls > 1) await revalidationGate;
        return HttpResponse.json(ownerSession);
      }),
      http.post("*/api/v1/private-comparisons/invitations", () =>
        HttpResponse.json(invitationCreated, { status: 201 }),
      ),
    );
    const clipboardWrite = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    const client = testClient();
    const user = await renderAndCreateInvitation(client);
    expect(await screen.findByLabelText("Lien d’invitation")).toBeTruthy();
    expect(authCalls).toBe(1);

    const copyAttempt = user.click(screen.getByRole("button", { name: "Copier le lien" }));
    await waitFor(() => expect(authCalls).toBe(2));
    act(() => {
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: "botnote:session-change",
          newValue: JSON.stringify({
            version: 1,
            type: "session-change",
            nonce: "00000000-0000-4000-8000-000000000002",
          }),
        }),
      );
    });

    expect(screen.queryByLabelText("Lien d’invitation")).toBeNull();
    expect(clipboardWrite).not.toHaveBeenCalled();
    act(() => releaseRevalidation?.());
    await copyAttempt;
    await waitFor(() => expect(authCalls).toBeGreaterThanOrEqual(3));
    expect(clipboardWrite).not.toHaveBeenCalled();
    expect(document.documentElement.outerHTML).not.toContain(TOKEN);
  });

  it("ne place le bearer dans aucun cache, stockage, titre, toast, aria-live ou console", async () => {
    installEmptyPrivateComparisonLists();
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations", () =>
        HttpResponse.json(invitationCreated, { status: 201 }),
      ),
    );
    const cacheOpen = vi.fn();
    const indexedDbOpen = vi.fn();
    const consoleInfo = vi.spyOn(console, "info").mockImplementation(() => undefined);
    vi.stubGlobal("caches", { open: cacheOpen });
    vi.stubGlobal("indexedDB", { open: indexedDbOpen });
    const client = testClient();

    await renderAndCreateInvitation(client);
    expect(await screen.findByLabelText("Lien d’invitation")).toBeTruthy();

    const querySnapshot = client
      .getQueryCache()
      .getAll()
      .map((query) => ({ key: query.queryKey, data: query.state.data }));
    const mutationSnapshot = client
      .getMutationCache()
      .getAll()
      .map((mutation) => ({ data: mutation.state.data, variables: mutation.state.variables }));
    expect(JSON.stringify(querySnapshot)).not.toContain(TOKEN);
    expect(JSON.stringify(mutationSnapshot)).not.toContain(TOKEN);
    expect(JSON.stringify(window.localStorage)).not.toContain(TOKEN);
    expect(JSON.stringify(window.sessionStorage)).not.toContain(TOKEN);
    expect(JSON.stringify(window.history.state)).not.toContain(TOKEN);
    expect(document.title).toBe("Comparaison privée · IMTégrale");
    expect(document.title).not.toContain("Alice Exemple");
    expect(
      Array.from(document.querySelectorAll("[aria-live]"))
        .map((node) => node.textContent)
        .join(""),
    ).not.toContain(TOKEN);
    expect(document.querySelector("[role='status']")?.textContent ?? "").not.toContain(TOKEN);
    expect(cacheOpen).not.toHaveBeenCalled();
    expect(indexedDbOpen).not.toHaveBeenCalled();
    expect(JSON.stringify(consoleInfo.mock.calls)).not.toContain(TOKEN);
  });

  it("ne persiste jamais le secret et l’efface du DOM à la fermeture", async () => {
    let requestBody: unknown;
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations", async ({ request }) => {
        requestBody = await request.json();
        return HttpResponse.json(invitationCreated, { status: 201 });
      }),
    );
    const client = testClient();
    function Harness() {
      const [open, setOpen] = useState(true);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Rouvrir
          </button>
          <PrivateComparisonInvitationModal
            open={open}
            onClose={() => setOpen(false)}
            onCreated={() => undefined}
            manifest={consentManifest}
            manifestPending={false}
            sessionScope={primarySessionScope(ownerSession)}
          />
        </>
      );
    }
    render(
      <Providers client={client}>
        <Harness />
      </Providers>,
    );
    const user = userEvent.setup();
    const clipboardWrite = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    const dialog = screen.getByRole("dialog", { name: "Créer une invitation" });
    for (const label of [
      "Répartition des grades",
      "ECTS obtenus",
      "ECTS alloués",
      "État de validation",
      "État de fraîcheur",
      "Détail des évaluations",
      "Coefficients des évaluations",
      "UE qui ne sont pas communes",
      "Publication ou partage public",
      "Chaque participant peut révoquer immédiatement la comparaison.",
      "L’autre participant peut recopier ou capturer les informations visibles avant une révocation.",
    ]) {
      expect(within(dialog).getByText(label)).toBeTruthy();
    }
    expect((screen.getByLabelText("Durée de la comparaison après acceptation") as HTMLSelectElement).value).toBe("30");
    const create = screen.getByRole("button", { name: "Créer le lien" });
    expect((create as HTMLButtonElement).disabled).toBe(true);
    for (const checkbox of screen.getAllByRole("checkbox")) await user.click(checkbox);
    await user.click(create);
    const link = await screen.findByLabelText("Lien d’invitation");
    expect((link as HTMLInputElement).value).toBe(`${window.location.origin}/comparisons/accept#invite=${TOKEN}`);
    expect(requestBody).toEqual({
      consent_version: 2,
      acknowledge_identity_visibility: true,
      acknowledge_academic_scope: true,
      acknowledge_copy_risk: true,
      duration_days: 30,
    });
    expect(client.getMutationCache().getAll()).toHaveLength(0);
    expect(
      JSON.stringify(
        client
          .getQueryCache()
          .getAll()
          .map((query) => query.queryKey),
      ),
    ).not.toContain(TOKEN);
    expect(JSON.stringify(window.history.state)).not.toContain(TOKEN);
    expect(JSON.stringify(window.localStorage)).not.toContain(TOKEN);
    expect(JSON.stringify(window.sessionStorage)).not.toContain(TOKEN);

    await user.click(screen.getByRole("button", { name: "Copier le lien" }));
    expect(clipboardWrite).toHaveBeenCalledWith(`${window.location.origin}/comparisons/accept#invite=${TOKEN}`);
    await user.click(screen.getAllByRole("button", { name: "Fermer" }).at(-1)!);
    await waitFor(() => expect(screen.queryByLabelText("Lien d’invitation")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Rouvrir" }));
    expect(screen.queryByLabelText("Lien d’invitation")).toBeNull();
    expect((screen.getByRole("button", { name: "Créer le lien" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("refuse une réponse one-shot dont le secret ne respecte pas le contrat", async () => {
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations", () =>
        HttpResponse.json({ ...invitationCreated, token: "secret-invalide" }, { status: 201 }),
      ),
    );
    render(
      <Providers client={testClient()}>
        <PrivateComparisonInvitationModal
          open
          onClose={() => undefined}
          onCreated={() => undefined}
          manifest={consentManifest}
          manifestPending={false}
          sessionScope={primarySessionScope(ownerSession)}
        />
      </Providers>,
    );
    const user = userEvent.setup();
    for (const checkbox of screen.getAllByRole("checkbox")) await user.click(checkbox);
    await user.click(screen.getByRole("button", { name: "Créer le lien" }));
    expect(await screen.findByText("Impossible de créer l’invitation pour le moment.")).toBeTruthy();
    expect(screen.queryByLabelText("Lien d’invitation")).toBeNull();
    expect(document.body.textContent).not.toContain("secret-invalide");
  });

  it("refuse un bearer one-shot lié à une autre portée de session", async () => {
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations", () =>
        HttpResponse.json(
          {
            ...invitationCreated,
            session_scope: replacementOwnerSession.session_scope,
          },
          { status: 201 },
        ),
      ),
    );
    render(
      <Providers client={testClient()}>
        <PrivateComparisonInvitationModal
          open
          onClose={() => undefined}
          onCreated={() => undefined}
          manifest={consentManifest}
          manifestPending={false}
          sessionScope={primarySessionScope(ownerSession)}
        />
      </Providers>,
    );
    const user = userEvent.setup();
    for (const checkbox of screen.getAllByRole("checkbox")) {
      await user.click(checkbox);
    }
    await user.click(screen.getByRole("button", { name: "Créer le lien" }));

    expect(await screen.findByText("Impossible de créer l’invitation pour le moment.")).toBeTruthy();
    expect(screen.queryByLabelText("Lien d’invitation")).toBeNull();
    expect(document.documentElement.outerHTML).not.toContain(TOKEN);
  });

  it("interdit la création lorsque le manifeste est indisponible", () => {
    render(
      <Providers client={testClient()}>
        <PrivateComparisonInvitationModal
          open
          onClose={() => undefined}
          onCreated={() => undefined}
          manifest={null}
          manifestPending={false}
          sessionScope={primarySessionScope(ownerSession)}
        />
      </Providers>,
    );

    expect(
      screen.getByText("Le périmètre de consentement est indisponible. Aucune invitation ne peut être créée."),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Créer le lien" })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("interdit la création lorsque le manifeste chargé est incomplet", () => {
    render(
      <Providers client={testClient()}>
        <PrivateComparisonInvitationModal
          open
          onClose={() => undefined}
          onCreated={() => undefined}
          manifest={{ ...consentManifest, included_sections: [] }}
          manifestPending={false}
          sessionScope={primarySessionScope(ownerSession)}
        />
      </Providers>,
    );

    expect(
      screen.getByText("Le périmètre de consentement est indisponible. Aucune invitation ne peut être créée."),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Créer le lien" })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("refuse une réponse one-shot dont la version du manifeste diffère de celle présentée", async () => {
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations", () =>
        HttpResponse.json({
          ...invitationCreated,
          consent_manifest: {
            ...consentManifest,
            consent_version: 3,
          },
        }),
      ),
    );
    render(
      <Providers client={testClient()}>
        <PrivateComparisonInvitationModal
          open
          onClose={() => undefined}
          onCreated={() => undefined}
          manifest={consentManifest}
          manifestPending={false}
          sessionScope={primarySessionScope(ownerSession)}
        />
      </Providers>,
    );
    const user = userEvent.setup();
    for (const checkbox of screen.getAllByRole("checkbox")) await user.click(checkbox);
    await user.click(screen.getByRole("button", { name: "Créer le lien" }));

    expect(await screen.findByText("Impossible de créer l’invitation pour le moment.")).toBeTruthy();
    expect(screen.queryByLabelText("Lien d’invitation")).toBeNull();
  });
});

describe("acceptation depuis le fragment", () => {
  it("efface le fragment avant une preview unique sous StrictMode puis accepte explicitement", async () => {
    let previewCalls = 0;
    let previewUrl = "";
    let acceptBody: unknown;
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations/preview", async ({ request }) => {
        previewCalls += 1;
        previewUrl = request.url;
        expect(await request.json()).toEqual({ token: TOKEN });
        expect(window.location.hash).toBe("");
        return HttpResponse.json(invitationPreview);
      }),
      http.post("*/api/v1/private-comparisons/invitations/accept", async ({ request }) => {
        acceptBody = await request.json();
        return HttpResponse.json(relations.comparisons[0], { status: 201 });
      }),
    );
    window.history.replaceState(null, "", `/comparisons/accept#invite=${TOKEN}`);
    const client = testClient();
    renderRoute(<PrivateComparisonAcceptPage />, "/comparisons/accept", client, true);
    expect(await screen.findByText("Camille Exemple te propose une comparaison")).toBeTruthy();
    expect(previewCalls).toBe(1);
    expect(new URL(previewUrl).hash).toBe("");
    expect(window.location.hash).toBe("");
    expect(screen.queryByText("15,2 / 20")).toBeNull();
    for (const label of [
      "Répartition des grades",
      "ECTS obtenus",
      "ECTS alloués",
      "État de validation",
      "Détail des évaluations",
      "UE qui ne sont pas communes",
      "Publication ou partage public",
    ]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    const user = userEvent.setup();
    const accept = screen.getByRole("button", { name: "Accepter la comparaison" });
    expect((accept as HTMLButtonElement).disabled).toBe(true);
    for (const checkbox of screen.getAllByRole("checkbox")) await user.click(checkbox);
    await user.click(accept);
    expect(await screen.findByTestId("comparison-detail-destination")).toBeTruthy();
    expect(acceptBody).toEqual({
      token: TOKEN,
      consent_version: 2,
      acknowledge_identity_visibility: true,
      acknowledge_academic_scope: true,
      acknowledge_copy_risk: true,
    });
    expect(client.getMutationCache().getAll()).toHaveLength(0);
    expect(JSON.stringify(window.history.state)).not.toContain(TOKEN);
  });

  it("interdit l’acceptation lorsque la preview ne fournit pas le manifeste canonique", async () => {
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations/preview", () => {
        const { consent_manifest: omittedManifest, ...response } = invitationPreview;
        expect(omittedManifest).toBe(consentManifest);
        return HttpResponse.json(response);
      }),
    );
    window.history.replaceState(null, "", `/comparisons/accept#invite=${TOKEN}`);

    renderRoute(<PrivateComparisonAcceptPage />, "/comparisons/accept");

    expect(await screen.findByText("Cette invitation n’est plus disponible")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Accepter la comparaison" })).toBeNull();
    expect(document.body.textContent).not.toContain(TOKEN);
  });

  it("refuse sans révéler l’identité du destinataire", async () => {
    let declined = 0;
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations/preview", () => HttpResponse.json(invitationPreview)),
      http.post("*/api/v1/private-comparisons/invitations/decline", async ({ request }) => {
        expect(await request.json()).toEqual({ token: TOKEN });
        declined += 1;
        return HttpResponse.json({ ok: true });
      }),
    );
    window.history.replaceState(null, "", `/comparisons/accept#invite=${TOKEN}`);
    renderRoute(<PrivateComparisonAcceptPage />, "/comparisons/accept");
    const user = userEvent.setup();
    await screen.findByText("Camille Exemple te propose une comparaison");
    await user.click(screen.getByRole("button", { name: "Refuser" }));
    await user.click(screen.getByRole("button", { name: "Refuser l’invitation" }));
    expect(await screen.findByTestId("comparisons-home")).toBeTruthy();
    expect(declined).toBe(1);
    expect(window.location.hash).toBe("");
  });

  it("n’appelle aucune API pour un fragment invalide, un reload ou une session token", async () => {
    let calls = 0;
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations/preview", () => {
        calls += 1;
        return HttpResponse.json(invitationPreview);
      }),
    );
    window.history.replaceState(null, "", "/comparisons/accept#invite=invalid");
    const first = renderRoute(<PrivateComparisonAcceptPage />, "/comparisons/accept");
    expect(await screen.findByText("Cette invitation n’est plus disponible")).toBeTruthy();
    expect(calls).toBe(0);
    first.unmount();

    window.history.replaceState(null, "", "/comparisons/accept");
    const second = renderRoute(<PrivateComparisonAcceptPage />, "/comparisons/accept");
    expect(await screen.findByText("Invitation à rouvrir")).toBeTruthy();
    expect(calls).toBe(0);
    second.unmount();

    const tokenSession: Session = { ...ownerSession, auth_method: "token" };
    const gate = render(
      <MemoryRouter>
        <PrivateComparisonAcceptGate session={tokenSession}>
          <PrivateComparisonAcceptPage />
        </PrivateComparisonAcceptGate>
      </MemoryRouter>,
    );
    expect(screen.getByText("Compte personnel requis")).toBeTruthy();
    expect(
      screen.getByText("Rouvre le lien d’invitation original avec ton compte personnel pour continuer."),
    ).toBeTruthy();
    gate.rerender(
      <MemoryRouter>
        <PrivateComparisonAcceptGate
          session={{
            ...ownerSession,
            private_comparisons: { available: false },
          }}
        >
          <PrivateComparisonAcceptPage />
        </PrivateComparisonAcceptGate>
      </MemoryRouter>,
    );
    expect(screen.getByText("Comparaisons privées indisponibles")).toBeTruthy();
    expect(
      screen.getByText("Rouvre le lien d’invitation original avec ton compte personnel pour continuer."),
    ).toBeTruthy();
    expect(calls).toBe(0);
  });

  it("rend toutes les erreurs d’invitation génériques et efface le token", async () => {
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations/preview", () =>
        HttpResponse.json(
          { detail: { code: "PRIVATE_COMPARISON_INELIGIBLE", message: "Promotion fictive différente" } },
          { status: 404 },
        ),
      ),
    );
    window.history.replaceState(null, "", `/comparisons/accept#invite=${TOKEN}`);
    renderRoute(<PrivateComparisonAcceptPage />, "/comparisons/accept");
    expect(await screen.findByText("Cette invitation n’est plus disponible")).toBeTruthy();
    expect(screen.queryByText(/Promotion fictive différente/i)).toBeNull();
    expect(document.body.textContent).not.toContain(TOKEN);
  });

  it("efface le token si la portée de session change avant la décision", async () => {
    let acceptCalls = 0;
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations/preview", () => HttpResponse.json(invitationPreview)),
      http.post("*/api/v1/private-comparisons/invitations/accept", () => {
        acceptCalls += 1;
        return HttpResponse.json(relations.comparisons[0], { status: 201 });
      }),
    );
    window.history.replaceState(null, "", `/comparisons/accept#invite=${TOKEN}`);
    const client = testClient();
    renderRoute(<PrivateComparisonAcceptPage />, "/comparisons/accept", client);
    expect(await screen.findByText("Camille Exemple te propose une comparaison")).toBeTruthy();

    client.setQueryData(queryKeys.session, {
      ...ownerSession,
      session_scope: `bss1_${"f".repeat(64)}`,
      account: { ...ownerSession.account!, id: "account-fictif-remplacement" },
    });

    expect(await screen.findByText("Invitation à rouvrir")).toBeTruthy();
    expect(acceptCalls).toBe(0);
    expect(document.body.textContent).not.toContain(TOKEN);
  });

  it("purge le bearer d’acceptation à pagehide et exige de rouvrir le lien après BFCache", async () => {
    let acceptCalls = 0;
    let declineCalls = 0;
    apiMockServer.use(
      http.post("*/api/v1/private-comparisons/invitations/preview", () => HttpResponse.json(invitationPreview)),
      http.post("*/api/v1/private-comparisons/invitations/accept", () => {
        acceptCalls += 1;
        return HttpResponse.json(relations.comparisons[0], { status: 201 });
      }),
      http.post("*/api/v1/private-comparisons/invitations/decline", () => {
        declineCalls += 1;
        return HttpResponse.json({ ok: true });
      }),
    );
    window.history.replaceState(null, "", `/comparisons/accept#invite=${TOKEN}`);
    renderRoute(<PrivateComparisonAcceptPage />, "/comparisons/accept");
    expect(await screen.findByText("Camille Exemple te propose une comparaison")).toBeTruthy();

    act(() => window.dispatchEvent(persistedPageTransition("pagehide")));
    expect(screen.queryByText("Camille Exemple te propose une comparaison")).toBeNull();
    expect(document.documentElement.outerHTML).not.toContain(TOKEN);

    act(() => window.dispatchEvent(persistedPageTransition("pageshow")));
    expect(await screen.findByText("Invitation à rouvrir")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Accepter la comparaison" })).toBeNull();
    expect(acceptCalls).toBe(0);
    expect(declineCalls).toBe(0);
  });
});

describe("listes et révocation", () => {
  it("sépare les relations actives, l’historique et tous les statuts d’invitation", async () => {
    let deletedInvitation = 0;
    apiMockServer.use(
      http.get("*/api/v1/private-comparisons/consent-manifest", () => HttpResponse.json(consentManifest)),
      http.get("*/api/v1/private-comparisons", () => HttpResponse.json(relations)),
      http.get("*/api/v1/private-comparisons/invitations", () => HttpResponse.json(invitations)),
      http.delete(`*/api/v1/private-comparisons/invitations/${INVITATION_ID}`, () => {
        deletedInvitation += 1;
        return HttpResponse.json({ ok: true });
      }),
    );
    const client = testClient();
    render(
      <Providers client={client}>
        <MemoryRouter>
          <PrivateComparisonsPage />
        </MemoryRouter>
      </Providers>,
    );
    expect(await screen.findByText("Camille Exemple")).toBeTruthy();
    expect(screen.getByText("Comparaison terminée")).toBeTruthy();
    expect(screen.queryByText("Morgan Exemple")).toBeNull();
    expect(screen.getByText(/Révoquée le/)).toBeTruthy();
    expect(
      screen.getByText(
        "Pour protéger les deux participants, les identités et résultats ne sont plus affichés après la fin d’une comparaison.",
      ),
    ).toBeTruthy();
    for (const status of ["Active", "Utilisée", "Expirée", "Révoquée"]) {
      expect(screen.getAllByText(status).length).toBeGreaterThan(0);
    }
    expect(screen.queryByRole("button", { name: /copier/i })).toBeNull();
    const user = userEvent.setup();
    const invitationsSection = screen.getByRole("heading", { name: "Invitations créées" }).closest("section")!;
    await user.click(within(invitationsSection).getByRole("button", { name: "Révoquer" }));
    await user.click(screen.getByRole("button", { name: "Révoquer l’invitation" }));
    await waitFor(() => expect(deletedInvitation).toBe(1));
  });
});

describe("détail bilatéral et cache privé", () => {
  it("affiche deux côtés symétriques et uniquement les UE communes", async () => {
    apiMockServer.use(http.get(`*/api/v1/private-comparisons/${RELATION_ID}`, () => HttpResponse.json(detail)));
    const { unmount, client } = renderRoute(<PrivateComparisonDetailPage />, `/comparisons/${RELATION_ID}`);
    expect(await screen.findByText("Comparaison avec Camille Exemple")).toBeTruthy();
    expect(screen.getAllByText("Toi").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Autre participant").length).toBeGreaterThan(0);
    expect(screen.getByText("UE-FICTIVE-201")).toBeTruthy();
    expect(screen.queryByText("UE-NON-COMMUNE")).toBeNull();
    expect(document.body.textContent).not.toMatch(/gagnant|perdant|meilleur|moins bon|évaluation|simulation/i);
    expect(document.title).toBe("Comparaison privée · IMTégrale");
    expect(client.getQueryData(queryKeys.privateComparison(ownerSession.account!.id, RELATION_ID))).toEqual(detail);
    unmount();
    await waitFor(() =>
      expect(client.getQueryData(queryKeys.privateComparison(ownerSession.account!.id, RELATION_ID))).toBeUndefined(),
    );
  });

  it("retire une ancienne valeur du cache dès qu’un 404 est observé", async () => {
    apiMockServer.use(
      http.get(`*/api/v1/private-comparisons/${RELATION_ID}`, () =>
        HttpResponse.json(
          { detail: { code: "PRIVATE_COMPARISON_UNAVAILABLE", message: "Relation privée absente" } },
          { status: 404 },
        ),
      ),
    );
    const client = testClient();
    client.setQueryData(queryKeys.privateComparison(ownerSession.account!.id, RELATION_ID), detail);
    renderRoute(<PrivateComparisonDetailPage />, `/comparisons/${RELATION_ID}`, client);
    expect(await screen.findByText("Cette comparaison n’est plus disponible")).toBeTruthy();
    expect(screen.queryByText("15,2 / 20")).toBeNull();
    expect(client.getQueryData(queryKeys.privateComparison(ownerSession.account!.id, RELATION_ID))).toBeUndefined();
  });

  it("ferme une relation qui expire hors ligne et purge immédiatement son cache", async () => {
    const expiringDetail = {
      ...detail,
      expires_at: new Date(Date.now() + 1_000).toISOString(),
    };
    apiMockServer.use(http.get(`*/api/v1/private-comparisons/${RELATION_ID}`, () => HttpResponse.json(expiringDetail)));
    const client = testClient();
    renderRoute(<PrivateComparisonDetailPage />, `/comparisons/${RELATION_ID}`, client);
    expect(await screen.findByText("Comparaison avec Camille Exemple")).toBeTruthy();

    Object.defineProperty(navigator, "onLine", { configurable: true, value: false });

    expect(
      await screen.findByText("Cette comparaison n’est plus disponible", undefined, {
        timeout: 2_000,
      }),
    ).toBeTruthy();
    expect(screen.queryByText("15,2 / 20")).toBeNull();
    expect(client.getQueryData(queryKeys.privateComparison(ownerSession.account!.id, RELATION_ID))).toBeUndefined();
  });

  it("révoque, purge le détail et revient à la liste", async () => {
    let deleted = 0;
    apiMockServer.use(
      http.get(`*/api/v1/private-comparisons/${RELATION_ID}`, () => HttpResponse.json(detail)),
      http.delete(`*/api/v1/private-comparisons/${RELATION_ID}`, () => {
        deleted += 1;
        return HttpResponse.json({ ok: true });
      }),
    );
    const client = testClient();
    renderRoute(<PrivateComparisonDetailPage />, `/comparisons/${RELATION_ID}`, client);
    const user = userEvent.setup();
    await screen.findByText("Comparaison avec Camille Exemple");
    await user.click(screen.getByRole("button", { name: "Mettre fin à la comparaison" }));
    await user.click(screen.getByRole("button", { name: "Mettre fin" }));
    expect(await screen.findByTestId("comparisons-home")).toBeTruthy();
    expect(deleted).toBe(1);
    expect(client.getQueryData(queryKeys.privateComparison(ownerSession.account!.id, RELATION_ID))).toBeUndefined();
  });
});
