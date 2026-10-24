import {
  Activity,
  BarChart3,
  Database,
  FlaskConical,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquare,
  Monitor,
  Moon,
  Settings,
  Sun,
  X,
} from "lucide-react";
import { Suspense, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";
import { useAuth } from "@/lib/auth";
import { useTheme, type ThemePreference } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { LoadingRows } from "../feedback";
import { Badge } from "../ui/primitives";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../ui/overlays";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/knowledge", label: "Knowledge sources", icon: Database },
  { to: "/chat", label: "AI chat", icon: MessageSquare },
  { to: "/evaluations", label: "Evaluation", icon: FlaskConical },
  { to: "/runs", label: "Run inspection", icon: Activity },
  { to: "/settings", label: "Settings", icon: Settings },
];

function Logo() {
  return (
    <div className="flex items-center gap-2 px-2">
      <div className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
        <BarChart3 className="size-4" aria-hidden />
      </div>
      <div className="leading-tight">
        <p className="text-sm font-semibold tracking-tight">Atlas AI</p>
        <p className="text-[11px] text-muted-foreground">Knowledge platform</p>
      </div>
    </div>
  );
}

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors",
              isActive ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )
          }
        >
          <Icon className="size-4" aria-hidden />
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

const THEMES: Array<{ value: ThemePreference; label: string; icon: typeof Sun }> = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
];

function UserMenu() {
  const { user, logout } = useAuth();
  const { preference, setPreference } = useTheme();
  const navigate = useNavigate();
  const initial = user?.email.charAt(0).toUpperCase() ?? "?";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left hover:bg-muted"
        aria-label="Account menu"
      >
        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold">{initial}</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{user?.email}</span>
          <span className="block text-[11px] text-muted-foreground capitalize">{user?.role}</span>
        </span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top" className="w-56">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        {THEMES.map(({ value, label, icon: Icon }) => (
          <DropdownMenuItem key={value} onSelect={() => setPreference(value)} aria-checked={preference === value} role="menuitemradio">
            <Icon aria-hidden /> {label}
            {preference === value && <span className="ml-auto text-xs text-primary">Active</span>}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => {
            logout();
            navigate("/login", { replace: true });
          }}
        >
          <LogOut aria-hidden /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const { role } = useAuth();
  return (
    <div className="flex min-h-dvh">
      <a href="#main" className="sr-only z-50 rounded bg-card px-3 py-2 focus:not-sr-only focus:fixed focus:top-2 focus:left-2">
        Skip to content
      </a>
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col gap-6 border-r border-border bg-card px-3 py-4 lg:flex">
        <Logo />
        <NavItems />
        <div className="mt-auto space-y-2">
          {role === "viewer" && (
            <Badge tone="neutral" className="mx-2">
              Read-only access
            </Badge>
          )}
          <UserMenu />
        </div>
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button className="absolute inset-0 bg-black/40" aria-label="Close menu" onClick={() => setMobileOpen(false)} />
          <aside className="relative flex h-full w-64 flex-col gap-6 border-r border-border bg-card px-3 py-4 animate-fade-in">
            <div className="flex items-center justify-between">
              <Logo />
              <button className="rounded p-1 hover:bg-muted" aria-label="Close menu" onClick={() => setMobileOpen(false)}>
                <X className="size-4" />
              </button>
            </div>
            <NavItems onNavigate={() => setMobileOpen(false)} />
            <div className="mt-auto">
              <UserMenu />
            </div>
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-12 items-center gap-2 border-b border-border bg-card/90 px-4 backdrop-blur lg:hidden">
          <button className="rounded p-1.5 hover:bg-muted" aria-label="Open menu" onClick={() => setMobileOpen(true)}>
            <Menu className="size-5" />
          </button>
          <span className="text-sm font-semibold">Atlas AI</span>
        </header>
        <main id="main" className="min-w-0 flex-1">
          <Suspense fallback={<LoadingRows rows={6} className="p-6" />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}

/** Standard padded page container (chat uses a full-bleed layout instead). */
export function PageContainer({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 lg:px-8", className)}>{children}</div>;
}
