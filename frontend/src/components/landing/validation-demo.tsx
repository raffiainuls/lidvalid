import { useEffect, useState } from "react";
import { Check, X, Database, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";

// Illustrative example only -- fixed fake data, NOT a live call to the real
// validation engine. Purpose is to make "metrik beda per tipe kolom" and the
// Tier1->Tier2 escalation concrete with actual numbers instead of asking
// visitors to imagine it from category names alone.
interface DemoRow {
  id: number;
  pelanggan: string;
  total: number;
  tanggal: string;
}

const SOURCE: DemoRow[] = [
  { id: 101, pelanggan: "Budi Santoso", total: 150000, tanggal: "2026-01-10" },
  { id: 102, pelanggan: "Siti Aminah", total: 275000, tanggal: "2026-01-11" },
  { id: 103, pelanggan: "Andi Wijaya", total: 98000, tanggal: "2026-01-12" },
  { id: 104, pelanggan: "Rina Kartika", total: 310000, tanggal: "2026-01-13" },
  { id: 105, pelanggan: "Dewi Lestari", total: 125000, tanggal: "2026-01-13" },
];

// id 103 hilang di target; id 104's tanggal bergeser 2 hari -- dua skenario
// row-level yang paling umum (missing key & value diff), plus keduanya ikut
// mengubah hasil Tier 1 (row count & tiap metrik statistik) supaya
// keterkaitan Tier1<->Tier2 kelihatan, bukan cuma sekadar 2 daftar terpisah.
const TARGET: DemoRow[] = [
  { id: 101, pelanggan: "Budi Santoso", total: 150000, tanggal: "2026-01-10" },
  { id: 102, pelanggan: "Siti Aminah", total: 275000, tanggal: "2026-01-11" },
  { id: 104, pelanggan: "Rina Kartika", total: 310000, tanggal: "2026-01-15" },
  { id: 105, pelanggan: "Dewi Lestari", total: 125000, tanggal: "2026-01-13" },
];

const sum = (rows: DemoRow[]) => rows.reduce((a, r) => a + r.total, 0);
const uniqueNames = (rows: DemoRow[]) => new Set(rows.map((r) => r.pelanggan)).size;
const maxDate = (rows: DemoRow[]) => rows.reduce((m, r) => (r.tanggal > m ? r.tanggal : m), rows[0].tanggal);
const rupiah = (n: number) => `Rp${n.toLocaleString("id-ID")}`;
const tgl = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("id-ID", { day: "numeric", month: "short" });

const MISSING_ID = 103;
const DIFF_ID = 104;

const STEPS = [
  { tier: 1 as const, key: "struktur", label: "1. Struktur & Tipe" },
  { tier: 1 as const, key: "completeness", label: "2. Completeness & Uniqueness" },
  { tier: 1 as const, key: "stat", label: "3. Metrik Statistik" },
  { tier: 2 as const, key: "missing", label: "4. Data Hilang" },
  { tier: 2 as const, key: "diff", label: "5. Nilai Berbeda" },
] as const;

type StepKey = (typeof STEPS)[number]["key"];

function MatchPill({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
        ok ? "bg-status-pass-bg text-status-pass" : "bg-status-fail-bg text-status-fail",
      )}
    >
      {ok ? <Check className="size-3" /> : <X className="size-3" />}
      {children}
    </span>
  );
}

