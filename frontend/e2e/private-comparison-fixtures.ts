import { createHash, randomBytes } from "node:crypto";
import type { Page, Route } from "@playwright/test";
import type { FakeAppState } from "./app-fixtures";

export type ComparisonActor =
  "alice" | "camille" | "viewer" | "token" | "other-program" | "other-promotion" | "outsider";

interface SyntheticActor {
  accountId: string;
  name: string;
  program: string;
  promotion: number;
  role: "owner" | "viewer";
  authMethod: "imt" | "passkey" | "token";
}

interface SyntheticInvitation {
  publicId: string;
  creator: ComparisonActor;
  tokenDigest: string;
  status: "active" | "consumed" | "expired" | "revoked";
  createdAt: string;
  expiresAt: string;
  durationDays: number;
}

interface SyntheticRelation {
  publicId: string;
  accountA: ComparisonActor;
  accountB: ComparisonActor;
  status: "active" | "expired" | "revoked";
  activatedAt: string;
  expiresAt: string;
}

export interface PrivateComparisonE2eState {
  enabled: boolean;
  invitations: SyntheticInvitation[];
  relations: SyntheticRelation[];
  privateRequestUrls: string[];
  secretInRequestUrl: boolean;
  tokenBodyPosts: number;
  createCalls: number;
  previewCalls: number;
  acceptCalls: number;
  declineCalls: number;
  revokeInvitationCalls: number;
  revokeRelationCalls: number;
}

const actors: Record<ComparisonActor, SyntheticActor> = {
  alice: {
    accountId: "account-comparison-alice-fictive",
    name: "Alice Exemple",
    program: "FIP-FICTIF",
    promotion: 2028,
    role: "owner",
    authMethod: "imt",
  },
  camille: {
    accountId: "account-comparison-camille-fictif",
    name: "Camille Exemple au nom officiel volontairement très long",
    program: "FIP-FICTIF",
    promotion: 2028,
    role: "owner",
    authMethod: "passkey",
  },
  viewer: {
    accountId: "account-comparison-viewer-fictif",
    name: "Viewer Exemple",
    program: "FIP-FICTIF",
    promotion: 2028,
    role: "viewer",
    authMethod: "token",
  },
  token: {
    accountId: "account-comparison-token-fictif",
    name: "Owner Token Exemple",
    program: "FIP-FICTIF",
    promotion: 2028,
    role: "owner",
    authMethod: "token",
  },
  "other-program": {
    accountId: "account-comparison-program-fictif",
    name: "Programme Exemple",
    program: "PROGRAMME-DIFFERENT-FICTIF",
    promotion: 2028,
    role: "owner",
    authMethod: "imt",
  },
  "other-promotion": {
    accountId: "account-comparison-promotion-fictive",
    name: "Promotion Exemple",
    program: "FIP-FICTIF",
    promotion: 2029,
    role: "owner",
    authMethod: "imt",
  },
  outsider: {
    accountId: "account-comparison-outsider-fictif",
    name: "Personne Extérieure Exemple",
    program: "FIP-FICTIF",
    promotion: 2028,
    role: "owner",
    authMethod: "imt",
  },
};

const consentManifest = {
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
        { response_path: "participant.summary.academic_verified_at", label: "Dernière vérification" },
        { response_path: "participant.summary.freshness", label: "Fraîcheur" },
        { response_path: "participant.summary.ue_count", label: "Nombre d’UE" },
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
        { response_path: "common_ues.participant.freshness", label: "Fraîcheur" },
        { response_path: "common_ues.participant.verified_at", label: "Dernière vérification" },
      ],
    },
    {
      key: "relation_metadata",
      title: "Relation privée",
      fields: [
        { response_path: "relation.public_id", label: "Identifiant opaque" },
        { response_path: "relation.status", label: "Statut" },
        { response_path: "relation.activated_at", label: "Activation" },
        { response_path: "relation.expires_at", label: "Expiration" },
        { response_path: "relation.consent_version", label: "Version du consentement" },
        { response_path: "relation.calculated_at", label: "Date de calcul" },
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
    minimal_history: "L’historique conserve seulement le statut et les dates.",
    private_only: "La comparaison reste privée aux deux participants.",
  },
  copy_risk: "L’autre participant peut recopier ou capturer les informations visibles avant une révocation.",
};

