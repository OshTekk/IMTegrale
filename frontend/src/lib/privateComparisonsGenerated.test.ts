import { afterEach, describe, expect, it, vi } from "vitest";
import {
  privateComparisonsAcceptPrivateComparisonInvitation,
  privateComparisonsCreatePrivateComparisonInvitation,
} from "../generated/api/sdk.gen";
import type {
  PrivateComparisonConsentManifestResponse,
  PrivateComparisonInvitationCreatedResponse,
  PrivateComparisonInvitationListResponse,
} from "../generated/api/types.gen";
import { apiData, throwOnApiError } from "./generatedApi";

const creatorConsent = {
  consent_version: 3,
  actor_role: "creator",
  manifest_digest: "a".repeat(64),
  acknowledge_identity_visibility: true,
  acknowledge_academic_scope: true,
  acknowledge_copy_risk: true,
} as const;
const acceptorConsent = {
  ...creatorConsent,
  actor_role: "acceptor",
  manifest_digest: "b".repeat(64),
} as const;
const sessionBinding = `bss1_${"f".repeat(64)}`;

const consentManifest: PrivateComparisonConsentManifestResponse = {
  consent_version: 3,
  actor_role: "creator",
  manifest_digest: creatorConsent.manifest_digest,
  identity_disclosure: {
    description: "L’identité du créateur est visible avant acceptation.",
    confirmation: "J’accepte cette visibilité.",
  },
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
  ],
  excluded_sections: [{ key: "detailed_assessments", label: "Détail des évaluations" }],
  duration_and_revocation: {
    duration: "Durée bornée.",
    expiration: "Expiration automatique.",
    immediate_revocation: "Révocation immédiate.",
    minimal_history: "Historique minimal.",
    private_only: "Consultation privée.",
  },
  academic_scope_confirmation: "J’accepte le périmètre académique décrit.",
  copy_risk: {
    description: "L’autre participant peut recopier les informations visibles.",
    confirmation: "Je comprends le risque de copie.",
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("private comparison generated contract", () => {
  it("keeps the one-shot token in the typed creation response", async () => {
    const oneShot = "pcinv1_" + "a".repeat(43);
    const payload: PrivateComparisonInvitationCreatedResponse = {
      public_id: "pci_" + "b".repeat(24),
      token: oneShot,
      session_scope: `bss1_${"c".repeat(64)}`,
      expires_at: "2099-01-08T00:00:00Z",
      relationship_duration_days: 30,
      consent_version: 3,
      consent_manifest: consentManifest,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(payload), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const created = await apiData(
      privateComparisonsCreatePrivateComparisonInvitation({
        body: { ...creatorConsent, duration_days: 30 },
        headers: { "X-IMTEGRALE-SESSION-BINDING": sessionBinding },
        throwOnError: throwOnApiError,
      }),
    );

    expect(created.token).toBe(oneShot);
    const request = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]?.[0];
    expect(request).toBeInstanceOf(Request);
    expect((request as Request).headers.get("X-IMTEGRALE-SESSION-BINDING")).toBe(sessionBinding);
    const listContract: PrivateComparisonInvitationListResponse = { invitations: [] };
    expect(listContract).toEqual({ invitations: [] });
  });

  it("sends an invitation token only in the accept POST body", async () => {
    const token = "pcinv1_" + "c".repeat(43);
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          public_id: "pc_" + "d".repeat(24),
          other_participant: { official_name: "Etudiante Fixture" },
          status: "active",
          activated_at: "2099-01-01T00:00:00Z",
          expires_at: "2099-01-31T00:00:00Z",
          academic_verified_at: "2099-01-01T00:00:00Z",
          freshness: "current",
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await apiData(
      privateComparisonsAcceptPrivateComparisonInvitation({
        body: { ...acceptorConsent, token },
        headers: { "X-IMTEGRALE-SESSION-BINDING": sessionBinding },
        throwOnError: throwOnApiError,
      }),
    );

    const request = fetchMock.mock.calls[0]?.[0];
    expect(request).toBeInstanceOf(Request);
    const typedRequest = request as Request;
    expect(typedRequest.method).toBe("POST");
    expect(typedRequest.url).not.toContain(token);
    expect(typedRequest.headers.get("X-IMTEGRALE-SESSION-BINDING")).toBe(sessionBinding);
    await expect(typedRequest.clone().json()).resolves.toMatchObject({ token });
  });
});
