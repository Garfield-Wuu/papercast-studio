import { usePapers } from "@/hooks/usePapers";
import { PaperList } from "@/components/papers/PaperList";
import { UploadDropzone } from "@/components/papers/UploadDropzone";

export function PapersPage() {
  const { data, isLoading, error } = usePapers();

  return (
    <div className="mx-auto max-w-screen-2xl px-5 py-8 space-y-8">
      <header className="flex items-baseline justify-between">
        <div>
          <h1>论文任务</h1>
          <p className="mt-1 text-sm text-fg-muted">
            上传 PDF 后会自动注册任务；点击行进入详情可启动流水线。
          </p>
        </div>
        <span className="text-xs text-fg-muted">
          共 {data?.length ?? 0} 个任务
        </span>
      </header>

      <UploadDropzone />

      {error && (
        <div
          className="rounded-lg border border-danger/40 bg-danger/10 p-4 text-sm text-danger"
          role="alert"
        >
          加载任务列表失败：{error.message}
        </div>
      )}

      <PaperList papers={data} loading={isLoading} />
    </div>
  );
}
