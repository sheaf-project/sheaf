import { Navigate, Outlet } from "react-router";
import { useAuth } from "@/hooks/use-auth";
import { AppSidebar } from "@/components/app-sidebar";
import { AccountPending } from "@/components/account-pending";
import { Skeleton } from "@/components/ui/skeleton";

export function AppLayout() {
  const { user, loading } = useAuth();

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
      <AppSidebar />
      <main className="flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
