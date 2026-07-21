import { useEffect, useState } from "react";
import { Check, X, Database, ArrowRight, ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

// Illustrative examples only -- fixed fake data, NOT a live call to the real
// validation engine. Purpose is to make "metrik beda per tipe kolom" and the
// Tier1->Tier2 escalation concrete with actual numbers instead of asking
// visitors to imagine it from category names alone.
type ColType = "number" | "string" | "date";
interface ColumnDef {
  key: string;
  label: string;
  type: ColType;
}
interface DemoRow {
  id: number;
  [key: string]: number | string | null;
}
interface DiffSpot {
  id: number;
  column: string;
}
interface Scenario {
  key: string;
  tabLabel: string;
  columns: ColumnDef[];
  source: DemoRow[];
  target: DemoRow[];
  missingId: number | null;
  diffs: DiffSpot[];
  /** column used for the completeness/uniqueness narrative in step 2 */
  focusColumn: string;
}

const SCENARIO_ECOMMERCE: Scenario = {
  key: "ecommerce",
  tabLabel: "E-Commerce: Pesanan",
  columns: [
    { key: "pelanggan", label: "pelanggan", type: "string" },
    { key: "total", label: "total", type: "number" },
    { key: "tanggal", label: "tanggal", type: "date" },
  ],
  source: [
    { id: 101, pelanggan: "Budi Santoso", total: 150000, tanggal: "2026-01-10" },
    { id: 102, pelanggan: "Siti Aminah", total: 275000, tanggal: "2026-01-11" },
    { id: 103, pelanggan: "Andi Wijaya", total: 98000, tanggal: "2026-01-12" },
    { id: 104, pelanggan: "Rina Kartika", total: 310000, tanggal: "2026-01-13" },
    { id: 105, pelanggan: "Dewi Lestari", total: 125000, tanggal: "2026-01-13" },
  ],
  target: [
    { id: 101, pelanggan: "Budi Santoso", total: 150000, tanggal: "2026-01-10" },
    { id: 102, pelanggan: "Siti Aminah", total: 275000, tanggal: "2026-01-11" },
    { id: 104, pelanggan: "Rina Kartika", total: 310000, tanggal: "2026-01-15" },
    { id: 105, pelanggan: "Dewi Lestari", total: 125000, tanggal: "2026-01-13" },
  ],
  missingId: 103,
  diffs: [{ id: 104, column: "tanggal" }],
  focusColumn: "pelanggan",
};

// A deliberately DIFFERENT failure shape: row COUNT matches on both sides
// (5=5) -- the exact "kelihatan sama" trap described earlier on this page --
// yet the migration still silently dropped a category value (completeness)
// and rounded a price (numeric stat), neither of which a row-count check
// alone would ever catch.
const SCENARIO_MIGRASI: Scenario = {
  key: "migrasi",
  tabLabel: "Migrasi Inventori Produk",
  columns: [
    { key: "produk", label: "produk", type: "string" },
    { key: "harga", label: "harga", type: "number" },
    { key: "kategori", label: "kategori", type: "string" },
  ],
  source: [
    { id: 201, produk: "Keyboard Mekanik", harga: 850000, kategori: "Elektronik" },
    { id: 202, produk: "Mouse Wireless", harga: 210000, kategori: "Elektronik" },
    { id: 203, produk: "Kemeja Flanel", harga: 175000, kategori: "Fashion" },
    { id: 204, produk: "Tas Ransel", harga: 320000, kategori: "Fashion" },
    { id: 205, produk: "Dompet Kulit", harga: 145000, kategori: "Aksesoris" },
  ],
  target: [
    { id: 201, produk: "Keyboard Mekanik", harga: 850000, kategori: "Elektronik" },
    { id: 202, produk: "Mouse Wireless", harga: 210000, kategori: "Elektronik" },
    { id: 203, produk: "Kemeja Flanel", harga: 175000, kategori: null },
    { id: 204, produk: "Tas Ransel", harga: 298000, kategori: "Fashion" },
    { id: 205, produk: "Dompet Kulit", harga: 145000, kategori: "Aksesoris" },
  ],
  missingId: null,
  diffs: [
    { id: 203, column: "kategori" },
    { id: 204, column: "harga" },
  ],
  focusColumn: "kategori",
};

const SCENARIOS = [SCENARIO_ECOMMERCE, SCENARIO_MIGRASI];

const rupiah = (n: number) => `Rp${n.toLocaleString("id-ID")}`;
const tgl = (d: string) =>
  new Date(d + "T00:00:00").toLocaleDateString("id-ID", { day: "numeric", month: "short" });

function formatCell(v: number | string | null, type: ColType): string {
  if (v === null || v === undefined) return "(kosong)";
  if (type === "number") return rupiah(v as number);
  if (type === "date") return tgl(v as string);
  return String(v);
}

function completenessPct(rows: DemoRow[], col: string): number {
  const filled = rows.filter((r) => r[col] !== null && r[col] !== undefined && r[col] !== "").length;
  return Math.round((filled / rows.length) * 100);
}
function distinctCount(rows: DemoRow[], col: string): number {
  return new Set(rows.filter((r) => r[col] !== null && r[col] !== undefined).map((r) => r[col])).size;
}
function sumCol(rows: DemoRow[], col: string): number {
  return rows.reduce((a, r) => a + ((r[col] as number) || 0), 0);
}
function maxDateCol(rows: DemoRow[], col: string): string {
  return rows.reduce((m, r) => ((r[col] as string) > m ? (r[col] as string) : m), rows[0][col] as string);
}

const STEPS = [
  { key: "struktur", label: "1. Struktur & Tipe" },
  { key: "completeness", label: "2. Completeness & Uniqueness" },
  { key: "stat", label: "3. Metrik Statistik" },
  { key: "missing", label: "4. Data Hilang" },
  { key: "diff", label: "5. Nilai Berbeda" },
] as const;
type StepKey = (typeof STEPS)[number]["key"];

function MatchPill({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium",
        ok ? "bg-status-pass-bg text-status-pass" : "bg-status-fail-bg text-status-fail",
      )}
    >
      {ok ? <Check className="size-3.5" /> : <X className="size-3.5" />}
      {children}
    </span>
  );
}

