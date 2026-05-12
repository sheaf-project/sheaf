import { apiFetch } from "./api-client";

// Build provenance baked into the bundle by Vite at build time. Empty in dev
// or when an image was built outside CI.
export const FRONTEND_BUILD = {
  gitCommit: import.meta.env.VITE_GIT_COMMIT || "",
  gitTag: import.meta.env.VITE_GIT_TAG || "",
  buildTime: import.meta.env.VITE_BUILD_TIME || "",
} as const;

export interface BackendVersion {
  version: string;
  git_commit: string | null;
  git_tag: string | null;
  build_time: string | null;
  mode: string;
}

export function getBackendVersion() {
  return apiFetch<BackendVersion>("/v1/version", { skipRefresh: true });
}

export interface BuildManifestFile {
  path: string;
  size: number;
  integrity: string;
}

export interface BuildManifest {
  version: number;
  git_commit: string;
  git_tag: string;
  build_time: string;
  files: BuildManifestFile[];
}

// Fetched from the served bundle, not the API — the manifest sits next to
// index.html and reflects what *this* nginx is actually shipping.
export async function getBuildManifest(): Promise<BuildManifest> {
  const res = await fetch("/build-manifest.json", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`build manifest unavailable (${res.status})`);
  }
  return (await res.json()) as BuildManifest;
}

export function shortSha(sha: string | null | undefined): string {
  if (!sha) return "";
  return sha.slice(0, 7);
}

// Display label for the running build. Prefers the tag (e.g. "v0.1.0"), falls
// back to a short commit, then to "dev".
export function buildLabel(opts: {
  gitTag?: string | null;
  gitCommit?: string | null;
}): string {
  if (opts.gitTag) return opts.gitTag;
  if (opts.gitCommit) return shortSha(opts.gitCommit);
  return "dev";
}

// --- Live bundle verification -----------------------------------------------
//
// SubtleCrypto-based SRI re-verification. For each file in the manifest:
//   1. Fetch the file from the running server.
//   2. Hash the response bytes with SHA-384.
//   3. Encode as "sha384-<base64>" — the SRI integrity format.
//   4. Compare to the manifest's recorded integrity.
//
// Identical in spirit to what the browser does for <script integrity=...> tags
// at load time, except this also covers index.html and static assets that
// don't carry SRI attributes. Runs entirely client-side; the server can't
// influence the hash function or the comparison, only the bytes it serves.

export type VerifyStatus = "pending" | "match" | "mismatch" | "error";

export interface VerifyFileResult {
  path: string;
  expected: string;
  actual: string | null;
  status: VerifyStatus;
  error?: string;
}

function bytesToBase64(bytes: Uint8Array): string {
  // Browsers don't ship a built-in Uint8Array → base64 yet (Uint8Array.toBase64
  // is Baseline-pending), so go via a binary string. btoa wants char codes
  // <256, which is what a byte array gives us.
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

async function sha384Sri(buf: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-384", buf);
  return "sha384-" + bytesToBase64(new Uint8Array(digest));
}

export async function verifyManifestFile(
  file: BuildManifestFile,
): Promise<VerifyFileResult> {
  try {
    const res = await fetch("/" + file.path, { cache: "no-store" });
    if (!res.ok) {
      return {
        path: file.path,
        expected: file.integrity,
        actual: null,
        status: "error",
        error: `HTTP ${res.status}`,
      };
    }
    const actual = await sha384Sri(await res.arrayBuffer());
    return {
      path: file.path,
      expected: file.integrity,
      actual,
      status: actual === file.integrity ? "match" : "mismatch",
    };
  } catch (err) {
    return {
      path: file.path,
      expected: file.integrity,
      actual: null,
      status: "error",
      error: err instanceof Error ? err.message : String(err),
    };
  }
}
