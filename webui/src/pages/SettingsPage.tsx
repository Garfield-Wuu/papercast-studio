import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Cpu, Server, Mic, Key, Save, Undo2, Eye, EyeOff, CheckCircle2, XCircle, Film, Loader2, ShieldCheck, Package, Star } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Input";
import { StatItem, StatRow } from "@/components/ui/StatItem";
import { useConfig, useUpdateConfig, useValidateConfig, type ConfigView, type ConfigUpdate } from "@/hooks/useConfig";
import { useVoices } from "@/hooks/useVoices";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.gen";
import { LLM_PRESETS, detectPresetKey, getPreset } from "@/lib/llm-presets";
import { cn } from "@/lib/cn";

type HealthResponse = components["schemas"]["HealthResponse"];

interface LlmRoleDraft {
  provider: string;
  model: string;
  base_url: string;
  api_key_env: string;
  max_tokens: number;
  temperature: number | null;
  timeout_sec: number;
}

interface TtsDraft {
  provider: string;
  voice: string;
  fallback_voice: string;
  speed: number;
  concurrency: number;
}

interface VideoDraft {
  resolution: string;
  fps: number;
  audio_bitrate: string;
}

interface SecretDraft {
  /** New value entered by the user. "" = clear; null = untouched. */
  value: string | null;
}

interface Draft {
  llm: { reader: LlmRoleDraft; author: LlmRoleDraft };
  tts: TtsDraft;
  video: VideoDraft;
  /** Keyed by env-var name (e.g. "ANTHROPIC_API_KEY"). */
  secrets: Record<string, SecretDraft>;
}

function ttsFromCfg(cfg: ConfigView): TtsDraft {
  const t = cfg.tts ?? {};
  return {
    provider: String(t.provider ?? "minimax"),
    voice: String(t.voice ?? "female_warm"),
    fallback_voice: String(t.fallback_voice ?? "male_calm"),
    speed: Number(t.speed ?? 1),
    concurrency: Number(t.concurrency ?? 3),
  };
}

function videoFromCfg(cfg: ConfigView): VideoDraft {
  const v = cfg.video ?? {};
  return {
    resolution: String(v.resolution ?? "1920x1080"),
    fps: Number(v.fps ?? 30),
    audio_bitrate: String(v.audio_bitrate ?? "192k"),
  };
}

function llmFromCfg(t: ConfigView["llm"][string]): LlmRoleDraft {
  return {
    provider: t.provider,
    model: t.model,
    base_url: t.base_url ?? "",
    api_key_env: t.api_key_env,
    max_tokens: t.max_tokens,
    temperature: t.temperature ?? null,
    timeout_sec: t.timeout_sec,
  };
}

function draftFromCfg(cfg: ConfigView): Draft {
  return {
    llm: {
      reader: llmFromCfg(cfg.llm.reader),
      author: llmFromCfg(cfg.llm.author),
    },
    tts: ttsFromCfg(cfg),
    video: videoFromCfg(cfg),
    secrets: {},
  };
}

/**
 * Build a `ConfigUpdateRequest` from the local draft. We only send the
 * fields that exist in the draft; the backend's deep-merge keeps
 * untouched leaves intact, so omitting `slides`/`review`/`scheduler`
 * here is safe.
 */
