import { Navigate, Outlet } from "react-router";
import { useAuth } from "@/hooks/use-auth";

export function AdminLayout() {
  const { user, loading } = useAuth();

  if (loading) return null;
  if (!user?.is_admin) return <Navigate to="/" replace />;

  return <Outlet />;
}
