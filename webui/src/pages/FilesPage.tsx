import { useCallback, useState } from "react";
import { Download, Eye, Trash2, Upload as UploadIcon } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { FileTree } from "@/components/files/FileTree";
import {
  downloadUrl,
  useDeletePath,
  useReveal,
  useRoots,
  useUploadToRoot,
  type FileNode,
} from "@/hooks/useFiles";
import { cn } from "@/lib/cn";

const READONLY_ROOTS = new Set(["archive", "templates", "prompts"]);
const DELETE_ALLOWED = new Set(["inbox", "work", "review", "output", "logs"]);

export function FilesPage() {
  const { data: rootsResp } = useRoots();
  const roots = rootsResp?.roots ?? [];
  const [activeRoot, setActiveRoot] = useState<string>("inbox");
  const [selected, setSelected] = useState<FileNode | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const upload = useUploadToRoot();
  const del = useDeletePath();
  const reveal = useReveal();

  const handleDrop = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      setUploading(true);
      try {
        for (const f of Array.from(files)) {
          await upload.mutateAsync({ root: "inbox", file: f });
        }
      } finally {
        setUploading(false);
      }
    },
    [upload],
  );

  const isReadOnly = READONLY_ROOTS.has(activeRoot);
  const canDelete = selected && DELETE_ALLOWED.has(activeRoot);

  return (
    <div className="mx-auto max-w-screen-2xl px-5 py-8 space-y-6">
      <header>
        <h1>文件管理</h1>
        <p className="mt-1 text-sm text-fg-muted">
          浏览 inbox / work / review / output 等所有运行时目录。仅 inbox 可上传，archive / templates / prompts 只读。
        </p>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-[200px_1fr] gap-4">
        {/* Roots */}
        <aside className="rounded-lg border border-border bg-surface overflow-hidden">
          <ul className="text-sm">
            {roots.map((r) => (
              <li key={r}>
                <button
                  type="button"
                  onClick={() => {
                    setActiveRoot(r);
                    setSelected(null);
                  }}
                  className={cn(
                    "block w-full px-4 py-2 text-left transition-colors",
                    "hover:bg-surface-2",
                    activeRoot === r &&
                      "bg-accent-soft text-accent font-medium",
                    READONLY_ROOTS.has(r) && "italic",
                  )}
                  title={READONLY_ROOTS.has(r) ? "只读" : undefined}
                >
                  {r}
                </button>
              </li>
            ))}
          </ul>
        </aside>

        {/* Main panel */}
        <main className="space-y-4">
          {/* Tree */}
          <Card>
            <div className="border-b border-border px-4 py-2 flex items-center justify-between">
              <span className="text-sm font-medium text-fg flex items-center gap-2">
                <span className="font-mono">{activeRoot}/</span>
                {isReadOnly && (
                  <span className="text-xs text-fg-muted/70">（只读）</span>
                )}
              </span>
              {selected && (
                <span className="text-xs text-fg-muted truncate max-w-md">
                  选中：{selected.rel_path || "（根）"}
                </span>
              )}
            </div>
            <div className="max-h-[60vh] overflow-y-auto scrollbar-thin py-1">
              <FileTree
                root={activeRoot}
                selectedPath={selected?.rel_path ?? null}
                onSelect={setSelected}
              />
            </div>
          </Card>

          {/* Actions for selected file */}
          {selected && !selected.is_dir && (
            <Card>
              <div className="px-4 py-3 flex flex-wrap items-center gap-3">
                <span className="font-mono text-sm text-fg flex-1 truncate">
                  {selected.rel_path}
                </span>
                <Button asChild variant="secondary" size="sm">
                  <a
                    href={downloadUrl(activeRoot, selected.rel_path)}
                    download={selected.name}
                  >
                    <Download size={14} />
                    下载
                  </a>
                </Button>
                <Button asChild variant="ghost" size="sm">
                  <a
                    href={downloadUrl(activeRoot, selected.rel_path)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <Eye size={14} />
                    在新页面打开
                  </a>
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={reveal.isPending}
                  onClick={() =>
                    reveal.mutate({ root: activeRoot, path: selected.rel_path })
                  }
                  title="在系统资源管理器中定位（仅本机）"
                >
                  <Eye size={14} />
                  在系统中打开
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!canDelete || del.isPending}
                  onClick={() => {
                    if (!confirm(`删除 ${activeRoot}/${selected.rel_path}？`)) return;
                    del.mutate(
                      { root: activeRoot, path: selected.rel_path },
                      { onSuccess: () => setSelected(null) },
                    );
                  }}
                  title={
                    !canDelete
                      ? `不允许在 ${activeRoot} 下删除`
                      : "删除"
                  }
                >
                  <Trash2 size={14} className="text-danger" />
                  删除
                </Button>
              </div>
            </Card>
          )}

          {/* Upload zone (inbox only, regardless of activeRoot) */}
          <Card>
            <label
              className={cn(
                "block px-6 py-8 cursor-pointer text-center transition-colors",
                dragOver
                  ? "bg-accent-soft/40 text-fg"
                  : "text-fg-muted hover:bg-surface-2",
              )}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                handleDrop(e.dataTransfer.files);
              }}
            >
              <UploadIcon size={20} className="inline-block text-accent mr-2" />
              <span className="text-sm">
                {uploading
                  ? "上传中…"
                  : "拖拽文件到这里上传到 inbox（多文件支持）"}
              </span>
              <input
                type="file"
                multiple
                className="hidden"
                onChange={(e) => {
                  handleDrop(e.target.files);
                  e.target.value = "";
                }}
              />
            </label>
          </Card>
        </main>
      </div>
    </div>
  );
}
