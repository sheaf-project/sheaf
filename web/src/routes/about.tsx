import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { Check, Loader2, X } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  type BuildManifest,
  FRONTEND_BUILD,
  getBackendVersion,
  getBuildManifest,
  shortSha,
  verifyManifestFile,
  type VerifyFileResult,
} from "@/lib/version";

const REPO = "sheaf-project/sheaf";
const REGISTRY = "ghcr.io/sheaf-project";
const GH_PACKAGES = `https://github.com/${REPO}/pkgs/container`;
const REKOR_SEARCH = "https://search.sigstore.dev/";

// Subset of files in the manifest we surface as "always verify". The full set
// can include hundreds of source-map and fingerprint-named chunks that aren't
// individually interesting to eyeball; verifying *every* file is still what
// we do — this is just what gets pinned to the top of the per-file list when
// rendering results.
const PINNED_VERIFY_PATHS = [
  "index.html",
  "build-manifest.json",
];

function ResultIcon({ status }: { status: VerifyFileResult["status"] }) {
  if (status === "pending")
    return <Loader2 className="size-3.5 animate-spin text-muted-foreground" />;
  if (status === "match")
    return (
      <Check className="size-3.5 text-emerald-600 dark:text-emerald-400" />
    );
  return <X className="size-3.5 text-destructive-foreground" />;
}

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="grid grid-cols-[8rem_1fr] gap-3 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono break-all">{value || "—"}</span>
    </div>
  );
}

