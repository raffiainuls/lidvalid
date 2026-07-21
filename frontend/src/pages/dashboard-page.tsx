import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis } from "recharts";
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
import { StatusBadge } from "@/components/status-badge";
import { useDashboard } from "@/hooks/use-dashboard";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { TrendPoint } from "@/lib/types";

const DOMINANT_COLOR: Record<TrendPoint["dominant"], string> = {
  pass: "var(--status-pass)",
  fail: "var(--status-fail)",
  error: "var(--status-error)",
};

function Kpi({ label, value, sub, to }: { label: string; value: ReactNode; sub?: ReactNode; to?: string }) {
  const body = (
    <Card className={cn("h-full", to && "transition-colors hover:border-primary/40")}>
      <CardContent className="pt-2">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
        <div className="mt-2 text-2xl font-semibold">{value}</div>
        {sub && <p className="mt-1 truncate text-xs text-muted-foreground">{sub}</p>}
      </CardContent>
    </Card>
  );
  return to ? <Link to={to} className="block">{body}</Link> : body;
}

function CountPill({ tone, count }: { tone: "pass" | "fail" | "error"; count: number }) {
  return (
    <span
      className={cn(
        "inline-flex min-w-6 items-center justify-center rounded-full px-2 py-0.5 text-[11px] font-semibold",
        tone === "pass" && "bg-status-pass-bg text-status-pass",
        tone === "fail" && "bg-status-fail-bg text-status-fail",
        tone === "error" && "bg-status-error-bg text-status-error",
      )}
    >
      {count}
    </span>
  );
}

export default function DashboardPage() {
  const { data, isLoading } = useDashboard();
  const navigate = useNavigate();

  if (isLoading || !data) {
    return (
      <div className="grid gap-4">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
        <Skeleton className="h-64" />
      </div>
    );
  }

  const { last_run, summary, pass_rate, running_runs, trend, problem_tables, recent_runs } = data;
  const maxBad = Math.max(...problem_tables.map((p) => p.bad_count), 1);

  return (
    <div className="grid gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi
          label="Run terakhir"
          value={last_run ? <StatusBadge status={last_run.status} /> : "—"}
          sub={last_run ? `${last_run.config_name} · ${formatDateTime(last_run.finished_at)}` : "Belum ada run"}
          to={last_run ? `/runs/${last_run.id}` : undefined}
        />
        <Kpi
          label="Pass rate (run terakhir)"
          value={pass_rate}
          sub={`${summary.pass ?? 0} pass / ${summary.tables_total ?? 0} tabel`}
        />
        <Kpi
          label="Tabel bermasalah"
          value={(summary.fail ?? 0) + (summary.error ?? 0)}
          sub={`${summary.fail ?? 0} fail · ${summary.error ?? 0} error`}
          to={last_run ? `/runs/${last_run.id}` : undefined}
        />
        <Kpi
          label="Sedang berjalan"
          value={running_runs.length}
          sub={running_runs.length ? `lihat run #${running_runs[0].id} ▸` : "tidak ada"}
          to={running_runs.length ? `/runs/${running_runs[0].id}` : undefined}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Tren Pass Rate (run terakhir)
            </CardTitle>
          </CardHeader>
          <CardContent>
            {trend.length ? (
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={trend}>
                  <XAxis dataKey="id" tickFormatter={(id) => `#${id}`} fontSize={11} />
                  <Tooltip
                    formatter={(value) => [`${value}%`, "Pass rate"]}
                    labelFormatter={(id) => `Run #${id}`}
                  />
                  <Bar dataKey="rate" radius={[4, 4, 0, 0]}>
                    {trend.map((t) => (
                      <Cell key={t.id} fill={DOMINANT_COLOR[t.dominant]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-sm text-muted-foreground">
                Belum cukup run selesai untuk menampilkan tren. Jalankan beberapa kali dulu.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Tabel Paling Bermasalah
            </CardTitle>
          </CardHeader>
          <CardContent>
            {problem_tables.length ? (
              <div className="grid gap-3">
                {problem_tables.map((p) => (
                  <div key={`${p.source_table}-${p.target_table}`} className="grid gap-1">
                    <div className="flex items-center justify-between gap-2 text-sm">
                      <span className="truncate font-mono text-xs" title={`${p.source_table} → ${p.target_table}`}>
                        {p.source_table} → {p.target_table}
                      </span>
                      <span className="shrink-0 text-xs text-muted-foreground">{p.bad_count}× fail/error</span>
                    </div>
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-status-fail"
                        style={{ width: `${Math.max((p.bad_count / maxBad) * 100, 4)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                Belum ada tabel yang FAIL/ERROR — atau belum ada run sama sekali.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Run Terbaru</CardTitle>
          <p className="text-sm text-muted-foreground">
            Klik baris mana pun untuk lihat detail &amp; drilldown per tabel.
          </p>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>#</TableHead>
                <TableHead>Config</TableHead>
                <TableHead>Mode</TableHead>
                <TableHead>Mulai</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Hasil</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recent_runs.length ? (
                recent_runs.map((r) => (
                  <TableRow
                    key={r.id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/runs/${r.id}`)}
                  >
                    <TableCell>
                      <Link to={`/runs/${r.id}`} className="hover:underline" onClick={(e) => e.stopPropagation()}>
                        {r.id}
                      </Link>
                    </TableCell>
                    <TableCell>{r.config_name}</TableCell>
                    <TableCell className="font-mono text-xs">{r.mode}</TableCell>
                    <TableCell>{formatDateTime(r.started_at)}</TableCell>
                    <TableCell>
                      <StatusBadge status={r.status} />
                    </TableCell>
                    <TableCell>
                      {r.summary && Object.keys(r.summary).length ? (
                        <div className="flex gap-1">
                          <CountPill tone="pass" count={r.summary.pass ?? 0} />
                          <CountPill tone="fail" count={r.summary.fail ?? 0} />
                          <CountPill tone="error" count={r.summary.error ?? 0} />
                        </div>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell colSpan={6} className="text-center text-sm text-muted-foreground">
                    Belum ada run. Buat config lalu klik "Run Now".
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
