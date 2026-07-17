import { api } from "./api";
import type { PasskeyItem, Session } from "../types";

interface WebAuthnOptionsResponse {
  challenge_id: string;
  publicKey: Record<string, unknown>;
}

function decodeBase64url(value: string): ArrayBuffer {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const binary = window.atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "="));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes.buffer;
}

function encodeBase64url(value: ArrayBuffer): string {
  const bytes = new Uint8Array(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function decodeDescriptors(value: unknown): PublicKeyCredentialDescriptor[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.map((item) => {
    const descriptor = item as { id: string; type: PublicKeyCredentialType; transports?: AuthenticatorTransport[] };
    return { ...descriptor, id: decodeBase64url(descriptor.id) };
  });
}

function creationOptions(raw: Record<string, unknown>): PublicKeyCredentialCreationOptions {
  const user = raw.user as { id: string; name: string; displayName: string };
  return {
    ...raw,
    challenge: decodeBase64url(raw.challenge as string),
    user: { ...user, id: decodeBase64url(user.id) },
    excludeCredentials: decodeDescriptors(raw.excludeCredentials),
  } as PublicKeyCredentialCreationOptions;
}

function requestOptions(raw: Record<string, unknown>): PublicKeyCredentialRequestOptions {
  return {
    ...raw,
    challenge: decodeBase64url(raw.challenge as string),
    allowCredentials: decodeDescriptors(raw.allowCredentials),
  } as PublicKeyCredentialRequestOptions;
}

function credentialPayload(credential: PublicKeyCredential): Record<string, unknown> {
  const response = credential.response;
  const base = {
    id: credential.id,
    rawId: encodeBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    clientExtensionResults: credential.getClientExtensionResults(),
  };
  // Safari can expose WebAuthn without publishing every response constructor globally.
  if ("attestationObject" in response) {
    const attestation = response as AuthenticatorAttestationResponse;
    return {
      ...base,
      response: {
        clientDataJSON: encodeBase64url(attestation.clientDataJSON),
        attestationObject: encodeBase64url(attestation.attestationObject),
        transports: attestation.getTransports(),
      },
    };
  }
  const assertion = response as AuthenticatorAssertionResponse;
  return {
    ...base,
    response: {
      clientDataJSON: encodeBase64url(assertion.clientDataJSON),
      authenticatorData: encodeBase64url(assertion.authenticatorData),
      signature: encodeBase64url(assertion.signature),
      userHandle: assertion.userHandle ? encodeBase64url(assertion.userHandle) : null,
    },
  };
}

export function passkeysSupported(): boolean {
  return typeof window.PublicKeyCredential !== "undefined" && Boolean(navigator.credentials);
}

export async function registerPasskey(name: string): Promise<PasskeyItem> {
  if (!passkeysSupported()) throw new Error("Les passkeys ne sont pas disponibles sur ce navigateur.");
  const options = await api<WebAuthnOptionsResponse>(
    "/api/v1/auth/passkeys/registration/options",
    { method: "POST", body: "{}" },
  );
  const credential = await navigator.credentials.create({
    publicKey: creationOptions(options.publicKey),
  });
  if (!(credential instanceof PublicKeyCredential)) throw new Error("Création de passkey annulée.");
  return api<PasskeyItem>("/api/v1/auth/passkeys", {
    method: "POST",
    body: JSON.stringify({
      challenge_id: options.challenge_id,
      name,
      credential: credentialPayload(credential),
    }),
  });
}

export async function authenticateWithPasskey(): Promise<Session> {
  if (!passkeysSupported()) throw new Error("Les passkeys ne sont pas disponibles sur ce navigateur.");
  const options = await api<WebAuthnOptionsResponse>(
    "/api/v1/auth/login/passkey/options",
    { method: "POST", body: "{}" },
  );
  const credential = await navigator.credentials.get({
    publicKey: requestOptions(options.publicKey),
  });
  if (!(credential instanceof PublicKeyCredential)) throw new Error("Connexion par passkey annulée.");
  return api<Session>("/api/v1/auth/login/passkey", {
    method: "POST",
    body: JSON.stringify({
      challenge_id: options.challenge_id,
      credential: credentialPayload(credential),
    }),
  });
}
