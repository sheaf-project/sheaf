import { createBrowserRouter } from "react-router";
import { AppLayout } from "./_layout";
import { LoginPage } from "./login";
import { DashboardPage } from "./dashboard";
import { MembersPage } from "./members";
import { FrontsPage } from "./fronts";
import { GroupsPage } from "./groups";
import { SettingsPage } from "./settings";

export const router = createBrowserRouter([
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "members", element: <MembersPage /> },
      { path: "fronts", element: <FrontsPage /> },
      { path: "groups", element: <GroupsPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
