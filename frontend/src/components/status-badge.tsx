import { cn } from "@/lib/utils";

// Run.status values (queued|running|completed|failed|cancelled) differ from
// RunTable.status values (pending|running|pass|fail|error|skipped|cancelled)
// -- the old Jinja2 UI used the SAME `.pill {status}` CSS classes for both
// without a real mapping, so "completed"/"failed" (Run-only values) silently
// matched no color rule at all and rendered unstyled. This maps every value
// from both vocabularies onto the same 5 semantic colors on purpose.
const STATUS_MAP: Record<string, { label: string; className: string }> = {
  pass: { label: "PASS", className: "bg-status-pass-bg text-status-pass" },
  completed: { label: "COMPLETED", className: "bg-status-pass-bg text-status-pass" },
  fail: { label: "FAIL", className: "bg-status-fail-bg text-status-fail" },
  failed: { label: "FAILED", className: "bg-status-fail-bg text-status-fail" },
  error: { label: "ERROR", className: "bg-status-error-bg text-status-error" },
  running: { label: "RUNNING", className: "bg-status-running-bg text-status-running" },
  queued: { label: "QUEUED", className: "bg-status-off-bg text-status-off" },
  pending: { label: "PENDING", className: "bg-status-off-bg text-status-off" },
  cancelled: { label: "CANCELLED", className: "bg-status-off-bg text-status-off" },
  skipped: { label: "SKIPPED", className: "bg-status-off-bg text-status-off" },
};

export function StatusBadge({
  status,
  label,
  className,
}: {
  status: string;
  /** Override the displayed text while keeping the status's color (e.g. Connection's own "Belum diuji"/"OK"/"Gagal" vocabulary). */
  label?: string;
  className?: string;
}) {
  const entry = STATUS_MAP[status] ?? { label: status.toUpperCase(), className: "bg-status-off-bg text-status-off" };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold whitespace-nowrap",
        entry.className,
        className,
      )}
    >
      {(label ?? entry.label).toUpperCase()}
    </span>
  );
}
