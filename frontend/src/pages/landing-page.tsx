import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  ShieldCheck,
  Layers,
  Database,
  SearchCode,
  Users,
  FileSpreadsheet,
  Activity,
  ArrowRight,
  ArrowDown,
  Plug,
  Wrench,
  PlayCircle,
  History,
  Sparkles,
  Rows3,
  Percent,
  Calculator,
  FileSearch,
  GitCompare,
  ArrowRightLeft,
  Workflow,
  ClipboardCheck,
  ShieldAlert,
  X,
  Check,
  Hash,
  CaseSensitive,
  CalendarClock,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

function Emphasis({ children }: { children: ReactNode }) {
  return <strong className="font-semibold text-foreground">{children}</strong>;
}

const WHY_PAIRS = [
  {
    id: "manual",
    without: (
      <>
        Bandingkan manual lewat query atau spreadsheet satu-satu —{" "}
        <Emphasis>lambat dan gampang ada yang kelewat</Emphasis> untuk ratusan tabel.
      </>
    ),
    with: (
      <>
        <Emphasis>Satu run</Emphasis> memvalidasi semua tabel sekaligus secara otomatis, hasilnya
        langsung tampil di dashboard.
      </>
    ),
  },
  {
    id: "kelihatan-sama",
    without: (
      <>
        "Kelihatan sama" jadi patokan — padahal{" "}
        <Emphasis>jumlah baris cocok bukan jaminan isinya benar-benar cocok</Emphasis>.
      </>
    ),
    with: (
      <>
        Validasi sampai ke level <Emphasis>completeness, uniqueness, dan statistik tiap kolom</Emphasis> —
        bukan cuma tebak-tebakan.
      </>
    ),
  },
  {
    id: "presisi",
    without: (
      <>
        Kalau ada yang beda, tim cuma tahu "tabel ini bermasalah" —{" "}
        <Emphasis>tanpa tahu baris/kolom mana persisnya</Emphasis>.
      </>
    ),
    with: (
      <>
        Drilldown sampai <Emphasis>baris & kolom persis</Emphasis> yang berbeda — siap disalin
        langsung ke query investigasi.
      </>
    ),
  },
];

const LAYERS = [
  {
    tier: "Tier 1",
    tierLabel: "Agregat — jalan ke SEMUA tabel",
    tone: "primary" as const,
    items: [
      {
        icon: Rows3,
        title: "Struktur Tabel & Tipe Data",
        desc: "Bandingkan jumlah baris, jumlah kolom, dan tipe data tiap kolom antara source & target — termasuk mendeteksi kolom yang cuma ada di salah satu sisi. Kolom yang beda kategori tipe otomatis dilewati dari validasi statistik berikutnya, supaya tidak jadi alarm palsu.",
      },
      {
        icon: Percent,
        title: "Completeness & Uniqueness",
        desc: "Untuk tiap kolom: berapa persen isinya tidak NULL (completeness), dan berapa banyak nilai unik di dalamnya (uniqueness) — dibandingkan source vs target untuk mendeteksi data yang hilang sebagian atau duplikasi.",
      },
      {
        icon: Calculator,
        title: "Metrik Statistik per Tipe Data",
        desc: "Bukan cuma SUM/MIN/MAX generik — formulanya beda per tipe kolom, lihat rincian di bawah.",
      },
    ],
  },
  {
    tier: "Tier 2",
    tierLabel: "Row-Level — HANYA untuk tabel yang FAIL",
    tone: "accent" as const,
    items: [
      {
        icon: FileSearch,
        title: "Data yang Hilang",
        desc: "Baris mana yang ada di source tapi hilang di target, atau sebaliknya — lengkap dengan key-nya, siap disalin ke WHERE ... IN (...) untuk investigasi atau re-insert.",
      },
      {
        icon: GitCompare,
        title: "Data dengan Nilai Berbeda",
        desc: "Untuk baris yang key-nya sama di kedua sisi, LidValid bandingkan nilai tiap kolom satu per satu — kalau ada yang beda, ditampilkan persis: kolom apa, nilai source, nilai target.",
      },
    ],
  },
];

