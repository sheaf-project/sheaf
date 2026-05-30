import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "@/contexts/auth-context";
import { ShieldModeBanner } from "@/components/shield-mode-banner";
import { Toaster } from "@/components/ui/sonner";
import { router } from "@/routes";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        {/* Pinned above everything (including the login/register pages
            and the authed app header) so a DDoS-mitigation banner is
            visible regardless of route. Non-dismissable: it renders
            null when the feature is off or shield is inactive, so the
            unconditional mount is safe. */}
        <ShieldModeBanner />
        <RouterProvider router={router} />
        <Toaster />
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