function opaque(prefix: "pc_" | "pci_", bytes = 18) {
  return `${prefix}${randomBytes(bytes).toString("base64url")}`;
}

function invitationToken() {
  return `pcinv1_${randomBytes(32).toString("base64url")}`;
}

function tokenDigest(token: string) {
  return createHash("sha256").update("imtegrale-private-comparison-invitation-v1\0").update(token).digest("hex");
}

function available(actor: ComparisonActor, state: PrivateComparisonE2eState) {
  const value = actors[actor];
  return state.enabled && value.role === "owner" && ["imt", "passkey"].includes(value.authMethod);
}

export function createPrivateComparisonE2eState(): PrivateComparisonE2eState {
  return {
    enabled: true,
    invitations: [],
    relations: [],
    privateRequestUrls: [],
    secretInRequestUrl: false,
    tokenBodyPosts: 0,
    createCalls: 0,
    previewCalls: 0,
    acceptCalls: 0,
    declineCalls: 0,
    revokeInvitationCalls: 0,
    revokeRelationCalls: 0,
  };
}

export function configurePrivateComparisonSession(
  appState: FakeAppState,
  comparisonState: PrivateComparisonE2eState,
  actor: ComparisonActor,
) {
  const value = actors[actor];
  appState.session = {
    ...appState.session,
    authenticated: true,
    session_scope: `bss1_${createHash("sha256").update(`synthetic-browser-session:${actor}`).digest("hex")}`,
    role: value.role,
    auth_method: value.authMethod,
    account: {
      id: value.accountId,
      display_name: value.name,
      imt_username: `${actor}.fictif`,
    },
    private_comparisons: { available: available(actor, comparisonState) },
  };
  appState.dashboard = {
    ...appState.dashboard,
    account: {
      ...(appState.dashboard.account as Record<string, unknown>),
      id: value.accountId,
      display_name: value.name,
      imt_username: `${actor}.fictif`,
    },
  };
}

export function seedInvitation(
  state: PrivateComparisonE2eState,
  creator: ComparisonActor = "alice",
  status: SyntheticInvitation["status"] = "active",
) {
  const token = invitationToken();
  state.invitations.push({
    publicId: opaque("pci_"),
    creator,
    tokenDigest: tokenDigest(token),
    status,
    createdAt: "2099-07-29T10:00:00Z",
    expiresAt: status === "expired" ? "2020-08-05T10:00:00Z" : "2099-08-05T10:00:00Z",
    durationDays: 30,
  });
  return token;
}

export function seedRelation(state: PrivateComparisonE2eState, status: SyntheticRelation["status"] = "active") {
  const relation: SyntheticRelation = {
    publicId: opaque("pc_"),
    accountA: "alice",
    accountB: "camille",
    status,
    activatedAt: "2099-07-29T11:00:00Z",
    expiresAt: status === "expired" ? "2020-08-28T11:00:00Z" : "2099-08-28T11:00:00Z",
  };
  state.relations.push(relation);
  return relation.publicId;
}

function relationList(state: PrivateComparisonE2eState, actor: ComparisonActor) {
  return {
    comparisons: state.relations
      .filter((relation) => relation.accountA === actor || relation.accountB === actor)
      .map((relation) => {
        const other = relation.accountA === actor ? relation.accountB : relation.accountA;
        return {
          public_id: relation.publicId,
          other_participant: { official_name: actors[other].name },
          status: relation.status,
          activated_at: relation.activatedAt,
          expires_at: relation.expiresAt,
          academic_verified_at: "2099-07-29T09:00:00Z",
          freshness: actor === "alice" ? "current" : "recommended",
        };
      }),
  };
}