function buildUpdateBody(d: Draft): ConfigUpdate {
  const llmDump = (r: LlmRoleDraft): Record<string, unknown> => ({
    provider: r.provider,
    model: r.model,
    base_url: r.base_url || null,
    api_key_env: r.api_key_env,
    max_tokens: r.max_tokens,
    temperature: r.temperature,
    timeout_sec: r.timeout_sec,
  });
  const secrets: Record<string, string> = {};
  for (const [k, s] of Object.entries(d.secrets)) {
    if (s.value === null) continue;          // untouched
    secrets[k] = s.value;                    // "" clears
  }
  return {
    llm: {
      reader: llmDump(d.llm.reader),
      author: llmDump(d.llm.author),
    },
    tts: { ...d.tts },
    video: { ...d.video },
    ...(Object.keys(secrets).length ? { secrets } : {}),
  };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

/**
 * Editable settings (P6.4). Three regions:
 *   1. Health + system dependencies (read-only)
 *   2. Per-role LLM cards with provider preset picker, model datalist,
 *      api key (password input → secrets), and per-role 测试连通性
 *   3. TTS / Video / Secrets fingerprint
 *
 * Secrets are written via `secrets.env` (atomic). API keys never round-trip
 * through ConfigView — only fingerprint is shown.
 */
export function SettingsPage() {
  const { data: cfg, isLoading } = useConfig();
  const update = useUpdateConfig();
  const validate = useValidateConfig();
  const { data: health } = useQuery<HealthResponse>({
    queryKey: ["health"],
    queryFn: () => api.get<HealthResponse>("/health"),
  });

  const [draft, setDraft] = useState<Draft | null>(null);
  const [validateResult, setValidateResult] = useState<Record<string, { ok: boolean; detail?: string }> | null>(null);
  const [savedFlash, setSavedFlash] = useState(false);
  const { data: voices } = useVoices();
  const favoriteVoices = useMemo(
    () => (voices ?? []).filter((v) => v.is_favorite),
    [voices],
  );

  // Re-seed draft whenever the server-side config loads or refreshes.
  useEffect(() => {
    if (cfg) setDraft(draftFromCfg(cfg));
  }, [cfg]);

  const dirty = useMemo(() => {
    if (!cfg || !draft) return false;
    const baseline = draftFromCfg(cfg);
    return JSON.stringify({ ...draft, secrets: secretsTouched(draft) }) !==
           JSON.stringify({ ...baseline, secrets: {} });
  }, [cfg, draft]);

  if (isLoading || !cfg || !draft) {
    return (
      <div className="mx-auto max-w-screen-md px-5 py-8 text-fg-muted">
        正在加载配置…
      </div>
    );
  }

  const onSave = async () => {
    const body = buildUpdateBody(draft);
    try {
      await update.mutateAsync(body);
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 2000);
      // Drop transient secret drafts now that they've been persisted.
      setDraft((prev) => prev ? { ...prev, secrets: {} } : prev);
    } catch (e) {
      // mutation surfaces error via update.error
    }
  };

  const onUndo = () => {
    if (cfg) setDraft(draftFromCfg(cfg));
    setValidateResult(null);
  };

  const onValidateAll = async () => {
    setValidateResult(null);
    // Flush any unsaved secrets/config before probing — otherwise the backend
    // reads stale env vars and reports keys as missing.
    if (dirty) {
      try {
        await update.mutateAsync(buildUpdateBody(draft));
        setDraft((prev) => prev ? { ...prev, secrets: {} } : prev);
      } catch {
        // update.error will surface the failure; don't proceed to validate
        return;
      }
    }
    const r = await validate.mutateAsync();
    setValidateResult(r.llm);
  };

  return (
    <div className="mx-auto max-w-screen-lg px-5 py-8 space-y-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1>配置</h1>
          <p className="mt-1 text-sm text-fg-muted">
            管理 LLM / TTS / 视频参数与密钥。所有更改写入 config/config.yaml 与 config/secrets.env，下一次请求即生效。
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {savedFlash && (
            <span className="text-xs text-success flex items-center gap-1">
              <CheckCircle2 size={14} /> 已保存
            </span>
          )}
          <Button variant="ghost" size="sm" onClick={onUndo} disabled={!dirty || update.isPending}>
            <Undo2 size={14} /> 撤销
          </Button>
          <Button variant="primary" size="sm" onClick={onSave} disabled={!dirty || update.isPending}>
            {update.isPending ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            保存所有更改
          </Button>
        </div>
      </header>

      {/* Overview stats — quick glance at how many roles + secrets + dependencies are configured. */}
      {(() => {
        const llmKeysSet = (["reader", "author"] as const).filter(
          (r) => cfg.llm[r]?.api_key_set,
        ).length;
        const ttsVoice = String(cfg.tts?.voice ?? "—");
        const depsTotal = health?.dependencies.length ?? 0;
        const depsOk = health?.dependencies.filter((d) => d.ok).length ?? 0;
        const fingerprintCount = Object.entries(cfg.secrets_fingerprint).filter(
          ([, v]) => v && v !== "unset",
        ).length;
        return (
          <StatRow>
            <StatItem
              icon={Cpu}
              value={`${llmKeysSet} / 2`}
              label="LLM 角色已配置"
              hint="精读 + 撰稿"
              tone={llmKeysSet === 2 ? "success" : "warning"}
            />
            <StatItem
              icon={Mic}
              value={ttsVoice}
              label="TTS 默认音色"
              hint={`语速 ${cfg.tts?.speed ?? "—"}`}
              tone="accent"
            />
            <StatItem
              icon={ShieldCheck}
              value={fingerprintCount}
              label="密钥已录入"
              hint="本地加密存放"
              tone={fingerprintCount > 0 ? "neutral" : "warning"}
            />
            <StatItem
              icon={Server}
              value={`${depsOk} / ${depsTotal}`}
              label="系统依赖就绪"
              hint={depsOk === depsTotal ? "全部就绪" : "尚有缺失"}
              tone={depsOk === depsTotal ? "success" : "warning"}
            />
          </StatRow>
        );
      })()}

      {update.error && (
        <div className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
          保存失败：{(update.error as Error).message}
        </div>
      )}

      {/* System info — version + dependencies + workspace paths */}
      <Section icon={<Server size={16} />} title="系统信息">
        <div className="space-y-4">
          {/* Version + summary */}
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
            <span className="text-fg-muted">
              papercast 版本：<span className="text-fg font-mono">{health?.version ?? "—"}</span>
            </span>
            <span className="text-fg-muted">
              状态：
              <span className={cn(
                "ml-1",
                health?.status === "ok" ? "text-success" : "text-warning",
              )}>
                {health?.status === "ok" ? "全部就绪" : "降级运行"}
              </span>
            </span>
          </div>

          {/* Dependencies */}
          <div>
            <div className="text-xs text-fg-muted mb-1.5 flex items-center gap-1.5">
              <Package size={12} />
              依赖检查
            </div>
            <ul className="grid grid-cols-1 sm:grid-cols-2 gap-y-1.5 gap-x-6 text-sm">
              {health?.dependencies.map((d) => (
                <li key={d.name} className="flex items-center gap-3">
                  <span
                    className={cn(
                      "size-2 rounded-full shrink-0",
                      d.ok ? "bg-success" : "bg-warning",
                    )}
                  />
                  <span className="font-mono text-xs text-fg w-28 shrink-0">{d.name}</span>
                  <span className="text-xs text-fg-muted truncate flex-1" title={d.detail || ""}>
                    {d.detail || (d.ok ? "ok" : "未配置")}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </Section>

      {/* LLM */}
      <Section
        icon={<Cpu size={16} />}
        title="LLM Providers"
        action={
          <Button
            variant="secondary"
            size="sm"
            onClick={onValidateAll}
            disabled={validate.isPending}
          >
            {validate.isPending ? <Loader2 size={14} className="animate-spin" /> : null}
            测试连通性
          </Button>
        }
      >
        <details className="rounded border border-border bg-surface-2/40 mb-3">
          <summary className="px-3 py-2 cursor-pointer text-xs text-fg-muted select-none flex items-center justify-between">
            <span>Reader / Author 角色说明（点击展开）</span>
            <span className="text-fg-muted/60">？</span>
          </summary>
          <div className="px-3 pb-3 pt-1 text-xs text-fg-muted leading-relaxed space-y-2">
            <p>
              <strong className="text-fg">Reader（精读）</strong>：负责
              <code className="px-1 mx-0.5 rounded bg-surface-2 font-mono">figures_split → read_done</code>
              阶段，把 PDF 转为五段式 reading.json（literature_intro / research_question / methods / findings / discussion + key_terms + fact_cards）。输入：PDF 全文 + 图表 caption。一篇约
              <span className="text-fg"> 8-15K tokens</span>，建议 max_tokens=8000。
            </p>
            <p>
              <strong className="text-fg">Author（作者/讲解）</strong>：负责
              <code className="px-1 mx-0.5 rounded bg-surface-2 font-mono">read_done → slides_done → script_done</code>
              两个阶段。Planner 基于 reading + figures + 模板 schema 规划 13 页 PPT；Scripter 基于 slides_plan + reading 写 13 段口播讲稿（90-160 字、学术汇报口吻）。一篇约
              <span className="text-fg"> 12-20K tokens</span>，建议 max_tokens=8000。
            </p>
            <p className="text-fg-muted/80">
              两个角色可以用同一个 provider（同一个 key），也可以拆开 — 例如 Reader 用 Claude，Author 用 DeepSeek 控制成本。切图阶段（figures_split）由本地 PDF 结构提取完成（visual_cluster），不需要多模态能力。
            </p>
          </div>
        </details>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {(["reader", "author"] as const).map((role) => (
            <LlmRoleCard
              key={role}
              role={role}
              value={draft.llm[role]}
              fingerprint={cfg.secrets_fingerprint[draft.llm[role].api_key_env]}
              secretDraft={draft.secrets[draft.llm[role].api_key_env] ?? null}
              probeStatus={validateResult?.[role]}
              onChange={(next) =>
                setDraft({ ...draft, llm: { ...draft.llm, [role]: next } })
              }
              onSecretChange={(envName, next) =>
                setDraft({
                  ...draft,
                  secrets: { ...draft.secrets, [envName]: next },
                })
              }
            />
          ))}
        </div>
      </Section>

      {/* TTS */}
      <Section icon={<Mic size={16} />} title="TTS 默认设置">
        {/* MiniMax API Key */}
        <Field
          label="MiniMax API Key"
          hint={cfg.secrets_fingerprint["MINIMAX_API_KEY"]
            ? `当前指纹：${cfg.secrets_fingerprint["MINIMAX_API_KEY"]}`
            : undefined}
        >
          <div className="flex gap-2">
            <Input
              type="password"
              value={draft.secrets["MINIMAX_API_KEY"]?.value ?? ""}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  secrets: {
                    ...draft.secrets,
                    MINIMAX_API_KEY: { value: e.target.value },
                  },
                })
              }
              placeholder={
                cfg.secrets_fingerprint["MINIMAX_API_KEY"] &&
                cfg.secrets_fingerprint["MINIMAX_API_KEY"] !== "unset"
                  ? "（已设置；输入新值会覆盖）"
                  : "粘贴 MiniMax API Key…"
              }
              autoComplete="off"
              className="font-mono text-xs"
            />
          </div>
        </Field>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <Field
            label="音色"
            hint={
              favoriteVoices.length === 0
                ? "我的收藏为空"
                : `从「我的收藏」中选 (${favoriteVoices.length} 个可选)`
            }
          >
            {favoriteVoices.length === 0 ? (
              <div className="rounded border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning flex items-center justify-between gap-2">
                <span>需要先添加音色到「我的收藏」</span>
                <Button asChild variant="ghost" size="sm">
                  <Link to="/voices" className="flex items-center gap-1">
                    <Star size={12} /> 去添加
                  </Link>
                </Button>
              </div>
            ) : (
              <>
                <select
                  className="h-9 w-full rounded border border-border bg-bg px-3 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
                  value={draft.tts.voice}
                  onChange={(e) => setDraft({ ...draft, tts: { ...draft.tts, voice: e.target.value } })}
                >
                  {/* If the current voice is not in favorites, surface it
                      anyway so we don't silently drop the user's prior pick. */}
                  {!favoriteVoices.some((v) => v.voice_id === draft.tts.voice) && draft.tts.voice && (
                    <option value={draft.tts.voice}>
                      {draft.tts.voice}（不在收藏中）
                    </option>
                  )}
                  {favoriteVoices.map((v) => (
                    <option key={v.voice_id} value={v.voice_id}>
                      {v.label || v.voice_id} · {v.source === "cloned" ? "克隆" : "系统"}
                    </option>
                  ))}
                </select>
                <Link
                  to="/voices"
                  className="text-[11px] text-accent hover:underline mt-1 inline-block"
                >
                  在语音管理中维护收藏 →
                </Link>
              </>
            )}
          </Field>
          <Field label="语速">
            <select
              className="h-9 w-full rounded border border-border bg-bg px-3 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
              value={String(draft.tts.speed)}
              onChange={(e) => setDraft({ ...draft, tts: { ...draft.tts, speed: Number(e.target.value) } })}
            >
              {[
                { v: 0.7, label: "0.7×（慢速）" },
                { v: 0.85, label: "0.85×（偏慢）" },
                { v: 1.0, label: "1.0×（标准）" },
                { v: 1.15, label: "1.15×（偏快）" },
                { v: 1.3, label: "1.3×（快速）" },
              ].map((o) => (
                <option key={o.v} value={String(o.v)}>{o.label}</option>
              ))}
            </select>
          </Field>
          <Field label="并发数" hint="同时处理的 TTS 任务数；过高可能触发限流">
            <select
              className="h-9 w-full rounded border border-border bg-bg px-3 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
              value={String(draft.tts.concurrency)}
              onChange={(e) => setDraft({ ...draft, tts: { ...draft.tts, concurrency: Number(e.target.value) } })}
            >
              {[1, 2, 3, 4, 6].map((n) => (
                <option key={n} value={String(n)}>{n}</option>
              ))}
            </select>
          </Field>
        </div>
      </Section>

      {/* Video */}
      <Section icon={<Film size={16} />} title="视频参数">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <Field label="分辨率">
            <select
              className="h-9 w-full rounded border border-border bg-bg px-3 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
              value={draft.video.resolution}
              onChange={(e) => setDraft({ ...draft, video: { ...draft.video, resolution: e.target.value } })}
            >
              {[
                { v: "1280x720", label: "720p (1280×720)" },
                { v: "1920x1080", label: "1080p (1920×1080)" },
                { v: "3840x2160", label: "4K (3840×2160)" },
              ].map((o) => (
                <option key={o.v} value={o.v}>{o.label}</option>
              ))}
            </select>
          </Field>
          <Field label="帧率">
            <select
              className="h-9 w-full rounded border border-border bg-bg px-3 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
              value={String(draft.video.fps)}
              onChange={(e) => setDraft({ ...draft, video: { ...draft.video, fps: Number(e.target.value) } })}
            >
              {[24, 30, 60].map((n) => (
                <option key={n} value={String(n)}>{n} fps</option>
              ))}
            </select>
          </Field>
          <Field label="音频码率">
            <select
              className="h-9 w-full rounded border border-border bg-bg px-3 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
              value={draft.video.audio_bitrate}
              onChange={(e) => setDraft({ ...draft, video: { ...draft.video, audio_bitrate: e.target.value } })}
            >
              {["128k", "192k", "256k", "320k"].map((b) => (
                <option key={b} value={b}>{b}</option>
              ))}
            </select>
          </Field>
        </div>
      </Section>

      {/* Secrets fingerprint */}
      <Section icon={<Key size={16} />} title="Secrets fingerprint">
        <p className="text-xs text-fg-muted mb-3">
          密钥仅显示前后几位，从不回传完整值。在上方各 LLM 卡片中输入新值即可覆盖；在此清空表示删除该行。
        </p>
        <ul className="text-xs space-y-1.5 font-mono">
          {Object.entries(cfg.secrets_fingerprint).map(([k, v]) => {
            const draftValue = draft.secrets[k]?.value ?? null;
            const willChange = draftValue !== null;
            return (
              <li key={k} className="flex items-center justify-between gap-3">
                <span className="text-fg-muted">{k}</span>
                <span className="flex items-center gap-2">
                  <span className={v === "unset" ? "text-warning" : "text-fg"}>
                    {v}
                  </span>
                  {willChange && (
                    <span className="text-accent">→ {draftValue === "" ? "(清除)" : "(已修改)"}</span>
                  )}
                  {v !== "unset" && !willChange && (
                    <button
                      type="button"
                      className="text-danger hover:underline"
                      onClick={() => setDraft({ ...draft, secrets: { ...draft.secrets, [k]: { value: "" } } })}
                    >
                      清除
                    </button>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function LlmRoleCard({
  role,
  value,
  fingerprint,
  secretDraft,
  probeStatus,
  onChange,
  onSecretChange,
}: {
  role: "reader" | "author";
  value: LlmRoleDraft;
  fingerprint: string | undefined;
  secretDraft: SecretDraft | null;
  probeStatus: { ok: boolean; detail?: string } | undefined;
  onChange: (next: LlmRoleDraft) => void;
  onSecretChange: (envName: string, next: SecretDraft) => void;
}) {
  const [showKey, setShowKey] = useState(false);
  const presetKey = useMemo(
    () => detectPresetKey({ provider: value.provider, base_url: value.base_url || null, api_key_env: value.api_key_env }),
    [value.provider, value.base_url, value.api_key_env],
  );
  const preset = getPreset(presetKey);
  const datalistId = `models-${role}`;
  const keySet = fingerprint && fingerprint !== "unset";

  const onPresetChange = (k: string) => {
    const p = getPreset(k);
    if (!p) return;
    onChange({
      ...value,
      provider: p.provider,
      base_url: p.base_url ?? "",
      api_key_env: p.api_key_env,
    });
  };

  return (
    <div className="rounded-lg border border-border bg-surface-2 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="font-medium text-fg uppercase tracking-wide text-xs">{role}</span>
        <RoleStatusPill keySet={Boolean(keySet)} probe={probeStatus} />
      </div>

      <Field label="预设" hint={preset?.label}>
        <select
          className="h-9 w-full rounded border border-border bg-bg px-3 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
          value={presetKey}
          onChange={(e) => onPresetChange(e.target.value)}
        >
          {LLM_PRESETS.map((p) => (
            <option key={p.key} value={p.key}>{p.label}</option>
          ))}
        </select>
      </Field>

      <Field label="模型">
        <Input
          value={value.model}
          onChange={(e) => onChange({ ...value, model: e.target.value })}
          list={datalistId}
          placeholder="例如 claude-sonnet-4-6"
        />
        {preset?.model_examples?.length ? (
          <datalist id={datalistId}>
            {preset.model_examples.map((m) => <option key={m} value={m} />)}
          </datalist>
        ) : null}
      </Field>

      <Field label="API Key" hint={fingerprint ? `当前指纹：${fingerprint}` : undefined}>
        <div className="flex gap-2">
          <Input
            type={showKey ? "text" : "password"}
            value={secretDraft?.value ?? ""}
            onChange={(e) => onSecretChange(value.api_key_env, { value: e.target.value })}
            placeholder={keySet ? "（已设置；输入新值会覆盖）" : "粘贴密钥…"}
            autoComplete="off"
            className="font-mono text-xs"
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => setShowKey((v) => !v)}
            aria-label={showKey ? "隐藏" : "显示"}
          >
            {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
          </Button>
        </div>
      </Field>

      <Field label="max_tokens">
        <select
          className="h-9 w-full rounded border border-border bg-bg px-3 text-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
          value={value.max_tokens}
          onChange={(e) => onChange({ ...value, max_tokens: Number(e.target.value) })}
        >
          {[2000, 4000, 6000, 8000, 12000, 16000, 32000].map((n) => (
            <option key={n} value={n}>
              {n.toLocaleString()}
            </option>
          ))}
        </select>
      </Field>

      <details className="rounded border border-border/60 bg-surface-2/30 group">
        <summary className="px-3 py-1.5 text-xs text-fg-muted cursor-pointer select-none flex items-center justify-between">
          <span>高级选项（默认无需修改）</span>
          <span className="text-[10px] opacity-60 group-open:hidden">展开</span>
        </summary>
        <div className="p-3 pt-1 space-y-3 border-t border-border/40 mt-1">
          <Field label="Base URL" hint="留空使用 SDK 默认">
            <Input
              value={value.base_url}
              onChange={(e) => onChange({ ...value, base_url: e.target.value })}
              placeholder={preset?.base_url ?? "https://..."}
            />
          </Field>

          <Field
            label="环境变量名"
            hint="API Key 在系统中的环境变量标识；通常用预设的就好"
          >
            <Input
              value={value.api_key_env}
              onChange={(e) => onChange({ ...value, api_key_env: e.target.value })}
              className="font-mono text-xs"
            />
          </Field>

          <div className="grid grid-cols-2 gap-2">
            <Field label="temperature">
              <Input
                type="number" step="0.05" min={0} max={2}
                value={value.temperature ?? ""}
                placeholder="（不发送）"
                onChange={(e) => {
                  const v = e.target.value;
                  onChange({ ...value, temperature: v === "" ? null : Number(v) });
                }}
              />
            </Field>
            <Field label="timeout_sec">
              <Input
                type="number" min={5} max={600} step={5}
                value={value.timeout_sec}
                onChange={(e) => onChange({ ...value, timeout_sec: Number(e.target.value) })}
              />
            </Field>
          </div>
        </div>
      </details>

      {probeStatus && !probeStatus.ok && (
        <div className="rounded bg-danger/10 border border-danger/30 px-2 py-1.5 text-xs text-danger">
          {probeStatus.detail || "失败"}
        </div>
      )}
      {probeStatus?.ok && probeStatus.detail && (
        <div className="rounded bg-success/10 border border-success/30 px-2 py-1.5 text-xs text-success">
          通：{probeStatus.detail}
        </div>
      )}
    </div>
  );
}

function RoleStatusPill({
  keySet,
  probe,
}: {
  keySet: boolean;
  probe: { ok: boolean; detail?: string } | undefined;
}) {
  if (probe) {
    return (
      <span
        className={cn(
          "rounded-full px-2 py-0.5 text-[11px] flex items-center gap-1",
          probe.ok ? "bg-success/15 text-success" : "bg-danger/15 text-danger",
        )}
      >
        {probe.ok ? <CheckCircle2 size={11} /> : <XCircle size={11} />}
        {probe.ok ? "已通" : "失败"}
      </span>
    );
  }
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[11px]",
        keySet ? "bg-success/15 text-success" : "bg-warning/15 text-warning",
      )}
    >
      {keySet ? "key 已配置" : "缺 key"}
    </span>
  );
}

function Section({
  icon,
  title,
  action,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-surface p-5 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-fg-muted flex items-center gap-2">
          {icon}
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <span className="block text-xs text-fg-muted flex items-center justify-between">
        <span>{label}</span>
        {hint && <span className="text-fg-muted/70 truncate ml-2 text-[11px]">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

function secretsTouched(d: Draft): Record<string, SecretDraft> {
  const out: Record<string, SecretDraft> = {};
  for (const [k, v] of Object.entries(d.secrets)) {
    if (v.value !== null) out[k] = v;
  }
  return out;
}

// keep import unused-warning quiet (dev-only utility, may be used later)
void Textarea;
