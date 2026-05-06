import { useEffect, useState } from "react";
import { Navigate, Outlet } from "react-router";
import { Menu } from "lucide-react";
import { useAuth } from "@/hooks/use-auth";
import { AppSidebar } from "@/components/app-sidebar";
import { AccountPending } from "@/components/account-pending";
import { AnnouncementBanners } from "@/components/announcement-banners";
import { DeletionBanner } from "@/components/deletion-banner";
import { LegalFooter } from "@/components/legal-footer";
import { Logo } from "@/components/logo";
import { RetentionTrimNoticeBanner } from "@/components/retention-trim-notice-banner";
import { SystemSafetyBanner } from "@/components/system-safety-banner";
import { OnboardingPrompt } from "@/components/onboarding-prompt";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

const SIDEBAR_COLLAPSED_KEY = "sheaf:sidebarCollapsed";

export function AppLayout() {
  const { user, loading } = useAuth();

  // Mobile drawer state — closed by default. Resets on every page nav
  // via the onClick handlers passed into the sidebar nav items.
  const [mobileOpen, setMobileOpen] = useState(false);

  // Desktop collapsed-icons state — persisted so it sticks across reloads.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
    } catch {
      // localStorage unavailable (private mode etc); just don't persist.
    }
  }, [collapsed]);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Skeleton className="h-8 w-32" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  // Show pending screen if account needs verification or approval
  if (!user.email_verified || user.account_status === "pending_approval") {
    return <AccountPending />;
  }

  return (
    <div className="flex h-screen">
      <AppSidebar
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed((c) => !c)}
        mobileOpen={mobileOpen}
        onMobileClose={() => setMobileOpen(false)}
      />
      {/* Mobile backdrop — clicking dismisses the drawer */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Mobile-only top bar with hamburger */}
        <header className="flex h-12 items-center gap-2 border-b bg-background px-3 md:hidden">
          <Button
            variant="ghost"
            size="icon"
            className="h-9 w-9"
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
          >
            <Menu className="h-5 w-5" />
          </Button>
          <Logo className="h-6 w-6 rounded-md" />
          <span className="font-semibold tracking-tight">Sheaf</span>
        </header>
        <AnnouncementBanners />
        {user.account_status === "pending_deletion" && <DeletionBanner />}
        <SystemSafetyBanner />
        <RetentionTrimNoticeBanner />
        <main className="flex-1 overflow-auto p-4 md:p-6">
          <Outlet />
        </main>
        <LegalFooter />
      </div>
      <OnboardingPrompt />
    </div>
  );
}
