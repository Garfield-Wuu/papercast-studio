import { useQuery } from "@tanstack/react-query";
import { Link, NavLink } from "react-router-dom";
import {
  Moon,
  Sun,
  Activity,
  Library,
  Folders,
  Mic2,
  Sliders,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useTheme } from "@/hooks/useTheme";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { components } from "@/lib/api.gen";

type HealthResponse = components["schemas"]["HealthResponse"];

interface NavSpec {
  to: string;
  label: string;
  icon: LucideIcon;
}

const NAV: NavSpec[] = [
  { to: "/", label: "工作区", icon: Library },
  { to: "/files", label: "文件管理", icon: Folders },
  { to: "/voices", label: "语音管理", icon: Mic2 },
  { to: "/settings", label: "配置", icon: Sliders },
];

/**
 * Fixed top header — logo / nav / health indicator / theme toggle.
 *
 * Intentionally narrow scope: anything paper-specific lives in the
 * page itself; this header only speaks "system-level" data
 * (health, navigation, theme).
 */
export function Header() {
  const { theme, toggle } = useTheme();
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<HealthResponse>("/health"),
    refetchInterval: 30_000,
    retry: 1,
  });

  return (
    <header className="sticky top-0 z-30 h-14 bg-surface/85 border-b border-border backdrop-blur">
      <div className="mx-auto h-full max-w-screen-2xl px-5 flex items-center gap-6">
        <Link
          to="/"
          className="flex items-center gap-2 text-fg font-semibold tracking-tight hover:opacity-80"
        >
          <span className="inline-block size-2 rounded-full bg-accent" />
          PaperCast Studio
        </Link>

        <nav className="flex items-center gap-1">
          {NAV.map((item) => (
            <NavItem key={item.to} to={item.to} icon={item.icon}>
              {item.label}
            </NavItem>
          ))}
        </nav>

        <span className="ml-auto" />

        <HealthBadge data={health} />

        <Button
          variant="ghost"
          size="icon"
          aria-label={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"}
          onClick={toggle}
        >
          {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
        </Button>
      </div>
    </header>
  );
}

function NavItem({
  to,
  icon: Icon,
  children,
}: {
  to: string;
  icon: LucideIcon;
  children: React.ReactNode;
}) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        cn(
          "px-3 h-9 inline-flex items-center gap-1.5 rounded text-sm transition-colors",
          isActive
            ? "bg-accent-soft text-accent"
            : "text-fg-muted hover:text-fg hover:bg-surface-2",
        )
      }
    >
      <Icon size={14} />
      {children}
    </NavLink>
  );
}

function HealthBadge({ data }: { data: HealthResponse | undefined }) {
  if (!data) {
    return (
      <span
        className="inline-flex items-center gap-1.5 text-xs text-fg-muted"
        aria-live="polite"
      >
        <span className="size-2 rounded-full bg-pending animate-pulse" />
        连接中
      </span>
    );
  }
  const ok = data.status === "ok";
  const failed = data.dependencies.filter((d) => !d.ok);
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs"
      aria-live="polite"
      title={
        ok
          ? "全部依赖就绪"
          : `缺少：${failed.map((d) => d.name).join(", ")}`
      }
    >
      <span
        className={cn(
          "size-2 rounded-full",
          ok ? "bg-success" : "bg-warning",
        )}
      />
      <Activity size={12} className="text-fg-muted" />
      <span className="text-fg-muted">v{data.version}</span>
    </span>
  );
}
