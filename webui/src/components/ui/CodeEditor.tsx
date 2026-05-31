import { lazy, Suspense } from "react";
import type { OnMount } from "@monaco-editor/react";

/**
 * Monaco is ~3 MB; we lazy-load it so the main bundle stays small.
 * The Suspense fallback shows a placeholder textarea-shaped skeleton
 * while the editor chunks download.
 *
 * Theme follows the page's `[data-theme]` value via a tiny effect on
 * mount — Monaco doesn't watch for changes after first paint, so
 * toggling theme inside an open editor needs a remount (we accept
 * that as a corner case).
 */

const MonacoEditor = lazy(() =>
  import("@monaco-editor/react").then((m) => ({ default: m.default })),
);

export type Language = "json" | "markdown" | "yaml" | "plaintext";

interface CodeEditorProps {
  value: string;
  language: Language;
  onChange?: (value: string) => void;
  height?: string | number;
  readOnly?: boolean;
}

export function CodeEditor({
  value,
  language,
  onChange,
  height = "60vh",
  readOnly = false,
}: CodeEditorProps) {
  const handleMount: OnMount = (_editor, monaco) => {
    const dark = document.documentElement.dataset.theme === "dark";
    monaco.editor.setTheme(dark ? "vs-dark" : "vs");
  };
  return (
    <Suspense fallback={<EditorSkeleton height={height} />}>
      <MonacoEditor
        value={value}
        language={language}
        height={height}
        onChange={(v) => onChange?.(v ?? "")}
        onMount={handleMount}
        options={{
          readOnly,
          minimap: { enabled: false },
          fontSize: 13,
          fontFamily: "var(--font-mono), ui-monospace, monospace",
          lineNumbers: "on",
          scrollBeyondLastLine: false,
          wordWrap: "on",
          automaticLayout: true,
          tabSize: 2,
          renderLineHighlight: "line",
        }}
      />
    </Suspense>
  );
}

function EditorSkeleton({ height }: { height: string | number }) {
  return (
    <div
      className="rounded border border-border bg-surface-2 animate-pulse"
      style={{ height }}
    />
  );
}
