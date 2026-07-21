import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { BarChart3, Download, Pause, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StatusBadge } from "@/components/status-badge";
import { useCancelRun, useResumeRun, useRun } from "@/hooks/use-runs";
import { formatDateTime } from "@/lib/format";
import { ApiError } from "@/lib/api";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const ACTIVE = new Set(["running", "queued"]);

const RESUME_SCOPES = [
  { value: "non_pass", label: "Semua non-PASS (fail + error + cancelled)" },
  { value: "fail", label: "Hanya FAIL" },
  { value: "error", label: "Hanya ERROR" },
  { value: "all", label: "Semua tabel" },
];

export default function RunDetailPage() {
  const { id } = useParams();
  const runId = Number(id);
  const navigate = useNavigate();
  const { data: run, isLoading } = useRun(runId);
  const cancelMutation = useCancelRun(runId);
  const resumeMutation = useResumeRun(runId);
  const [scope, setScope] = useState("non_pass");

  async function handleCancel() {
    try {
      await cancelMutation.mutateAsync();
      toast.success("Run dibatalkan");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal membatalkan run");
    }
  }

  async function handleResume() {
    try {
      const result = await resumeMutation.mutateAsync(scope);
      toast.success(`Re-run dimulai (Run #${result.run_id})`);
      navigate(`/runs/${result.run_id}`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal memulai re-run");
    }
  }

  if (isLoading || !run) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const counts = run.tables.reduce(
    (acc, t) => {
      acc.total += 1;
      if (t.status === "pass") acc.pass += 1;
      if (t.status === "fail") acc.fail += 1;
      if (t.status === "error") acc.error += 1;
      return acc;
    },
    { total: 0, pass: 0, fail: 0, error: 0 },
  );

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            Run #{run.id} — {run.config_name}
          </h1>
          <StatusBadge status={run.status} />
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" asChild>
            <Link to={`/configs/${run.config_id}/status`}>
              <BarChart3 className="size-4" /> Status Tabel
            </Link>
          </Button>
          {ACTIVE.has(run.status) && (
            <Button variant="destructive" onClick={handleCancel} disabled={cancelMutation.isPending}>
              <Pause className="size-4" /> Cancel
            </Button>
          )}
          {TERMINAL.has(run.status) && (
            <>
              <Select value={scope} onValueChange={setScope}>
                <SelectTrigger className="w-72">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RESUME_SCOPES.map((s) => (
                    <SelectItem key={s.value} value={s.value}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button variant="outline" onClick={handleResume} disabled={resumeMutation.isPending}>
                <RotateCcw className="size-4" /> Re-run
              </Button>
              <Button variant="outline" asChild>
                <a href={`/api/runs/${run.id}/export.xlsx`}>
                  <Download className="size-4" /> Export Excel
                </a>
              </Button>
            </>
          )}
        </div>
      </div>
      <p className="-mt-4 font-mono text-sm text-muted-foreground">
        {run.mode} · trigger: {run.trigger_type} · mulai {formatDateTime(run.started_at)}
        {run.finished_at && ` · selesai ${formatDateTime(run.finished_at)}`}
      </p>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardContent className="pt-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Tabel total</p>
            <div className="mt-2 text-2xl font-semibold">{counts.total}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Pass</p>
            <div className="mt-2 text-2xl font-semibold text-status-pass">{counts.pass}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Fail</p>
            <div className="mt-2 text-2xl font-semibold text-status-fail">{counts.fail}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Error</p>
            <div className="mt-2 text-2xl font-semibold text-status-error">{counts.error}</div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tabel</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Tier</TableHead>
                <TableHead>Rows src/tgt</TableHead>
                <TableHead>Δrow</TableHead>
                <TableHead>Agg✖</TableHead>
                <TableHead>Miss</TableHead>
                <TableHead>Diff</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {run.tables.map((t) => (
                <TableRow
                  key={t.id}
                  className="cursor-pointer"
                  onClick={() => navigate(`/runs/${run.id}/tables/${t.id}`)}
                >
                  <TableCell className="font-mono text-xs">
                    {t.source_table} → {t.target_table}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={t.status} />
                  </TableCell>
                  <TableCell className="font-mono text-xs">{t.tier_reached ?? "—"}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {t.source_rows ?? "—"} / {t.target_rows ?? "—"}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{t.row_diff ?? "—"}</TableCell>
                  <TableCell className="font-mono text-xs">{t.agg_stat_mismatch ?? "—"}</TableCell>
                  <TableCell className="font-mono text-xs">{t.missing_count ?? "—"}</TableCell>
                  <TableCell className="font-mono text-xs">{t.differing_values ?? "—"}</TableCell>
                  <TableCell>
                    <Button size="sm" variant="outline">
                      Detail ▸
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <div className="max-h-72 overflow-y-auto rounded-md bg-muted/40 p-3 font-mono text-xs">
            {run.events.length ? (
              [...run.events].reverse().map((e, i) => (
                <div key={i} className="py-0.5">
                  [{e.kind}] {e.message}
                </div>
              ))
            ) : (
              <p className="text-muted-foreground">Belum ada event.</p>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
