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
