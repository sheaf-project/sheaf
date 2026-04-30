import { createBrowserRouter } from "react-router";
import { AppLayout } from "./_layout";
import { LoginPage } from "./login";
import { DashboardPage } from "./dashboard";
import { MembersPage } from "./members";
import { FrontsPage } from "./fronts";
import { GroupsPage } from "./groups";
import { SettingsLayout } from "./settings/_layout";
import { SettingsIndex } from "./settings/index";
import { SettingsSystemPage } from "./settings/system";
import { SettingsSafetyPage } from "./settings/safety";
import { SettingsAccountPage } from "./settings/account";
import { SettingsAppearancePage } from "./settings/appearance";
import { SettingsDataPage } from "./settings/data";
import { SettingsDangerPage } from "./settings/danger";
import { ImportPage } from "./import";
import { AboutPage } from "./about";
import { JournalsPage } from "./journals";
import { JournalDetailPage } from "./journals.$id";
import { AdminLayout } from "./admin/_layout";
import { VerifyEmailPage } from "./verify-email";
import { ForgotPasswordPage } from "./forgot-password";
import { ResetPasswordPage } from "./reset-password";
import { AdminDashboard } from "./admin/index";
import { AdminUsersPage } from "./admin/users";
import { AdminApprovalsPage } from "./admin/approvals";
import { AdminInvitesPage } from "./admin/invites";
import { AdminAnnouncementsPage } from "./admin/announcements";
import { AdminJobsPage } from "./admin/jobs";

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
    path: "/forgot-password",
    element: <ForgotPasswordPage />,
  },
  {
    path: "/reset-password",
    element: <ResetPasswordPage />,
  },
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "members", element: <MembersPage /> },
      { path: "journals", element: <JournalsPage /> },
      { path: "journals/:entryId", element: <JournalDetailPage /> },
      { path: "fronts", element: <FrontsPage /> },
      { path: "groups", element: <GroupsPage /> },
      {
        path: "settings",
        element: <SettingsLayout />,
        children: [
          { index: true, element: <SettingsIndex /> },
          { path: "system", element: <SettingsSystemPage /> },
          { path: "safety", element: <SettingsSafetyPage /> },
          { path: "account", element: <SettingsAccountPage /> },
          { path: "appearance", element: <SettingsAppearancePage /> },
          { path: "data", element: <SettingsDataPage /> },
          { path: "danger", element: <SettingsDangerPage /> },
        ],
      },
      { path: "import", element: <ImportPage /> },
      { path: "about", element: <AboutPage /> },
      {
        path: "admin",
        element: <AdminLayout />,
        children: [
          { index: true, element: <AdminDashboard /> },
          { path: "users", element: <AdminUsersPage /> },
          { path: "approvals", element: <AdminApprovalsPage /> },
          { path: "invites", element: <AdminInvitesPage /> },
          { path: "announcements", element: <AdminAnnouncementsPage /> },
          { path: "jobs", element: <AdminJobsPage /> },
        ],
      },
    ],
  },
]);
