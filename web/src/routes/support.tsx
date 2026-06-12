import { useQuery } from "@tanstack/react-query";
import { Mail, ExternalLink, Bug, ShieldAlert, Activity } from "lucide-react";
import { getAuthConfig } from "@/lib/auth";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

// The Sheaf project itself - identical on every instance, so hardcoded.
const REPO = "sheaf-project/sheaf";
const ISSUES_URL = `https://github.com/${REPO}/issues`;
const SECURITY_EMAIL = "security@sheaf.sh";
const SECURITY_POLICY_URL = `https://github.com/${REPO}/blob/main/SECURITY.md`;

const linkClass =
  "inline-flex items-center gap-1.5 underline hover:text-foreground";

export function SupportPage() {
  const { data: config } = useQuery({
    queryKey: ["auth-config"],
    queryFn: getAuthConfig,
  });

  const supportEmail = config?.support_email;
  const supportUrl = config?.support_url;
  const supportNote = config?.support_note;
  const statusUrl = config?.status_url;
  const hasOperatorSupport =
    Boolean(supportEmail) ||
    Boolean(supportUrl) ||
    Boolean(supportNote) ||
    Boolean(statusUrl);

  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader title="Support" />

      {/* Operator support - only rendered when the instance operator has
          configured at least one channel. A bare-bones selfhost that sets
          none of the SUPPORT_* env vars simply doesn't show this card. */}
      {hasOperatorSupport ? (
        <Card>
          <CardHeader>
            <CardTitle>Contact this instance</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p className="text-muted-foreground">
              This Sheaf instance is run by its own operator. For help with
              your account, billing, or instance-specific issues, reach them
              here.
            </p>
            {supportNote ? (
              <p className="whitespace-pre-line">{supportNote}</p>
            ) : null}
            <div className="space-y-2">
              {supportEmail ? (
                <div>
                  <a href={`mailto:${supportEmail}`} className={linkClass}>
                    <Mail className="h-3.5 w-3.5" />
                    {supportEmail}
                  </a>
                </div>
              ) : null}
              {supportUrl ? (
                <div>
                  <a
                    href={supportUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={linkClass}
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                    Support site
                  </a>
                </div>
              ) : null}
              {statusUrl ? (
                <div>
                  <a
                    href={statusUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={linkClass}
                  >
                    <Activity className="h-3.5 w-3.5" />
                    Service status
                  </a>
                </div>
              ) : null}
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* The Sheaf project - static, the same everywhere. */}
      <Card>
        <CardHeader>
          <CardTitle>Sheaf project</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <div className="space-y-1">
            <div className="flex items-center gap-2 font-medium">
              <Bug className="h-4 w-4" />
              Bug reports &amp; feature requests
            </div>
            <p className="text-muted-foreground">
              Found a bug in Sheaf itself, or want to suggest a feature? Open an
              issue on the project tracker. For problems specific to this
              instance, contact the operator above first.
            </p>
            <a
              href={ISSUES_URL}
              target="_blank"
              rel="noopener noreferrer"
              className={linkClass}
            >
              <ExternalLink className="h-3.5 w-3.5" />
              GitHub issues
            </a>
          </div>

          <div className="space-y-1">
            <div className="flex items-center gap-2 font-medium">
              <ShieldAlert className="h-4 w-4" />
              Security disclosure
            </div>
            <p className="text-muted-foreground">
              Please report security vulnerabilities privately rather than in a
              public issue. See the security policy for the disclosure process
              and PGP key.
            </p>
            <div className="space-y-1">
              <a href={`mailto:${SECURITY_EMAIL}`} className={linkClass}>
                <Mail className="h-3.5 w-3.5" />
                {SECURITY_EMAIL}
              </a>
              <div>
                <a
                  href={SECURITY_POLICY_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={linkClass}
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  Security policy
                </a>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
