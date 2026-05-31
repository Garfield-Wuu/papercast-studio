import { useState } from "react";
import {
  ChevronRight,
  ChevronDown,
  Folder,
  FolderOpen,
  File as FileIcon,
  FileImage,
  FileText,
  FileVideo,
  FileAudio,
  FileJson,
  FileCode,
} from "lucide-react";
import { useFileTree, type FileNode } from "@/hooks/useFiles";
import { cn } from "@/lib/cn";

interface Props {
  root: string;
  selectedPath: string | null;
  onSelect: (node: FileNode) => void;
}

/**
 * Lazy file tree. The top-level fetch hits /api/files?root=X; each
 * folder expands by triggering its own query keyed by (root, path).
 * That keeps memory and network bounded — a hundred-deep work/ tree
 * doesn't roundtrip until the user actually clicks into it.
 */
export function FileTree({ root, selectedPath, onSelect }: Props) {
  const { data, isLoading, error } = useFileTree(root, "");

  if (isLoading) return <p className="px-3 py-4 text-xs text-fg-muted">加载中…</p>;
  if (error)
    return (
      <p className="px-3 py-4 text-xs text-danger">加载失败：{error.message}</p>
    );
  if (!data || data.nodes.length === 0)
    return <p className="px-3 py-4 text-xs text-fg-muted">（空）</p>;

  return (
    <ul role="tree" aria-label={`${root} 文件树`} className="text-sm">
      {data.nodes.map((node) => (
        <Branch
          key={node.rel_path}
          root={root}
          node={node}
          depth={0}
          selectedPath={selectedPath}
          onSelect={onSelect}
        />
      ))}
    </ul>
  );
}

interface BranchProps {
  root: string;
  node: FileNode;
  depth: number;
  selectedPath: string | null;
  onSelect: (node: FileNode) => void;
}

function Branch({ root, node, depth, selectedPath, onSelect }: BranchProps) {
  const [expanded, setExpanded] = useState(false);
  const childrenQuery = useFileTreeIfExpanded(root, node.rel_path, expanded && node.is_dir);
  const isSelected = selectedPath === node.rel_path;
  const indent = { paddingLeft: 12 + depth * 16 };

  if (node.is_dir) {
    return (
      <li role="treeitem" aria-expanded={expanded}>
        <button
          type="button"
          onClick={() => {
            setExpanded((e) => !e);
            onSelect(node);
          }}
          className={cn(
            "flex w-full items-center gap-1.5 py-1 pr-3 text-left transition-colors",
            "hover:bg-surface-2",
            isSelected && "bg-accent-soft text-accent",
          )}
          style={indent}
        >
          {expanded ? (
            <ChevronDown size={14} className="shrink-0 text-fg-muted" />
          ) : (
            <ChevronRight size={14} className="shrink-0 text-fg-muted" />
          )}
          {expanded ? (
            <FolderOpen size={14} className="shrink-0 text-accent" />
          ) : (
            <Folder size={14} className="shrink-0 text-fg-muted" />
          )}
          <span className="truncate">{node.name}</span>
        </button>
        {expanded && (
          <ul role="group">
            {childrenQuery.isLoading && (
              <li
                className="text-xs text-fg-muted py-1"
                style={{ paddingLeft: 12 + (depth + 1) * 16 }}
              >
                加载中…
              </li>
            )}
            {childrenQuery.error && (
              <li
                className="text-xs text-danger py-1"
                style={{ paddingLeft: 12 + (depth + 1) * 16 }}
              >
                {childrenQuery.error.message}
              </li>
            )}
            {childrenQuery.data?.nodes.map((child) => (
              <Branch
                key={child.rel_path}
                root={root}
                node={child}
                depth={depth + 1}
                selectedPath={selectedPath}
                onSelect={onSelect}
              />
            ))}
          </ul>
        )}
      </li>
    );
  }

  // Leaf: file row
  return (
    <li role="treeitem">
      <button
        type="button"
        onClick={() => onSelect(node)}
        className={cn(
          "flex w-full items-center gap-1.5 py-1 pr-3 text-left transition-colors",
          "hover:bg-surface-2",
          isSelected && "bg-accent-soft text-accent",
        )}
        style={indent}
      >
        <span className="w-3.5 shrink-0" />
        <FileTypeIcon name={node.name} className="shrink-0 text-fg-muted" />
        <span className="flex-1 truncate">{node.name}</span>
        {node.size !== null && (
          <span className="text-xs text-fg-muted/70 ml-2 shrink-0">
            {formatSize(node.size)}
          </span>
        )}
      </button>
    </li>
  );
}

function useFileTreeIfExpanded(root: string, path: string, enabled: boolean) {
  const q = useFileTree(enabled ? root : undefined, path);
  return q;
}

function FileTypeIcon({ name, className }: { name: string; className?: string }) {
  const ext = name.split(".").pop()?.toLowerCase();
  const Icon = (() => {
    switch (ext) {
      case "png": case "jpg": case "jpeg": case "webp": case "gif":
        return FileImage;
      case "mp4": case "mov": case "webm":
        return FileVideo;
      case "mp3": case "wav": case "m4a": case "ogg":
        return FileAudio;
      case "json": case "yaml": case "yml":
        return FileJson;
      case "py": case "ts": case "tsx": case "js": case "jsx":
        return FileCode;
      case "md": case "txt": case "log":
        return FileText;
      default:
        return FileIcon;
    }
  })();
  return <Icon size={14} className={className} />;
}

function formatSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
