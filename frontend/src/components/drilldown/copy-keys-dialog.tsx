import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

async function tryClipboardCopy(text: string): Promise<string> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return "✓ Disalin ke clipboard.";
    } catch {
      return "Auto-copy gagal (perlu HTTPS/localhost) — teks sudah ditampilkan, tinggal Ctrl+C.";
    }
  }
  return "Teks sudah ditampilkan, tinggal Ctrl+C.";
}

/** Backs the "Copy key bermasalah" buttons on Missing Keys / Value Diffs —
 * fetches EVERY matching key (not just the current page) as a paste-ready
 * SQL value list. Escape-to-close and backdrop-click come free from Radix
 * Dialog, unlike the old hand-rolled modal's manual keydown listener. */
export function CopyKeysDialog({
  open,
  onOpenChange,
  url,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  url: string | null;
}) {
  const [text, setText] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (!open || !url) return;
    let cancelled = false;
    setText("Memuat…");
    setStatus("");
    fetch(url, { credentials: "include" })
      .then((r) => r.text())
      .then(async (t) => {
        if (cancelled) return;
        setText(t);
        setStatus(await tryClipboardCopy(t));
      })
      .catch(() => {
        if (cancelled) return;
        setText("");
        setStatus("Gagal memuat key dari server.");
      });
    return () => {
      cancelled = true;
    };
  }, [open, url]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>📋 Key bermasalah</DialogTitle>
          <DialogDescription>
            Siap tempel ke <span className="font-mono">WHERE ... IN (...)</span>.
          </DialogDescription>
        </DialogHeader>
        <Textarea
          readOnly
          rows={10}
          value={text}
          // shadcn's Textarea defaults to `field-sizing-content`, which
          // grows the box to fit ALL content (thousands of keys) instead of
          // respecting `rows` -- override back to a fixed, scrollable box.
          className="max-h-64 resize-none overflow-y-auto font-mono text-xs [field-sizing:fixed]"
          onFocus={(e) => e.currentTarget.select()}
        />
        <DialogFooter className="items-center sm:justify-between">
          <span className="text-xs text-muted-foreground">{status}</span>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Tutup
            </Button>
            <Button onClick={async () => setStatus(await tryClipboardCopy(text))}>
              📋 Salin ke Clipboard
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