function DemoTable({
  title,
  rows,
  step,
}: {
  title: string;
  rows: DemoRow[];
  step: StepKey;
}) {
  return (
    <div className="flex-1 rounded-xl border bg-card p-3 shadow-sm">
      <div className="mb-2 flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground uppercase">
        <Database className="size-3.5" /> {title}
      </div>
      <table className="w-full border-collapse text-left text-[11px]">
        <thead>
          <tr className="text-muted-foreground">
            <th className="pb-1 pr-2 font-medium">id</th>
            <th className="pb-1 pr-2 font-medium">pelanggan</th>
            <th className="pb-1 pr-2 font-medium">total</th>
            <th className="pb-1 font-medium">tanggal</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const isMissingHighlight = step === "missing" && r.id === MISSING_ID;
            const isDiffHighlight = step === "diff" && r.id === DIFF_ID;
            return (
              <tr
                key={r.id}
                className={cn(
                  "border-t transition-colors",
                  isMissingHighlight && "bg-status-fail-bg",
                  isDiffHighlight && "bg-status-fail-bg/60",
                )}
              >
                <td className="py-1 pr-2 font-mono">{r.id}</td>
                <td className="py-1 pr-2">{r.pelanggan}</td>
                <td className="py-1 pr-2 font-mono">{rupiah(r.total)}</td>
                <td
                  className={cn(
                    "py-1 font-mono",
                    isDiffHighlight && "rounded bg-status-fail/20 px-1 font-semibold text-status-fail",
                  )}
                >
                  {tgl(r.tanggal)}
                </td>
              </tr>
            );
          })}
          {step === "missing" && (
            <tr className="border-t border-dashed">
              <td colSpan={4} className="py-1 text-center text-[10px] text-status-fail">
                (id {MISSING_ID} tidak ada di sini)
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function ValidationDemo() {
  const [stepIdx, setStepIdx] = useState(0);

  useEffect(() => {
    const t = setInterval(() => setStepIdx((s) => (s + 1) % STEPS.length), 3200);
    return () => clearInterval(t);
  }, []);

  const step = STEPS[stepIdx].key;
  const targetForStep = step === "missing" ? TARGET.filter((r) => r.id !== MISSING_ID) : TARGET;
  // ^ visually the row's already absent from TARGET; this no-op filter just
  // documents intent -- TARGET never had id 103 to begin with.

  return (
    <div className="mx-auto mt-10 max-w-3xl">
      <div className="flex flex-col gap-3 sm:flex-row">
        <DemoTable title="Source (5 baris)" rows={SOURCE} step={step} />
        <div className="hidden items-center justify-center sm:flex">
          <ArrowRight className="size-4 text-muted-foreground" />
        </div>
        <DemoTable title={`Target (${targetForStep.length} baris)`} rows={targetForStep} step={step} />
      </div>

      {/* Result panel -- content swaps per step, this is the "animation" */}
      <div className="mt-4 min-h-[92px] rounded-xl border bg-muted/40 p-4">
        <p className="mb-2 font-mono text-[11px] font-semibold tracking-wide text-primary uppercase">
          {STEPS[stepIdx].label}
        </p>
        {step === "struktur" && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-muted-foreground">Jumlah baris:</span>
            <MatchPill ok={false}>
              {SOURCE.length} vs {targetForStep.length} — TIDAK COCOK
            </MatchPill>
            <MatchPill ok>Kolom & tipe data cocok</MatchPill>
          </div>
        )}
        {step === "completeness" && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-muted-foreground">Completeness:</span>
            <MatchPill ok>100% vs 100%</MatchPill>
            <span className="text-muted-foreground">Uniqueness (pelanggan):</span>
            <MatchPill ok={false}>
              {uniqueNames(SOURCE)} vs {uniqueNames(targetForStep)} nilai unik
            </MatchPill>
          </div>
        )}
        {step === "stat" && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <MatchPill ok={false}>
              SUM(total): {rupiah(sum(SOURCE))} vs {rupiah(sum(targetForStep))}
            </MatchPill>
            <MatchPill ok={false}>
              COUNT DISTINCT(pelanggan): {uniqueNames(SOURCE)} vs {uniqueNames(targetForStep)}
            </MatchPill>
            <MatchPill ok={false}>
              MAX(tanggal): {tgl(maxDate(SOURCE))} vs {tgl(maxDate(targetForStep))}
            </MatchPill>
          </div>
        )}
        {step === "missing" && (
          <p className="text-xs text-muted-foreground">
            Tier 1 gagal (row count & metrik beda) → eskalasi ke Tier 2: baris{" "}
            <span className="font-mono font-semibold text-status-fail">id {MISSING_ID} (Andi Wijaya)</span> ada di
            Source tapi <span className="font-semibold text-status-fail">hilang di Target</span> — lengkap key-nya,
            siap disalin ke query investigasi.
          </p>
        )}
        {step === "diff" && (
          <p className="text-xs text-muted-foreground">
            Baris <span className="font-mono font-semibold">id {DIFF_ID} (Rina Kartika)</span> ada di kedua sisi,
            tapi kolom <span className="font-semibold">tanggal</span> beda:{" "}
            <span className="font-mono text-status-fail">
              {tgl(SOURCE.find((r) => r.id === DIFF_ID)!.tanggal)} → {tgl(TARGET.find((r) => r.id === DIFF_ID)!.tanggal)}
            </span>{" "}
            — inilah yang bikin MAX(tanggal) di Tier 1 tidak cocok.
          </p>
        )}
      </div>

      {/* Progress dots */}
      <div className="mt-3 flex justify-center gap-1.5">
        {STEPS.map((s, i) => (
          <button
            key={s.key}
            type="button"
            aria-label={s.label}
            onClick={() => setStepIdx(i)}
            className={cn(
              "h-1.5 rounded-full transition-all",
              i === stepIdx ? "w-6 bg-primary" : "w-1.5 bg-border hover:bg-muted-foreground/40",
            )}
          />
        ))}
      </div>
    </div>
  );
}
