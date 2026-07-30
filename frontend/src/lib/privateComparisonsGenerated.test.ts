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

const consent = {
  consent_version: 2,
  acknowledge_identity_visibility: true,
  acknowledge_academic_scope: true,
  acknowledge_copy_risk: true,
} as const;

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
  ],
  excluded_sections: [{ key: "detailed_assessments", label: "Détail des évaluations" }],
  duration_and_revocation: {
    duration: "Durée bornée.",
    expiration: "Expiration automatique.",
    immediate_revocation: "Révocation immédiate.",
    minimal_history: "Historique minimal.",
    private_only: "Consultation privée.",
  },
  copy_risk: "L’autre participant peut recopier les informations visibles.",
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
      expires_at: "2099-01-08T00:00:00Z",
      relationship_duration_days: 30,
      consent_version: 2,
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
        body: { ...consent, duration_days: 30 },
        throwOnError: throwOnApiError,
      }),
    );

    expect(created.token).toBe(oneShot);
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
        body: { ...consent, token },
        throwOnError: throwOnApiError,
      }),
    );

    const request = fetchMock.mock.calls[0]?.[0];
    expect(request).toBeInstanceOf(Request);
    const typedRequest = request as Request;
    expect(typedRequest.method).toBe("POST");
    expect(typedRequest.url).not.toContain(token);
    await expect(typedRequest.clone().json()).resolves.toMatchObject({ token });
  });
});