function summary(actor: ComparisonActor) {
  const alice = actor === "alice";
  return {
    identity: { official_name: actors[actor].name },
    summary: {
      average: alice ? 13.4 : 15.2,
      gpa: alice ? 3.1 : 3.5,
      validated_ects: alice ? 54 : 57,
      ue_count: alice ? 12 : 13,
      freshness: alice ? "current" : "recommended",
      academic_verified_at: alice ? "2099-07-29T09:00:00Z" : "2099-07-28T09:00:00Z",
      grade_distribution: alice
        ? { A: 1, B: 3, C: 4, D: 2, E: 1, FX: 1, F: 0 }
        : { A: 3, B: 4, C: 3, D: 1, E: 1, FX: 0, F: 1 },
    },
  };
}

function ueSide(actor: ComparisonActor) {
  const alice = actor === "alice";
  return {
    title: "UE commune de démonstration entièrement fictive",
    year: "2",
    semester: "S7",
    average: alice ? 12.8 : 14.7,
    grade: alice ? "C" : "B",
    gpa: alice ? 3 : 3.5,
    earned_ects: 5,
    allocated_ects: 5,
    validated: true,
    freshness: alice ? "current" : "recommended",
    verified_at: alice ? "2099-07-29T09:00:00Z" : "2099-07-28T09:00:00Z",
  };
}

function relationDetail(relation: SyntheticRelation, actor: ComparisonActor) {
  const other = relation.accountA === actor ? relation.accountB : relation.accountA;
  return {
    public_id: relation.publicId,
    status: "active",
    consent_version: 2,
    activated_at: relation.activatedAt,
    expires_at: relation.expiresAt,
    calculated_at: "2099-07-29T12:00:00Z",
    current: summary(actor),
    other: summary(other),
    common_ues: [
      {
        official_code: "UE-COMMUNE-FICTIVE",
        current: ueSide(actor),
        other: ueSide(other),
      },
    ],
  };
}

const responseHeaders = {
  "Cache-Control": "private, no-store",
  Pragma: "no-cache",
  Vary: "Cookie",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
};

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json; charset=utf-8",
    headers: responseHeaders,
    body: JSON.stringify(body),
  });
}

async function unavailable(route: Route) {
  await json(
    route,
    { detail: { code: "PRIVATE_COMPARISON_UNAVAILABLE", message: "Comparaison privée indisponible." } },
    404,
  );
}

