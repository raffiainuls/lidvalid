import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { BarChart3, ChevronLeft, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/status-badge";
import { MissingTab } from "@/components/drilldown/missing-tab";
import { DiffsTab } from "@/components/drilldown/diffs-tab";
import { CopyKeysDialog } from "@/components/drilldown/copy-keys-dialog";
import { useRunTable, keysUrl } from "@/hooks/use-runs";
import { useRerunTable } from "@/hooks/use-configs";
import { ApiError } from "@/lib/api";

const TERMINAL_TABLE_STATUSES = new Set(["pass", "fail", "error", "cancelled"]);

export default function TableDrilldownPage() {
  const { runId: runIdParam, runTableId: runTableIdParam } = useParams();
  const runId = Number(runIdParam);
  const runTableId = Number(runTableIdParam);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") || "ringkasan";

  const { data: rt, isLoading } = useRunTable(runId, runTableId);
  const rerunMutation = useRerunTable(rt?.config_id ?? 0);

  const [missingPage, setMissingPage] = useState(1);
  const [diffsPage, setDiffsPage] = useState(1);
  const [selectedColumn, setSelectedColumn] = useState("");
  const [copyOpen, setCopyOpen] = useState(false);
  const [copyUrl, setCopyUrl] = useState<string | null>(null);

  function setTab(next: string) {
    setSearchParams((prev) => {
      const p = new URLSearchParams(prev);
      p.set("tab", next);
      return p;
    });
  }

  function openCopyKeys(kind: string, column?: string) {
    setCopyUrl(keysUrl(runId, runTableId, kind, column));
    setCopyOpen(true);
  }

  async function handleRerun() {
    if (!rt) return;
    try {
      const result = await rerunMutation.mutateAsync(rt.source_table);
      toast.success(`Re-run dimulai (Run #${result.run_id})`);
      navigate(`/runs/${result.run_id}`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal memulai re-run");
    }
  }

  if (isLoading || !rt) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-10 w-96" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const typeMismatchLabel = rt.type_mismatch_count ? ` (${rt.type_mismatch_count})` : "";

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="font-mono text-xl font-semibold tracking-tight">
            {rt.source_table} → {rt.target_table}
          </h1>
          <StatusBadge status={rt.status} label={`${rt.status.toUpperCase()} · tier ${rt.tier_reached ?? "—"}`} />
        </div>
        <div className="flex items-center gap-2">
          {TERMINAL_TABLE_STATUSES.has(rt.status) && (
            <Button variant="outline" onClick={handleRerun} disabled={rerunMutation.isPending}>
              <RotateCcw className="size-4" /> Re-run tabel ini
            </Button>
          )}
          <Button variant="outline" asChild>
            <Link to={`/configs/${rt.config_id}/status`}>
              <BarChart3 className="size-4" /> Status Tabel
            </Link>
          </Button>
          <Button variant="outline" asChild>
            <Link to={`/runs/${runId}`}>
              <ChevronLeft className="size-4" /> kembali ke Run #{runId}
            </Link>
          </Button>
        </div>
      </div>
      <p className="-mt-2 font-mono text-sm text-muted-foreground">
        rows: {rt.source_rows ?? "—"} / {rt.target_rows ?? "—"}
        {rt.rl_metrics && ` · key: ${rt.rl_metrics.key_columns.join(", ")} · ${rt.rl_metrics.value_columns.length} kolom dibandingkan`}
        {rt.error && ` · error: ${rt.error}`}
      </p>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="flex-wrap">
          <TabsTrigger value="ringkasan">Ringkasan</TabsTrigger>
          <TabsTrigger value="agregat">Temuan Agregat ({rt.agg_findings.length})</TabsTrigger>
          <TabsTrigger value="tipekolom">Tipe Kolom{typeMismatchLabel}</TabsTrigger>
          <TabsTrigger value="periode">Periode ({rt.period_count})</TabsTrigger>
          <TabsTrigger value="missing">Missing Keys ({rt.missing_count})</TabsTrigger>
          <TabsTrigger value="diffs">Value Diffs ({rt.total_diff_count})</TabsTrigger>
          <TabsTrigger value="sql">SQL</TabsTrigger>
          <TabsTrigger value="log">
            Log{rt.status === "error" ? " ⚠" : ""}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="ringkasan" className="grid gap-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardContent className="pt-2">
                <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Report 1 — Table</h3>
                <dl className="mt-3 grid grid-cols-2 gap-y-1.5 text-sm">
                  <dt className="text-muted-foreground">Source rows</dt>
                  <dd className="font-mono">{rt.source_rows ?? "—"}</dd>
                  <dt className="text-muted-foreground">Target rows</dt>
                  <dd className="font-mono">{rt.target_rows ?? "—"}</dd>
                  <dt className="text-muted-foreground">Row diff</dt>
                  <dd className="font-mono">{rt.row_diff ?? "—"}</dd>
                  <dt className="text-muted-foreground">Source cols</dt>
                  <dd className="font-mono">{rt.source_cols ?? "—"}</dd>
                  <dt className="text-muted-foreground">Target cols</dt>
                  <dd className="font-mono">{rt.target_cols ?? "—"}</dd>
                  <dt className="text-muted-foreground">Extra target cols</dt>
                  <dd className="font-mono">{rt.extra_target_columns.join(", ") || "—"}</dd>
                </dl>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-2">
                <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Row-level (Tier 2)</h3>
                {rt.rl_metrics ? (
                  <dl className="mt-3 grid grid-cols-2 gap-y-1.5 text-sm">
                    <dt className="text-muted-foreground">Mode</dt>
                    <dd className="font-mono">{rt.rl_metrics.mode ?? rt.mode}</dd>
                    <dt className="text-muted-foreground">Missing in source</dt>
                    <dd className="font-mono">{rt.rl_metrics.missing_in_source}</dd>
                    <dt className="text-muted-foreground">Missing in target</dt>
                    <dd className="font-mono">{rt.rl_metrics.missing_in_target}</dd>
                    <dt className="text-muted-foreground">Differing values</dt>
                    <dd className="font-mono">{rt.rl_metrics.differing_values}</dd>
                    <dt className="text-muted-foreground">Chunks</dt>
                    <dd className="font-mono">{rt.chunks_total}</dd>
                  </dl>
                ) : (
                  <p className="mt-3 text-sm text-muted-foreground">Tabel PASS di Tier 1 — row-level tidak perlu dijalankan.</p>
                )}
              </CardContent>
            </Card>
          </div>
          {rt.investigate_query && (
            <div>
              <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Query Investigasi (periode mismatch)
              </h3>
              <pre className="overflow-x-auto rounded-md bg-muted/40 p-3 font-mono text-xs whitespace-pre-wrap">{rt.investigate_query}</pre>
            </div>
          )}
        </TabsContent>

        <TabsContent value="agregat">
          <AggregateTable
            rows={rt.agg_findings}
            emptyText="Tidak ada temuan agregat (PASS)."
          />
        </TabsContent>

        <TabsContent value="tipekolom" className="grid gap-3">
          <p className="text-sm text-muted-foreground">
            Perbandingan tipe data mentah antar source &amp; target per kolom — dipakai untuk memutuskan
            apakah metrik statistik boleh dibandingkan sama sekali. Kolom dengan kategori BEDA otomatis
            dilewati dari perbandingan stat.
          </p>
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50 text-left text-xs text-muted-foreground">
                  <th className="p-2">Kolom</th>
                  <th className="p-2">Tipe Source</th>
                  <th className="p-2">Tipe Target</th>
                  <th className="p-2">Kategori</th>
                </tr>
              </thead>
              <tbody>
                {rt.column_type_details.length ? (
                  rt.column_type_details.map((c) => (
                    <tr key={c.column} className="border-b last:border-0">
                      <td className="p-2 font-mono text-xs">{c.column}</td>
                      <td className="p-2 font-mono text-xs">{c.source_type ?? "—"}</td>
                      <td className="p-2 font-mono text-xs">{c.target_type ?? "—"}</td>
                      <td className="p-2">
                        {c.category_match === null ? (
                          <span className="text-muted-foreground">—</span>
                        ) : c.category_match ? (
                          <StatusBadge status="pass" label="cocok" />
                        ) : (
                          <StatusBadge status="fail" label="beda kategori" />
                        )}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={4} className="p-4 text-center text-sm text-muted-foreground">
                      Belum ada data tipe kolom untuk tabel ini.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </TabsContent>

        <TabsContent value="periode" className="grid gap-3">
          <p className="text-sm text-muted-foreground">
            Hanya periode yang MISMATCH ditampilkan — baris "row count" kalau jumlah barisnya beda,
            dan/atau satu baris per kolom+metrik statistik yang nilainya beda.
          </p>
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50 text-left text-xs text-muted-foreground">
                  <th className="p-2">Granularity</th>
                  <th className="p-2">Periode</th>
                  <th className="p-2">Jenis</th>
                  <th className="p-2">Kolom</th>
                  <th className="p-2">Source</th>
                  <th className="p-2">Target</th>
                  <th className="p-2">Δ</th>
                </tr>
              </thead>
              <tbody>
                {rt.period_findings.length ? (
                  rt.period_findings.map((f, i) => (
                    <tr key={i} className="border-b last:border-0">
                      <td className="p-2 font-mono text-xs">{f.category === "period_monthly" ? "bulanan" : "tahunan"}</td>
                      <td className="p-2 font-mono text-xs">{f.period}</td>
                      <td className="p-2 font-mono text-xs">
                        {f.metric === null && f.column_name === null ? "row count" : f.metric}
                      </td>
                      <td className="p-2 font-mono text-xs">{f.column_name ?? "—"}</td>
                      <td className="p-2 font-mono text-xs">{f.source_value}</td>
                      <td className="p-2 font-mono text-xs">{f.target_value}</td>
                      <td className="p-2 font-mono text-xs">{f.difference ?? "—"}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={7} className="p-4 text-center text-sm text-muted-foreground">
                      Tidak ada periode yang mismatch.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </TabsContent>

        <TabsContent value="missing">
          <MissingTab
            runId={runId}
            runTableId={runTableId}
            page={missingPage}
            onPageChange={setMissingPage}
            onCopyKeys={openCopyKeys}
          />
        </TabsContent>

        <TabsContent value="diffs">
          <DiffsTab
            runId={runId}
            runTableId={runTableId}
            diffColumns={rt.diff_columns}
            diffColumnCounts={rt.diff_column_counts}
            totalDiffCount={rt.total_diff_count}
            selectedColumn={selectedColumn}
            onColumnChange={(col) => {
              setSelectedColumn(col);
              setDiffsPage(1);
            }}
            page={diffsPage}
            onPageChange={setDiffsPage}
            onCopyKeys={openCopyKeys}
          />
        </TabsContent>

        <TabsContent value="sql" className="grid gap-3">
          {Object.keys(rt.queries).length ? (
            Object.entries(rt.queries).map(([label, sql]) => (
              <div key={label}>
                <p className="mb-1 text-xs font-medium text-muted-foreground">{label}</p>
                <pre className="overflow-x-auto rounded-md bg-muted/40 p-3 font-mono text-xs whitespace-pre-wrap">{sql}</pre>
              </div>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">Tidak ada query tersimpan.</p>
          )}
        </TabsContent>

        <TabsContent value="log" className="grid gap-3">
          {rt.error && (
            <div className="rounded-md bg-status-error-bg px-3 py-2 text-sm text-status-error">
              <strong>Error:</strong> {rt.error}
            </div>
          )}
          {rt.event_log.length ? (
            <>
              <p className="text-sm text-muted-foreground">
                Jejak proses validasi tabel ini ({rt.event_log.length} event terakhir):
              </p>
              <div className="max-h-96 overflow-y-auto rounded-md bg-muted/40 p-3 font-mono text-xs">
                {rt.event_log.map((e, i) => (
                  <div
                    key={i}
                    className={
                      e.kind === "traceback" || e.kind === "retry" ? "py-0.5 text-status-error" : "py-0.5"
                    }
                  >
                    [{e.ts}] [{e.kind}] {e.message}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              Tidak ada log tersimpan untuk tabel ini — run ini jalan sebelum fitur log ditambahkan, atau
              tabel di-reap saat server restart.
            </p>
          )}
        </TabsContent>
      </Tabs>

      <CopyKeysDialog open={copyOpen} onOpenChange={setCopyOpen} url={copyUrl} />
    </div>
  );
}

function AggregateTable({
  rows,
  emptyText,
}: {
  rows: { category: string; column_name: string | null; metric: string | null; source_value: string | null; target_value: string | null }[];
  emptyText: string;
}) {
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/50 text-left text-xs text-muted-foreground">
            <th className="p-2">Kategori</th>
            <th className="p-2">Kolom</th>
            <th className="p-2">Metrik</th>
            <th className="p-2">Source</th>
            <th className="p-2">Target</th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((f, i) => (
              <tr key={i} className="border-b last:border-0">
                <td className="p-2 font-mono text-xs">{f.category}</td>
                <td className="p-2 font-mono text-xs">{f.column_name ?? "—"}</td>
                <td className="p-2 font-mono text-xs">{f.metric ?? "—"}</td>
                <td className="p-2 font-mono text-xs">{f.source_value}</td>
                <td className="p-2 font-mono text-xs">{f.target_value}</td>
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={5} className="p-4 text-center text-sm text-muted-foreground">
                {emptyText}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
