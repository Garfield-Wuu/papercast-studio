import { useEffect, useState } from "react";
import { Dialog, DialogContent, DialogBody, DialogFooter } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { CodeEditor, type Language } from "@/components/ui/CodeEditor";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  language: Language;
  initialValue: string;
  onSave: (value: string) => Promise<void> | void;
  saving?: boolean;
}

/**
 * Monaco in a dialog. Tracks dirty state so the Save button is only
 * active when the value differs from the original. ESC / overlay
 * click prompts only when dirty.
 */
export function EditorDialog({
  open,
  onOpenChange,
  title,
  description,
  language,
  initialValue,
  onSave,
  saving,
}: Props) {
  const [value, setValue] = useState(initialValue);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setValue(initialValue);
      setError(null);
    }
  }, [open, initialValue]);

  const dirty = value !== initialValue;

  const tryClose = (next: boolean) => {
    if (!next && dirty) {
      if (!window.confirm("修改尚未保存，确认放弃？")) return;
    }
    onOpenChange(next);
  };

  const handleSave = async () => {
    setError(null);
    try {
      await onSave(value);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Dialog open={open} onOpenChange={tryClose}>
      <DialogContent size="xl" title={title} description={description}>
        <DialogBody className="p-0">
          <div className="border-y border-border">
            <CodeEditor
              value={value}
              language={language}
              onChange={setValue}
              height="60vh"
            />
          </div>
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
          <Button
            variant="primary"
            disabled={!dirty || saving}
            onClick={handleSave}
          >
            {saving ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