// Rincian visual utk "Metrik Statistik" di atas -- sengaja badge/pill, BUKAN
// paragraf, supaya kelihatan sekali lihat kalau formulanya beda per tipe
// kolom (angka vs teks vs tanggal tidak masuk akal dibandingkan dengan cara
// yang sama).
const STAT_TYPE_METRICS = [
  { icon: Hash, label: "Kolom Angka", metrics: ["SUM", "MIN", "MAX"] },
  { icon: CaseSensitive, label: "Kolom Teks", metrics: ["Jumlah Nilai Unik", "Panjang Min", "Panjang Max", "Panjang Rata-rata"] },
  { icon: CalendarClock, label: "Kolom Tanggal & Waktu", metrics: ["Rentang Min–Max", "Checksum Selisih Hari"] },
];

const FEATURES = [
  {
    icon: Layers,
    title: "Tiered Validation",
    desc: "Cek agregat (murah) dulu untuk semua tabel, lalu otomatis eskalasi ke row-level (presisi) hanya untuk tabel yang FAIL — satu run, dua jawaban.",
  },
  {
    icon: Database,
    title: "Multi-Engine",
    desc: "MySQL, PostgreSQL, Oracle, ClickHouse, AWS Athena, Alibaba MaxCompute, dan SQLite — satu implementasi validasi yang sama untuk ketujuhnya, termasuk semua edge-case collation & tipe data lintas engine.",
  },
  {
    icon: SearchCode,
    title: "Drilldown Per Baris",
    desc: "Sampai ke baris & kolom persis mana yang berbeda — missing keys, value diffs, tipe kolom — bukan cuma \"tabel ini beda\".",
  },
  {
    icon: Users,
    title: "Multi-User & RBAC",
    desc: "Role admin/editor/viewer, dengan data (connection, config, run) diskop per pemilik — admin tetap bisa lihat semuanya untuk troubleshooting.",
  },
  {
    icon: FileSpreadsheet,
    title: "Export Excel",
    desc: "Hasil validasi lengkap bisa diekspor ke .xlsx, kompatibel dengan format laporan yang sudah dipakai tim sebelumnya.",
  },
  {
    icon: Activity,
    title: "Progress Live",
    desc: "Pantau run yang sedang jalan secara real-time — status per tabel, log proses, tanpa perlu refresh manual.",
  },
];

const USE_CASES = [
  {
    icon: ArrowRightLeft,
    title: "Validasi Migrasi Data",
    desc: "Pindah database engine, cloud provider, atau ke data warehouse baru — pastikan tidak ada data yang hilang atau berubah selama proses pemindahan.",
  },
  {
    icon: Workflow,
    title: "Monitoring Pipeline ETL/ELT",
    desc: "Jalankan validasi secara rutin untuk mendeteksi data drift antara sumber operasional dan data warehouse sedini mungkin — bukan setelah laporan bulanan sudah kadung salah.",
  },
  {
    icon: ClipboardCheck,
    title: "UAT Sebelum Go-Live",
    desc: "Sebelum sistem baru resmi dipakai, bandingkan datanya dengan sistem lama untuk memastikan hasil migrasi/sinkronisasi sudah benar sebelum stakeholder mulai pakai.",
  },
  {
    icon: ShieldAlert,
    title: "Audit & Rekonsiliasi Data",
    desc: "Bandingkan angka dari dua sumber berbeda (mis. sistem finansial vs data warehouse) untuk kebutuhan audit atau rekonsiliasi data secara berkala.",
  },
];

const STEPS = [
  {
    icon: Plug,
    title: "Hubungkan",
    desc: "Tambahkan koneksi sumber & target — MySQL, PostgreSQL, Oracle, ClickHouse, AWS Athena, MaxCompute, atau SQLite — kredensial dienkripsi.",
  },
  {
    icon: Wrench,
    title: "Petakan Tabel",
    desc: "Buat config, petakan tabel sumber ke target, dengan auto-suggest key columns dari DDL.",
  },
  {
    icon: PlayCircle,
    title: "Jalankan & Telusuri",
    desc: "Jalankan validasi, lihat hasilnya langsung di dashboard, dan telusuri sampai baris terakhir.",
  },
];

