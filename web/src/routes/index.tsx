import { createBrowserRouter } from "react-router";
import { AppLayout } from "./_layout";
import { LoginPage } from "./login";
import { DashboardPage } from "./dashboard";
import { MembersPage } from "./members";
import { FrontsPage } from "./fronts";
import { GroupsPage } from "./groups";
import { SettingsPage } from "./settings";
import { ImportPage } from "./import";
import { AdminLayout } from "./admin/_layout";
import { VerifyEmailPage } from "./verify-email";
import { AdminDashboard } from "./admin/index";
import { AdminUsersPage } from "./admin/users";
import { AdminApprovalsPage } from "./admin/approvals";

export const router = createBrowserRouter([
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    path: "/verify-email",
    element: <VerifyEmailPage />,
  },
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "members", element: <MembersPage /> },
      { path: "fronts", element: <FrontsPage /> },
      { path: "groups", element: <GroupsPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "import", element: <ImportPage /> },
      {
        path: "admin",
        element: <AdminLayout />,
        children: [
          { index: true, element: <AdminDashboard /> },
          { path: "users", element: <AdminUsersPage /> },
          { path: "approvals", element: <AdminApprovalsPage /> },
        ],
      },
    ],
  },
]);
