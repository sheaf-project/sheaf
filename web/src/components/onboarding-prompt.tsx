import { useState } from "react";
import { useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Shield, KeyRound, Upload } from "lucide-react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api-client";
import { patchWebSettings } from "@/lib/client-settings";

function useWebSettings() {
  return useQuery({
    queryKey: ["client-settings", "web"],
    queryFn: async () => {
      try {
        const res = await apiFetch<{ settings: Record<string, unknown> }>(
          "/v1/settings/client/web",
          // Fresh accounts have no blob yet, so the backend 404s by design;
          // that is the normal pre-onboarding state, not a toastable error.
          { skipErrorToast: true },
        );
        return res.settings;
      } catch {
        return {};
      }
    },
    staleTime: 60 * 1000,
  });
}

export function OnboardingPrompt() {
  const { user } = useAuth();
  const { data: settings, isLoading } = useWebSettings();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [dismissing, setDismissing] = useState(false);

  if (isLoading || !user) return null;
  if (settings?.onboarding_complete === true) return null;

  async function markComplete() {
    setDismissing(true);
    try {
      await patchWebSettings({ onboarding_complete: true });
      qc.invalidateQueries({ queryKey: ["client-settings", "web"] });
    } finally {
      setDismissing(false);
    }
  }

  async function goTo(path: string) {
    await markComplete();
    navigate(path);
  }

  return (
    <Dialog open onOpenChange={(open) => !open && markComplete()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Welcome to Sheaf</DialogTitle>
          <DialogDescription>
            Quick optional setup to protect your account and system data. You
            can always configure these later in Settings.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-2">
          <OptionRow
            icon={<KeyRound className="h-4 w-4" />}
            title="Set up two-factor auth"
            desc="Add an extra step at sign-in, and unlock the TOTP-based auth tiers for destructive actions."
            enabled={!user.totp_enabled}
            badge={user.totp_enabled ? "Already on" : undefined}
            onClick={() => goTo("/settings/account")}
          />
          <OptionRow
            icon={<Shield className="h-4 w-4" />}
            title="Configure System Safety"
            desc="Optional grace periods and re-auth prompts before members, groups, tags, fields, or front entries can be deleted."
            enabled
            onClick={() => goTo("/settings/safety")}
          />
          <OptionRow
            icon={<Upload className="h-4 w-4" />}
            title="Import existing data"
            desc="Coming from SimplyPlural or another Sheaf account? Bring your members, fronts, and history across."
            enabled
            onClick={() => goTo("/import")}
          />
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={markComplete}
            disabled={dismissing}
          >
            Skip for now
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function OptionRow({
  icon,
  title,
  desc,
  enabled,
  badge,
  onClick,
}: {
  icon: React.ReactNode;
  title: string;
  desc: string;
  enabled: boolean;
  badge?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={!enabled}
      onClick={onClick}
      className="flex w-full items-start gap-3 rounded-md border px-3 py-3 text-left hover:bg-muted disabled:opacity-60 disabled:cursor-not-allowed"
    >
      <span className="mt-0.5 shrink-0 text-muted-foreground">{icon}</span>
      <span className="flex-1 min-w-0">
        <span className="flex items-center gap-2">
          <span className="font-medium text-sm">{title}</span>
          {badge && (
            <span className="text-xs rounded bg-muted px-1.5 py-0.5 text-muted-foreground">
              {badge}
            </span>
          )}
        </span>
        <span className="block text-xs text-muted-foreground mt-0.5">
          {desc}
        </span>
      </span>
    </button>
  );
}
