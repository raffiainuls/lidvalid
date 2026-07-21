import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { BarChart3, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { TableMappingEditor } from "@/components/configs/table-mapping-editor";
import { useConfig, useRunConfig } from "@/hooks/use-configs";
import { MODE_OVERRIDE_OPTIONS } from "@/lib/constants";
import { formatDateTime } from "@/lib/format";
import { ApiError } from "@/lib/api";

const RUN_MODE_DEFAULT = "__default__";

export default function ConfigDetailPage() {
  const { id } = useParams();
  const configId = Number(id);
  const navigate = useNavigate();
  const { data: config, isLoading } = useConfig(configId);
  const runMutation = useRunConfig(configId);
  const [runMode, setRunMode] = useState(RUN_MODE_DEFAULT);

  async function handleRunNow() {
    try {
      const result = await runMutation.mutateAsync(runMode === RUN_MODE_DEFAULT ? "" : runMode);
      toast.success(`Run #${result.run_id} dimulai`);
      navigate(`/runs/${result.run_id}`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal menjalankan run");
    }
  }

  if (isLoading || !config) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{config.name}</h1>
        <div className="flex items-center gap-2">
          <Button variant="outline" asChild>
            <Link to={`/configs/${config.id}/status`}>
              <BarChart3 className="size-4" /> Status Tabel
            </Link>
          </Button>
          <Select value={runMode} onValueChange={setRunMode}>
            <SelectTrigger className="w-56 font-mono text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={RUN_MODE_DEFAULT}>(pakai default: {config.default_mode})</SelectItem>
              {MODE_OVERRIDE_OPTIONS.map((m) => (
                <SelectItem key={m.value} value={m.value}>
                  {m.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button onClick={handleRunNow} disabled={runMutation.isPending}>
            <Play className="size-4" /> Run Now
          </Button>
        </div>
      </div>
      <p className="-mt-4 font-mono text-sm text-muted-foreground">
        {config.source_connection.name} ({config.source_connection.engine}) → {config.target_connection.name} (
        {config.target_connection.engine}) · {config.tables.length} tabel
      </p>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Pemetaan Tabel</CardTitle>
        </CardHeader>
        <CardContent>
          <TableMappingEditor config={config} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Riwayat Run</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>#</TableHead>
                <TableHead>Mode</TableHead>
                <TableHead>Mulai</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Hasil</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {config.runs.length ? (
                config.runs.map((r) => (
                  <TableRow key={r.id} className="cursor-pointer" onClick={() => navigate(`/runs/${r.id}`)}>
                    <TableCell>
                      <Link to={`/runs/${r.id}`} className="hover:underline" onClick={(e) => e.stopPropagation()}>
                        {r.id}
                      </Link>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{r.mode}</TableCell>
                    <TableCell>{formatDateTime(r.started_at)}</TableCell>
                    <TableCell>
                      <StatusBadge status={r.status} />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {r.summary && Object.keys(r.summary).length
                        ? `${r.summary.pass ?? 0} pass / ${r.summary.fail ?? 0} fail / ${r.summary.error ?? 0} error`
                        : "—"}
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-sm text-muted-foreground">
                    Belum pernah dijalankan.
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