export async function installFakePrivateComparisonApi(
  page: Page,
  state: PrivateComparisonE2eState,
  actor: ComparisonActor,
) {
  await page.route("**/api/v1/private-comparisons**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    state.privateRequestUrls.push(request.url());
    if (request.url().includes("pcinv1_")) state.secretInRequestUrl = true;
    if (!available(actor, state)) {
      await unavailable(route);
      return;
    }

    const body = request.postDataJSON() as Record<string, unknown> | null;
    if (body && typeof body.token === "string") state.tokenBodyPosts += 1;

    if (url.pathname === "/api/v1/private-comparisons/consent-manifest" && request.method() === "GET") {
      await json(route, consentManifest);
      return;
    }
    if (url.pathname === "/api/v1/private-comparisons/invitations" && request.method() === "POST") {
      state.createCalls += 1;
      const token = invitationToken();
      const invitation: SyntheticInvitation = {
        publicId: opaque("pci_"),
        creator: actor,
        tokenDigest: tokenDigest(token),
        status: "active",
        createdAt: "2099-07-29T10:00:00Z",
        expiresAt: "2099-08-05T10:00:00Z",
        durationDays: Number(body?.duration_days ?? 30),
      };
      state.invitations.push(invitation);
      await json(
        route,
        {
          public_id: invitation.publicId,
          token,
          consent_version: 2,
          expires_at: invitation.expiresAt,
          relationship_duration_days: invitation.durationDays,
          consent_manifest: consentManifest,
        },
        201,
      );
      return;
    }
    if (url.pathname === "/api/v1/private-comparisons/invitations" && request.method() === "GET") {
      await json(route, {
        invitations: state.invitations
          .filter((invitation) => invitation.creator === actor)
          .map((invitation) => ({
            public_id: invitation.publicId,
            created_at: invitation.createdAt,
            expires_at: invitation.expiresAt,
            relationship_duration_days: invitation.durationDays,
            status: invitation.status,
          })),
      });
      return;
    }
    if (
      ["preview", "accept", "decline"].some(
        (action) => url.pathname === `/api/v1/private-comparisons/invitations/${action}`,
      ) &&
      request.method() === "POST"
    ) {
      const action = url.pathname.split("/").at(-1)!;
      if (action === "preview") state.previewCalls += 1;
      if (action === "accept") state.acceptCalls += 1;
      if (action === "decline") state.declineCalls += 1;
      const suppliedToken = typeof body?.token === "string" ? body.token : "";
      const invitation = state.invitations.find(
        (candidate) => candidate.tokenDigest === tokenDigest(suppliedToken) && candidate.status === "active",
      );
      if (!invitation || invitation.creator === actor) {
        await unavailable(route);
        return;
      }
      const creator = actors[invitation.creator];
      const recipient = actors[actor];
      if (creator.program !== recipient.program || creator.promotion !== recipient.promotion) {
        await unavailable(route);
        return;
      }
      if (action === "preview") {
        await json(route, {
          creator: { official_name: creator.name },
          expires_at: invitation.expiresAt,
          relationship_duration_days: invitation.durationDays,
          consent_version: 2,
          consent_manifest: consentManifest,
        });
        return;
      }
      if (action === "decline") {
        invitation.status = "revoked";
        await json(route, { ok: true });
        return;
      }
      const existing = state.relations.find(
        (relation) =>
          relation.status === "active" &&
          [relation.accountA, relation.accountB].includes(actor) &&
          [relation.accountA, relation.accountB].includes(invitation.creator),
      );
      if (existing) {
        await unavailable(route);
        return;
      }
      invitation.status = "consumed";
      const relation: SyntheticRelation = {
        publicId: opaque("pc_"),
        accountA: invitation.creator,
        accountB: actor,
        status: "active",
        activatedAt: "2099-07-29T11:00:00Z",
        expiresAt: "2099-08-28T11:00:00Z",
      };
      state.relations.push(relation);
      await json(route, relationList(state, actor).comparisons.at(-1), 201);
      return;
    }
    if (url.pathname === "/api/v1/private-comparisons" && request.method() === "GET") {
      await json(route, relationList(state, actor));
      return;
    }
    const invitationPrefix = "/api/v1/private-comparisons/invitations/";
    if (url.pathname.startsWith(invitationPrefix) && request.method() === "DELETE") {
      const invitation = state.invitations.find(
        (candidate) => candidate.publicId === decodeURIComponent(url.pathname.slice(invitationPrefix.length)),
      );
      if (!invitation || invitation.creator !== actor) {
        await unavailable(route);
        return;
      }
      state.revokeInvitationCalls += 1;
      invitation.status = "revoked";
      await json(route, { ok: true });
      return;
    }
    const relationPrefix = "/api/v1/private-comparisons/";
    if (url.pathname.startsWith(relationPrefix)) {
      const relation = state.relations.find(
        (candidate) => candidate.publicId === decodeURIComponent(url.pathname.slice(relationPrefix.length)),
      );
      if (!relation || relation.status !== "active" || (relation.accountA !== actor && relation.accountB !== actor)) {
        await unavailable(route);
        return;
      }
      if (request.method() === "GET") {
        await json(route, relationDetail(relation, actor));
        return;
      }
      if (request.method() === "DELETE") {
        state.revokeRelationCalls += 1;
        relation.status = "revoked";
        await json(route, { ok: true });
        return;
      }
    }
    await unavailable(route);
  });
}
