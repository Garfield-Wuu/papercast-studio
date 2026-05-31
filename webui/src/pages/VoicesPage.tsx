import { VoiceList } from "@/components/voices/VoiceList";
import { CloneWizard } from "@/components/voices/CloneWizard";

/**
 * Voices page (P8 rewrite).
 *
 *   Top    : VoiceList — system voices + locally-cloned voices, with
 *            language / source filters; per-row inline preview.
 *   Bottom : CloneWizard — 3-step flow (script → audio → register).
 *
 * The whole flow targets a "<5 minute time-to-cloned-voice" UX:
 *   1. user types research keywords → Author LLM drafts a 1000-char
 *      academic-talk sample (or picks a built-in / pastes their own)
 *   2. user records straight in the browser (5 min cap, live waveform)
 *      or uploads an existing recording
 *   3. user picks a voice_id, submits — server transcodes webm→mp3 if
 *      needed and forwards to MiniMax
 */
export function VoicesPage() {
  return (
    <div className="mx-auto max-w-screen-xl px-5 py-8 space-y-6">
      <header>
        <h1>音色管理</h1>
        <p className="mt-1 text-sm text-fg-muted">
          浏览 MiniMax 系统音色、试听克隆音色，或者通过下方向导克隆专属音色。本地清单存于
          <code className="px-1 mx-0.5 rounded bg-surface-2 font-mono text-xs">config/voices.json</code>
          ，删除仅影响本地，不会回收云端音色。
        </p>
      </header>

      <VoiceList />
      <CloneWizard />
    </div>
  );
}
