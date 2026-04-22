import { useQuery } from "@tanstack/react-query";
import { getAuthConfig } from "@/lib/auth";

export function LegalFooter() {
  const { data: config } = useQuery({
    queryKey: ["auth-config"],
    queryFn: getAuthConfig,
  });

  const terms = config?.terms_url;
  const privacy = config?.privacy_url;

  if (!terms && !privacy) return null;

  return (
    <footer className="border-t bg-background px-4 py-2 text-center text-xs text-muted-foreground">
      {terms && (
        <a
          href={terms}
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-foreground hover:underline"
        >
          Terms of Service
        </a>
      )}
      {terms && privacy && <span className="mx-2">·</span>}
      {privacy && (
        <a
          href={privacy}
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-foreground hover:underline"
        >
          Privacy Policy
        </a>
      )}
    </footer>
  );
}
