import { NavLink, Outlet } from "react-router";
import { PageHeader } from "@/components/page-header";
import { cn } from "@/lib/utils";
import {
  User as UserIcon,
  Shield,
  KeyRound,
  Palette,
  Share2,
  Database,
  Wrench,
  AlertTriangle,
} from "lucide-react";

const sections = [
  { to: "/settings/system", label: "System", icon: UserIcon },
  { to: "/settings/safety", label: "Safety", icon: Shield },
  { to: "/settings/account", label: "Account", icon: KeyRound },
  { to: "/settings/appearance", label: "Appearance", icon: Palette },
  { to: "/settings/relationships", label: "Relationships", icon: Share2 },
  { to: "/settings/data", label: "Data", icon: Database },
  { to: "/settings/advanced", label: "Advanced", icon: Wrench },
  { to: "/settings/danger", label: "Danger zone", icon: AlertTriangle },
];

export function SettingsLayout() {
  return (
    <>
      <PageHeader title="Settings" />
      <div className="flex flex-col gap-6 md:flex-row">
        <nav className="md:w-48 md:shrink-0">
          <ul className="flex flex-row flex-wrap gap-1 md:flex-col md:gap-0.5">
            {sections.map((s) => (
              <li key={s.to}>
                <NavLink
                  to={s.to}
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground",
                    )
                  }
                >
                  <s.icon className="h-4 w-4" />
                  {s.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="flex-1 min-w-0">
          <div className="grid gap-6 max-w-2xl">
            <Outlet />
          </div>
        </div>
      </div>
    </>
  );
}