export function AboutPage() {
  const { data: backend, isLoading } = useQuery({
    queryKey: ["version", "backend"],
    queryFn: getBackendVersion,
  });
  const { data: manifest, error: manifestError } = useQuery({
    queryKey: ["version", "manifest"],
    queryFn: getBuildManifest,
    retry: false,
  });
  const [verifyResults, setVerifyResults] = useState<VerifyFileResult[]>([]);
  const [verifying, setVerifying] = useState(false);
  const [showAllVerify, setShowAllVerify] = useState(false);

  async function runVerify(m: BuildManifest) {
    setVerifying(true);
    // Seed every file as pending so the UI shows immediate feedback.
    const initial: VerifyFileResult[] = m.files.map((f) => ({
      path: f.path,
      expected: f.integrity,
      actual: null,
      status: "pending",
    }));
    setVerifyResults(initial);
    // Fan out with bounded concurrency (4 in flight) — a typical bundle has
    // ~60 files, and hammering the server with all of them at once both
    // looks dramatic in the network panel and isn't faster on http/1.1.
    const concurrency = 4;
    let next = 0;
    const accum = [...initial];
    async function worker() {
      while (true) {
        const idx = next++;
        if (idx >= m.files.length) return;
        const result = await verifyManifestFile(m.files[idx]);
        accum[idx] = result;
        // Snapshot so React re-renders with each completion.
        setVerifyResults([...accum]);
      }
    }
    await Promise.all(
      Array.from({ length: Math.min(concurrency, m.files.length) }, worker),
    );
    setVerifying(false);
  }

  const verifySummary = (() => {
    if (verifyResults.length === 0) return null;
    const match = verifyResults.filter((r) => r.status === "match").length;
    const mismatch = verifyResults.filter((r) => r.status === "mismatch").length;
    const errored = verifyResults.filter((r) => r.status === "error").length;
    const pending = verifyResults.filter((r) => r.status === "pending").length;
    return { match, mismatch, errored, pending, total: verifyResults.length };
  })();

  const sortedVerifyResults = (() => {
    if (verifyResults.length === 0) return [];
    const pinned: VerifyFileResult[] = [];
    const rest: VerifyFileResult[] = [];
    for (const r of verifyResults) {
      if (PINNED_VERIFY_PATHS.includes(r.path)) pinned.push(r);
      else rest.push(r);
    }
    // Inside each bucket, surface failures and pending work above passes so the
    // interesting rows are visible without scrolling.
    const order = (r: VerifyFileResult) =>
      r.status === "mismatch" || r.status === "error"
        ? 0
        : r.status === "pending"
          ? 1
          : 2;
    pinned.sort((a, b) => order(a) - order(b));
    rest.sort((a, b) => order(a) - order(b) || a.path.localeCompare(b.path));
    return [...pinned, ...rest];
  })();

  const frontendCommit = FRONTEND_BUILD.gitCommit;
  const backendCommit = backend?.git_commit ?? "";
  const commitsMatch =
    !!frontendCommit && !!backendCommit && frontendCommit === backendCommit;
  const eitherUnknown = !frontendCommit || !backendCommit;

  const ref = backend?.git_tag
    ? `${REGISTRY}/sheaf:${backend.git_tag.replace(/^v/, "")}`
    : backendCommit
      ? `${REGISTRY}/sheaf:sha-${backendCommit}`
      : null;

  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader title="About this build" />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-3">
            Backend
            {isLoading ? (
              <Skeleton className="h-5 w-16" />
            ) : (
              <Badge variant="outline" className="font-mono">
                {backend?.version ?? "unknown"}
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <Field label="Version" value={backend?.version} />
          <Field label="Git tag" value={backend?.git_tag} />
          <Field label="Git commit" value={backendCommit || null} />
          <Field label="Build time" value={backend?.build_time} />
          <Field label="Mode" value={backend?.mode} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Frontend</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <Field label="Git tag" value={FRONTEND_BUILD.gitTag || null} />
          <Field label="Git commit" value={frontendCommit || null} />
          <Field label="Build time" value={FRONTEND_BUILD.buildTime || null} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-3">
            Frontend ↔ Backend match
            {!isLoading && (
              <Badge
                variant={commitsMatch ? "default" : eitherUnknown ? "outline" : "destructive"}
              >
                {commitsMatch ? "match" : eitherUnknown ? "unknown" : "mismatch"}
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {commitsMatch && (
            <p className="text-muted-foreground">
              Frontend and backend were built from the same commit{" "}
              <span className="font-mono">{shortSha(frontendCommit)}</span>.
            </p>
          )}
          {!commitsMatch && !eitherUnknown && (
            <p className="text-muted-foreground">
              Frontend commit{" "}
              <span className="font-mono">{shortSha(frontendCommit)}</span>{" "}
              does not match backend commit{" "}
              <span className="font-mono">{shortSha(backendCommit)}</span>. This
              can happen during a staged rollout, or if a reverse proxy is
              serving an older asset bundle.
            </p>
          )}
          {eitherUnknown && (
            <p className="text-muted-foreground">
              One side reports no build provenance — likely a dev build or an
              image built outside the official CI pipeline.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-3">
            Bundle integrity
            {manifest && (
              <Badge variant="outline">{manifest.files.length} files</Badge>
            )}
            {manifestError && (
              <Badge variant="outline">unavailable</Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {manifest && (
            <>
              <p className="text-muted-foreground">
                Every JS/CSS file in the served bundle has a sha384 integrity
                hash injected into <code>index.html</code> and listed in{" "}
                <a
                  href="/build-manifest.json"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline hover:text-foreground"
                >
                  /build-manifest.json
                </a>
                . Browsers will refuse to run a script whose hash doesn't match
                — and the manifest itself records what those hashes should be
                at build time.
              </p>
              <Field
                label="Manifest tag"
                value={manifest.git_tag || null}
              />
              <Field
                label="Manifest commit"
                value={manifest.git_commit || null}
              />
              <Field
                label="Manifest built"
                value={manifest.build_time || null}
              />
              <div className="border-t pt-3 mt-3 space-y-2">
                <div className="flex flex-wrap items-center gap-3">
                  <Button
                    size="sm"
                    onClick={() => runVerify(manifest)}
                    disabled={verifying}
                  >
                    {verifying ? (
                      <>
                        <Loader2 className="size-3.5 mr-1.5 animate-spin" />
                        Verifying…
                      </>
                    ) : verifyResults.length > 0 ? (
                      "Verify again"
                    ) : (
                      "Verify this page"
                    )}
                  </Button>
                  {verifySummary && (
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <span>
                        {verifySummary.match}/{verifySummary.total} match
                      </span>
                      {verifySummary.mismatch > 0 && (
                        <Badge variant="destructive">
                          {verifySummary.mismatch} mismatch
                        </Badge>
                      )}
                      {verifySummary.errored > 0 && (
                        <Badge variant="outline">
                          {verifySummary.errored} unreachable
                        </Badge>
                      )}
                    </div>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  Re-fetches every file in the manifest, computes a SHA-384
                  hash in this browser, and compares to the manifest's
                  recorded integrity. The hash function and comparison live
                  in your browser — the server can only influence the bytes
                  it serves, which is exactly what we're checking.
                </p>
                {verifyResults.length > 0 && (
                  <>
                    <ul className="space-y-1 text-xs font-mono max-h-72 overflow-y-auto rounded-md border bg-muted/40 p-2">
                      {(showAllVerify
                        ? sortedVerifyResults
                        : sortedVerifyResults.slice(0, 12)
                      ).map((r) => (
                        <li
                          key={r.path}
                          className="flex items-center gap-2"
                          title={
                            r.status === "mismatch"
                              ? `expected ${r.expected}\nactual   ${r.actual ?? "(none)"}`
                              : r.error
                          }
                        >
                          <ResultIcon status={r.status} />
                          <span className="break-all">{r.path}</span>
                          {r.status === "error" && r.error && (
                            <span className="text-destructive-foreground ml-auto">
                              {r.error}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                    {sortedVerifyResults.length > 12 && (
                      <button
                        type="button"
                        className="text-xs underline text-muted-foreground hover:text-foreground"
                        onClick={() => setShowAllVerify((s) => !s)}
                      >
                        {showAllVerify
                          ? `Show first 12`
                          : `Show all ${sortedVerifyResults.length} files`}
                      </button>
                    )}
                  </>
                )}
              </div>
            </>
          )}
          {manifestError && (
            <p className="text-muted-foreground">
              No build manifest at <code>/build-manifest.json</code> — likely a
              dev build or an image built outside CI.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Attestations &amp; transparency</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">
            Every signed image, build manifest, and SBOM lands on a public
            transparency log (Rekor) and is browsable from GHCR. The links
            below let you cross-check what this instance reports against what
            Sheaf's CI actually published, without trusting the server you're
            hitting.
          </p>
          <ul className="space-y-1.5 text-sm">
            <li>
              <a
                href={`${GH_PACKAGES}/sheaf`}
                target="_blank"
                rel="noopener noreferrer"
                className="underline hover:text-foreground"
              >
                Backend image attestations on GHCR →
              </a>{" "}
              <span className="text-muted-foreground">
                (cosign signature, SPDX SBOM)
              </span>
            </li>
            <li>
              <a
                href={`${GH_PACKAGES}/sheaf-web`}
                target="_blank"
                rel="noopener noreferrer"
                className="underline hover:text-foreground"
              >
                Frontend image attestations on GHCR →
              </a>{" "}
              <span className="text-muted-foreground">
                (cosign signature, SPDX SBOM, build manifest predicate)
              </span>
            </li>
            <li>
              <a
                href={REKOR_SEARCH}
                target="_blank"
                rel="noopener noreferrer"
                className="underline hover:text-foreground"
              >
                Rekor transparency log search →
              </a>
              <div className="mt-1 text-xs text-muted-foreground space-y-1">
                <p>
                  Use the <strong>Hash</strong> tab and paste the image{" "}
                  <code>sha256</code> digest, not the git commit. The{" "}
                  <strong>Commit SHA</strong> tab only finds entries with a
                  structured SLSA-provenance predicate; Sheaf currently signs
                  with plain cosign, where the commit lives on the signature
                  certificate but isn't in Rekor's primary index. (Switching
                  to SLSA provenance is queued as future work.)
                </p>
                <p>
                  To grab the digest:
                </p>
                <ul className="list-disc ml-5 space-y-0.5">
                  <li>
                    Easiest: run the <code>cosign verify</code> command in
                    the next card — its output line{" "}
                    <code>The following checks were performed…</code> is
                    followed by a JSON blob whose{" "}
                    <code>critical.image.docker-manifest-digest</code> field
                    is the sha256 you want.
                  </li>
                  <li>
                    Or on the GHCR page above, find the version tagged{" "}
                    {backendCommit ? (
                      <code>sha-{shortSha(backendCommit)}…</code>
                    ) : (
                      <code>sha-&lt;commit&gt;</code>
                    )}{" "}
                    and copy the digest shown at the top of its detail page.
                  </li>
                </ul>
              </div>
            </li>
            {backend?.git_tag && (
              <li>
                <a
                  href={`https://github.com/${REPO}/releases/tag/${backend.git_tag}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline hover:text-foreground"
                >
                  Release page for {backend.git_tag} →
                </a>{" "}
                <span className="text-muted-foreground">
                  (frontend tarball, signed manifest, CHANGELOG section)
                </span>
              </li>
            )}
          </ul>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Verify this build</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">
            Sheaf publishes signed Docker images via{" "}
            <a
              href="https://www.sigstore.dev/"
              target="_blank"
              rel="noopener noreferrer"
              className="underline hover:text-foreground"
            >
              sigstore
            </a>{" "}
            with keyless OIDC signing. You can verify that this instance is
            running an image built and signed by the official CI workflow.
          </p>
          {ref && (
            <pre className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs">
{`cosign verify ${ref} \\
  --certificate-identity-regexp "https://github.com/${REPO}/.github/workflows/ci.yml@.*" \\
  --certificate-oidc-issuer https://token.actions.githubusercontent.com`}
            </pre>
          )}
          <p className="text-muted-foreground">
            Or run the bundled script:
          </p>
          <pre className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs">
{`./scripts/verify-release.sh ${typeof window !== "undefined" ? window.location.origin : "<instance-url>"}`}
          </pre>
          <p className="text-muted-foreground">
            See{" "}
            <a
              href={`https://github.com/${REPO}/blob/main/docs/VERIFYING.md`}
              target="_blank"
              rel="noopener noreferrer"
              className="underline hover:text-foreground"
            >
              docs/VERIFYING.md
            </a>{" "}
            for the full trust model.
          </p>
        </CardContent>
      </Card>

      <p className="text-center text-xs text-muted-foreground">
        <Link to="/" className="hover:text-foreground hover:underline">
          ← Back to dashboard
        </Link>
      </p>
    </div>
  );
}
