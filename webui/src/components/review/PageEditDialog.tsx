import { useEffect, useMemo, useState } from "react";
import { Dialog, DialogContent, DialogBody, DialogFooter } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Input";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pageNo: number;
  layout: string;
  /** Per-page slide fields ({title, bullets, image_id, …}). */
  fields: Record<string, unknown>;
  /** Spoken script body for this page (no Markdown header). */
  script: string;
  /** Optional PNG URL of the rendered slide thumbnail. */
  thumbnailUrl?: string;
  saving: boolean;
  /**
   * Called with the new per-page values. The parent merges them back
   * into the full slides_plan.json / script.md and PUTs to the server.
   */
  onSave: (next: { fields: Record<string, unknown>; script: string }) => Promise<void>;
}

/**
 * Side-by-side per-page editor.
 *
 * Left:  thumbnail + slides_plan fields (one input per field; arrays
 *        get a textarea joined by "\n" for ease of editing)
 * Right: spoken script for this page (plain textarea)
 *
 * Reasoning: editing the raw slides_plan.json or script.md is brittle
 * — a stray comma, a missing `## Page N` header, or a forgotten
 * trailing brace breaks the parse. Restricting edits to one page's
 * structured fields keeps the file format invariant; the parent
 * component is responsible for stitching the new values back in.
 */
export function PageEditDialog({
  open,
  onOpenChange,
  pageNo,
  layout,
  fields,
  script,
  thumbnailUrl,
  saving,
  onSave,
}: Props) {
  const [scriptDraft, setScriptDraft] = useState(script);
  const [fieldsDraft, setFieldsDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  // Convert the dict from slides_plan into editable string form. Arrays
  // become newline-joined textareas; scalars become plain inputs.
  // Keep the original list/string shape per key in `fieldShapes` so we
  // can restore the right type on save.
  const fieldShapes = useMemo(() => {
    const shapes: Record<string, "list" | "string" | "json"> = {};
    for (const [k, v] of Object.entries(fields)) {
      if (Array.isArray(v)) shapes[k] = "list";
      else if (typeof v === "string") shapes[k] = "string";
      else shapes[k] = "json";
    }
    return shapes;
  }, [fields]);

  useEffect(() => {
    if (!open) return;
    const draft: Record<string, string> = {};
    for (const [k, v] of Object.entries(fields)) {
      if (Array.isArray(v)) draft[k] = v.map(String).join("\n");
      else if (typeof v === "string") draft[k] = v;
      else draft[k] = JSON.stringify(v, null, 2);
    }
    setFieldsDraft(draft);
    setScriptDraft(script);
    setError(null);
  }, [open, fields, script]);

  const dirty =
    scriptDraft !== script ||
    Object.entries(fieldsDraft).some(([k, v]) => {
      const original = fields[k];
      if (Array.isArray(original)) return v !== original.map(String).join("\n");
      if (typeof original === "string") return v !== original;
      return v !== JSON.stringify(original, null, 2);
    });

  const tryClose = (next: boolean) => {
    if (!next && dirty) {
      if (!window.confirm("修改尚未保存，确认放弃？")) return;
    }
    onOpenChange(next);
  };

  const handleSave = async () => {
    setError(null);
    // Reassemble into the original shapes.
    const nextFields: Record<string, unknown> = {};
    for (const [k, str] of Object.entries(fieldsDraft)) {
      const shape = fieldShapes[k];
      if (shape === "list") {
        nextFields[k] = str.split("\n").map((l) => l.trim()).filter(Boolean);
      } else if (shape === "json") {
        try {
          nextFields[k] = JSON.parse(str);
        } catch (e) {
          setError(`字段 "${k}" 不是合法 JSON：${e instanceof Error ? e.message : String(e)}`);
          return;
        }
      } else {
        nextFields[k] = str;
      }
    }
    try {
      await onSave({ fields: nextFields, script: scriptDraft });
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Dialog open={open} onOpenChange={tryClose}>
      <DialogContent
        size="xl"
        title={`编辑 Page ${pageNo}`}
        description={`layout: ${layout} · 仅修改本页内容；保存后会更新 slides_plan.json 和 script.md`}
      >
        <DialogBody className="grid grid-cols-1 lg:grid-cols-[1fr_1fr] gap-4 max-h-[70vh] overflow-y-auto scrollbar-thin">
          {/* Left — slide preview + structured fields */}
          <section className="space-y-3">
            {thumbnailUrl ? (
              <div className="rounded border border-border bg-surface-2 overflow-hidden">
                <img
                  src={thumbnailUrl}
                  alt={`Page ${pageNo} 缩略图`}
                  className="block w-full h-auto bg-bg"
                />
              </div>
            ) : (
              <div className="rounded border border-dashed border-border bg-surface-2 grid place-items-center aspect-video text-xs text-fg-muted">
                未渲染缩略图
              </div>
            )}
            <div className="space-y-2.5">
              <h4 className="text-xs font-medium text-fg-muted">PPT 字段</h4>
              {Object.entries(fieldShapes).map(([k, shape]) => (
                <FieldEditor
                  key={k}
                  name={k}
                  shape={shape}
                  value={fieldsDraft[k] ?? ""}
                  onChange={(v) =>
                    setFieldsDraft((d) => ({ ...d, [k]: v }))
                  }
                />
              ))}
              {Object.keys(fieldShapes).length === 0 && (
                <p className="text-xs text-fg-muted">（这一页没有可编辑字段）</p>
              )}
            </div>
          </section>

          {/* Right — script for this page */}
          <section className="space-y-2.5">
            <div className="flex items-baseline justify-between">
              <h4 className="text-xs font-medium text-fg-muted">讲稿（本页）</h4>
              <span className="text-xs text-fg-muted">
                {scriptDraft.length} 字 · 约 {Math.round((scriptDraft.length / 220) * 60)} 秒
              </span>
            </div>
            <Textarea
              value={scriptDraft}
              onChange={(e) => setScriptDraft(e.target.value)}
              className="min-h-[60vh] text-sm leading-relaxed"
              placeholder="（本页无讲稿）"
            />
            <p className="text-xs text-fg-muted">
              提示：数字写汉字 · 百分号写「百分之 N」· IEEE 写 "I Triple E"。详见 prompts/script.md。
            </p>
          </section>
        </DialogBody>

        <DialogFooter>
          {error && (
            <span className="mr-auto text-xs text-danger" role="alert">
              {error}
            </span>
          )}
          <Button variant="ghost" onClick={() => tryClose(false)}>
            取消
          </Button>
          <Button variant="primary" onClick={handleSave} disabled={!dirty || saving}>
            {saving ? "保存中…" : "保存本页"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function FieldEditor({
  name,
  shape,
  value,
  onChange,
}: {
  name: string;
  shape: "list" | "string" | "json";
  value: string;
  onChange: (next: string) => void;
}) {
  const isList = shape === "list";
  const isLong = value.length > 50 || isList || shape === "json";
  return (
    <label className="block space-y-1">
      <span className="text-xs text-fg flex items-center gap-1.5">
        <span className="font-mono text-fg">{name}</span>
        {isList && (
          <span className="text-[10px] text-fg-muted">每行一条</span>
        )}
        {shape === "json" && (
          <span className="text-[10px] text-warning">JSON</span>
        )}
      </span>
      {isLong ? (
        <Textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="min-h-[80px] font-mono text-xs"
        />
      ) : (
        <Input value={value} onChange={(e) => onChange(e.target.value)} />
      )}
    </label>
  );
}