function DemoTable({
  title,
  rows,
  columns,
  step,
  scenario,
}: {
  title: string;
  rows: DemoRow[];
  columns: ColumnDef[];
  step: StepKey;
  scenario: Scenario;
}) {
  return (
    <div className="flex-1 rounded-xl border bg-card p-4 shadow-sm">
      <div className="mb-3 flex items-center gap-2 font-mono text-xs text-muted-foreground uppercase">
        <Database className="size-4" /> {title}
      </div>
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr className="text-muted-foreground">
            <th className="pb-2 pr-3 font-medium">id</th>
            {columns.map((c) => (
              <th key={c.key} className="pb-2 pr-3 font-medium">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const isMissingHighlight = step === "missing" && r.id === scenario.missingId;
            const rowDiffs = step === "diff" ? scenario.diffs.filter((d) => d.id === r.id) : [];
            return (
              <tr
                key={r.id}
                className={cn(
                  "border-t transition-colors",
                  isMissingHighlight && "bg-status-fail-bg",
                  rowDiffs.length > 0 && "bg-status-fail-bg/50",
                )}
              >
                <td className="py-1.5 pr-3 font-mono">{r.id}</td>
                {columns.map((c) => {
                  const isDiffCell = rowDiffs.some((d) => d.column === c.key);
                  const raw = r[c.key];
                  return (
                    <td
                      key={c.key}
                      className={cn(
                        "py-1.5 pr-3 font-mono",
                        raw === null && "text-muted-foreground italic",
                        isDiffCell && "rounded bg-status-fail/20 px-1.5 font-semibold text-status-fail",
                      )}
                    >
                      {formatCell(raw, c.type)}
                    </td>
                  );
                })}
              </tr>
            );
          })}
          {step === "missing" && scenario.missingId !== null && (
            <tr className="border-t border-dashed">
              <td colSpan={columns.length + 1} className="py-1.5 text-center text-xs text-status-fail">
                (id {scenario.missingId} tidak ada di sini)
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function ValidationDemo() {
  const [scenarioIdx, setScenarioIdx] = useState(0);
  const [stepIdx, setStepIdx] = useState(0);
  const scenario = SCENARIOS[scenarioIdx];

  useEffect(() => {
    const t = setTimeout(() => setStepIdx((s) => (s + 1) % STEPS.length), 5000);
    return () => clearTimeout(t);
  }, [stepIdx, scenarioIdx]);

  function selectScenario(i: number) {
    setScenarioIdx(i);
    setStepIdx(0);
  }
  function goToStep(i: number) {
    setStepIdx((i + STEPS.length) % STEPS.length);
  }

  const step = STEPS[stepIdx].key;
  const { source, target, columns, missingId, diffs, focusColumn } = scenario;
  const numberCols = columns.filter((c) => c.type === "number");
  const stringCols = columns.filter((c) => c.type === "string");
  const dateCols = columns.filter((c) => c.type === "date");
  const rowCountOk = source.length === target.length;
  const completenessSrc = completenessPct(source, focusColumn);
  const completenessTgt = completenessPct(target, focusColumn);
  const uniqueSrc = distinctCount(source, focusColumn);
  const uniqueTgt = distinctCount(target, focusColumn);

  return (
    <div className="mx-auto mt-10 max-w-5xl">
      {/* Scenario switcher */}
      <div className="mb-6 flex justify-center">
        <div className="inline-flex flex-wrap justify-center gap-1 rounded-xl border bg-card p-1 shadow-sm">
          {SCENARIOS.map((s, i) => (
            <button
              key={s.key}
              type="button"
              onClick={() => selectScenario(i)}
              className={cn(
                "rounded-lg px-4 py-2 text-sm font-medium transition-colors",
                i === scenarioIdx
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              {s.tabLabel}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-4 sm:flex-row">
        <DemoTable title={`Source (${source.length} baris)`} rows={source} columns={columns} step={step} scenario={scenario} />
        <div className="hidden items-center justify-center sm:flex">
          <ArrowRight className="size-5 text-muted-foreground" />
        </div>
        <DemoTable title={`Target (${target.length} baris)`} rows={target} columns={columns} step={step} scenario={scenario} />
      </div>

      {/* Result panel + manual nav -- content swaps per step, this is the "animation" */}
      <div className="mt-5 flex items-center gap-2 sm:items-stretch">
        <button
          type="button"
          aria-label="Tahap sebelumnya"
          onClick={() => goToStep(stepIdx - 1)}
          className="flex h-12 shrink-0 items-center justify-center rounded-xl border bg-card px-3 shadow-sm transition-colors hover:bg-muted sm:h-auto"
        >
          <ChevronLeft className="size-5" />
        </button>

        <div className="min-h-[110px] flex-1 rounded-xl border bg-muted/40 p-5">
          <p className="mb-3 font-mono text-xs font-semibold tracking-wide text-primary uppercase">
            {STEPS[stepIdx].label}
          </p>
          {step === "struktur" && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">Jumlah baris:</span>
              <MatchPill ok={rowCountOk}>
                {source.length} vs {target.length} {rowCountOk ? "— SAMA" : "— TIDAK COCOK"}
              </MatchPill>
              <MatchPill ok>Kolom & tipe data cocok</MatchPill>
              {rowCountOk && (
                <span className="w-full text-xs text-muted-foreground">
                  Jangan senang dulu — jumlah baris sama bukan jaminan isinya sama, cek tahap berikutnya.
                </span>
              )}
            </div>
          )}
          {step === "completeness" && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">Completeness ({focusColumn}):</span>
              <MatchPill ok={completenessSrc === completenessTgt}>
                {completenessSrc}% vs {completenessTgt}%
              </MatchPill>
              <span className="text-muted-foreground">Uniqueness ({focusColumn}):</span>
              <MatchPill ok={uniqueSrc === uniqueTgt}>
                {uniqueSrc} vs {uniqueTgt} nilai unik
              </MatchPill>
            </div>
          )}
          {step === "stat" && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              {numberCols.map((c) => {
                const s = sumCol(source, c.key);
                const t = sumCol(target, c.key);
                return (
                  <MatchPill key={c.key} ok={s === t}>
                    SUM({c.key}): {rupiah(s)} vs {rupiah(t)}
                  </MatchPill>
                );
              })}
              {stringCols.map((c) => {
                const s = distinctCount(source, c.key);
                const t = distinctCount(target, c.key);
                return (
                  <MatchPill key={c.key} ok={s === t}>
                    COUNT DISTINCT({c.key}): {s} vs {t}
                  </MatchPill>
                );
              })}
              {dateCols.map((c) => {
                const s = maxDateCol(source, c.key);
                const t = maxDateCol(target, c.key);
                return (
                  <MatchPill key={c.key} ok={s === t}>
                    MAX({c.key}): {tgl(s)} vs {tgl(t)}
                  </MatchPill>
                );
              })}
            </div>
          )}
          {step === "missing" && (
            <p className="text-sm text-muted-foreground">
              {missingId !== null ? (
                <>
                  Tier 1 gagal → eskalasi ke Tier 2: baris{" "}
                  <span className="font-mono font-semibold text-status-fail">id {missingId}</span> ada di Source
                  tapi <span className="font-semibold text-status-fail">hilang di Target</span> — lengkap key-nya,
                  siap disalin ke query investigasi.
                </>
              ) : (
                <>
                  Tidak ada baris yang hilang di skenario ini — jumlah baris SAMA di kedua sisi. Tapi Tier 1 tetap
                  gagal (lihat tahap Completeness & Metrik Statistik), jadi tetap eskalasi ke Tier 2 buat cari
                  kolom mana persisnya yang beda — lihat tahap berikutnya.
                </>
              )}
            </p>
          )}
          {step === "diff" && (
            <div className="space-y-1.5 text-sm text-muted-foreground">
              {diffs.map((d) => {
                const col = columns.find((c) => c.key === d.column)!;
                const sv = source.find((r) => r.id === d.id)![d.column];
                const tv = target.find((r) => r.id === d.id)![d.column];
                return (
                  <p key={`${d.id}-${d.column}`}>
                    Baris <span className="font-mono font-semibold">id {d.id}</span> ada di kedua sisi, tapi kolom{" "}
                    <span className="font-semibold">{d.column}</span> beda:{" "}
                    <span className="font-mono text-status-fail">
                      {formatCell(sv, col.type)} → {formatCell(tv, col.type)}
                    </span>
                  </p>
                );
              })}
            </div>
          )}
        </div>

        <button
          type="button"
          aria-label="Tahap berikutnya"
          onClick={() => goToStep(stepIdx + 1)}
          className="flex h-12 shrink-0 items-center justify-center rounded-xl border bg-card px-3 shadow-sm transition-colors hover:bg-muted sm:h-auto"
        >
          <ChevronRight className="size-5" />
        </button>
      </div>

      {/* Progress dots */}
      <div className="mt-4 flex justify-center gap-2">
        {STEPS.map((s, i) => (
          <button
            key={s.key}
            type="button"
            aria-label={s.label}
            onClick={() => goToStep(i)}
            className={cn(
              "h-2 rounded-full transition-all",
              i === stepIdx ? "w-8 bg-primary" : "w-2 bg-border hover:bg-muted-foreground/40",
            )}
          />
        ))}
      </div>
    </div>
  );
}
