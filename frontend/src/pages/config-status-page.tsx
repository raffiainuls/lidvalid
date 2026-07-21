import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { Settings } from "lucide-react";
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
import { StatusBadge } from "@/components/status-badge";
import { useConfigStatus, useRerunTable } from "@/hooks/use-configs";
import { formatDateTime } from "@/lib/format";
import { ApiError } from "@/lib/api";

function Kpi({ label, value, colorClass }: { label: string; value: number; colorClass?: string }) {
  return (
    <Card>
      <CardContent className="pt-2">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
        <div className={`mt-2 text-2xl font-semibold ${colorClass ?? ""}`}>{value}</div>
      </CardContent>
    </Card>
  );
}

export default function ConfigStatusPage() {
  const { id } = useParams();
  const configId = Number(id);
  const { data, isLoading } = useConfigStatus(configId);
  const rerunMutation = useRerunTable(configId);

  async function handleRerun(sourceTable: string) {
    try {
      const result = await rerunMutation.mutateAsync(sourceTable);
      toast.success(`Re-run ${sourceTable} dimulai (Run #${result.run_id})`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal memulai re-run");
    }
  }

  if (isLoading || !data) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const { rows, counts } = data;
  const other = rows.length - (counts.pass ?? 0) - (counts.fail ?? 0) - (counts.error ?? 0);

  return (
    <div className="grid gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Status Tabel — {data.config.name}</h1>
        <Button variant="outline" asChild>
          <Link to={`/configs/${configId}`}>
            <Settings className="size-4" /> Config
          </Link>
        </Button>
      </div>
      <p className="-mt-4 text-sm text-muted-foreground">
        Status TERKINI setiap tabel lintas semua run (bukan cuma run terakhir). Riwayat menampilkan
        maks. {data.history_limit} run terakhir per tabel, terbaru di kiri.
      </p>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi label="Pass" value={counts.pass ?? 0} colorClass="text-status-pass" />
        <Kpi label="Fail" value={counts.fail ?? 0} colorClass="text-status-fail" />
        <Kpi label="Error" value={counts.error ?? 0} colorClass="text-status-error" />
        <Kpi label="Lainnya / belum pernah run" value={other} />
      </div>

      <Card>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tabel</TableHead>
                <TableHead>Status Terkini</TableHead>
                <TableHead>Run</TableHead>
                <TableHead>Selesai</TableHead>
                <TableHead>Riwayat (baru → lama)</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length ? (
                rows.map((row) => (
                  <TableRow key={row.source_table}>
                    <TableCell className="font-mono text-xs">
                      {row.source_table} → {row.target_table}
                      {row.removed && (
                        <StatusBadge status="skipped" label="dihapus dari config" className="ml-2" />
                      )}
                      {!row.removed && !row.enabled && (
                        <StatusBadge status="skipped" label="nonaktif" className="ml-2" />
                      )}
                    </TableCell>
                    <TableCell>
                      {row.latest ? (
                        <Link to={`/runs/${row.latest.run_id}/tables/${row.latest.id}`}>
                          <StatusBadge status={row.latest.status} />
                        </Link>
                      ) : (
                        <span className="text-xs text-muted-foreground">belum pernah run</span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {row.latest ? <Link to={`/runs/${row.latest.run_id}`}>#{row.latest.run_id}</Link> : "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {row.latest ? formatDateTime(row.latest.finished_at) : "—"}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {row.history.length ? (
                          row.history.slice(0, 10).map((h) => (
                            <Link key={h.id} to={`/runs/${h.run_id}/tables/${h.id}`} title={`Run #${h.run_id} — ${h.status.toUpperCase()}`}>
                              <StatusBadge status={h.status} label={h.status.slice(0, 4)} />
                            </Link>
                          ))
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                        {row.history.length > 10 && (
                          <span className="text-xs text-muted-foreground">+{row.history.length - 10}</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      {!row.removed && row.enabled && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => handleRerun(row.source_table)}
                          disabled={rerunMutation.isPending}
                        >
                          ↻ Re-run
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell colSpan={6} className="text-center text-sm text-muted-foreground">
                    Config ini belum punya tabel.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
