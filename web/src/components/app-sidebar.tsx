import { NavLink } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/hooks/use-auth";
import { useCurrentFronts } from "@/hooks/use-fronts";
import { getUnread } from "@/lib/messages";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/logo";
import { ThemeModeToggle } from "@/components/theme-mode-toggle";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  Users,
  Clock,
  FolderOpen,
  Settings,
  LogOut,
  Shield,
  BookOpen,
  Bell,
  BellRing,
  BarChart3,
  Vote,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  X,
} from "lucide-react";
import type { ComponentType } from "react";

const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/members", label: "Members", icon: Users },
  { to: "/journals", label: "Journals", icon: BookOpen },
  { to: "/fronts", label: "Fronts", icon: Clock },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
  { to: "/groups", label: "Groups", icon: FolderOpen },
  { to: "/notifications", label: "Notifications", icon: Bell },
  { to: "/reminders", label: "Reminders", icon: BellRing },
  { to: "/polls", label: "Polls", icon: Vote },
  { to: "/messages", label: "Messages", icon: MessageSquare },
  { to: "/settings", label: "Settings", icon: Settings },
];

const adminItems = [
  { to: "/admin", label: "Admin", icon: Shield, exact: true, top: true },
  { to: "/admin/users", label: "Users" },
  { to: "/admin/approvals", label: "Approvals" },
  { to: "/admin/invites", label: "Invites" },
  { to: "/admin/announcements", label: "Announcements" },
  { to: "/admin/jobs", label: "Jobs" },
  { to: "/admin/audit", label: "Audit log" },
];

export function AppSidebar({
  collapsed = false,
  onToggleCollapse,
  mobileOpen = false,
  onMobileClose,
}: {
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  mobileOpen?: boolean;
  onMobileClose?: () => void;
}) {
  const { user, logout } = useAuth();

  // Pick the first fronting member as the perspective for unread counts.
  // If no one is fronting, skip the query - badge stays hidden.
  const { data: currentFronts } = useCurrentFronts();
  const callerMemberId = currentFronts?.[0]?.member_ids?.[0];
  const { data: unread } = useQuery({
    queryKey: ["messages", "unread", callerMemberId],
    queryFn: () => getUnread(callerMemberId!),
    enabled: !!callerMemberId,
    refetchInterval: 30_000,
  });
  const messagesUnread = unread?.total ?? 0;

  // Collapsed-icons-only is a desktop-only convenience. On mobile the
  // sidebar is shown as a drawer overlay where horizontal space is plentiful,
  // so always render the full label set there. Forcing isCollapsed=false
  // whenever the drawer is open (which only happens on mobile, since the
  // hamburger is md:hidden) handles the case where a user collapsed on
  // desktop and then opened the same UI on a phone.
  const isCollapsed = collapsed && !mobileOpen;

  return (
    <aside
      className={cn(
        // Desktop layout: static, in-flow, full height.
        "flex h-screen flex-col border-r bg-sidebar text-sidebar-foreground transition-[width] duration-150",
        // Mobile drawer: fixed-positioned overlay, slides in.
        "fixed inset-y-0 left-0 z-50 md:static",
        mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
        "transition-transform md:transition-[width]",
        isCollapsed ? "w-16" : "w-56",
      )}
      aria-hidden={!mobileOpen ? undefined : false}
    >
      <div
        className={cn(
          "flex h-14 items-center border-b",
          isCollapsed ? "justify-center px-2" : "justify-between px-4",
        )}
      >
        {!isCollapsed && (
          <div className="flex items-center gap-2 min-w-0">
            <Logo className="h-7 w-7 rounded-md shrink-0" />
            <span className="text-lg font-semibold tracking-tight truncate">
              Sheaf
            </span>
          </div>
        )}
        {isCollapsed && <Logo className="h-7 w-7 rounded-md" />}
        {!isCollapsed && (
          <div className="flex items-center gap-1">
            <ThemeModeToggle className="text-sidebar-foreground/70" />
            {/* Mobile-only close button */}
            {onMobileClose && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-sidebar-foreground/70 md:hidden"
                onClick={onMobileClose}
                aria-label="Close menu"
              >
                <X className="h-4 w-4" />
              </Button>
            )}
          </div>
        )}
      </div>
      <nav className="flex-1 space-y-1 overflow-y-auto p-3">
        {navItems.map((item) => (
          <SidebarNavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            label={item.label}
            icon={item.icon}
            collapsed={isCollapsed}
            onClick={onMobileClose}
            badge={item.to === "/messages" ? messagesUnread : undefined}
          />
        ))}
        {user?.is_admin &&
          adminItems.map((item) => {
            // Sub-items are hidden when collapsed: just show the parent
            // Admin row. Clicking it goes to /admin where the sub-pages
            // live as tabs anyway.
            if (isCollapsed && !item.top) return null;
            return (
              <SidebarNavLink
                key={item.to}
                to={item.to}
                end={item.exact}
                label={item.label}
                icon={item.icon}
                collapsed={isCollapsed}
                indented={!item.top}
                onClick={onMobileClose}
              />
            );
          })}
      </nav>
      <div className="border-t p-3 space-y-1">
        {/* Desktop-only collapse toggle */}
        {onToggleCollapse && (
          <Button
            variant="ghost"
            size="sm"
            className={cn(
              "hidden md:flex w-full text-sidebar-foreground/70",
              isCollapsed ? "justify-center px-0" : "justify-start gap-3",
            )}
            onClick={onToggleCollapse}
            aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {isCollapsed ? (
              <PanelLeftOpen className="h-4 w-4" />
            ) : (
              <>
                <PanelLeftClose className="h-4 w-4" />
                Collapse
              </>
            )}
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className={cn(
            "w-full text-sidebar-foreground/70",
            isCollapsed ? "justify-center px-0" : "justify-start gap-3",
          )}
          onClick={logout}
          aria-label="Log out"
          title="Log out"
        >
          <LogOut className="h-4 w-4" />
          {!isCollapsed && "Log out"}
        </Button>
      </div>
    </aside>
  );
}

function SidebarNavLink({
  to,
  end,
  label,
  icon: Icon,
  collapsed,
  indented,
  onClick,
  badge,
}: {
  to: string;
  end?: boolean;
  label: string;
  icon?: ComponentType<{ className?: string }>;
  collapsed: boolean;
  indented?: boolean;
  onClick?: () => void;
  badge?: number;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      onClick={onClick}
      title={collapsed ? label : undefined}
      className={({ isActive }) =>
        cn(
          "relative flex items-center rounded-md text-sm font-medium transition-colors",
          collapsed
            ? "justify-center px-2 py-2"
            : indented
              ? "gap-3 pl-9 pr-3 py-2"
              : "gap-3 px-3 py-2",
          isActive
            ? "bg-sidebar-accent text-sidebar-accent-foreground"
            : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground",
        )
      }
    >
      {Icon && <Icon className="h-4 w-4 shrink-0" />}
      {!collapsed && <span className="truncate flex-1">{label}</span>}
      {!collapsed && badge !== undefined && badge > 0 && (
        <span className="ml-auto rounded-full bg-primary px-1.5 py-0.5 text-[10px] font-medium leading-none text-primary-foreground">
          {badge > 99 ? "99+" : badge}
        </span>
      )}
      {collapsed && badge !== undefined && badge > 0 && (
        <span className="absolute -right-0.5 -top-0.5 size-2 rounded-full bg-primary" />
      )}
    </NavLink>
  );
}