function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <p className="mb-3 text-center font-mono text-xs font-semibold tracking-widest text-primary uppercase">
      {children}
    </p>
  );
}

export default function LandingPage() {
  return (
    <div className="flex min-h-svh flex-col scroll-smooth">
      <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2 font-semibold">
            <ShieldCheck className="size-5 text-primary" />
            LidValid
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" asChild>
              <Link to="/register">Daftar</Link>
            </Button>
            <Button asChild>
              <Link to="/login">Masuk</Link>
            </Button>
          </div>
        </div>
      </header>

      <main className="flex-1">
        {/* Hero */}
        <section className="relative overflow-hidden">
          <div className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
            <div className="absolute top-[-10%] left-1/2 h-[36rem] w-[36rem] -translate-x-1/2 rounded-full bg-primary/15 blur-3xl" />
            <div className="absolute top-1/3 left-[10%] h-64 w-64 rounded-full bg-status-running/10 blur-3xl" />
            <div className="absolute top-1/4 right-[8%] h-64 w-64 rounded-full bg-status-pass/10 blur-3xl" />
          </div>

          <div className="mx-auto max-w-6xl px-6 pt-20 pb-24 text-center">
            <Badge variant="secondary" className="mb-6 px-3 py-1 text-xs">
              <Sparkles className="size-3" /> 7 Engine Didukung — MySQL sampai AWS Athena
            </Badge>
            <h1 className="text-4xl font-semibold tracking-tight text-balance sm:text-5xl md:text-6xl">
              Validasi data,
              <br />
              <span className="text-primary">tuntas sampai baris terakhir.</span>
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-muted-foreground text-balance">
              LidValid membandingkan data sumber dan target lintas 7 engine — MySQL, PostgreSQL,
              Oracle, ClickHouse, AWS Athena, Alibaba MaxCompute, dan SQLite — dari ringkasan
              agregat sampai ke baris & kolom persis yang berbeda, dalam satu platform.
            </p>
            <div className="mt-8 flex items-center justify-center gap-3">
              <Button size="lg" className="shadow-lg shadow-primary/20" asChild>
                <Link to="/login">
                  Masuk ke Dashboard <ArrowRight className="size-4" />
                </Link>
              </Button>
              <Button size="lg" variant="outline" asChild>
                <a href="#tentang">Pelajari Lebih Lanjut</a>
              </Button>
            </div>

            {/* Mini visual: source -> LidValid -> target. Source & target
                support the exact same 7 engines symmetrically -- shown
                identically on both sides rather than implying a fixed pairing. */}
            <div className="mx-auto mt-16 flex max-w-2xl items-center justify-center gap-3 sm:gap-6">
              <div className="flex-1 rounded-xl border bg-card p-4 shadow-sm">
                <p className="font-mono text-xs text-muted-foreground">SOURCE</p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {["MySQL", "PostgreSQL", "Oracle", "+4 lainnya"].map((e) => (
                    <Badge key={e} variant="outline" className="text-[10px]">
                      {e}
                    </Badge>
                  ))}
                </div>
              </div>
              <div className="flex flex-col items-center gap-1">
                <div className="flex size-11 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-md shadow-primary/30">
                  <ShieldCheck className="size-5" />
                </div>
                <span className="font-mono text-[10px] text-muted-foreground">LidValid</span>
              </div>
              <div className="flex-1 rounded-xl border bg-card p-4 shadow-sm">
                <p className="font-mono text-xs text-muted-foreground">TARGET</p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {["ClickHouse", "Athena", "MaxCompute", "+4 lainnya"].map((e) => (
                    <Badge key={e} variant="outline" className="text-[10px]">
                      {e}
                    </Badge>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Apa itu & kenapa */}
        <section id="tentang" className="border-t bg-muted/30 py-20">
          <div className="mx-auto max-w-6xl px-6">
            <div className="mx-auto max-w-3xl text-center">
              <Eyebrow>Tentang LidValid</Eyebrow>
              <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Apa Itu LidValid?</h2>
              <p className="mt-4 text-muted-foreground">
                <strong className="text-foreground">LidValid</strong> adalah platform validasi
                data yang membandingkan data antara dua sisi — biasanya <em>source</em>{" "}
                (sistem/database asal) dan <em>target</em> (sistem/database tujuan) — untuk
                memastikan tidak ada data yang hilang, berubah, atau tidak konsisten di antara
                keduanya. Mendukung 7 engine — MySQL, PostgreSQL, Oracle, ClickHouse, AWS Athena,
                Alibaba MaxCompute, dan SQLite — dengan cara kerja yang sama persis di semuanya,
                jadi tim tidak perlu belajar tool berbeda untuk tiap kombinasi engine.
              </p>
            </div>

            <div className="mt-14 overflow-hidden rounded-2xl border bg-card shadow-sm">
              <div className="grid divide-y md:grid-cols-2 md:divide-x md:divide-y-0">
                <div className="bg-status-fail-bg/40 p-6">
                  <p className="flex items-center gap-2 font-medium text-status-fail">
                    <X className="size-4" /> Tanpa LidValid
                  </p>
                  <div className="mt-4 space-y-4">
                    {WHY_PAIRS.map((w) => (
                      <p key={w.id} className="text-sm text-muted-foreground">
                        {w.without}
                      </p>
                    ))}
                  </div>
                </div>
                <div className="bg-status-pass-bg/40 p-6">
                  <p className="flex items-center gap-2 font-medium text-status-pass">
                    <Check className="size-4" /> Dengan LidValid
                  </p>
                  <div className="mt-4 space-y-4">
                    {WHY_PAIRS.map((w) => (
                      <p key={w.id} className="text-sm text-muted-foreground">
                        {w.with}
                      </p>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Latar belakang / sejarah */}
        <section id="sejarah" className="border-t py-20">
          <div className="mx-auto max-w-3xl px-6">
            <div className="text-center">
              <Eyebrow>Latar Belakang</Eyebrow>
              <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-primary/10">
                <History className="size-6 text-primary" />
              </div>
            </div>
            <div className="mt-6 space-y-4 text-muted-foreground">
              <p>
                LidValid awalnya dibangun untuk menjawab kebutuhan yang spesifik: memastikan data
                yang sudah dipindahkan (migrasi) dari satu sistem ke sistem lain benar-benar utuh
                dan tidak berubah selama proses migrasi berlangsung. Migrasi data — entah karena
                pindah database engine, pindah ke data warehouse baru, atau proses ETL/ELT rutin —
                selalu punya risiko: baris yang gagal ter-copy, nilai yang berubah karena perbedaan
                tipe data, atau kolom yang tidak ikut terbawa.
              </p>
              <p>
                Tanpa alat validasi yang sistematis, masalah semacam ini biasanya baru ketahuan
                setelah laporan atau analisis di sisi baru menghasilkan angka yang janggal —
                padahal sudah terlambat untuk melacak baris mana yang sebenarnya bermasalah.
              </p>
              <blockquote className="rounded-r-lg border-l-4 border-primary bg-primary/5 py-3 pl-5 text-foreground not-italic">
                LidValid menggabungkan dua kebutuhan yang sebelumnya ditangani terpisah — validasi
                agregat/statistik dan validasi row-level — menjadi satu platform dengan alur kerja
                yang lebih efisien lewat <strong>Tiered Validation</strong>: cek yang murah dulu
                untuk semua tabel, baru investigasi mendalam untuk yang benar-benar bermasalah.
              </blockquote>
            </div>
          </div>
        </section>

        {/* Proses validasi berlapis */}
        <section id="proses" className="border-t bg-muted/30 py-20">
          <div className="mx-auto max-w-4xl px-6">
            <div className="text-center">
              <Eyebrow>Bagaimana Cara Kerjanya</Eyebrow>
              <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Proses Validasi Berlapis</h2>
              <p className="mx-auto mt-3 max-w-2xl text-muted-foreground">
                Setiap tabel divalidasi lewat 5 lapisan pengecekan, dikelompokkan jadi 2 tier —
                supaya tabel yang sudah PASS tidak perlu ikut menanggung biaya pengecekan yang
                lebih berat.
              </p>
            </div>

            {/* Diagram arsitektur: Sumber/Target -> Tier 1 -> cabang PASS/FAIL -> Tier 2 -> Drilldown */}
            <div className="mt-14 flex flex-col items-center">
              <div className="flex w-full max-w-md items-center gap-3">
                <div className="flex flex-1 items-center gap-2 rounded-xl border bg-card px-4 py-3 shadow-sm">
                  <Database className="size-4 shrink-0 text-muted-foreground" />
                  <span className="text-sm font-medium">DB Sumber</span>
                </div>
                <div className="flex flex-1 items-center gap-2 rounded-xl border bg-card px-4 py-3 shadow-sm">
                  <Database className="size-4 shrink-0 text-muted-foreground" />
                  <span className="text-sm font-medium">DB Target</span>
                </div>
              </div>
              <ArrowDown className="my-2 size-5 text-muted-foreground" />
            </div>

            <div className="space-y-6">
              {LAYERS.map((group, groupIdx) => (
                <div key={group.tier}>
                  <div
                    className={cn(
                      "rounded-2xl border-2 p-6",
                      group.tone === "primary"
                        ? "border-primary/30 bg-primary/5"
                        : "border-status-running/30 bg-status-running-bg/40",
                    )}
                  >
                    <div className="mb-5 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span
                        className={cn(
                          "rounded-full px-3 py-1 font-mono text-xs font-bold",
                          group.tone === "primary"
                            ? "bg-primary text-primary-foreground"
                            : "bg-status-running text-white",
                        )}
                      >
                        {group.tier}
                      </span>
                      <span className="text-sm font-medium text-muted-foreground">{group.tierLabel}</span>
                    </div>
                    <div className={cn("grid gap-4", group.items.length === 3 ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
                      {group.items.map((layer, i) => (
                        <div
                          key={layer.title}
                          className="group rounded-xl border bg-card p-5 transition-all hover:-translate-y-0.5 hover:shadow-md"
                        >
                          <div className="flex items-center gap-3">
                            <div
                              className={cn(
                                "flex size-9 shrink-0 items-center justify-center rounded-full font-mono text-sm font-bold",
                                group.tone === "primary"
                                  ? "bg-primary/10 text-primary"
                                  : "bg-status-running-bg text-status-running",
                              )}
                            >
                              {groupIdx === 0 ? i + 1 : i + 4}
                            </div>
                            <layer.icon className="size-5 text-muted-foreground transition-colors group-hover:text-primary" />
                          </div>
                          <h3 className="mt-3 text-sm font-medium">{layer.title}</h3>
                          <p className="mt-2 text-xs text-muted-foreground">{layer.desc}</p>
                        </div>
                      ))}
                    </div>

                    {groupIdx === 0 && (
                      <div className="mt-4 rounded-xl border border-dashed bg-card/60 p-4">
                        <p className="mb-3 text-center text-xs font-medium text-muted-foreground">
                          Rincian Metrik Statistik — beda formula per tipe kolom
                        </p>
                        <div className="grid gap-3 sm:grid-cols-3">
                          {STAT_TYPE_METRICS.map((t) => (
                            <div key={t.label} className="rounded-lg border bg-card p-3">
                              <div className="flex items-center gap-1.5">
                                <t.icon className="size-3.5 text-primary" />
                                <span className="text-xs font-semibold">{t.label}</span>
                              </div>
                              <div className="mt-2 flex flex-wrap gap-1">
                                {t.metrics.map((m) => (
                                  <Badge key={m} variant="outline" className="text-[10px]">
                                    {m}
                                  </Badge>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                  {groupIdx === 0 && (
                    <div className="flex flex-col items-center gap-3 py-3">
                      <div className="flex items-center gap-2 rounded-full border border-status-pass/40 bg-status-pass-bg px-4 py-1.5 text-xs font-medium text-status-pass">
                        <Check className="size-3.5" /> PASS — semua metrik cocok, tabel valid (selesai di sini)
                      </div>
                      <div className="flex flex-col items-center gap-1 text-status-fail">
                        <ArrowDown className="size-5" />
                        <span className="text-center text-xs font-medium">
                          FAIL — ada metrik yang beda, eskalasi ke Tier 2
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              ))}
              <div className="flex flex-col items-center pt-1">
                <ArrowDown className="size-5 text-muted-foreground" />
                <div className="mt-2 flex items-center gap-2 rounded-full border bg-card px-4 py-2 text-sm font-medium shadow-sm">
                  <SearchCode className="size-4 text-primary" /> Drilldown: baris & kolom persis yang beda
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Features */}
        <section id="fitur" className="border-t py-20">
          <div className="mx-auto max-w-6xl px-6">
            <div className="text-center">
              <Eyebrow>Fitur</Eyebrow>
              <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Semua yang Dibutuhkan Tim Data</h2>
            </div>
            <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map((f) => (
                <Card
                  key={f.title}
                  className="border-none shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-lg"
                >
                  <CardContent className="pt-2">
                    <div className="flex size-12 items-center justify-center rounded-xl bg-primary/10">
                      <f.icon className="size-6 text-primary" />
                    </div>
                    <h3 className="mt-4 font-medium">{f.title}</h3>
                    <p className="mt-2 text-sm text-muted-foreground">{f.desc}</p>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        </section>

        {/* Use cases */}
        <section id="use-case" className="border-t bg-muted/30 py-20">
          <div className="mx-auto max-w-6xl px-6">
            <div className="text-center">
              <Eyebrow>Use Case</Eyebrow>
              <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Cocok Untuk Apa Saja?</h2>
              <p className="mx-auto mt-3 max-w-2xl text-muted-foreground">
                Validasi migrasi cuma titik awalnya — pola yang sama berguna di mana pun ada dua
                sumber data yang harus tetap konsisten.
              </p>
            </div>
            <div className="mt-10 grid gap-5 sm:grid-cols-2">
              {USE_CASES.map((u) => (
                <div
                  key={u.title}
                  className="flex gap-4 rounded-xl border bg-card p-5 transition-all hover:-translate-y-0.5 hover:shadow-md"
                >
                  <div className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary/10">
                    <u.icon className="size-5 text-primary" />
                  </div>
                  <div>
                    <h3 className="font-medium">{u.title}</h3>
                    <p className="mt-2 text-sm text-muted-foreground">{u.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* How it works */}
        <section className="border-t py-20">
          <div className="mx-auto max-w-6xl px-6">
            <div className="text-center">
              <Eyebrow>Mulai Pakai</Eyebrow>
              <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Cara Kerja</h2>
            </div>
            <div className="relative mt-14 grid gap-10 sm:grid-cols-3">
              <div className="absolute top-6 right-0 left-0 hidden h-px bg-border sm:block" />
              {STEPS.map((s, i) => (
                <div key={s.title} className="relative text-center">
                  <div className="relative mx-auto flex size-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-md shadow-primary/20">
                    <s.icon className="size-6" />
                  </div>
                  <h3 className="mt-4 font-medium">
                    {i + 1}. {s.title}
                  </h3>
                  <p className="mt-2 text-sm text-muted-foreground">{s.desc}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Closing CTA */}
        <section className="border-t bg-muted/30 py-20">
          <div className="mx-auto max-w-2xl px-6 text-center">
            <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Siap mulai validasi?</h2>
            <p className="mt-3 text-muted-foreground">
              Buat akun sendiri dan langsung mulai kelola Connection & Config milik Anda -- tidak
              perlu menunggu diundang admin.
            </p>
            <div className="mt-8 flex items-center justify-center gap-3">
              <Button size="lg" className="shadow-lg shadow-primary/20" asChild>
                <Link to="/register">
                  Buat Akun <ArrowRight className="size-4" />
                </Link>
              </Button>
              <Button size="lg" variant="outline" asChild>
                <Link to="/login">Sudah Punya Akun</Link>
              </Button>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t py-8">
        <div className="mx-auto max-w-6xl px-6 text-center text-sm text-muted-foreground">
          LidValid — alat validasi data internal. &copy; {new Date().getFullYear()}.
        </div>
      </footer>
    </div>
  );
}
