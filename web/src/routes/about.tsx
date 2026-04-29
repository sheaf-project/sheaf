import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  FRONTEND_BUILD,
  getBackendVersion,
  getBuildManifest,
  shortSha,
} from "@/lib/version";

const REPO = "sheaf-project/sheaf";
const REGISTRY = "ghcr.io/sheaf-project";

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
