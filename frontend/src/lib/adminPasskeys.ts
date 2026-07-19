import {
  adminAdminPasskeyAuthenticationOptions,
  adminAdminPasskeyRegistrationOptions,
  adminCreateAdminPasskey,
  adminVerifyAdminPasskeyAssertion,
} from "../generated/api/sdk.gen";
import type { AdminSession } from "../types";
import { apiData, throwOnApiError } from "./generatedApi";
import { createPasskeyCredential, getPasskeyCredential, passkeysSupported } from "./passkeys";

export async function registerAdminPasskey(name: string): Promise<AdminSession> {
  if (!passkeysSupported()) throw new Error("Les passkeys ne sont pas disponibles sur ce navigateur.");
  const options = await apiData(adminAdminPasskeyRegistrationOptions({ throwOnError: throwOnApiError }));
  const credential = await createPasskeyCredential(options.publicKey);
  return apiData(
    adminCreateAdminPasskey({
      body: { challenge_id: options.challenge_id, name, credential },
      throwOnError: throwOnApiError,
    }),
  );
}

export async function verifyAdminPasskey(): Promise<AdminSession> {
  if (!passkeysSupported()) throw new Error("Les passkeys ne sont pas disponibles sur ce navigateur.");
  const options = await apiData(adminAdminPasskeyAuthenticationOptions({ throwOnError: throwOnApiError }));
  const credential = await getPasskeyCredential(options.publicKey);
  return apiData(
    adminVerifyAdminPasskeyAssertion({
      body: { challenge_id: options.challenge_id, credential },
      throwOnError: throwOnApiError,
    }),
  );
}
