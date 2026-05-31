import { useQuery } from "@tanstack/react-query";
import { Cpu, Server, Mic, Key } from "lucide-react";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.gen";
import { cn } from "@/lib/cn";

type ConfigView = components["schemas"]["ConfigView"];
type HealthResponse = components["schemas"]["HealthResponse"];

/**
 * Read-only settings view (P4). Editing the LLM provider / secrets /
 * voice id will land in P6 alongside the audio cloning panel.
 */
export function SettingsPage() {
  const { data: cfg, isLoading: cfgLoading } = useQuery<ConfigView>({
    queryKey: ["config"],
    queryFn: () => api.get<ConfigView>("/config"),
  });
  const { data: health } = useQuery<HealthResponse>({
    queryKey: ["health"],
    queryFn: () => api.get<HealthResponse>("/health"),
  });

  if (cfgLoading || !cfg) {
    return (
      <div className="mx-auto max-w-screen-md px-5 py-8 text-fg-muted">
        正在加载配置…
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-screen-md px-5 py-8 space-y-8">
      <header>
        <h1>设置</h1>
        <p className="mt-1 text-sm text-fg-muted">
          只读视图。编辑能力（API key、音色克隆、模板替换）将在 P6 阶段提供。
        </p>
      </header>

      <Section icon={<Server size={16} />} title="系统依赖">
        <ul className="space-y-1.5 text-sm">
          {health?.dependencies.map((d) => (
            <li key={d.name} className="flex items-center gap-3">
              <span
                className={cn(
                  "size-2 rounded-full",
                  d.ok ? "bg-success" : "bg-warning",
                )}
              />
              <span className="font-mono text-xs text-fg w-28">{d.name}</span>
              <span className="text-xs text-fg-muted truncate flex-1">
                {d.detail || (d.ok ? "ok" : "未配置")}
              </span>
            </li>
          ))}
        </ul>
      </Section>

      <Section icon={<Cpu size={16} />} title="LLM Providers">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {(["reader", "author"] as const).map((role) => {
            const t = cfg.llm[role];
            if (!t) return null;
            return (
              <div
                key={role}
                className="rounded border border-border p-3 text-xs space-y-1.5 bg-surface-2"
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium text-fg uppercase tracking-wide">
                    {role}
                  </span>
                  <span
                    className={cn(
                      "rounded-full px-2 py-0.5",
                      t.api_key_set ? "bg-success/15 text-success" : "bg-warning/15 text-warning",
                    )}
                  >
                    {t.api_key_set ? "key 已配置" : "缺 key"}
                  </span>
                </div>
                <Row k="provider" v={t.provider} />
                <Row k="model" v={t.model} mono />
                {t.base_url && <Row k="base_url" v={t.base_url} mono />}
                <Row k="env_name" v={t.api_key_env} mono />
                <Row k="max_tokens" v={String(t.max_tokens)} />
                <Row k="timeout_sec" v={String(t.timeout_sec)} />
              </div>
            );
          })}
        </div>
      </Section>

      <Section icon={<Mic size={16} />} title="TTS 默认设置">
        <ul className="text-sm space-y-1.5">
          <Row k="provider" v={cfg.tts?.provider as string} />
          <Row k="voice (默认)" v={cfg.tts?.voice as string} mono />
          <Row k="speed" v={String(cfg.tts?.speed)} />
          <Row k="concurrency" v={String(cfg.tts?.concurrency)} />
        </ul>
      </Section>

      <Section icon={<Key size={16} />} title="Secrets fingerprint">
        <p className="text-xs text-fg-muted mb-2">
          密钥仅显示前后几位，不暴露完整值。
        </p>
        <ul className="text-xs space-y-1 font-mono">
          {Object.entries(cfg.secrets_fingerprint).map(([k, v]) => (
            <li key={k} className="flex justify-between">
              <span className="text-fg-muted">{k}</span>
              <span className={v === "unset" ? "text-warning" : "text-fg"}>
                {v}
              </span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

function Section({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-surface p-5 space-y-3">
      <h2 className="text-sm font-medium text-fg-muted flex items-center gap-2">
        {icon}
        {title}
      </h2>
      {children}
    </section>
  );
}

function Row({ k, v, mono = false }: { k: string; v: string; mono?: boolean }) {
  return (
    <li className="flex items-baseline justify-between gap-3">
      <span className="text-fg-muted">{k}</span>
      <span className={cn("text-fg truncate", mono && "font-mono text-xs")}>{v}</span>
    </li>
  );
}
