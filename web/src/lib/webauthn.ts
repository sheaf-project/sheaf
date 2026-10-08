/**
 * The browser half of the passkey ceremonies.
 *
 * The server speaks WebAuthn's JSON serialisation (every binary field is
 * base64url without padding), which is exactly what the modern browser API
 * produces and consumes: `PublicKeyCredential.parseCreationOptionsFromJSON`,
 * `parseRequestOptionsFromJSON` and `credential.toJSON()`. Browsers that
 * predate those three (Firefox before 119, Safari before 17.4) get a hand
 * decode and encode of the same shapes, so the server contract is one
 * format regardless of browser.
 *
 * Nothing here decides anything: it hands the server's options to the
 * platform, hands the platform's answer back, and turns the platform's
 * errors into either "say nothing" (the user cancelled) or one sentence.
 */

export type PasskeyCreationOptions = Record<string, unknown>;
export type PasskeyRequestOptions = Record<string, unknown>;
/** `PublicKeyCredential.toJSON()` output, opaque to the client. */
export type PasskeyCredentialJSON = Record<string, unknown>;

export function isPasskeySupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.PublicKeyCredential !== "undefined" &&
    typeof navigator.credentials?.create === "function"
  );
}

// --- base64url -------------------------------------------------------------------

function fromBase64url(value: string): ArrayBuffer {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (value.length % 4)) % 4);
  const bin = atob(padded);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

function toBase64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// --- options: JSON to the shapes navigator.credentials wants -----------------------

type PKC = typeof PublicKeyCredential & {
  parseCreationOptionsFromJSON?: (o: unknown) => PublicKeyCredentialCreationOptions;
  parseRequestOptionsFromJSON?: (o: unknown) => PublicKeyCredentialRequestOptions;
};

function creationOptions(json: PasskeyCreationOptions): PublicKeyCredentialCreationOptions {
  const pkc = PublicKeyCredential as PKC;
  if (typeof pkc.parseCreationOptionsFromJSON === "function") {
    return pkc.parseCreationOptionsFromJSON(json);
  }
  const o = json as {
    rp: PublicKeyCredentialRpEntity;
    user: { id: string; name: string; displayName: string };
    challenge: string;
    pubKeyCredParams: PublicKeyCredentialParameters[];
    timeout?: number;
    excludeCredentials?: { id: string; type: "public-key"; transports?: AuthenticatorTransport[] }[];
    authenticatorSelection?: AuthenticatorSelectionCriteria;
    attestation?: AttestationConveyancePreference;
  };
  return {
    rp: o.rp,
    user: { id: fromBase64url(o.user.id), name: o.user.name, displayName: o.user.displayName },
    challenge: fromBase64url(o.challenge),
    pubKeyCredParams: o.pubKeyCredParams,
    timeout: o.timeout,
    excludeCredentials: (o.excludeCredentials ?? []).map((c) => ({
      id: fromBase64url(c.id),
      type: c.type,
      transports: c.transports,
    })),
    authenticatorSelection: o.authenticatorSelection,
    attestation: o.attestation,
  };
}

function requestOptions(json: PasskeyRequestOptions): PublicKeyCredentialRequestOptions {
  const pkc = PublicKeyCredential as PKC;
  if (typeof pkc.parseRequestOptionsFromJSON === "function") {
    return pkc.parseRequestOptionsFromJSON(json);
  }
  const o = json as {
    challenge: string;
    timeout?: number;
    rpId?: string;
    userVerification?: UserVerificationRequirement;
    allowCredentials?: { id: string; type: "public-key"; transports?: AuthenticatorTransport[] }[];
  };
  return {
    challenge: fromBase64url(o.challenge),
    timeout: o.timeout,
    rpId: o.rpId,
    userVerification: o.userVerification,
    allowCredentials: (o.allowCredentials ?? []).map((c) => ({
      id: fromBase64url(c.id),
      type: c.type,
      transports: c.transports,
    })),
  };
}

// --- credentials: the platform's answer back to JSON --------------------------------

type CredentialWithJSON = PublicKeyCredential & { toJSON?: () => unknown };

function credentialToJSON(cred: PublicKeyCredential): PasskeyCredentialJSON {
  const withJson = cred as CredentialWithJSON;
  if (typeof withJson.toJSON === "function") {
    return withJson.toJSON() as PasskeyCredentialJSON;
  }
  const base = {
    id: cred.id,
    rawId: toBase64url(cred.rawId),
    type: cred.type,
    authenticatorAttachment: (cred as { authenticatorAttachment?: string | null })
      .authenticatorAttachment ?? undefined,
    clientExtensionResults: cred.getClientExtensionResults(),
  };
  const r = cred.response;
  if ("attestationObject" in r) {
    const att = r as AuthenticatorAttestationResponse & { getTransports?: () => string[] };
    return {
      ...base,
      response: {
        clientDataJSON: toBase64url(att.clientDataJSON),
        attestationObject: toBase64url(att.attestationObject),
        transports: typeof att.getTransports === "function" ? att.getTransports() : [],
      },
    };
  }
  const asr = r as AuthenticatorAssertionResponse;
  return {
    ...base,
    response: {
      clientDataJSON: toBase64url(asr.clientDataJSON),
      authenticatorData: toBase64url(asr.authenticatorData),
      signature: toBase64url(asr.signature),
      userHandle: asr.userHandle ? toBase64url(asr.userHandle) : null,
    },
  };
}

// --- the two ceremonies ----------------------------------------------------------

/** Run `navigator.credentials.create()` with the server's options. */
export async function createPasskey(
  options: PasskeyCreationOptions,
): Promise<PasskeyCredentialJSON> {
  const cred = await navigator.credentials.create({ publicKey: creationOptions(options) });
  if (!cred) throw new PasskeyCancelled();
  return credentialToJSON(cred as PublicKeyCredential);
}

/** Run `navigator.credentials.get()` with the server's options. */
export async function getPasskey(
  options: PasskeyRequestOptions,
): Promise<PasskeyCredentialJSON> {
  const cred = await navigator.credentials.get({ publicKey: requestOptions(options) });
  if (!cred) throw new PasskeyCancelled();
  return credentialToJSON(cred as PublicKeyCredential);
}

/** The user dismissed the platform sheet (or it timed out). Say nothing. */
export class PasskeyCancelled extends Error {
  constructor() {
    super("cancelled");
    this.name = "PasskeyCancelled";
  }
}

/**
 * One sentence for a platform error, or null when the right response is no
 * message at all (the user cancelled). The browser reports every failure
 * as a DOMException whose `name` is the only stable signal.
 */
export function passkeyErrorMessage(err: unknown): string | null {
  if (err instanceof PasskeyCancelled) return null;
  if (err instanceof DOMException) {
    switch (err.name) {
      case "NotAllowedError":
        // Dismissed, timed out, or no user verification offered. The
        // platform already showed its own sheet; a second message nags.
        return null;
      case "InvalidStateError":
        return "This device already has a passkey for this account.";
      case "NotSupportedError":
        return "This browser or device cannot create that kind of passkey.";
      case "SecurityError":
        return "This instance's address does not match the one the passkey rules allow. Sign in with your password.";
      case "AbortError":
        return null;
      default:
        return "The passkey step did not complete. Try again, or sign in with your password.";
    }
  }
  if (err instanceof Error && err.message) return err.message;
  return "The passkey step did not complete. Try again, or sign in with your password.";
}
