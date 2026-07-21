# TECHNICAL.md — Dokumentasi Teknis LidValid

Dokumen ini menjelaskan **bagaimana LidValid bekerja secara internal**: arsitektur, alur data,
dan — per permintaan — **penjelasan tiap file, tiap fungsi, dan logika baris-per-baris** untuk
bagian-bagian penting. Untuk gambaran fitur & cara pakai, baca [README.md](README.md) dulu.
Untuk visi produk lengkap (PRD, wireframe, roadmap), baca dokumen di
`d:\data-pipeline-batch\docs\validation-platform\`.

Cara membaca dokumen ini: bagian 1-4 memberi peta besar (arsitektur, alur). Bagian 5 & 6 adalah
referensi kode — bisa dibaca berurutan (untuk belajar dari nol) atau dicari per-file (`Ctrl+F` nama
file) saat butuh memahami satu bagian spesifik.

---

## 1. Dua Lapis Arsitektur

LidValid terdiri dari **dua lapis yang sengaja dipisah keras**:

```
┌─────────────────────────────────────────────────────────────┐
│  app/                    — LAPISAN WEB                       │
│  FastAPI + SQLAlchemy ORM + Jinja2 + vanilla JS              │
│  Tahu: user, session, koneksi DB tersimpan, config, run,     │
│        HTTP request/response, HTML                           │
│  TIDAK tahu: bagaimana cara membandingkan dua tabel           │
└───────────────────────────┬───────────────────────────────────┘
                             │ memanggil sebagai library biasa
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  validation_core/        — LAPISAN ENGINE                    │
│  Python murni (pandas + koneksi DB), TIDAK ada FastAPI,       │
│  TIDAK ada SQLAlchemy ORM, TIDAK tahu apa itu "session" HTTP  │
│  Tahu: cara membandingkan tabel A vs tabel B (agregat &       │
│        row-level), cara membangun query per dialect SQL       │
└─────────────────────────────────────────────────────────────┘
```

**Kenapa dipisah begini?** Supaya `validation_core` bisa dites dan dipakai **tanpa web sama
sekali** — lihat `tests/conftest.py` yang langsung memanggil `create_connector()` dan
`AggregateValidator` tanpa menyentuh FastAPI atau database ORM apa pun. Ini juga berarti kalau
suatu saat arsitektur web-nya diganti total (misal ke React + Celery, sesuai dokumen arsitektur
asli), `validation_core` tidak perlu ditulis ulang — tinggal panggil dari worker Celery alih-alih
dari `threading.Thread`.

Aturan ketat yang dipegang: **`validation_core` tidak pernah mengimpor apa pun dari `app`**. Kalau
kamu lihat `from .. import models` atau `from fastapi import ...` di dalam folder
`validation_core/`, itu adalah bug arsitektur.

---

## 2. Alur Kerja End-to-End

### 2.1 Alur permintaan HTTP biasa (misal buka halaman Dashboard)

```
Browser → GET /dashboard
    → app/main.py: FastAPI app menerima request
    → app/routers/ui.py: fungsi dashboard() dipanggil
        → Depends(require_login)   → app/auth.py: cek session cookie, kalau kosong
                                       raise RedirectToLogin → ditangkap exception
                                       handler → redirect ke /login
        → Depends(get_db)           → app/database.py: buka SQLAlchemy Session baru
        → query database via db.query(models.Run)... (app/models.py = ORM)
        → susun dict context (recent_runs, trend, problem_tables, ...)
        → templates.TemplateResponse(request, "dashboard.html", context)
    → app/templates/dashboard.html dirender oleh Jinja2 jadi HTML
    → HTML dikirim balik ke browser
```

### 2.2 Alur menjalankan validasi (yang paling penting — "Tiered Validation")

Ini alur inti seluruh aplikasi. Dipicu saat user klik tombol **"Run Now"** di halaman config:

```
1. Browser POST /configs/{id}/run
   → app/routers/ui.py::config_run_now()
     → run_service.create_run(db, config, mode, trigger_type="manual")
         - Insert 1 baris ke tabel `runs` (status="queued")
         - Insert 1 baris ke tabel `run_tables` PER tabel yang enabled di config
           (status="pending") — jadi sebelum eksekusi pun user sudah bisa lihat
           daftar tabel yang AKAN divalidasi
     → run_service.start_run_async(run.id)
         - Membuat 1 `threading.Thread` baru (daemon) yang menjalankan
           `_execute_run(run_id)` DI BACKGROUND — fungsi route langsung
           `return RedirectResponse("/runs/{run.id}")` tanpa menunggu thread
           selesai, sehingga user langsung diarahkan ke halaman monitoring live

2. DI DALAM THREAD BACKGROUND — run_service._execute_run(run_id):
   a. Ambil objek Run + ValidationConfig + 2 Connection (source & target) dari DB
   b. **PENTING**: baca source_conn.database, target_conn.database, run.mode
      SEBAGAI STRING BIASA sekarang juga (lihat §6.8 soal kenapa ini kritis)
   c. Set status semua RunTable jadi "running", commit
   d. Untuk setiap RunTable, siapkan objek TableSpec (validation_core) dari
      ConfigTable yang tersimpan di DB — key_columns, chunk_column, date_column, dst
   e. Buka ThreadPoolExecutor dengan N worker (N = table_concurrency, default 4)
   f. Untuk SETIAP tabel, submit fungsi worker() ke pool:
        worker() TIDAK PERNAH menyentuh SQLAlchemy — hanya membuat Connector
        baru (validation_core) dan memanggil vc_run_table(...)

3. DI DALAM SETIAP WORKER THREAD — validation_core.runner.tiered.run_table():
   a. TIER 1 — AGGREGATE (murah):
        AggregateValidator(...).run() menjalankan Report 1-5:
        - Report 1: COUNT(*) source vs target
        - Report 2: completeness & uniqueness per kolom
        - Report 3: statistik per kolom (sum/min/max/count/len/datediff)
        - Report 4 & 5: breakdown bulanan & tahunan
        Semua ini query SQL AGREGAT — TIDAK PERNAH menarik row mentah.
   b. Kalau mode="aggregate" ATAU hasilnya PASS → SELESAI, tidak lanjut ke Tier 2.
   c. Kalau FAIL dan mode mengizinkan (tiered/rowlevel_*) → TIER 2 — ROW-LEVEL:
        RowLevelValidator(...).run() men-scan tabel per CHUNK (mis. 2 juta id
        sekaligus), membandingkan tiap chunk dengan compare_chunk_multi(),
        mengumpulkan missing keys & value diffs.
   d. Kembalikan TableRunResult(status, aggregate, rowlevel, queries, ...)

4. KEMBALI DI THREAD UTAMA (_execute_run) — begitu satu worker selesai:
   a. run_service._persist_table_result() menerjemahkan TableRunResult (objek
      Python murni dari validation_core) menjadi baris-baris di tabel SQL:
      - Update kolom-kolom di `run_tables` (status, source_rows, agg_metrics, ...)
      - Insert N baris ke `findings_aggregate` (satu per mismatch kolom/periode)
      - Insert N baris ke `findings_rowlevel` (satu per missing key / value diff)
   b. Publish event ke events_bus (untuk polling live di browser)
   c. Setelah SEMUA tabel selesai → update `runs.status` = "completed",
      hitung `runs.summary` (jumlah pass/fail/error)

5. DI BROWSER — halaman /runs/{id} (run_detail.html) melakukan polling:
   setiap 2 detik, JavaScript memanggil:
   - GET /api/runs/{id}/status  → cek apakah run sudah selesai
   - GET /runs/{id}/tables-fragment → HTML terbaru daftar status tabel
   Begitu status bukan "running"/"queued" lagi, halaman di-reload penuh.
```

### 2.3 Alur drilldown (row mana yang beda)

```
Run Report (/runs/{id})
  → klik baris tabel yang FAIL
  → GET /runs/{id}/tables/{run_table_id}?tab=diffs
    → app/routers/ui.py::table_drilldown()
      → ambil rt.rowlevel_findings dari DB (relationship SQLAlchemy)
      → filter finding_type == "value_diff"
      → render table_drilldown.html tab "Value Diffs"
        → tampilkan tabel: key | kolom | nilai_source | nilai_target
```

---

## 3. Struktur Direktori Lengkap

```
lidvalid/
├── validation_core/                    # ENGINE (murni Python, tanpa web)
│   ├── __init__.py
│   ├── categories.py                    # kategori tipe kolom + values_match()
│   ├── models.py                        # TableSpec, RunSettings (dataclass biasa)
│   ├── events.py                        # ProgressEvent (dataclass event progres)
│   ├── excel_export.py                  # export .xlsx dari list[TableRunResult]
│   ├── connectors/
│   │   ├── base.py                       # abstract Dialect + Connector
│   │   ├── mysql.py                      # MySqlDialect + MySqlConnector
│   │   ├── clickhouse.py                 # ClickHouseDialect + ClickHouseConnector
│   │   ├── sqlite_demo.py                # SqliteDialect + SqliteConnector
│   │   └── registry.py                   # create_connector() — factory
│   ├── aggregate/
│   │   └── validator.py                  # AggregateValidator — Report 1-5
│   ├── rowlevel/
│   │   ├── comparator.py                 # compare_chunk_multi() dkk (murni fungsi)
│   │   └── runner.py                     # RowLevelValidator — loop per-chunk
│   └── runner/
│       ├── events.py                      # re-export dari ..events (hindari circular import)
│       ├── retry.py                       # run_with_retry()
│       └── tiered.py                      # run_table() — orkestrasi Tier 1 → Tier 2
│
├── app/                                 # WEB (FastAPI)
│   ├── main.py                           # entry point, lifespan startup
│   ├── database.py                       # SQLAlchemy engine/session, init_db()
│   ├── models.py                         # ORM: User, Connection, ValidationConfig, ...
│   ├── security.py                       # enkripsi Fernet + hashing password
│   ├── auth.py                           # session auth, require_login()
│   ├── services/
│   │   ├── connections_service.py         # ORM Connection → validation_core ConnectionParams
│   │   ├── discovery_service.py           # list tabel/kolom, auto-suggest mapping
│   │   ├── events_bus.py                  # in-memory pub/sub progres run
│   │   ├── run_service.py                 # orkestrasi run (thread, persist hasil)
│   │   └── export_service.py              # export Excel dari data ORM
│   ├── routers/
│   │   ├── ui.py                          # semua halaman HTML + form POST
│   │   └── api.py                         # endpoint JSON (polling status)
│   ├── templates/                        # Jinja2 — 1 file per halaman
│   └── static/app.css                    # CSS (tanpa framework, tanpa build step)
│
├── tests/                                # pytest — 155 test, semua via SQLite lokal
│   └── test_rbac.py                      # role gating (viewer/editor/admin) + data scoping (owner_id)
├── scripts/
│   ├── seed_demo.py                       # generator data demo + 1 run contoh
│   └── create_user.py                     # CLI buat/update akun (belum ada UI user-management)
├── data/                                 # runtime: lidvalid.sqlite, secret.key, exports/
├── requirements.txt
├── pytest.ini
│
├── Dockerfile                            # image produksi (python:3.12-slim, non-root)
├── .dockerignore
├── docker-compose.yml                    # app + Caddy, network "internal" ber-subnet tetap
├── Caddyfile                             # reverse proxy + HTTPS otomatis (Let's Encrypt/sslip.io)
├── .env.example                          # template env var produksi (LIDVALID_*)
└── .gitignore
```

Lihat §9 (RBAC & Data Scoping) dan §10 (Deployment) untuk penjelasan bagian yang ditambahkan
setelah migrasi ke VPS (2026-07-21) — rebrand dari nama sebelumnya (ValidaHub), penegakan role,
per-user data scoping, containerization, dan arsitektur akses database staging dari VPS.

---

## 4. `validation_core` — Penjelasan Detail Per File

### 4.1 `categories.py`

**Tujuan**: satu tempat untuk menjawab dua pertanyaan yang dipakai di mana-mana —
"kolom ini termasuk kategori tipe apa?" dan "apakah dua nilai metrik ini boleh dianggap sama?"

```python
META_COLUMNS = frozenset({'ingested_at', 'version', '_dlt_load_id', '_dlt_id'})
```
Baris ini adalah gabungan (union) dari dua konstanta yang dulu terpisah di dua tool lama
(`PIPELINE_COLS` di `validation-data` cuma 2 kolom, `CLICKHOUSE_META_COLUMNS` di
`validation_database` 4 kolom). Kolom-kolom ini SELALU dikecualikan dari perbandingan statistik
karena memang ditambahkan otomatis oleh pipeline DLT, bukan bagian dari data asli.

```python
_CATEGORIES = {
    'numeric': ['int', 'tinyint', ..., 'year'],   # <- 'year' baris tambahan (fix bug)
    'string':  ['varchar', 'char', 'text', ...],
    'date':    ['date', 'date32'],
    'timestamp': ['datetime', 'timestamp', 'datetime64'],
    'boolean': ['boolean', 'bool'],
}
```
Dictionary ini adalah "kamus" pemetaan nama tipe kolom mentah (dari `INFORMATION_SCHEMA` MySQL
atau `system.columns` ClickHouse) ke satu dari 5 kategori. Kata `'year'` ditambahkan secara sengaja
di kategori numeric — ini FIX untuk bug lama: MySQL punya tipe `YEAR` yang dulu tidak dikenali
kamus ini, jatuh ke fallback `'string'`, padahal ClickHouse menyimpan kolom yang sama sebagai
`UInt16` (numeric) — akibatnya kode lama mencoba `length(UInt16_column)` di ClickHouse dan crash.
`'date32'` ditambahkan belakangan dengan alasan serupa: ClickHouse `Date32` (varian `Date` dengan
jangkauan lebih lebar, 1900-01-01..2299-12-31) tidak cocok dengan keyword `'date'` manapun di
`get_category()` (lihat logika matching di bawah — `"date32" != "date"` dan tidak diikuti `(`/` `),
jadi jatuh ke fallback `'string'` — akibatnya kolom Date32 diam-diam DILEWATI dari floor-1970/ceiling
max-date (§4.3-4.5) karena kode itu hanya jalan untuk kategori `'date'`/`'timestamp'`.

```python
def get_category(col_type: str) -> str:
    t = col_type.lower().strip()
    if t.startswith('array'):
        return 'array'
    if t.startswith('nullable('):
        return get_category(t[9:-1])   # <- rekursif! "Nullable(DateTime)" -> get_category("DateTime")
    for cat, keywords in _CATEGORIES.items():
        for kw in keywords:
            if t == kw or t.startswith(kw + '(') or t.startswith(kw + ' '):
                return cat
    return 'string'   # fallback aman kalau tipe tak dikenal
```
Baris demi baris:
1. `t = col_type.lower().strip()` — normalisasi case & whitespace, karena MySQL kadang kirim
   `"VARCHAR"`, ClickHouse kirim `"String"`.
2. Cek prefix `"array"` dulu — ClickHouse punya `Array(String)`, `Array(UInt8)`, dll. Ini HARUS
   dicek sebelum loop kategori supaya tidak salah ketangkap sebagai `string`.
3. Cek prefix `"nullable("` — ClickHouse membungkus tipe apa pun jadi `Nullable(X)` kalau kolom
   boleh NULL. Kode ini men-strip pembungkus itu (`t[9:-1]` = ambil isi dalam kurung, buang 9
   karakter `"nullable("` dari depan dan `")"` dari belakang) lalu memanggil `get_category` lagi
   secara REKURSIF terhadap tipe di dalamnya. Jadi `Nullable(Nullable(Int32))` pun tetap benar
   (dua lapis rekursi) karena hasil pemanggilan pertama akan mengecek prefix `nullable(` lagi.
4. Loop tiap kategori, tiap keyword: cocok kalau tipe PERSIS sama dengan keyword (`t == kw`, mis.
   `"int"`), ATAU diikuti kurung buka (`t.startswith(kw + '(')`, mis. `"decimal(10,2)"` cocok
   dengan keyword `"decimal"`), ATAU diikuti spasi (`t.startswith(kw + ' ')`, untuk kasus MySQL
   seperti `"int unsigned"`).
5. Kalau tidak ada yang cocok sama sekali → `'string'` (fallback paling aman: dianggap teks biasa).

```python
def ceil_stat(v) -> int:
    return math.ceil(float(v) - 1e-9)
```
Ini fungsi 1 baris tapi krusial. Masalah yang dipecahkan: MySQL `AVG()` mengembalikan 4 desimal
(`15.5714`), ClickHouse mengembalikan presisi penuh (`15.571428571...`). Kalau dibandingkan mentah
`15.5714 != 15.571428571` → dianggap beda padahal sama. Solusinya: bulatkan KEDUANYA ke atas
(`ceil`) sampai jadi integer, lalu baru dibandingkan. Angka `1e-9` yang dikurangkan sebelum `ceil`
adalah "nudge" kecil untuk mencegah noise floating-point (mis. `4.0000000000001` akibat pembulatan
biner) ikut naik jadi `5` gara-gara `ceil()` — dikurangi dulu sedikit supaya nilai yang "harusnya
4.0 tapi keganggu noise jadi 4.0000000001" tetap kembali ke bawah sebelum di-ceil.

```python
def values_match(sv, tv) -> bool:
    import pandas as pd
    if pd.isna(sv) and pd.isna(tv):
        return True
    try:
        sv_f, tv_f = float(sv), float(tv)
        if math.isnan(sv_f) or math.isnan(tv_f):
            return True
        return ceil_stat(sv_f) == ceil_stat(tv_f)
    except (TypeError, ValueError):
        def _norm(v):
            return re.sub(r'\.0+$', '', str(v).strip())
        return _norm(sv) == _norm(tv)
```
Ini fungsi yang paling sering dipanggil di seluruh codebase — **satu-satunya tempat** yang
memutuskan "apakah dua angka/nilai boleh dianggap sama". Alur logikanya:
1. `pd.isna(sv) and pd.isna(tv)` — kalau KEDUANYA kosong/NaN, anggap sama (bukan mismatch).
2. `try: float(sv), float(tv)` — coba paksa jadi angka. Kalau berhasil:
   - Kalau salah satu jadi `NaN` SETELAH di-cast float (kasus aneh yang tidak tertangkap
     `pd.isna` di langkah 1, mis. string `"nan"` literal) → anggap sama juga (skip, bukan
     mismatch — filosofinya: kalau salah satu sisi memang tidak punya nilai valid untuk
     dibandingkan, jangan tuduh itu "beda", cukup lewati).
   - Kalau keduanya angka valid → bandingkan pakai `ceil_stat` (lihat di atas).
3. `except (TypeError, ValueError)` — kalau `float()` gagal (berarti ini string non-angka, misal
   tanggal atau nama), masuk jalur perbandingan STRING: `_norm()` membuang trailing `.000...` (fix
   untuk kasus MySQL `"2025-08-14 00:38:00"` vs ClickHouse `toString()` yang menghasilkan
   `"2025-08-14 00:38:00.000"` — beda literal string padahal representasi waktu yang sama) dan
   `.strip()` whitespace, baru dibandingkan sebagai string.

### 4.2 `models.py` (validation_core, BUKAN app/models.py yang ORM)

Berisi 2 dataclass murni Python (tanpa dependensi database apa pun):

```python
@dataclass
class TableSpec:
    source_table: str
    target_table: str
    key_columns: list[str] = field(default_factory=lambda: ["id"])
    chunk_column: str | None = None
    date_column: str | None = None
    exclude_columns: list[str] = field(default_factory=list)
    mode_override: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    enabled: bool = True
    note: str = ""

    def effective_chunk_column(self) -> str:
        return self.chunk_column or self.key_columns[0]
```
Ini adalah "spesifikasi" satu pasangan tabel yang mau divalidasi — padanan langsung 1 baris di
tabel `config_tables` (ORM). `effective_chunk_column()` adalah helper: kalau user tidak set
`chunk_column` secara eksplisit, defaultnya adalah kolom key PERTAMA (`key_columns[0]`) —
konsisten dengan behavior tool lama `validation_database`.

```python
@dataclass
class RunSettings:
    mode: str = "tiered"
    meta_columns: frozenset = field(default_factory=lambda: META_COLUMNS)
    id_chunk_size: int = 2_000_000
    full_mode_row_threshold: int = 5_000_000
    stat_ref_date: str = "2010-01-15"
    table_concurrency: int = 4
    skip_period_breakdown: bool = False
    fuzzy_threshold: float = 1.0
    numeric_rel_tolerance: float = 1e-6
    numeric_abs_tolerance: float = 1e-9
    retry_max: int = 3
    retry_backoff_seconds: int = 20
    heartbeat_seconds: int = 30
    rowlevel_sample_cap: int = 10_000
```
Semua angka "ajaib" yang dulu hardcoded di kedua tool lama (`2010-01-15` untuk datediff,
`2000000` untuk chunk size, `3` untuk retry, dst) sekarang jadi field yang bisa di-override per
config (lihat `run_service.build_run_settings()` di §6.8).

`numeric_rel_tolerance`/`numeric_abs_tolerance` TIDAK ada di kedua tool lama sama sekali — field baru
yang menutup celah nyata: sisi agregat sudah lama punya toleransi presisi (`ceil_stat`, §4.1), tapi
sisi row-level (`column_diff_mask`, §4.9) dulu membandingkan angka EXACT, sehingga dua engine yang
menyerialisasi nilai float yang SAMA dengan presisi desimal berbeda (kasus nyata: kolom
`yearly_need`, `482.437346437` vs `482.43734643734643`) membanjiri tab Value Diffs dengan
false-positive.

### 4.3 `connectors/base.py` — Dialect & Connector

Ini file paling penting untuk memahami KENAPA engine ini bisa mendukung MySQL, ClickHouse, dan
SQLite dengan HANYA SATU implementasi Report 1-5 (tidak ada `if engine == 'mysql': ... else: ...`
di `aggregate/validator.py`).

**`class Dialect`** — kelas dasar yang mendefinisikan "kontrak" apa saja yang berbeda antar engine
SQL. Tiap method punya implementasi default yang AMAN (identity/no-op), dan engine yang butuh
perilaku berbeda meng-override method spesifik saja:

| Method | Fungsi | Siapa yang override & kenapa |
|---|---|---|
| `quote_ident(col)` | Bungkus nama kolom biar aman dari reserved keyword | MySQL & ClickHouse → backtick `` `col` ``; SQLite → `"col"` |
| `table_ref(db, table, final)` | Bentuk `db.table`, tambah `FINAL` kalau perlu | ClickHouse override untuk selalu bisa tambah `FINAL`; SQLite override untuk buang prefix db (1 file = 1 schema) |
| `date_floor_1970(expr, category)` | "Ratakan" tanggal < 1970 | KETIGA engine override — simetris di kedua sisi (lihat §4.8), bukan cuma MySQL: MySQL/SQLite pakai literal string biasa (`GREATEST`/`MAX`), ClickHouse butuh `category` untuk pilih cast `toDate(...)`/`toDateTime(...)` yang tipe-nya cocok |
| `date_ceiling(expr, category, max_value)` | Cap tanggal ke batas maksimum (kebalikan floor) | Sama seperti `date_floor_1970` — identity kalau `max_value` `None` (tidak ada sisi ClickHouse) |
| `date_max_bound(col_type)` | Batas maksimum tanggal engine ini bisa simpan | HANYA ClickHouse override (lewat `clickhouse_date_max()`, §4.5) — MySQL/SQLite tidak punya batas yang realistis untuk data bisnis (MySQL DATE/DATETIME sampai tahun 9999), jadi `None` |
| `wrap_minmax_datetime(expr)` | Bungkus `MIN()/MAX()` datetime jadi string | HANYA ClickHouse override (`toString(...)`) — cegah pandas `OutOfBoundsDatetime` |
| `period_expr(col, granularity)` | Ekspresi SQL untuk "kelompokkan per bulan/tahun" | Ketiga engine override (beda fungsi native masing-masing) |
| `period_expr_literal(...)` | Sama seperti di atas tapi versi "aman ditempel manual ke SQL client" | Hanya MySQL override (lihat §4.4 soal `%%` vs `%`) |
| `datediff_expr(col, ref)` | Selisih hari dari tanggal referensi | Ketiga engine override |
| `date_range_filter(col, start, end)` | Klausa `WHERE` filter tanggal | Ketiga engine override |
| `distinct_string_expr(expr)` | Ekspresi untuk `COUNT(DISTINCT ...)` string, case-insensitive | MySQL override (tambah `BINARY`) |
| `is_not_null_ratio(col)` | Rasio non-NULL | ClickHouse & SQLite override (SQLite butuh `CAST ... AS REAL` biar tidak truncated integer division) |

Karena semua perbedaan spesifik-engine ini dikumpulkan di SATU tempat (dialect), kode
`AggregateValidator` bisa menulis logika Report 1-5 SATU KALI dan hanya memanggil
`dialect.method_apa_pun(...)` — engine mana pun yang lagi dipakai otomatis dapat perilaku yang
benar. Ini adalah pola desain **Strategy Pattern**.

**`class Connector(ABC)`** — pembungkus koneksi database sungguhan (bukan lagi soal SQL, tapi soal
"bagaimana cara benar-benar mengeksekusi query dan dapat hasil"):
```python
class Connector(ABC):
    dialect: Dialect          # tiap subclass WAJIB set atribut class ini

    @abstractmethod
    def query_df(self, sql: str) -> pd.DataFrame: ...   # jalankan query, kembalikan DataFrame

    def query_df_stream(self, sql: str):                 # default: 1 blok saja
        yield self.query_df(sql)

    @abstractmethod
    def get_schema(self, database, table) -> pd.DataFrame: ...  # daftar (nama_kolom, tipe_kolom)

    @abstractmethod
    def list_tables(self, database) -> list[str]: ...

    def test_connection(self) -> dict:
        start = time.monotonic()
        try:
            self.query_df(self._probe_sql())          # default: "SELECT 1"
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return {"ok": True, "latency_ms": latency_ms, "error": None}
        except Exception as exc:
            return {"ok": False, "latency_ms": None, "error": str(exc)}
```
`test_connection()` sudah diimplementasikan LENGKAP di kelas dasar (subclass tidak perlu
menulis ulang) — cukup override `_probe_sql()` kalau `"SELECT 1"` tidak valid untuk engine
tertentu. Dipakai oleh tombol "Test" di halaman Connections.

```python
def get_primary_key(self, database: str, table: str) -> list[str]:
    return []   # default: "tidak tahu" -- bukan @abstractmethod, opt-in per engine
```
Method ini BUKAN `@abstractmethod` (beda dari `query_df`/`get_schema`/`list_tables`) — defaultnya
mengembalikan list kosong, artinya "engine ini belum tahu cara mendeteksi key". Dipakai untuk
auto-suggest `key_columns` (termasuk composite key) di config builder — lihat §4.4-4.6 untuk
implementasi tiap engine dan §5.6 untuk cara hasilnya dipakai.

### 4.4 `connectors/mysql.py`

`MySqlDialect.period_expr()` adalah bagian paling rawan salah kalau di-refactor tanpa paham
konteksnya:
```python
def period_expr(self, col: str, granularity: str) -> str:
    if granularity == 'monthly':
        return f"DATE_FORMAT({col}, '%%Y-%%m')"
    return f"DATE_FORMAT({col}, '%%Y')"
```
`%%` (dobel persen) DISENGAJA, bukan typo. Alasannya berlapis:
1. Query ini dieksekusi lewat `pd.read_sql(sql, conn)` di mana `conn` adalah koneksi SQLAlchemy
   dengan dialect `mysql+pymysql`.
2. Dialect itu memakai **paramstyle "pyformat"** — artinya sebelum SQL mentah ini benar-benar
   dikirim ke server MySQL, DBAPI/SQLAlchemy memprosesnya melalui mekanisme substitusi ala
   `%`-formatting Python, di mana `%%` akan di-collapse jadi `%` TUNGGAL.
3. Jadi string Python `'%%Y-%%m'` yang kita tulis di kode → setelah lewat lapisan itu → yang
   BENAR-BENAR diterima server MySQL adalah `'%Y-%m'` (satu persen) — yaitu format spesifier
   `DATE_FORMAT` yang valid (`%Y`=tahun 4 digit, `%m`=bulan 2 digit).
4. Kalau kita tulis `'%Y-%m'` (satu persen) langsung di kode Python, lapisan pyformat itu akan
   coba menafsirkannya sebagai token substitusi Python (`%Y` bukan token valid) dan bisa error atau
   salah baca.

Karena kerumitan ini HANYA relevan untuk jalur eksekusi terprogram (lewat SQLAlchemy), method
`period_expr_literal()` dibuat TERPISAH khusus untuk teks SQL yang ditujukan supaya manusia
copy-paste langsung ke klien MySQL (fitur "Salin Query Investigasi") — di situ dipakai `%Y-%m`
tunggal karena tidak ada lapisan pyformat yang menerjemahkannya.

```python
def distinct_string_expr(self, expr: str) -> str:
    return f"BINARY LOWER(TRIM({expr}))"
```
`BINARY` memaksa perbandingan byte-mentah. Kenapa perlu: collation default MySQL
(`utf8mb4_0900_ai_ci`) bersifat *accent-insensitive* (`à` dianggap sama dengan `a`) dan bahkan
menyamakan beberapa whitespace non-standar. ClickHouse selalu membandingkan byte apa adanya. Tanpa
`BINARY`, `COUNT(DISTINCT LOWER(TRIM(col)))` di MySQL bisa menghasilkan angka LEBIH KECIL daripada
ClickHouse untuk data yang SAMA persis, menimbulkan mismatch palsu. **Catatan penting yang harus
diingat**: jangan ganti dengan `COLLATE utf8mb4_bin` — collation itu bersifat `PAD SPACE` (spasi di
akhir string diabaikan saat dibandingkan), jadi malah menimbulkan mismatch ke arah SEBALIKNYA.

```python
def date_floor_1970(self, expr: str, category: str) -> str:
    return f"GREATEST({expr}, '1970-01-01')"

def date_ceiling(self, expr: str, category: str, max_value: str | None) -> str:
    if not max_value:
        return expr
    return f"LEAST({expr}, '{max_value}')"
```
`category` diabaikan di MySQL (dan SQLite) — `GREATEST`/`LEAST` dengan literal string ISO date
bekerja sama baiknya untuk kolom `DATE` maupun `DATETIME`, tidak perlu cast tipe eksplisit seperti
ClickHouse (§4.5). `date_ceiling` identity (`return expr`) kalau `max_value` `None` — artinya sisi
lain dari perbandingan ini BUKAN ClickHouse, jadi tidak ada batas maksimum yang perlu ditiru (lihat
`date_max_bound` di §4.3: MySQL sendiri selalu mengembalikan `None` di situ, karena `DATE`/`DATETIME`
MySQL sanggup sampai tahun 9999 — bukan batas yang realistis untuk data bisnis mana pun).

`MySqlConnector.__init__()` — membuka koneksi via SQLAlchemy:
```python
@event.listens_for(self._engine, "connect")
def _set_session_vars(dbapi_conn, _):
    with dbapi_conn.cursor() as cur:
        cur.execute("SET SESSION wait_timeout=86400")
        cur.execute("SET SESSION interactive_timeout=86400")
        cur.execute("SET SESSION net_read_timeout=3600")
        cur.execute("SET SESSION net_write_timeout=3600")
```
Event listener ini dipanggil OTOMATIS oleh SQLAlchemy setiap kali koneksi FISIK baru dibuka ke
MySQL (bukan setiap query — koneksi di-pool). Isinya menaikkan berbagai timeout session MySQL ke
nilai besar (86400 detik = 24 jam untuk idle timeout, 3600 detik = 1 jam untuk baca/tulis), supaya
query berat (chunk 2 juta baris, atau breakdown periode di tabel jutaan baris) tidak diputus paksa
oleh server di tengah jalan.

```python
def get_primary_key(self, database: str, table: str) -> list[str]:
    q = (
        f"SELECT COLUMN_NAME AS c FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
        f"WHERE TABLE_SCHEMA = '{database}' AND TABLE_NAME = '{table}' "
        f"AND CONSTRAINT_NAME = 'PRIMARY' ORDER BY ORDINAL_POSITION"
    )
    df = self.query_df(q)
    return df["c"].tolist() if "c" in df.columns else []
```
`INFORMATION_SCHEMA.KEY_COLUMN_USAGE` berisi satu baris PER KOLOM yang menjadi bagian dari sebuah
constraint — untuk PRIMARY KEY composite (mis. `PRIMARY KEY (order_id, material_id)`), ini
menghasilkan 2 baris. `ORDER BY ORDINAL_POSITION` WAJIB ada — tanpanya, urutan baris yang
dikembalikan tidak terjamin mengikuti urutan deklarasi di DDL, padahal urutan kolom composite key
itu PENTING (dipakai apa adanya oleh `composite_key()` di §4.9 untuk menyusun string key
`"{order_id}_{material_id}"` — kalau kebalik jadi `"{material_id}_{order_id}"`, hasilnya tetap
valid sebagai key tapi TIDAK KONSISTEN kalau suatu saat dibandingkan dengan hasil dari engine lain
yang urutannya beda). `CONSTRAINT_NAME = 'PRIMARY'` menyaring supaya hanya PRIMARY KEY yang
terambil, bukan unique key/index lain yang juga tercatat di tabel yang sama.

### 4.5 `connectors/clickhouse.py`

```python
_DATE_MAX = "2149-06-06"
_DATE32_MAX = "2299-12-31"
_DATETIME_MAX = "2106-02-07 06:28:15"
_DATETIME64_MAX = "2299-12-31 23:59:59"

def clickhouse_date_max(col_type: str) -> str:
    t = col_type.lower().strip()
    if t.startswith("nullable("):
        t = t[9:-1]
    if t.startswith("datetime64"):
        return _DATETIME64_MAX
    if t.startswith("datetime") or t.startswith("timestamp"):
        return _DATETIME_MAX
    if t.startswith("date32"):
        return _DATE32_MAX
    if t.startswith("date"):
        return _DATE_MAX
    return _DATETIME64_MAX
```
Nilai batas ini diambil dari dokumentasi ClickHouse (dicek Juli 2026): `Date` `[1970-01-01,
2149-06-06]`, `Date32` `[1900-01-01, 2299-12-31]`, `DateTime` `[1970-01-01 00:00:00, 2106-02-07
06:28:15]` (32-bit detik-sejak-epoch), `DateTime64` `[1900-01-01, 2299-12-31 23:59:59.999999999]`
(menyempit jadi `2262-04-11` HANYA di presisi nanodetik maksimum — penyempitan sekali ini TIDAK
dibedakan di sini, `2299-12-31 23:59:59` dipakai untuk semua presisi `DateTime64`).

**Urutan pengecekan prefix penting dan pernah salah**: `"datetime"` dan `"datetime64"` SAMA-SAMA
mulai dengan `"date"` (`"datetime64(3)".startswith("date")` → `True`!) — kalau cek `"date"` polos
lebih dulu, `DateTime64` (dan `DateTime`) akan SELALU salah ketangkap sebagai `Date` biasa dan
dapat batas yang jauh lebih sempit. Fungsi ini HARUS mengecek yang paling SPESIFIK dulu
(`datetime64` → `datetime`/`timestamp` → `date32` → `date`) — bug ini sempat benar-benar terjadi
saat menulisnya dan langsung ketahuan oleh `tests/test_connectors.py::TestClickHouseDateMax` (yang
menguji SEMUA varian, bukan cuma `Date` polos) sebelum sempat dipakai di production.

`clickhouse_date_max()` dipisah jadi fungsi standalone (bukan langsung jadi method) supaya bisa
dites tanpa koneksi ClickHouse sungguhan — pola yang sama dengan `parse_sorting_key()` di bawah.

```python
def date_floor_1970(self, expr: str, category: str) -> str:
    lit = "toDate('1970-01-01')" if category == "date" else "toDateTime('1970-01-01 00:00:00')"
    return f"if(isNull({expr}), NULL, greatest({expr}, {lit}))"

def date_ceiling(self, expr: str, category: str, max_value: str | None) -> str:
    if not max_value:
        return expr
    caster = "toDate" if category == "date" else "toDateTime"
    return f"if(isNull({expr}), NULL, least({expr}, {caster}('{max_value}')))"

def date_max_bound(self, col_type: str) -> str:
    return clickhouse_date_max(col_type)
```
Beda dari MySQL/SQLite: ClickHouse butuh `category` untuk memilih cast (`toDate` vs `toDateTime`)
yang TIPE-nya cocok dengan literal yang dibandingkan — ClickHouse tidak selonggar MySQL soal
membandingkan string literal langsung ke kolom `Date`/`DateTime` tanpa cast eksplisit.
`date_max_bound()` inilah SATU-SATUNYA override nyata dari method itu di seluruh dialect (MySQL &
SQLite mewarisi default `None` di §4.3) — mencerminkan fakta bahwa ClickHouse memang satu-satunya
engine di sini yang punya batas atas tanggal yang di-enforce di level penyimpanan.

**Insiden nyata: `if(isNull(...), NULL, ...)` bukan hiasan, ini fix bug production.** Versi awal
`date_floor_1970`/`date_ceiling` cuma `greatest(expr, lit)`/`least(expr, lit)` polos — user melapor
(dengan bukti run production sungguhan) kolom `deleted_at` yang NULL di KEDUA sisi tetap terdeteksi
mismatch: `MIN`/`MAX` di sisi ClickHouse menunjukkan `1970-01-01 00:00:00`, bukan `NULL` seperti sisi
MySQL. Penyebabnya: **sejak ClickHouse 24.12**, `greatest()`/`least()` **MENGABAIKAN argumen NULL**
(mengembalikan argumen yang LAIN, bukan `NULL`) — kebalikan dari MySQL/SQLite yang mengikuti standar
SQL (`GREATEST(NULL, x) = NULL`). Jadi `greatest(NULL, toDateTime('1970-01-01 00:00:00'))` di
ClickHouse mengembalikan `1970-01-01 00:00:00`, diam-diam mengubah NULL asli jadi tanggal floor —
merusak `MIN`/`MAX` (menghitung nilai palsu) DAN `uniqueness` (`COUNT(DISTINCT ...)` yang normalnya
mengabaikan NULL jadi ikut menghitung nilai floor palsu itu sebagai 1 distinct value tambahan).
Diverifikasi langsung ke ClickHouse production (`raw_ws_orders`, 3.331.669 baris): ekspresi lama
mengembalikan `0` NULL meski `3.331.100` baris genuinely NULL; ekspresi baru (dengan guard)
mengembalikan tepat `3.331.100` — cocok 100%. Fix: bungkus dengan `if(isNull(expr), NULL, ...)` yang
secara EKSPLISIT menegaskan ulang semantik NULL, tidak bergantung pada perilaku versi ClickHouse
tertentu yang bisa berubah lagi (sudah pernah berubah sekali di 24.12). Lihat
https://github.com/ClickHouse/ClickHouse/issues/65039. MySQL/SQLite tidak perlu perubahan — `GREATEST`/
`MAX` scalar mereka sudah benar dari awal (lihat §4.4/§4.6). Regression test:
`tests/test_connectors.py::TestDialectDateClamping` (menguji bentuk SQL persis termasuk guard-nya).

Kenapa floor/ceiling ini perlu diterapkan bahkan di sisi ClickHouse sendiri (bukan cuma sisi
lain): untuk `Date`/`DateTime` biasa ini murni no-op (nilainya SUDAH pasti dalam batas, dijamin oleh
tipe data itu sendiri) — tapi untuk `Date32`/`DateTime64` (yang jangkauannya lebih lebar, BISA
menyimpan nilai < 1970 atau melewati batas `Date`/`DateTime` biasa), floor/ceiling ini betulan
mengubah nilai yang dibandingkan. Lihat `AggregateValidator._date_ceiling_bounds` (§4.8) untuk cara
kedua sisi perbandingan disamakan ke batas yang SAMA.

```python
def query_df_stream(self, sql: str):
    with self._client.query_df_stream(sql) as stream:
        for batch_df in stream:
            if not batch_df.empty:
                yield batch_df
```
Ini generator (pakai `yield`) yang membaca hasil query ClickHouse per-blok nativenya, BUKAN dengan
`LIMIT ... OFFSET ...` (paginasi semacam itu berperilaku O(n²) di ClickHouse — makin jauh offset,
makin lambat — dan gampang timeout di tabel besar). `with ... as stream:` memakai client
`clickhouse-connect` yang sudah punya dukungan native streaming. Filter `if not batch_df.empty`
mencegah blok kosong (yang kadang muncul di ujung stream) ikut diproses sia-sia. **Catatan**: method
ini SAAT INI belum benar-benar dipanggil oleh `RowLevelValidator` (yang masih pakai `query_df` biasa
per chunk) — chunk sudah cukup kecil (`id_chunk_size` default 2 juta baris) sehingga belum
memerlukan streaming per-baris; method ini disediakan untuk penggunaan mendatang / tabel super
lebar.

```python
def parse_sorting_key(raw: str) -> list[str]:
    if not raw:
        return []
    tokens = [t.strip() for t in raw.split(",")]
    return [t for t in tokens if _SIMPLE_IDENT_RE.match(t)]

def get_primary_key(self, database: str, table: str) -> list[str]:
    q = f"SELECT sorting_key AS sk FROM system.tables WHERE database = '{database}' AND name = '{table}'"
    df = self.query_df(q)
    if df.empty:
        return []
    return parse_sorting_key(str(df["sk"].iloc[0] or ""))
```
ClickHouse tidak punya konsep PRIMARY KEY seperti MySQL — sinyal terdekat untuk "key alami" tabel
adalah `sorting_key` di `system.tables`, yaitu isi klausa `ORDER BY` dari `CREATE TABLE`. Ini
BUKAN kebetulan cocok: `ORDER BY` di ClickHouse jugalah kolom yang dipakai untuk men-dedup baris
`ReplacingMergeTree` (yang sudah dibahas di §4.8 sebagai alasan kenapa `FINAL` dipakai) — jadi
kolom yang sama yang menjaga keunikan baris secara fisik di ClickHouse, dipakai lagi di sini
sebagai sinyal key logis.

Masalahnya: `sorting_key` bisa berisi EKSPRESI, bukan cuma nama kolom polos — misalnya
`toYYYYMM(created_at), id`. Ekspresi seperti `toYYYYMM(created_at)` TIDAK BISA dipakai sebagai
`key_column` (fungsi `quote_ident()`/`_qid()` di seluruh codebase ini mengasumsikan key adalah nama
kolom polos yang bisa dibungkus backtick dan dipakai di klausa `WHERE col BETWEEN lo AND hi` —
bukan ekspresi). `parse_sorting_key()` (fungsi TERPISAH dari method-nya, supaya bisa dites tanpa
koneksi ClickHouse sungguhan — lihat `tests/test_connectors.py::TestClickHouseSortingKeyParsing`)
memecah string itu per koma, lalu `_SIMPLE_IDENT_RE.match(t)` (regex `^[A-Za-z_][A-Za-z0-9_]*$`)
MENYARING token yang bukan nama kolom polos. Jadi `"toYYYYMM(created_at), id"` menjadi `["id"]` —
token ekspresi dibuang diam-diam, bukan mengembalikan error, karena tujuannya cuma "saran" yang
tetap bisa direview/diedit manual oleh user di config builder.

### 4.6 `connectors/sqlite_demo.py`

Engine ini **tidak ada** di kedua tool lama — ditambahkan murni untuk kebutuhan demo/testing lokal
tanpa VPN (lihat docstring file). Yang menarik secara teknis: `date_floor_1970()` di-override
memakai fitur SQLite yang jarang diketahui:
```python
def date_floor_1970(self, expr: str, category: str) -> str:
    return f"MAX({expr}, '1970-01-01')"

def date_ceiling(self, expr: str, category: str, max_value: str | None) -> str:
    if not max_value:
        return expr
    return f"MIN({expr}, '{max_value}')"
```
SQLite `MAX()`/`MIN()` dengan **2 argumen atau lebih** berperilaku sebagai fungsi SCALAR
(per-baris), bukan fungsi agregat — persis semantik `GREATEST()`/`LEAST()` di MySQL. Karena kolom
tanggal disimpan sebagai teks ISO 8601 (`"2025-06-15"`), perbandingan string leksikografis
`MAX()`/`MIN()` di sini otomatis setara perbandingan tanggal (format ISO memang didesain agar bisa
dibandingkan sebagai string). `category` diabaikan (sama seperti MySQL, §4.4) — SQLite ini cuma
dipakai untuk demo/testing lokal, `date_max_bound()`-nya (diwarisi dari `Dialect`, §4.3) selalu
`None`, jadi `date_ceiling` di sini praktis selalu identity kecuali dites langsung.

`is_not_null_ratio()` dan `count_distinct()` di-override untuk memaksa `CAST(... AS REAL)` karena
SQLite melakukan **pembagian integer** kalau kedua operand integer (`5/10` = `0`, bukan `0.5`) —
beda dari MySQL/ClickHouse yang otomatis menghasilkan desimal.

`SqliteConnector.get_schema()` tidak bisa pakai `INFORMATION_SCHEMA` (SQLite tidak selalu
mendukungnya), jadi memakai `PRAGMA table_info("table")` — perintah khusus SQLite yang
mengembalikan tuple `(cid, name, type, notnull, dflt_value, pk)` per kolom; kode ini mengambil
index `[1]` (nama) dan `[2]` (tipe).

```python
def get_primary_key(self, database: str, table: str) -> list[str]:
    cur = self._conn.execute(f'PRAGMA table_info("{table}")')
    pk_cols = [(r[5], r[1]) for r in cur.fetchall() if r[5]]
    pk_cols.sort(key=lambda x: x[0])
    return [name for _, name in pk_cols]
```
Kolom `pk` (index `[5]`) dari `PRAGMA table_info` bernilai `0` untuk kolom yang BUKAN bagian
PRIMARY KEY, dan bernilai POSISI-1-based di dalam PRIMARY KEY untuk kolom yang termasuk — untuk
`PRIMARY KEY (order_id, material_id)`, `order_id` dapat `pk=1` dan `material_id` dapat `pk=2`.
`if r[5]` membuang baris non-key (`pk=0` falsy), lalu `pk_cols.sort(key=lambda x: x[0])` mengurutkan
berdasarkan posisi itu — PENTING supaya urutan composite key SELALU sesuai urutan deklarasi DDL,
sama seperti pertimbangan `ORDER BY ORDINAL_POSITION` di sisi MySQL (§4.4).

### 4.7 `connectors/registry.py`

```python
_REGISTRY: dict[str, type[Connector]] = {
    "mysql": MySqlConnector,
    "clickhouse": ClickHouseConnector,
    "sqlite": SqliteConnector,
}

def create_connector(params: ConnectionParams) -> Connector:
    engine = params.engine.lower().strip()
    try:
        cls = _REGISTRY[engine]
    except KeyError:
        raise ValueError(f"Unsupported engine '{engine}'. Supported: {', '.join(SUPPORTED_ENGINES)}")
    return cls(params)
```
Pola **Factory** sederhana: satu dictionary memetakan nama string engine ke CLASS (bukan
instance) connector-nya. `create_connector()` melihat `params.engine`, cari class yang cocok, lalu
instansiasi (`cls(params)`) dan kembalikan. Untuk menambah engine baru (mis. PostgreSQL di masa
depan), yang perlu dilakukan HANYA: (1) buat `PostgresDialect(Dialect)` + `PostgresConnector`
seperti pola MySQL/ClickHouse, (2) tambah 1 baris di `_REGISTRY`. Tidak ada tempat lain di
`validation_core` yang perlu disentuh.

### 4.8 `aggregate/validator.py` — `AggregateValidator` (Report 1-5)

Ini file paling besar & paling penting di `validation_core`. Class `AggregateValidator`
membandingkan SATU pasangan tabel source/target lewat 5 "Report" — port generik dari
`DBValidator` di tool lama, tapi tidak lagi hardcode `if source_type == 'mysql'`.

**Constructor** (`__init__`) menyimpan semua konteks (connector source & target, nama
database/tabel, kolom tanggal, rentang tanggal, settings) dan menghitung 1 flag penting:
```python
self._same_dialect = source.dialect.name == target.dialect.name
```
Flag ini menentukan nanti apakah "query investigasi" (lihat `_gen_investigate_query`) bisa
digabung jadi 1 query `JOIN` (kalau kedua sisi sama-sama ClickHouse, misalnya, sehingga secara
teknis BISA di-JOIN dalam satu request) atau harus dipecah jadi 2 blok terpisah (kalau beda engine,
mis. MySQL vs ClickHouse — tidak mungkin JOIN lintas server berbeda dalam satu query).

**`_table_ref(side)`** — helper kecil yang dipanggil di HAMPIR semua method lain:
```python
def _table_ref(self, side: str) -> str:
    if side == "source":
        return self.source.dialect.table_ref(self.source_db, self.source_table, final=True)
    return self.target.dialect.table_ref(self.target_db, self.target_table, final=True)
```
Selalu memanggil `table_ref(..., final=True)` — artinya "kalau dialect ini mendukung `FINAL`
(ClickHouse), selalu tambahkan". Ini otomatis benar untuk MySQL (yang `supports_final = False` di
dialect-nya, jadi `final=True` diabaikan) tanpa perlu percabangan eksplisit di sini.

**Report 1 — `gen_report_table_details(df)`**:
```python
def gen_report_table_details(self, df: pd.DataFrame) -> dict:
    src_q = f"SELECT COUNT(*) AS total_row FROM {self._table_ref('source')}{self._date_filter('source')}"
    tgt_q = f"SELECT COUNT(*) AS total_row FROM {self._table_ref('target')}{self._date_filter('target')}"
    ...
    src_rows = int(self._run_source(src_q)["total_row"].iloc[0])
    tgt_rows = int(self._run_target(tgt_q)["total_row"].iloc[0])
    return {
        ...
        "validate_total_row": src_rows == tgt_rows,
        "source_extra_column": df[df["target_column_type"].isna()]["column_name"].tolist(),
        "target_extra_column": df[df["source_column_type"].isna()]["column_name"].tolist(),
    }
```
`df` di sini adalah hasil `pd.merge(src_schema, tgt_schema, on="column_name", how="outer")` — join
LUAR antara daftar kolom source dan target. Baris yang punya `source_column_type` tapi
`target_column_type`-nya `NaN` berarti kolom itu HANYA ada di source (hilang di target) —
sebaliknya untuk `target_extra_column`. Row count dibandingkan lewat query `COUNT(*)` biasa (murah,
tidak menarik data mentah).

**`_date_ceiling_bounds(source_type, target_type)`** — helper dipakai bareng oleh Report 2, 3, dan
4/5 (di bawah), jadi dijelaskan sekali di sini:
```python
def _date_ceiling_bounds(self, source_type, target_type) -> tuple[str | None, str | None]:
    date_bound: str | None = None
    ts_bound: str | None = None
    for dialect, col_type in ((self.source.dialect, source_type), (self.target.dialect, target_type)):
        if not col_type or (isinstance(col_type, float) and pd.isna(col_type)):
            continue
        col_type = str(col_type)
        bound = dialect.date_max_bound(col_type)
        if not bound:
            continue
        cat = get_category(col_type)
        if cat == "date":
            date_bound = bound if date_bound is None else min(date_bound, bound)
        elif cat == "timestamp":
            ts_bound = bound if ts_bound is None else min(ts_bound, bound)
    return date_bound, ts_bound
```
Ini bagian dari fix "clamp tanggal 2 arah" (README) — pelengkap `date_floor_1970` (yang SELALU
1970-01-01, konstanta universal) untuk sisi ATAS: ClickHouse punya batas maksimum tanggal yang
BEDA-BEDA tergantung tipe kolom persisnya (`Date` vs `Date32` vs `DateTime` vs `DateTime64`, lihat
§4.5), jadi batasnya harus DIHITUNG per kolom, bukan konstanta. Cuma dialect yang benar-benar
ClickHouse yang punya `date_max_bound()` non-`None` (§4.3/§4.5) — MySQL/SQLite selalu `None`, jadi
dilewati (`if not bound: continue`). Hasilnya dipisah jadi 2 slot (`date_bound`/`ts_bound`) alih-alih
1 nilai gabungan, supaya kolom kategori `'date'` dan kolom kategori `'timestamp'` masing-masing
dapat literal yang FORMAT-nya cocok dengan cast-nya sendiri di pemanggil (`toDate(...)` butuh
tanggal polos, `toDateTime(...)` butuh tanggal+jam) — kalau digabung jadi 1 nilai dan salah satu sisi
kebetulan beda kategori dari sisi lain (schema drift, jarang tapi mungkin), format yang salah bisa
menghasilkan SQL ClickHouse yang invalid. Kalau KEDUA sisi sama-sama ClickHouse dengan tipe berbeda
(mis. `Date32` vs `Date`, mart-vs-mart), `min(...)` mengambil batas yang lebih KETAT — supaya kedua
sisi di-cap ke titik yang SAMA, bukan batas masing-masing yang berbeda.

**Report 2 — `_completeness_exprs()` + `gen_report_column_details()`**:
```python
def _completeness_exprs(self, dialect, col: str, col_type: str, ceiling_bound: str | None) -> list[str]:
    cat = get_category(col_type)
    q = dialect.quote_ident(col)
    if cat == "string":
        dist_expr = dialect.distinct_string_expr(q)
    elif cat in ("date", "timestamp"):
        dist_expr = dialect.date_ceiling(dialect.date_floor_1970(q, cat), cat, ceiling_bound)
    else:
        dist_expr = q
    return [
        f"{dialect.is_not_null_ratio(q)} AS {col}_completeness",
        f"{dialect.count_distinct(dist_expr)} AS {col}_uniqueness",
    ]
```
Fungsi ini membangun SEPASANG ekspresi SQL untuk 1 kolom: `completeness` (rasio non-NULL) dan
`uniqueness` (rasio nilai unik). `dist_expr` (ekspresi yang dipakai DI DALAM `COUNT(DISTINCT ...)`)
berbeda tergantung kategori kolom: string dinormalisasi dulu (`distinct_string_expr` — lihat §4.4
soal `BINARY LOWER TRIM`), tanggal di-floor 1970 DAN di-cap ke `ceiling_bound` (dipanggil BERURUTAN,
`date_ceiling(date_floor_1970(...), ...)` — supaya tanggal pre-1970 ATAU pasca-batas-ClickHouse di
salah satu sisi tidak dihitung sebagai nilai berbeda dari sisi yang sudah di-clamp), tipe lain
dipakai apa adanya. `ceiling_bound` dihitung SEKALI per kolom di pemanggil
(`gen_report_column_details`, lewat `_date_ceiling_bounds` di atas) lalu dioper ke KEDUA panggilan
(source & target) untuk kolom itu — supaya kedua sisi dibatasi ke nilai yang SAMA persis, bukan
masing-masing menghitung batasnya sendiri. Method pemanggilnya mengumpulkan ekspresi ini untuk SEMUA
kolom jadi SATU query besar (`SELECT expr1, expr2, ..., exprN FROM table`) — jadi completeness &
uniqueness SELURUH kolom dihitung dalam 1 kali round-trip DB, bukan 1 query per kolom.

**Report 3 — `_col_metric_selects()` + `gen_report_column_type_details()`**:
```python
def _col_metric_selects(self, dialect, col: str, col_type: str, ceiling_bound: str | None) -> list[str]:
    cat = get_category(col_type)
    q = dialect.quote_ident(col)
    parts = [f"COUNT({q}) AS {col}_count"]
    if cat == "numeric":
        parts += [f"SUM({q}) AS {col}_sum", f"MIN({q}) AS {col}_min", f"MAX({q}) AS {col}_max"]
    elif cat == "array":
        lf = dialect.len_fn()
        parts += [f"SUM({lf}({q})) AS {col}_sum", ...]
    elif cat in ("date", "timestamp"):
        dq = dialect.date_ceiling(dialect.date_floor_1970(q, cat), cat, ceiling_bound)
        parts.append(f"{dialect.wrap_minmax_datetime(f'MIN({dq})')} AS {col}_min")
        parts.append(f"{dialect.wrap_minmax_datetime(f'MAX({dq})')} AS {col}_max")
        parts.append(f"SUM({dialect.datediff_expr(dq, self.settings.stat_ref_date)}) AS {col}_datediff")
    elif cat == "string":
        dist_expr = dialect.distinct_string_expr(q)
        ...
    return parts
```
Ini "menu metrik" per kategori tipe — angka/statistik apa yang masuk akal dihitung untuk tiap jenis
kolom:
- **numeric**: `count`, `sum`, `min`, `max`
- **array** (ClickHouse only): `count`, dan `sum/min/max` dari PANJANG array (`length()`), bukan
  nilai array itu sendiri (karena SUM sebuah array tidak bermakna)
- **date/timestamp**: `min`, `max` (di-floor ke 1970 DAN di-cap ke `ceiling_bound` dulu, lalu
  dibungkus `wrap_minmax_datetime` kalau perlu), dan `datediff` — SUM dari selisih hari tiap baris
  terhadap 1 tanggal referensi (`stat_ref_date`, default `2010-01-15`). Ini adalah trik
  "fingerprint" — dua tabel yang datanya identik akan punya total selisih hari yang identik juga;
  kalau beda, ada baris yang tanggalnya berubah.
- **string**: `countd` (distinct count), `len_min/max/avg` (panjang string minimum/maksimum/rata²,
  dihitung dari versi TRIM supaya spasi liar di salah satu sisi tidak mengganggu)

Perhatikan bahwa `dq = dialect.date_ceiling(dialect.date_floor_1970(q, cat), cat, ceiling_bound)`
dipanggil SEKALI dan hasilnya (`dq`) dipakai untuk MIN, MAX, DAN datediff — bukan dipanggil 3 kali
terpisah. Ini penting supaya ketiga metrik konsisten memakai ekspresi tanggal yang SAMA persis.

**`_metric_keys()`** adalah pasangan dari `_col_metric_selects` — tapi untuk MEMBACA hasil, bukan
membangun query. Kalau `_col_metric_selects` menentukan ALIAS apa yang dipakai di SQL
(`{col}_sum`, `{col}_min`, dst), `_metric_keys` mengembalikan daftar SUFFIX (`sum`, `min`, `max`)
yang dipakai untuk mengekstrak nilai dari DataFrame hasil query (`row[k] = src_result[f"{col}_{k}"]`).
Kedua fungsi ini HARUS selalu sinkron (kategori & urutan metrik yang sama) — makanya keduanya
memanggil `get_category(col_type)` di awal untuk menentukan cabang yang sama.

**Report 4 & 5 — `gen_report_period_breakdown(granularity, shared_cols)`**:

`shared_cols` datang dari `_shared_stat_cols(df)`, yang mengembalikan `[(col, category,
ceiling_bound), ...]` — bukan cuma `(col, category)` seperti sebelum fix clamp tanggal. Ceiling
bound-nya dihitung SEKALI DI SINI (lewat `_date_ceiling_bounds`, sudah punya kedua tipe kolom mentah
di tangan) lalu dioper ke `_period_stat_selects(dialect, col, cat, ceiling_bound)` — pemanggilnya
(`gen_report_period_breakdown` di bawah dan `_gen_investigate_query`) tidak perlu tahu cara
menghitung bound-nya sendiri, cukup teruskan tuple 3-elemen apa adanya.

Ini method paling panjang. Alurnya:
1. `src_expr`/`tgt_expr` — ekspresi SQL "kelompokkan per bulan/tahun" dari `dialect.period_expr()`.
2. Kalau ada `shared_cols` (kolom yang ada di kedua sisi dengan kategori sama — dari
   `_shared_stat_cols`), tambahkan SUM/MIN/MAX per kolom itu KE DALAM query `GROUP BY period` yang
   sama (jadi bukan hanya `COUNT(*) per periode`, tapi juga statistik per kolom PER periode).
3. Jalankan 2 query (source & target), lalu:
```python
merged = pd.merge(src_df, tgt_df, on="period", how="outer").sort_values("period").reset_index(drop=True)
merged["difference"] = merged["source_row"] - merged["target_row"]
merged["row_match"] = merged["difference"] == 0
```
`how="outer"` PENTING — kalau salah satu sisi punya periode yang sisi lain TIDAK punya (misal
source ada data Januari 2020 tapi target kosong di bulan itu), baris itu tetap muncul di hasil
merge (dengan `NaN` di sisi yang kosong) sehingga TIDAK diam-diam hilang dari laporan.
4. Untuk setiap baris (= setiap periode), kumpulkan METRIK mana saja yang mismatch — bukan cuma
   hitung jumlahnya:
```python
def _mismatch_detail(row):
    details = []
    for sc in src_metric_cols:
        tc = "tgt_" + sc[4:]                      # "src_sum_amount" -> "tgt_sum_amount"
        if not values_match(row[sc], row[tc]):
            metric, col = self._parse_period_alias(sc[4:])
            details.append({"column": col, "metric": metric, "source": row[sc], "target": row[tc]})
    return details
merged["mismatch_detail"] = merged.apply(_mismatch_detail, axis=1)
merged["stat_mismatch"] = merged["mismatch_detail"].apply(len)
merged["match"] = merged["row_match"] & (merged["stat_mismatch"] == 0)
```
`sc[4:]` memotong 4 karakter pertama (`"src_"`) untuk mendapat alias metrik polos (mis.
`"sum_amount"`), lalu `_parse_period_alias()` (helper baru, longest-prefix-match atas
`("min_len_", "max_len_", "sum_len_", "sum_", "min_", "max_", "datediff_")`) memisahkannya jadi
`(metric_label, column_name)` — `"sum_amount"` → `("sum", "amount")`, `"min_len_entity_name"` →
`("min_len", "entity_name")` (urutan prefix HARUS yang-lebih-spesifik-dulu, kalau tidak
`"min_len_x"` salah kepotong jadi metric="min", column="len_x"). `values_match` (dari
`categories.py`, §4.1) dipakai lagi di sini — bukti bahwa fungsi itu memang "single source of
truth" perbandingan nilai di seluruh engine.

**Insiden yang melahirkan `mismatch_detail`**: sebelum ini, `stat_mismatch` cuma disimpan sebagai
ANGKA (jumlah metrik yang beda), detail kolom/metriknya dihitung sekilas lalu DIBUANG begitu
`_count_mismatch` selesai. Akibatnya: periode dengan row count IDENTIK di kedua sisi (Δ=0) tapi
salah satu statistik kolomnya beda tetap muncul di tab Periode sebagai "mismatch" TANPA alasan yang
terlihat — user mengira itu bug/alarm palsu. `_persist_aggregate_findings` (§5.8) sekarang memecah
tiap periode mismatch jadi finding TERPISAH: satu untuk row count (hanya kalau row count-nya memang
beda) plus satu per `(column, metric)` di `mismatch_detail` — jadi periode Δ=0 yang ke-flag tetap
punya baris eksplisit "sum kolom X beda", bukan kosong. Regression test: `tests/test_period_findings.py`.

**`_gen_investigate_query(shared_cols)`** — menghasilkan teks SQL siap-salin untuk investigasi
manual (fitur "Salin Query Investigasi" di UI). Dua cabang:
- `self._same_dialect == True` → bangun SATU query `WITH src AS (...), tgt AS (...) SELECT ...
  FULL OUTER JOIN`, karena kedua sisi bisa diakses dari satu koneksi/client yang sama.
- Kalau beda dialect → bangun DUA blok SQL terpisah, dengan komentar `-- Run in mysql` /
  `-- Run in clickhouse`, supaya manusia tahu blok mana dijalankan di klien mana.

**`run()`** — method utama yang mengorkestrasi semuanya:
```python
def run(self) -> AggregateResult:
    src_schema = self.get_schema_source()
    tgt_schema = self.get_schema_target()
    ...
    if src_schema.empty:
        raise ValueError(f"Source table not found or has no columns: {self.source_table_path}")
    df = pd.merge(src_schema, tgt_schema, on="column_name", how="outer")

    table_details = self.gen_report_table_details(df)
    column_details = self.gen_report_column_details(df)
    src_type_details, tgt_type_details = self.gen_report_column_type_details(df)

    if self.date_column and not self.settings.skip_period_breakdown:
        shared_cols = self._shared_stat_cols(df)
        investigate_query = self._gen_investigate_query(shared_cols)
        monthly = self.gen_report_period_breakdown("monthly", shared_cols)
        yearly = self.gen_report_period_breakdown("yearly", shared_cols)

    return AggregateResult(...)
```
Urutan operasinya PENTING: schema dulu (perlu untuk semua report lain tahu kolom apa saja yang
ada), lalu Report 1, 2, 3 (tidak butuh `date_column`), baru Report 4 & 5 (hanya jalan KALAU
`date_column` di-set DAN `skip_period_breakdown` tidak diaktifkan). Kalau tabel tidak ditemukan
(schema kosong) → `raise ValueError` — ini akan ditangkap sebagai status "ERROR" di level yang
lebih atas (`runner/tiered.py`).

**`AggregateResult.summarize()`** — mengubah hasil mentah (yang berisi banyak DataFrame) jadi 1
dictionary ringkas berisi angka-angka & status PASS/FAIL. Kriteria PASS:
```python
overall_ok = (
    row_match
    and col_completeness_mismatch == 0
    and col_uniqueness_mismatch == 0
    and stat_mismatch == 0
    and monthly_mismatch == 0
    and yearly_mismatch == 0
)
```
Semua 6 syarat harus terpenuhi — kalau SATU saja gagal, statusnya `"FAIL"`. Perhatikan: jumlah
KOLOM yang beda (`extra_source_columns`/`extra_target_columns`) TIDAK termasuk syarat PASS/FAIL —
kolom tambahan dari pipeline (mis. `_dlt_id`) memang WAJAR ada, jadi tidak boleh menggagalkan
validasi.

### 4.9 `rowlevel/comparator.py`

Modul ini berisi HANYA fungsi murni (tanpa I/O, tanpa koneksi DB) — mudah dites tanpa mock apa pun
(lihat `tests/test_rowlevel_comparator.py`).

```python
def composite_key(df: pd.DataFrame, key_columns: list[str]) -> pd.Series:
    if len(key_columns) == 1:
        return df[key_columns[0]].astype(str)
    return df[list(key_columns)].agg(lambda row: "_".join(str(x) for x in row), axis=1)
```
Kalau key cuma 1 kolom (kasus umum: `id`), langsung cast ke string. Kalau composite key (misal
`order_id` + `material_id`), gabungkan nilai tiap kolom dengan `"_"` per BARIS (`axis=1` berarti
fungsi dijalankan per baris, bukan per kolom) — jadi baris dengan `order_id=1, material_id=20`
menjadi key string `"1_20"`.

```python
def column_diff_mask(a, b, threshold, rel_tol=DEFAULT_REL_TOL, abs_tol=DEFAULT_ABS_TOL):
    if is_numeric_dtype(a) and is_numeric_dtype(b):
        an, bn = pd.to_numeric(a, errors="coerce"), pd.to_numeric(b, errors="coerce")
        both_present = ~(an.isna() & bn.isna())

        if is_integer_dtype(an) and is_integer_dtype(bn):
            return (an != bn) & both_present

        max_abs = np.fmax(an.abs().to_numpy(), bn.abs().to_numpy())
        tol = np.maximum(max_abs * rel_tol, abs_tol)
        diff = (an - bn).abs().to_numpy()
        with np.errstate(invalid="ignore"):
            is_close = diff <= tol
        return pd.Series(~is_close, index=a.index) & both_present
    if is_datetime64_any_dtype(a) or is_datetime64_any_dtype(b):
        an, bn = pd.to_datetime(a, errors="coerce"), pd.to_datetime(b, errors="coerce")
        return (an != bn) & ~(an.isna() & bn.isna())
    an, bn = a.astype("string"), b.astype("string")
    return (an != bn) & ~(an.isna() & bn.isna())
```
Fungsi ini mengembalikan Series BOOLEAN (mask) — `True` di posisi baris yang NILAINYA BEDA antara
kolom `a` dan `b`. Di cabang datetime & string, polanya: `(an != bn) & ~(an.isna() & bn.isna())`
artinya "beda DAN BUKAN kasus keduanya kosong". Bagian `~(an.isna() & bn.isna())` inilah yang
membuat `(NULL, NULL)` dianggap SAMA (bukan mismatch) — tanpa baris ini, `NaN != NaN` di pandas akan
bernilai `True` (karena NaN tidak pernah dianggap sama dengan NaN sendiri secara matematis), yang
akan salah menuduh dua baris yang SAMA-SAMA kosong sebagai "beda nilai".

**Cabang numerik** (yang paling sering menimbulkan pertanyaan user — screenshot nyata: kolom
`yearly_need` dengan ratusan baris "beda" padahal cuma beda presisi desimal) TIDAK lagi exact-compare
sejak fitur toleransi ditambahkan:
1. `both_present = ~(an.isna() & bn.isna())` — sama seperti cabang lain, dihitung DULU sebelum
   memutuskan jalur mana yang dipakai, supaya semantik `(NULL,NULL)==sama` konsisten di kedua jalur
   di bawahnya.
2. `if is_integer_dtype(an) and is_integer_dtype(bn):` — kalau KEDUA sisi bertipe integer murni
   (`int64`/`uint64` — ini HANYA terjadi kalau kolom itu TIDAK punya NULL sama sekali, karena numpy
   tidak bisa merepresentasikan NaN dalam array integer; begitu ada NULL, pandas otomatis
   meng-upcast kolom itu ke `float64`), langsung EXACT compare, TIDAK ada toleransi sama sekali.
   Alasan: toleransi relatif yang dikalibrasi untuk noise float (`rel_tol=1e-6`) bisa "menelan"
   selisih genuine sebesar 1 pada angka BESAR (mis. `10_000_000` vs `10_000_001` — toleransinya jadi
   `10_000_000 * 1e-6 = 10`, lebih besar dari selisih 1!) kalau dipaksakan ke kolom yang memang
   integer murni seperti ID/quantity/count.
3. Kalau BUKAN integer murni (genuine float, ATAU integer-dengan-NULL yang ter-upcast jadi float —
   di kasus ini toleransinya tetap aman karena magnitude kolom count/id yang wajar biasanya jauh di
   bawah 1 juta, jadi `tol < 1` dan selisih 1 tetap tertangkap): hitung toleransi ala
   `math.isclose()` — `tol = max(rel_tol * max(|a|,|b|), abs_tol)`, lalu `diff <= tol` berarti
   "cukup dekat, anggap sama". `np.fmax` (bukan `np.maximum`) dipilih khusus supaya kalau CUMA SATU
   sisi NaN, magnitude sisi yang valid tetap dipakai untuk menghitung toleransi (`np.maximum` akan
   ikut menghasilkan NaN kalau salah satu input NaN, `np.fmax` tidak). `np.errstate(invalid="ignore")`
   membungkam warning numpy yang muncul saat membandingkan NaN dengan `<=` (hasilnya tetap `False`
   secara default, cuma menghindari noise di log).
4. `return pd.Series(~is_close, index=a.index) & both_present` — `~is_close` = "TIDAK cukup dekat" =
   "berbeda". Di-AND-kan lagi dengan `both_present` untuk tetap menjaga aturan `(NULL,NULL)=sama`
   yang sama seperti cabang lain (kalau keduanya NULL, `diff` jadi NaN, `is_close` otomatis `False`
   lewat perbandingan NaN, tapi `both_present=False` yang akhirnya membatalkan flag "beda" itu).

Default `rel_tol=1e-6, abs_tol=1e-9` bisa di-override per config lewat `RunSettings` (lihat §4.2) —
diteruskan dari `RowLevelValidator.run()` → `compare_chunk_multi()` → `column_diff_mask()`. Karena
fungsi ini bekerja MURNI di atas data yang SUDAH ditarik ke pandas (tidak tahu SQL/engine asal
datanya), toleransi ini otomatis berlaku untuk PASANGAN ENGINE APA PUN — MySQL vs ClickHouse,
ClickHouse vs ClickHouse mart-ke-mart, dst — tanpa perlu percabangan khusus per pasangan engine.

```python
def compare_chunk_multi(first_df, second_df, key_columns, value_columns, mode, threshold):
    ...
    first_df["__key"] = composite_key(first_df, key_columns)
    second_df["__key"] = composite_key(second_df, key_columns)

    s1 = set(first_df["__key"])
    s2 = set(second_df["__key"])
    missing_in_target = list(s1 - s2)   # source punya, target tidak
    missing_in_source = list(s2 - s1)   # target punya, source tidak

    if mode == "missing" or not value_columns:
        return missing_in_source, missing_in_target, []

    f = first_df.drop_duplicates("__key", keep="last")
    s = second_df.drop_duplicates("__key", keep="last")
    merged = pd.merge(f[...], s[...], on="__key", how="inner", suffixes=("__s", "__t"))
    ...
    for col in value_columns:
        a, b = merged[f"{col}__s"], merged[f"{col}__t"]
        mask = column_diff_mask(a, b, threshold)
        if mask.any():
            sub = merged.loc[mask, ["__key", f"{col}__s", f"{col}__t"]]
            part = pd.DataFrame({"key": sub["__key"].values, "column": col, ...})
            records.extend(part.to_dict("records"))
    return missing_in_source, missing_in_target, records
```
Ini fungsi INTI row-level validation — dipanggil sekali PER CHUNK (bukan sekali untuk seluruh
tabel). Logikanya:
1. Bangun kolom bantu `"__key"` di kedua DataFrame (dari `composite_key`).
2. `set(...) - set(...)` — operasi selisih himpunan Python biasa: `s1 - s2` = elemen yang ADA di
   `s1` tapi TIDAK ADA di `s2`. Karena `first_df` = source dan `second_df` = target: `s1 - s2`
   berarti "key yang source punya tapi target tidak" → target-lah yang KEHILANGAN baris ini,
   makanya disebut `missing_in_target`. (Perhatikan penamaan ini kebalikan dari intuisi pertama —
   `missing_in_X` berarti "X yang kehilangannya", bukan "yang hilang DARI X".)
3. Kalau mode `"missing"` (atau tidak ada kolom nilai untuk dibandingkan), berhenti di sini —
   tidak perlu buang waktu membandingkan nilai kolom.
4. Kalau mode `"full"`: `drop_duplicates("__key", keep="last")` — kalau ada key duplikat dalam 1
   chunk (seharusnya tidak terjadi kalau data bersih, tapi sebagai jaga-jaga), ambil kemunculan
   TERAKHIR. `pd.merge(..., how="inner", suffixes=("__s","__t"))` — gabungkan HANYA baris dengan
   key yang ADA DI KEDUA SISI (baris yang cuma ada di satu sisi sudah tertangani sebagai "missing"
   di langkah 2, tidak perlu diperiksa nilainya).
5. Untuk SETIAP kolom nilai (`value_columns`), panggil `column_diff_mask` untuk cari baris yang
   BEDA, lalu susun jadi record format PANJANG (`long format`): `{key, column, source_value,
   target_value}` — satu baris hasil PER SEL yang beda (bukan satu baris per row-tabel-asal). Ini
   format yang sama seperti yang tersimpan di tabel `findings_rowlevel`.

### 4.10 `rowlevel/runner.py` — `RowLevelValidator`

**Konstanta penting**:
```python
MAX_DIFF_RECORDS_IN_MEMORY = 200_000
```
Pengaman terhadap insiden nyata di tool lama (`realloc of size 4294967296 failed` — OOM pada tabel
lebar dengan banyak mismatch). Setelah jumlah record yang terkumpul melebihi angka ini, ENGINE
BERHENTI MENYIMPAN detail baru (tapi tetap MENGHITUNG total yang benar) — lihat method `run()`.

**`run()`** — alur lengkap:
```python
def run(self) -> RowLevelResult:
    key_columns = list(self.table.key_columns)
    chunk_column = self.table.effective_chunk_column()
    chunk_size = int(self.settings.id_chunk_size)

    value_columns: list[str] = []
    if self.mode == "full":
        value_columns = detect_value_columns(self.source, ..., self.table.exclude_columns)
```
Kalau mode `"full"`, kolom yang dibandingkan di-AUTO-DETECT (bukan diminta user menulis satu-satu)
lewat `detect_value_columns` — ambil irisan kolom yang ada di KEDUA tabel, kurangi key columns,
kurangi `META_COLUMNS`, kurangi `exclude_columns` yang diset user.

```python
    try:
        mm1_q = build_minmax_query(self.source.dialect, chunk_column, self.source_table_ref)
        mm2_q = build_minmax_query(self.target.dialect, chunk_column, self.target_table_ref)
        mm1 = self.source.query_df(mm1_q)
        mm2 = self.target.query_df(mm2_q)
        gmin = int(min(mm1.iloc[0, 0], mm2.iloc[0, 0]))
        gmax = int(max(mm1.iloc[0, 1], mm2.iloc[0, 1]))
    except (ValueError, TypeError):
        full_scan = True
```
Ambil MIN & MAX dari kolom chunk di KEDUA sisi, lalu pakai `min()` dari dua MIN dan `max()` dari
dua MAX — supaya rentang chunk mencakup SEMUA id yang mungkin ada di KEDUA sisi (kalau hanya pakai
range dari salah satu sisi, id yang cuma ada di sisi lain bisa terlewat dari pengecekan). Kalau
`chunk_column` ternyata bukan kolom numerik (`int(...)` gagal → `ValueError`/`TypeError`), fallback
ke `full_scan = True` — artinya nanti hanya ADA SATU "chunk" yang mencakup seluruh tabel tanpa
filter `WHERE ... BETWEEN`.

```python
    if full_scan:
        chunk_bounds = [(None, None)]
    else:
        chunk_bounds = [(lo, lo + chunk_size - 1) for lo in range(gmin, gmax + 1, chunk_size)]
```
`chunk_bounds` adalah daftar pasangan `(lo, hi)` — batas bawah & atas tiap chunk. Contoh: kalau
`gmin=1, gmax=5_000_000, chunk_size=2_000_000` → hasilnya `[(1, 2000000), (2000001, 4000000),
(4000001, 5000000)]` (chunk terakhir otomatis lebih pendek karena `range()` berhenti begitu
melewati `gmax+1`).

```python
    for idx, (lo, hi) in enumerate(chunk_bounds, start=1):
        q1 = build_range_query_multi(self.source.dialect, key_columns, value_columns, ..., lo, hi)
        q2 = build_range_query_multi(self.target.dialect, key_columns, value_columns, ..., lo, hi)
        with ThreadPoolExecutor(max_workers=2) as ex:
            fu1 = ex.submit(self.source.query_df, q1)
            fu2 = ex.submit(self.target.query_df, q2)
            d1 = fu1.result()
            d2 = fu2.result()
        m_src, m_tgt, recs = compare_chunk_multi(d1, d2, key_columns, value_columns, self.mode, ...)
```
INI adalah loop utamanya — per chunk: (1) bangun query range untuk kedua sisi, (2)
`ThreadPoolExecutor(max_workers=2)` menjalankan fetch KEDUA SISI SECARA PARALEL (bukan berurutan —
kalau source butuh 3 detik dan target butuh 3 detik, dengan paralel totalnya ~3 detik bukan ~6
detik), (3) baru bandingkan hasil chunk itu dengan `compare_chunk_multi` (§4.9). Memory tetap
terjaga karena `d1`/`d2` (DataFrame chunk itu) dibuang begitu iterasi loop pindah ke chunk
berikutnya — tidak pernah menahan SELURUH tabel di memory sekaligus.

```python
        missing_source_count += len(m_src)
        ...
        if len(all_diffs) < MAX_DIFF_RECORDS_IN_MEMORY:
            all_diffs.extend(recs)
        else:
            truncated = True
```
Counter (`missing_source_count`, dst) SELALU bertambah tepat sesuai jumlah asli — akurat 100%
berapa pun besarnya. Tapi daftar detail (`all_diffs`, yang menyimpan record LENGKAP per mismatch)
berhenti ditambahi begitu sudah mencapai `MAX_DIFF_RECORDS_IN_MEMORY` — dan flag `truncated=True`
diset supaya pemanggil (layer web) tahu bahwa detail yang dikembalikan tidak lengkap (walau
hitungan totalnya tetap benar).

### 4.11-4.13 `events.py`, `runner/retry.py`, `runner/tiered.py`

**`events.py`** (di root `validation_core/`, BUKAN di dalam `runner/`) — sengaja diletakkan di
level teratas package untuk menghindari **circular import**: `rowlevel/runner.py` butuh
`ProgressEvent`, dan `runner/tiered.py` butuh `rowlevel/runner.py` — kalau `ProgressEvent`
ditaruh di dalam package `runner/`, mengimpornya dari `rowlevel` akan memaksa Python menjalankan
`runner/__init__.py` DULU (yang mengimpor `tiered.py`, yang mengimpor `rowlevel/runner.py` —
lingkaran!). File `runner/events.py` sekarang hanya berisi re-export 1 baris untuk kompatibilitas.
Isinya sendiri cuma 1 dataclass:
```python
@dataclass
class ProgressEvent:
    kind: EventKind             # 'phase' | 'checkpoint' | 'retry' | 'table_done' | ...
    message: str
    data: dict[str, Any] = field(default_factory=dict)
```

**`runner/retry.py`** — `run_with_retry(fn, settings, on_retry)`:
```python
def run_with_retry(fn, settings, on_retry=None):
    last_err = None
    for attempt in range(1, settings.retry_max + 1):
        try:
            return fn(), attempt
        except Exception as exc:
            last_err = exc
            if is_transient_error(exc) and attempt < settings.retry_max:
                wait = settings.retry_backoff_seconds * attempt
                if on_retry:
                    on_retry(attempt, exc, wait)
                time.sleep(wait)
                continue
            raise
    raise last_err
```
Pola **retry dengan backoff linear** (bukan eksponensial): percobaan ke-1 gagal → tunggu
`backoff*1` detik; percobaan ke-2 gagal → tunggu `backoff*2` detik; dst. HANYA di-retry kalau
`is_transient_error(exc)` bernilai True (dicek lewat pencarian kata kunci seperti `"timed out"`,
`"connection"`, `"unreachable"` di pesan error — persis pola yang dipakai `validate_batch.py` di
tool lama) DAN belum mencapai percobaan terakhir. Kalau error BUKAN transient (misal `ValueError`
karena tabel tidak ada — jelas bukan masalah jaringan), langsung `raise` tanpa buang waktu retry.

**`runner/tiered.py`** — `run_table()`, fungsi paling sering disebut di seluruh dokumen ini. Sudah
dijelaskan alurnya di §2.2 langkah 3. Detail tambahan yang penting:
```python
if effective_mode in ("aggregate", "tiered"):
    ...
    if effective_mode == "aggregate" or status == "PASS":
        return TableRunResult(..., tier_reached=1, ...)   # <- BERHENTI DI SINI
    tier = 2
    rl_mode = "missing" if aggregate_summary["source_rows"] > settings.full_mode_row_threshold else "full"
```
Baris `rl_mode = "missing" if ... else "full"` adalah keputusan PENTING: kalau tabel yang FAIL
ternyata BESAR (row count > `full_mode_row_threshold`, default 5 juta), Tier 2 otomatis dijalankan
dalam mode `"missing"` saja (murah — cuma cek key yang hilang, TIDAK membandingkan tiap kolom
nilai). Kalau tabelnya KECIL, baru dijalankan mode `"full"` (mahal tapi lengkap — cek value diff
tiap kolom juga). Ini meniru kebijakan operasional nyata di tool lama (`validate_batch.py`
menandai tabel >5 juta baris sebagai `mode: missing`).

```python
if effective_mode != "tiered":
    status = "PASS" if rl_ok else "FAIL"
elif rl_ok:
    status = "PASS"
# else: Tier 2 found real missing/differing rows -- FAIL stands.
```
**Riwayat keputusan desain ini** (berubah DUA KALI, keduanya karena kasus nyata — lihat README
"Perubahan perilaku: Tier 2 yang bersih total sekarang meng-override FAIL palsu dari Tier 1"):

1. *Versi awal*: `status = "FAIL"` dipertahankan begitu Tier 1 bilang FAIL, APA PUN hasil Tier 2 —
   row-level murni alat bantu diagnosis, bukan pemutus vonis. Berubah setelah insiden
   `deleted_at`/NULL (§4.5) membuktikan Tier 1 bisa false-positive.
2. *Versi kedua*: override hanya untuk `rowlevel_result.mode == "full"`, dengan alasan mode
   `"missing"` (tabel besar, di atas `full_mode_row_threshold`) cuma cek keberadaan key
   (`value_columns = []`, §4.10) — `differing_values_count == 0` di situ trivially true, bukan bukti
   nilai cocok. Batasan ini langsung kena kasus nyata: `dim_ws_entity_material_activities` (71 juta
   baris → otomatis mode `missing`) tetap FAIL meski Tier 2 bersih.
3. *Versi final (sekarang)*: user memutuskan eksplisit (dua kali) — **Tier 2 bersih = PASS di kedua
   mode**. Trade-off yang diterima sadar: tabel besar yang FAIL Tier 1-nya murni karena drift NILAI
   kolom (bukan baris hilang) sekarang terbaca PASS, karena dalam praktik false-positive Tier 1
   (NULL→1970, presisi float) jauh lebih sering terjadi daripada kasus "nilai drift tapi row count
   dan keberadaan key persis sama".

Kalau mode-nya murni `"rowlevel_full"`/`"rowlevel_missing"` (tanpa Tier 1), row-level tetap
satu-satunya vonis (cabang pertama) — tidak relevan dengan override ini.

Regression test: `tests/test_tiered_runner.py::TestTier2OverridesFalsePositiveTier1Fail` — mem-fake
`AggregateValidator`/`RowLevelValidator` langsung (monkeypatch) supaya logika keputusannya bisa
diuji tanpa mengonstruksi data yang mereproduksi skenario false-positive (pemicu aslinya — bug versi
ClickHouse — tidak bisa direproduksi lewat fixture SQLite). 4 skenario: `full`+bersih → PASS,
`full`+beda nyata → FAIL, `missing`+bersih → PASS, `missing`+baris hilang → FAIL.

**`resume_run(db, finished_run, scope)`** (di `app/services/run_service.py`, §5.8 — disebut di sini
karena terkait erat): berevolusi dua kali atas permintaan user — semula hanya tabel yang BELUM
SELESAI (bukan `pass`/`fail`), lalu "semua non-`pass`", dan bentuk finalnya **pilihan scope
eksplisit per klik** lewat dropdown di `run_detail.html`: `RESUME_SCOPES` = `all` (semua tabel) /
`fail` (hanya FAIL) / `error` (hanya ERROR) / `non_pass` (default; fail+error+cancelled). Scope tak
dikenal jatuh ke `non_pass`. Kalau scope tidak cocok tabel mana pun, `resume_run` mengembalikan
`None` TANPA membuat run — route menampilkan flash error. Ini penting: kode pra-scope memakai
`table_filter=remaining or None`, artinya seleksi KOSONG diam-diam berubah jadi `None` = "SEMUA
tabel" — salah klik "Hanya ERROR" di run tanpa error akan meluncurkan run 99-tabel penuh tanpa
disengaja. Test: `tests/test_run_service.py::test_resume_scopes_select_the_right_tables` (keempat
scope + fallback scope tak dikenal) & `test_resume_with_empty_scope_selection_creates_nothing`.

### 4.14 `excel_export.py`

Fitur transisi (bukan output utama lagi) — `write_excel(results, output_path)` menerima
`list[TableRunResult]` LANGSUNG dari hasil `run_table()` (dengan `AggregateResult` lengkap berisi
semua DataFrame Report 1-5 yang masih ada di memory), dan menulis 1 file `.xlsx` dengan sheet
`Summary` + 1 set sheet per tabel (`_columns`, `_src_types`, `_tgt_types`, `_monthly`, `_yearly`,
`_diffs`). **Perhatikan**: fungsi ini HANYA bisa dipakai tepat setelah run selesai (selagi objek
`AggregateResult` masih hidup di memory) — untuk export run yang SUDAH LAMA selesai (dibaca dari
database), yang dipakai adalah `app/services/export_service.py` (§6.9), yang membangun ulang
laporan dari data yang SUDAH tersimpan di DB (lebih ringkas, hanya berisi temuan/mismatch, bukan
seluruh kolom).

---

## 5. `app/` — Backend Web (Penjelasan Detail)

### 5.1 `database.py`

```python
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'lidvalid.sqlite'}")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
```
`DATABASE_URL` default ke file SQLite lokal, tapi bisa di-override lewat environment variable ke
connection string PostgreSQL kapan pun (skema SQLAlchemy portabel). `check_same_thread=False`
KHUSUS untuk SQLite — secara default, driver `sqlite3` Python melarang satu koneksi dipakai dari
thread yang BUKAN thread pembuatnya; flag ini mematikan proteksi itu (perlu karena aplikasi ini
memakai banyak thread — lihat `run_service.py`). `SessionLocal` adalah "pabrik" Session — tiap kali
dipanggil `SessionLocal()`, dihasilkan Session BARU yang independen.

```python
if _IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
```
**Insiden nyata yang melahirkan blok ini**: selagi satu run (68 tabel ClickHouse asli, mode
`tiered`, berjam-jam) berjalan di background thread `run_service` (§5.8), request HTTP LAIN yang
cuma perlu BACA (mis. cek login di setiap halaman) mulai gagal dengan `sqlite3.OperationalError:
database is locked`. Penyebabnya: mode jurnal DEFAULT SQLite (*rollback journal*, `PRAGMA
journal_mode=DELETE`) mengambil lock EKSKLUSIF atas SELURUH FILE selama sebuah transaksi tulis
berlangsung — commit demi commit dari worker validasi (`_persist_table_result()` per tabel selesai)
cukup untuk membuat pembaca lain ter-block, dan driver `sqlite3` Python punya `timeout` default 5
detik sebelum menyerah dan melempar error itu.

`PRAGMA journal_mode=WAL` (*Write-Ahead Logging*) mengubah model konkurensinya: pembaca boleh terus
jalan SELAGI ada satu penulis aktif (pembaca melihat snapshot data per-versi, bukan ikut terkunci) —
persis pola beban aplikasi ini (banyak baca pendek dari browser, satu proses tulis panjang dari
validasi). `busy_timeout=30000` menaikkan jendela tunggu-lalu-coba-lagi bawaan SQLite untuk kasus
kontensi PENULIS-vs-PENULIS yang masih tersisa (WAL tidak menghilangkan itu — SQLite tetap cuma
boleh 1 penulis aktif kapan pun), supaya operasi itu MENUNGGU sejenak dulu alih-alih langsung gagal.
`@event.listens_for(engine, "connect")` memastikan PRAGMA ini dipasang di SETIAP koneksi fisik baru
yang dibuka dari connection pool — bukan cuma sekali di awal, karena PRAGMA `journal_mode`/`busy_timeout`
adalah pengaturan PER-KONEKSI, bukan pengaturan yang tersimpan permanen di file (`journal_mode=WAL`
sebenarnya PERSISTEN di file setelah diset sekali, tapi `busy_timeout` HARUS diulang tiap koneksi).

```python
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```
Ini adalah **FastAPI dependency generator** — pola `yield` di sini membuat FastAPI otomatis
menutup Session (`db.close()`) setelah request selesai diproses, APAPUN hasilnya (sukses atau
exception) — karena kode setelah `yield` di dalam `finally` selalu jalan. Dipakai di semua route
lewat `Depends(get_db)`.

```python
def init_db() -> None:
    from . import models
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_findings_rowlevel_run_table_type "
            "ON findings_rowlevel (run_table_id, finding_type)"
        ))
        existing_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(run_tables)"))}
        if "column_type_details" not in existing_cols:
            conn.execute(text("ALTER TABLE run_tables ADD COLUMN column_type_details TEXT"))
        conn.commit()
```
`Base.metadata.create_all()` hanya membuat tabel yang BELUM ADA — kalau tabelnya sudah ada (database
production yang sudah lama jalan), SQLAlchemy tidak menyentuhnya sama sekali, TERMASUK index/kolom
baru yang ditambahkan belakangan ke definisi model (lihat `Index` di `FindingRowLevel` dan
`RunTable.column_type_details`, §5.2). Project ini tidak punya Alembic/migration framework, jadi
apa pun yang ditambahkan setelah database production sudah berisi data harus di-backfill manual
lewat SQL mentah — aman dipanggil di setiap startup (no-op kalau sudah ada, dan `create_all()`
sendiri sudah otomatis membuatnya untuk database yang BENAR-BENAR baru). Index ini ditambahkan saat
memperbaiki halaman drilldown yang lambat (§5.10, README) — query `WHERE run_table_id = ? AND
finding_type = ?` di tabel `findings_rowlevel` (120 ribuan baris dan terus tumbuh) sebelumnya
full-scan tanpa index sama sekali.

SQLite `ALTER TABLE ... ADD COLUMN` TIDAK punya sintaks `IF NOT EXISTS` (beda dari `CREATE INDEX`
di atas) — jadi backfill kolom `column_type_details` (untuk tab "Tipe Kolom", §5.10) harus cek
`PRAGMA table_info(run_tables)` dulu secara eksplisit dan hanya `ALTER TABLE` kalau kolomnya
memang belum ada, supaya tetap aman dipanggil berkali-kali di setiap startup.

Catatan operasional: `CREATE INDEX` adalah operasi DDL yang butuh lock EKSKLUSIF di SQLite — WAL mode
(atas) TIDAK membebaskan DDL dari aturan ini (beda dengan INSERT/UPDATE/SELECT biasa). Kalau ada
writer lain yang aktif terus-menerus (mis. run tiered besar yang commit per tabel), backfill index
manual bisa gagal dengan `database is locked` walau sudah menunggu puluhan detik — pengalaman nyata
saat menambahkan index ini ke database production yang sedang menjalankan run 68 tabel. Solusinya
bukan memaksa/retry terus, tapi biarkan `init_db()` yang menjalankannya otomatis di startup BERIKUTNYA
(saat tidak ada writer aktif) — tidak perlu langkah manual terpisah.

### 5.2 `models.py` (ORM) — Skema Database

13 kelas SQLAlchemy, masing-masing = 1 tabel. Berikut peta relasinya:

```
User                    (1 tabel, berdiri sendiri — login + role)
   │
   │ owner_id (FK, nullable) — lihat §9
   ▼
Connection ──┬── ValidationConfig.source_connection_id
             └── ValidationConfig.target_connection_id
                       │  (juga punya owner_id)
                       ├── ConfigTable (1 config punya N table mapping)
                       │
                       └── Run (1 config bisa dijalankan berkali-kali, juga punya owner_id)
                                │
                                └── RunTable (1 run punya N baris, 1 per ConfigTable)
                                        │
                                        ├── FindingAggregate (N per RunTable — temuan agregat)
                                        └── FindingRowLevel  (N per RunTable — temuan row-level)

RunEvent — didefinisikan tapi TIDAK DIPAKAI aktif (lihat catatan di §6.8)
```

**`owner_id` (ditambahkan 2026-07-21, lihat §9)** — `Connection`, `ValidationConfig`, dan `Run`
masing-masing punya `owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)`. `User`,
`ConfigTable`, `RunTable`, `FindingAggregate`, `FindingRowLevel` **TIDAK** punya kolom ini —
kepemilikan cukup dilacak di level "akar" (Connection/Config/Run), baris turunannya (ConfigTable,
RunTable, Finding) otomatis ikut ter-scope lewat JOIN ke parent-nya di query (lihat §9.2). Kolom ini
`nullable=True` supaya `Base.metadata.create_all()` tidak bentrok dengan kode lama yang belum
mengenal field ini, dan supaya jalur ALTER TABLE (§5.1) bisa menambah kolom ke database yang sudah
ada tanpa perlu NOT NULL default palsu.

Field JSON (mis. `ConfigTable.key_columns`, `RunTable.agg_metrics`) memakai tipe `JSON` bawaan
SQLAlchemy — di SQLite disimpan sebagai TEXT (serialize/deserialize otomatis oleh SQLAlchemy), di
PostgreSQL akan otomatis jadi kolom `JSONB` asli kalau `DATABASE_URL` diganti — TIDAK PERLU
mengubah kode model sama sekali untuk migrasi itu.

`cascade="all, delete-orphan"` pada relationship (mis. `ValidationConfig.tables`,
`RunTable.aggregate_findings`) berarti: kalau parent-nya dihapus (atau child dilepas dari list
relationship-nya), child ikut terhapus otomatis dari database — mencegah baris "yatim" menumpuk.

`FindingRowLevel` punya `__table_args__ = (Index("ix_findings_rowlevel_run_table_type",
"run_table_id", "finding_type"),)` — index komposit pada dua kolom yang SELALU dipakai bersama di
`WHERE` (`table_drilldown()`, §5.10, selalu memfilter by `run_table_id` DAN `finding_type`). Tabel ini
bisa berisi ratusan ribu baris (setiap run_table bisa menyumbang sampai `rowlevel_sample_cap` × 3
jenis finding), jadi tanpa index query-nya full-scan. Lihat §5.1 untuk kenapa index ini butuh
backfill manual di `init_db()`, bukan otomatis lewat `create_all()`.

`RunTable.column_type_details = Column(JSON, default=list)` — daftar perbandingan tipe kolom PENUH
(source type, target type, apakah kategorinya cocok) untuk SETIAP kolom, bukan cuma yang mismatch.
Beda dari `agg_metrics`/`FindingAggregate` kategori `"stat"` (yang cuma menyimpan MISMATCH metrik,
dengan asumsi kategori kedua sisi SUDAH sama) — field ini menjawab pertanyaan yang sebelumnya sama
sekali tidak terjawab di mana pun di UI: "apakah tipe kolom ini di source vs target itu SENDIRI
cocok?" (lihat `AggregateValidator._shared_stat_cols`, §4.8, yang selama ini diam-diam MELEWATI
kolom berkategori beda dari perbandingan stat, tanpa mencatat kenapa). Diisi oleh
`run_service._build_column_type_details()` (§5.8), ditampilkan di tab "Tipe Kolom" (§5.10). Sama
seperti index di atas, kolom baru ini butuh backfill manual (`ALTER TABLE`, §5.1) karena
`create_all()` tidak mengubah tabel yang sudah ada.

### 5.3 `security.py`

```python
def _load_or_create_key() -> bytes:
    env_key = os.environ.get("LIDVALID_SECRET_KEY")
    if env_key:
        return env_key.encode() if isinstance(env_key, str) else env_key
    if os.environ.get("LIDVALID_ENV", "development") == "production":
        raise RuntimeError(
            "LIDVALID_SECRET_KEY must be set when LIDVALID_ENV=production -- refusing "
            "to fall back to data/secret.key, which would silently break decryption of "
            "every already-stored connection secret and invalidate all sessions on the "
            "next redeploy. Generate one with: python -c \"from cryptography.fernet "
            "import Fernet; print(Fernet.generate_key().decode())\""
        )
    _KEY_PATH.parent.mkdir(exist_ok=True)
    if _KEY_PATH.exists():
        return _KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    _KEY_PATH.write_bytes(key)
    return key

_fernet = Fernet(_load_or_create_key())
```
Urutan prioritas kunci enkripsi: (1) environment variable `LIDVALID_SECRET_KEY` kalau di-set
(cocok untuk produksi — kunci tidak pernah menyentuh disk lokal), (2) **kalau `LIDVALID_ENV=production`
dan kunci itu TIDAK di-set — gagal keras (`RuntimeError`), TIDAK diam-diam jatuh ke file lokal**, (3)
file `data/secret.key` kalau sudah ada (dev/demo — dibuat sekali, dipakai berulang di run berikutnya),
(4) kalau tidak ada satu pun, GENERATE baru dan simpan ke file supaya persist untuk run berikutnya.
`_fernet` adalah objek Fernet SATU-SATUNYA yang dipakai seluruh aplikasi (dibuat sekali saat module
di-import pertama kali — bukan per-request), memakai algoritma AES-128 dalam mode CBC dengan HMAC
untuk autentikasi (skema standar library `cryptography`).

**Kenapa cek (2) ditambahkan (2026-07-21, saat migrasi ke VPS)**: kunci ini dipakai untuk DUA hal
sekaligus — enkripsi password koneksi database (`encrypt_secret`/`decrypt_secret` di bawah) DAN
signing session cookie (`SessionMiddleware`, §5.12). Kalau di produksi kunci ini sampai jatuh ke
fallback file lokal (mis. karena volume `data/` tidak ter-mount dengan benar saat container
di-recreate), lalu container itu di-*rebuild* tanpa volume yang sama, file `secret.key` yang baru
akan ter-generate ULANG — bukan cuma memutus SEMUA sesi login yang aktif, tapi juga membuat SETIAP
`Connection.secret_encrypted` yang sudah tersimpan **permanen tidak bisa didekripsi lagi** (kunci
lama hilang). Exception yang gagal-cepat di startup jauh lebih baik daripada kehilangan data secara
diam-diam beberapa hari kemudian. Lihat §10.4 untuk `LIDVALID_SECRET_KEY` di deployment VPS
sungguhan (nilainya harus SAMA dengan yang dulu dipakai men-enkripsi database yang dimigrasi, bukan
di-generate baru).

```python
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt}${digest.hex()}"

def verify_password(password: str, stored: str) -> bool:
    _, iterations, salt, digest_hex = stored.split("$")
    check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(check.hex(), digest_hex)
```
Format hash yang disimpan: `"pbkdf2$260000$<salt_hex>$<digest_hex>"` — 4 bagian dipisah `$` (mirip
format hash Django/Passlib). `salt` acak per-password (`secrets.token_hex(16)` = 16 byte acak
kriptografis, bukan `random` biasa) supaya dua user dengan password SAMA tetap menghasilkan hash
BERBEDA (mencegah serangan rainbow table). `hmac.compare_digest()` dipakai (bukan `==` biasa) untuk
membandingkan hash — ini **constant-time comparison**, mencegah *timing attack* (penyerang
menebak password karakter-per-karakter dengan mengukur berapa lama waktu perbandingan berhenti di
karakter mana).

### 5.4 `auth.py`

```python
class RedirectToLogin(Exception):
    pass

def require_login(request: Request, db: Session = Depends(get_db)) -> models.User:
    user = get_current_user(request, db)
    if not user:
        raise RedirectToLogin()
    return user
```
Pola yang dipakai: `require_login` adalah FastAPI dependency yang dipasang di HAMPIR semua route
lewat `Depends(require_login)`. Kalau user belum login, alih-alih mengembalikan response error
biasa, ia **melempar exception custom** `RedirectToLogin`. Exception ini ditangkap di level
aplikasi (lihat `main.py`) oleh handler:
```python
async def redirect_to_login_handler(request: Request, exc: RedirectToLogin):
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)
```
Efeknya: SATU baris `Depends(require_login)` di signature route sudah cukup membuat route itu
otomatis redirect ke halaman login (dengan `?next=` supaya setelah login user dibalikkan ke
halaman yang tadinya dituju) — tidak perlu menulis pengecekan `if not user: return redirect(...)`
berulang-ulang di SETIAP fungsi route.

```python
def require_role(*roles: str):
    def _dep(user: models.User = Depends(require_login)) -> models.User:
        if user.role not in roles and user.role != "admin":
            raise RedirectToLogin()
        return user
    return _dep
```
`require_role(*roles)` **sekarang aktif dipasang** di route yang butuh lebih dari sekadar login
(§9.1) — dependency FACTORY: `Depends(require_role("editor"))` mengembalikan sebuah dependency baru
yang, di belakang layar, DULU memanggil `require_login` (jadi user yang belum login tetap kena
redirect ke `/login` seperti biasa), BARU KEMUDIAN mengecek role. `user.role != "admin"` di baris
kondisi berarti **admin selalu lolos apa pun argumen `roles`-nya** — TIDAK ADA hierarki
viewer→editor→admin di sini; memanggil `require_role("viewer")` justru SALAH (akan menolak editor,
karena `"editor" not in ("viewer",)` dan role-nya bukan `"admin"`). Konvensi yang dipakai konsisten
di seluruh `routers/`: route viewer-boleh-akses pakai `require_login` polos, route editor-ke-atas
pakai `require_role("editor")`, tidak ada route yang admin-only murni (lihat §9.1 kenapa).

```python
def is_admin(user: models.User) -> bool:
    return user.role == "admin"

def scope_query(query, model, user: models.User):
    if is_admin(user):
        return query
    return query.filter(model.owner_id == user.id)

def check_owner(obj, user: models.User) -> None:
    if obj is None or (not is_admin(user) and obj.owner_id != user.id):
        raise HTTPException(status_code=404)
```
Tiga helper ini (ditambahkan 2026-07-21) menegakkan **per-user data scoping** — dibahas lengkap di
§9.2. Ringkas: `scope_query()` dipakai di route LIST (nambah `.filter(owner_id == user.id)` kalau
bukan admin), `check_owner()` dipakai di route yang mengambil SATU objek by-id (`db.get(...)`) —
melempar `404` (bukan `RedirectToLogin`/303) kalau objeknya tidak ada ATAU user bukan admin dan
bukan pemiliknya. `404`, bukan `403`, dipilih dengan sengaja: menyembunyikan bahkan KEBERADAAN baris
milik user lain, bukan cuma menolak aksesnya — praktik keamanan umum supaya user B tidak bisa
menyimpulkan "oh, config id 42 itu ADA, cuma bukan punya saya" hanya dari kode status yang berbeda.

### 5.5 `services/connections_service.py`

```python
def to_connection_params(conn: models.Connection) -> ConnectionParams:
    return ConnectionParams(
        engine=conn.engine, host=conn.host or "", port=conn.port or 0,
        database=conn.database or "", username=conn.username or "",
        password=security.decrypt_secret(conn.secret_encrypted),
        params=conn.params or {},
    )
```
Ini fungsi JEMBATAN paling penting antara dua lapis arsitektur — mengambil ORM object
`models.Connection` (yang tahu soal database, enkripsi, dst) dan mengubahnya jadi
`validation_core.ConnectionParams` (dataclass murni yang TIDAK tahu apa-apa soal ORM/enkripsi).
Password di-DEKRIPSI di sinilah, TEPAT SEBELUM dipakai — tidak pernah disimpan dalam bentuk
plaintext di tempat lain.

### 5.6 `services/discovery_service.py`

```python
def suggest_mappings(source_conn, target_conn, prefix=""):
    src_tables = list_tables(source_conn)
    tgt_tables = set(list_tables(target_conn))
    suggestions = []
    for src in src_tables:
        candidate = f"{prefix}{src}"
        if candidate in tgt_tables:
            matched_target, match_rule = candidate, f"prefix:{prefix}" if prefix else "identical"
        elif src in tgt_tables:
            matched_target, match_rule = src, "identical"
        else:
            suggestions.append(_blank_suggestion(src, "", "unmatched", ["id"]))
            continue

        key_columns = (
            get_primary_key(source_conn, src)
            or get_primary_key(target_conn, matched_target)
            or ["id"]
        )
        suggestions.append(_blank_suggestion(src, matched_target, match_rule, key_columns))
    return suggestions
```
Algoritma pencocokan nama TIDAK berubah dari sebelumnya: untuk tiap tabel di source, coba 2 aturan
berurutan — (1) apakah `prefix + nama_tabel` ada di target (mis. `ws_orders` → `raw_ws_orders`
kalau prefix=`"raw_"`)? (2) kalau tidak, apakah nama PERSIS SAMA ada di target? Ini bukan machine
learning atau fuzzy matching apa pun — murni aturan string sederhana, sengaja dibuat predictable &
mudah dijelaskan ke user.

Yang BARU: begitu pasangan tabel ketemu (bukan `"unmatched"`), `key_columns` di-auto-fill lewat
`get_primary_key()` — dicoba dari SOURCE dulu (biasanya OLTP, pemilik definitif primary key),
fallback ke TARGET kalau source tidak punya info PK (mis. view, atau user DB tidak punya privilege
baca `INFORMATION_SCHEMA`), fallback terakhir ke `["id"]` polos kalau dua-duanya kosong. Tabel
`"unmatched"` (target belum ketemu) SENGAJA TIDAK memanggil `get_primary_key()` sama sekali (langsung
`continue` dengan `["id"]`) — menghindari query metadata sia-sia untuk tabel yang toh masih harus
diisi manual oleh user.

`_blank_suggestion()` adalah helper kecil yang menyeragamkan BENTUK dict suggestion — dipakai baik
oleh `suggest_mappings()` (auto-suggest by name+DDL) maupun `suggest_from_config()` (di bawah),
supaya `config_detail.html` bisa merender baris suggestion dari SUMBER MANAPUN dengan template yang
sama persis (field `chunk_column`/`date_column`/`exclude_columns`/`mode_override` selalu ada,
walau kosong untuk suggestion hasil auto-suggest by name).

```python
def suggest_from_config(other_config, existing_source_tables):
    return [
        {
            "source_table": t.source_table, "target_table": t.target_table,
            "match_rule": f"copied:{other_config.name}",
            "key_columns": list(t.key_columns or ["id"]),
            "chunk_column": t.chunk_column or "", "date_column": t.date_column or "",
            "exclude_columns": list(t.exclude_columns or []), "mode_override": t.mode_override or "",
        }
        for t in other_config.tables if t.source_table not in existing_source_tables
    ]
```
Fitur "Salin pemetaan dari config lain" — dipakai saat dua config berbeda (mis. dua config yang
sama-sama menyentuh tabel `ws_order_item_stocks` dengan composite key `order_id, material_id`,
tapi untuk pasangan koneksi source/target yang beda) mau berbagi hasil kerja yang SUDAH pernah
diisi manual/di-tuning (key columns, chunk column, exclude columns, mode override) tanpa perlu
mengetik ulang. Beda dari `suggest_mappings()`: fungsi ini TIDAK menyentuh koneksi database sama
sekali — murni membaca `ConfigTable` yang SUDAH tersimpan di config LAIN. Filter `if
t.source_table not in existing_source_tables` mencegah duplikasi: tabel yang source_table-nya SUDAH
ada di config saat ini dilewati (bukan ditimpa), konsisten dengan `suggest_mappings()` yang juga
tidak menyarankan ulang tabel yang sudah ada (lihat filter di `ui.py::config_suggest()` — §5.10).

```python
def columns_by_table(conn, table_names):
    try:
        connector = create_connector(connections_service.to_connection_params(conn))
    except Exception:
        return {name: [] for name in table_names}

    result = {}
    try:
        for name in table_names:
            if name in result:
                continue
            try:
                df = connector.get_schema(conn.database, name)
                result[name] = df["column_name"].tolist() if "column_name" in df.columns else []
            except Exception:
                result[name] = []
    finally:
        connector.close()
    return result
```
Fungsi ini yang membuat field **Key columns / Chunk col / Date col / Exclude** di editor tabel jadi
dropdown (bukan ketik manual) — dipanggil oleh `ui.py::_table_columns_for()` (§5.10) untuk SEMUA
tabel yang akan tampil di satu render halaman (baik yang sudah tersimpan maupun suggestion),
sekaligus, dalam SATU koneksi yang dibuka sekali lalu dipakai berulang (`connector.get_schema()`
dipanggil di dalam loop, TANPA membuka/menutup koneksi per tabel) — penting untuk config dengan
puluhan/ratusan tabel, di mana membuka koneksi baru per tabel per field akan sangat lambat.

Dua lapis penanganan galat, keduanya SENGAJA tidak melempar exception ke pemanggil:
1. `create_connector(...)` gagal total (host mati, VPN putus) → SELURUH tabel yang diminta
   dipetakan ke `[]` (bukan raise) — halaman config tetap bisa dibuka, field-nya jatuh balik jadi
   `<input>` teks biasa (lihat macro `column_cells` di §6.13) alih-alih membuat SELURUH halaman error.
2. Satu tabel spesifik gagal di-introspeksi (tabel sudah dihapus, user tidak punya privilege baca) →
   HANYA tabel itu yang dapat `[]`, tabel lain di config yang sama tetap dapat dropdown normal.

`if name in result: continue` mencegah tabel yang namanya MUNCUL BERULANG (mis. tabel yang sama ada
di `tables` maupun ikut ke-suggest lagi) di-query dua kali — cukup sekali per nama tabel unik.

### 5.7 `services/events_bus.py`

```python
class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._events: dict[int, list[dict]] = {}
        self._done: set[int] = set()
        self._cancel_requested: set[int] = set()

    def publish(self, run_id, event):
        with self._lock:
            self._events.setdefault(run_id, []).append(event)

bus = EventBus()   # <- SATU instance global, dipakai seluruh aplikasi
```
`threading.Lock()` WAJIB di sini karena `publish()` dipanggil dari BANYAK worker thread sekaligus
(satu per tabel yang sedang divalidasi) sementara `get_since()` dibaca dari thread request HTTP
yang BEDA lagi (saat browser polling). Tanpa lock, dua thread yang menulis ke `list` Python yang
sama secara bersamaan BISA (meski jarang) merusak struktur internal list itu. `bus` dibuat sebagai
1 variabel modul-level (bukan di-instansiasi ulang per request) — inilah yang membuatnya
"in-memory pub/sub": semua bagian aplikasi yang `import bus` dari file ini merujuk ke OBJEK YANG
SAMA persis.

### 5.8 `services/run_service.py` — Orkestrasi Run (paling kompleks)

Sudah dijelaskan alurnya di §2.2. Detail tambahan penting:

**`reap_orphaned_runs(db)`** — dipanggil SEKALI di setiap startup server (dari `main.py`, §5.12),
SEBELUM permintaan HTTP apa pun dilayani:
```python
def reap_orphaned_runs(db: Session) -> int:
    orphaned = db.query(models.Run).filter(models.Run.status.in_(["running", "queued"])).all()
    for run in orphaned:
        for rt in run.tables:
            if rt.status in ("running", "pending"):
                rt.status = "error"
                rt.error = "Run diinterupsi: proses server sebelumnya berhenti ..."
                rt.finished_at = _utcnow()
        run.status = "failed"
        run.error = "Run diinterupsi: server berhenti/di-restart sebelum run ini selesai. ..."
        run.finished_at = _utcnow()
        counts = {}
        for rt in run.tables:
            counts[rt.status] = counts.get(rt.status, 0) + 1
        run.summary = {"tables_total": len(run.tables), "pass": counts.get("pass", 0), ...}
    if orphaned:
        db.commit()
    return len(orphaned)
```
**Kenapa fungsi ini ada — insiden nyata**: `run_tables.progress` HANYA di-update saat satu tabel
SELESAI (`_persist_table_result()`), TIDAK PERNAH selagi sedang berjalan (row-level chunking hanya
mengirim progres ke `events_bus` yang in-memory, §5.7 — bukan ke database). Akibatnya, di database,
tabel yang SEDANG diproses secara wajar (menunggu query lambat ke ClickHouse, misalnya) TAMPIL SAMA
PERSIS (`status="running", progress=0.0`) dengan tabel yang thread pemroses-nya sudah MATI (server
crash/di-restart) berjam-jam lalu. Tidak ada cara membedakan "masih jalan wajar" dari "sudah mati"
hanya dari isi tabel `run_tables` — inilah yang membuat sebuah run 68-tabel yang genuinely masih
berjalan sempat disangka macet, dan proses server yang MASIH memvalidasinya sungguhan dihentikan
paksa (menghilangkan progres komputasi yang sedang berjalan di memory, TIDAK BISA di-resume dari
titik itu — lihat README.md "Insiden: database is locked").

`reap_orphaned_runs` tidak mencoba menyelesaikan ambiguitas itu SAAT run sedang berjalan (memang
tidak mungkin dari luar tanpa progres per-chunk yang lebih granular di database — lihat catatan
"belum ada" di README). Yang bisa dipastikan dengan aman: begitu PROSES BARU mulai (artinya proses
SEBELUMNYA — apa pun yang terjadi padanya, crash atau di-restart sengaja — sudah benar-benar
berhenti), Run mana pun yang statusnya MASIH `"running"`/`"queued"` di database PASTI tidak punya
thread pemilik lagi (`_execute_run` cuma pernah jalan di DALAM proses yang memanggil
`start_run_async`, tidak ada mekanisme resume-antar-proses). Jadi baru pada titik INI —
tepat setelah proses baru mulai, sebelum melayani request apa pun — aman & tepat untuk menandai
Run macam itu sebagai `"failed"` dengan pesan jelas, daripada dibiarkan terlihat "hidup" selamanya
di UI. Tabel yang SUDAH `pass`/`fail` (benar-benar selesai sebelum proses lama mati) TIDAK disentuh
— cuma yang `"running"`/`"pending"` yang direklasifikasi jadi `"error"`.

**`build_run_settings(config)`**:
```python
def build_run_settings(config):
    base = RunSettings()
    overrides = config.settings or {}
    for field_name in ("meta_columns", "id_chunk_size", ...):
        if field_name in overrides:
            value = overrides[field_name]
            if field_name == "meta_columns":
                value = frozenset(value) | META_COLUMNS
            setattr(base, field_name, value)
    return base
```
Mengambil `RunSettings()` default (dari `validation_core`), lalu menimpa field APA PUN yang di-set
user di kolom JSON `ValidationConfig.settings`. Kasus khusus `meta_columns`: kalau user menambah
kolom exclude sendiri, hasilnya di-UNION (`|`) dengan `META_COLUMNS` bawaan — jadi user MENAMBAH
pengecualian, bukan MENGGANTI daftar bawaan (kolom `_dlt_id` dkk tetap selalu dikecualikan apa pun
yang di-set user).

**Bug threading nyata & perbaikannya** (paling penting untuk dipahami di file ini):
```python
# SEBELUM diperbaiki (salah):
def worker(rt_id, table_spec):
    ...
    result = vc_run_table(src, tgt, source_conn.database, target_conn.database, ...)
    #                              ^^^^^^^^^^^^^^^^^^^^^^  <- dibaca dari dalam thread!

# SESUDAH diperbaiki (benar):
source_db_name = source_conn.database   # dibaca SEKALI di thread utama
target_db_name = target_conn.database
run_mode = run.mode
...
def worker(rt_id, table_spec):
    result = vc_run_table(src, tgt, source_db_name, target_db_name, table_spec, run_mode, ...)
```
Kenapa versi "sebelum" salah: `source_conn` adalah objek ORM yang terikat ke SATU `Session`
SQLAlchemy (`db`) di thread UTAMA. SQLAlchemy, secara default, meng-*expire* (menganggap basi)
SEMUA atribut objek ORM setiap kali `db.commit()` dipanggil — dan kode ini memang memanggil
`db.commit()` (untuk update status "running") SEBELUM worker thread mulai jalan. Begitu atribut
"basi", pembacaan BERIKUTNYA (`source_conn.database`) memicu SQLAlchemy diam-diam menjalankan
QUERY BARU ke database untuk me-refresh nilainya — dan query itu dijalankan LEWAT Session yang
SAMA (`db`), yang HANYA aman dipakai dari SATU thread. Kalau 2 worker thread (untuk 2 tabel
berbeda) KEBETULAN membaca atribut ini di saat yang BERSAMAAN, dua-duanya mencoba memakai Session
yang sama secara konkuren → korup di level cursor SQL, muncul sebagai `IndexError: tuple index out
of range` yang sangat membingungkan karena errornya jauh di dalam kode SQLAlchemy sendiri, bukan
di kode aplikasi. Perbaikannya: baca SEMUA nilai yang dibutuhkan worker SEBAGAI STRING PYTHON BIASA
di thread utama, SEBELUM thread pool dibuka — setelah itu, worker thread TIDAK PERNAH lagi
menyentuh objek SQLAlchemy apa pun, hanya string & angka biasa (yang aman dibagi antar thread) plus
`validation_core` (yang memang didesain thread-safe — tiap worker membuat `Connector` BARU sendiri
lewat `create_connector()`, tidak berbagi koneksi).

**`_execute_run(run_id)`** — potongan penting lain:
```python
table_specs: dict[int, TableSpec] = {}
for rt in run_tables:
    if rt.config_table_id:
        ct = db.get(models.ConfigTable, rt.config_table_id)
        table_specs[rt.id] = to_table_spec(ct)
    ...

def worker(rt_id, table_spec):
    ...

with ThreadPoolExecutor(max_workers=max(1, settings.table_concurrency)) as ex:
    futures = {}
    for rt in run_tables:
        if bus.is_cancel_requested(run_id):
            break
        futures[ex.submit(worker, rt.id, table_specs[rt.id])] = rt
```
`table_specs` dibangun DULU, SEBELUM masuk ke `ThreadPoolExecutor` — alasan yang SAMA seperti bug
di atas: `db.get(models.ConfigTable, ...)` adalah operasi ORM yang harus dilakukan di thread utama.
`if bus.is_cancel_requested(run_id): break` DI DALAM loop submit ini sendirian TIDAK CUKUP untuk
membuat Cancel berguna — lihat insiden di bawah.

```python
cancel_seen = False
for future in as_completed(futures):
    if not cancel_seen and bus.is_cancel_requested(run_id):
        cancel_seen = True
        for f in futures:
            f.cancel()

    rt = futures[future]
    try:
        rt_id, result, err = future.result()
    except CancelledError:
        rt.status = "cancelled"
        rt.finished_at = _utcnow()
        db.commit()
        continue

    rt = db.get(models.RunTable, rt_id)
    if err is not None:
        rt.status = "error"
        rt.error = err
    else:
        _persist_table_result(rt, result, settings)
    db.commit()
```
`as_completed(futures)` mengembalikan future SESUAI URUTAN SELESAI (bukan urutan submit) — jadi
tabel yang cepat selesai (misal aggregate-only PASS) langsung diproses & dicommit ke DB duluan,
tanpa menunggu tabel lain yang masih dalam Tier 2 row-level yang lambat. Ini membuat progres yang
dilihat user di UI benar-benar mencerminkan urutan penyelesaian NYATA, bukan urutan antrean.

**Insiden: Cancel tidak menghentikan run yang sedang jalan.** Sebelum blok `cancel_seen` di atas ada,
`as_completed(futures)` MENUNGGU SETIAP future selesai tanpa syarat — jadi walau `is_cancel_requested`
dicek DI DALAM loop submit (baris sebelumnya), pengecekan itu nyaris tidak pernah berguna: `submit()`
tidak menunggu apa pun, jadi loop submit untuk, katakanlah, 68 tabel selesai dalam hitungan
milidetik — jauh sebelum user (yang baru mau cancel setelah run jalan beberapa menit/jam) sempat
klik tombolnya. Satu-satunya pengecekan cancel LAIN ada SETELAH `as_completed()` return — yaitu
setelah SEMUA future (semua 68 tabel) selesai — membuat pengecekan itu juga tidak berguna: pada
titik itu tidak ada lagi yang bisa dibatalkan, semuanya sudah kadung selesai.

Perbaikannya memanfaatkan properti `concurrent.futures.Future.cancel()`: method ini HANYA berhasil
(return `True`) untuk task yang BELUM mulai dieksekusi worker thread-nya (masih antre di internal
queue executor) — untuk task yang SUDAH berjalan, `cancel()` cuma return `False` tanpa efek apa pun
(Python tidak bisa membunuh thread yang sedang berjalan secara paksa). Begitu `cancel_seen` pertama
kali `True` (dicek di setiap iterasi `as_completed`, jadi terdeteksi SECEPAT future berikutnya
selesai — bukan menunggu loop submit lagi), kode memanggil `.cancel()` untuk SEMUA future sekaligus:
yang sudah berjalan (dibatasi `table_concurrency`, misal 4) tidak terpengaruh dan tetap selesai
secara alami; SISANYA (yang masih antre) langsung dibatalkan dan `as_completed` segera
menghasilkannya (dengan `CancelledError` saat `.result()` dipanggil) tanpa perlu ditunggu. Efeknya:
"tunggu 68 tabel selesai" berubah jadi "tunggu HANYA batch yang kebetulan sedang jalan saat cancel
diklik" — perbaikan yang jauh, meski tetap tidak instan (tabel yang sedang jalan tetap harus selesai
dulu, karena `validation_core` tidak punya checkpoint cancellation internal). Regression test:
`tests/test_run_cancel.py` (mem-fake `vc_run_table` jadi `time.sleep` supaya bisa mengukur waktu
tanpa data sungguhan; `table_concurrency=2`, 20 tabel — sengaja jauh lebih besar dari concurrency-nya
supaya selisih waktu "broken" (~10s minimum) vs "fixed" (beberapa detik) cukup lebar untuk tahan
noise scheduling CPU di mesin yang lagi sibuk — cancel diminta 0,2 detik setelah start; test
memverifikasi run selesai jauh di bawah waktu broken-nya, dan tabel yang belum sempat jalan
benar-benar `cancelled`, bukan `pass`).

**Bug KEDUA yang ketemu SETELAH fix di atas** (baru bisa kejadian begitu Cancel beneran berefek —
sebelumnya jalur kode ini nyaris tidak pernah tereksekusi dengan makna): tabel yang tidak sempat
di-submit sama sekali (submit loop `break` di baris cancel-check paling awal) ditangani jalur LAMA:
```python
if bus.is_cancel_requested(run_id):
    for rt in run_tables:
        db.refresh(rt)
        if rt.status == "running":
            rt.status = "cancelled"
    run.status = "cancelled"
else:
    run.status = "completed"
run.finished_at = _utcnow()
run.summary = _summarize_run(run_tables, db)   # <- masalahnya di sini
db.commit()
```
`_summarize_run()` (lihat definisinya di bawah) memanggil `db.refresh(rt)` PER TABEL — tapi flip
`rt.status = "cancelled"` di atas belum di-`commit()` saat itu terjadi, jadi `refresh()` diam-diam
MEMBUANG perubahan in-memory itu dan memuat ulang status LAMA (`"running"`) dari database. Hasil
nyata: run selesai dengan `status="cancelled"`, tapi SEMUA `RunTable`-nya tetap `"running"`
SELAMANYA — bug yang polanya identik dengan yang sudah dicatat & dihindari di `reap_orphaned_runs()`
(komentarnya di atas persis menjelaskan pitfall ini), cuma terlewat di jalur cancel ini. Fix: buang
`db.refresh(rt)` dari `_summarize_run()` — `run_tables` yang diterimanya adalah objek ORM yang SAMA,
di Session yang SAMA, yang BARU SAJA diupdate oleh pemanggil (baik lewat loop `as_completed` atau
fallback di atas); refresh di situ tidak pernah perlu, cuma berisiko. Regression test khusus
(deterministik, tidak bergantung timing scheduler):
`test_cancel_before_any_table_starts_marks_everything_cancelled` — minta cancel SEBELUM
`start_run_async` dipanggil sama sekali, supaya jalur "belum sempat submit" ini SELALU teraktivasi.

**Jejak event per tabel (`RunTable.event_log`) + bug `result.error` yang tidak pernah tersimpan.**
Dua hal terkait yang lahir dari satu insiden (user: "kenapa tabel ini ERROR? tidak ada penjelasan
apa pun"):
1. *Bug*: `tiered.run_table()` menangkap semua exception level-tabel SENDIRI dan mengembalikan
   `TableRunResult(status="ERROR", error=str(exc))` — objek hasil normal, BUKAN exception. Jadi
   cabang `err is not None` di `_execute_run` (satu-satunya tempat yang dulu mengisi `rt.error`)
   hampir tidak pernah aktif; tabel error tersimpan dengan `error=NULL`. Fix: `rt.error =
   result.error` di `_persist_table_result`.
2. *Fitur*: `_execute_run` sekarang merekam jejak event per tabel — `make_on_event` menulis setiap
   `ProgressEvent` ke `collections.deque(maxlen=200)` milik tabel itu (SELAIN tetap publish ke bus)
   — dan mem-persist-nya sebagai `rt.event_log` (kolom JSON baru, backfill via ALTER TABLE di
   `init_db()`, §5.1) saat tabel selesai/cancel. Kalau error, traceback lengkap
   (`TableRunResult.error_trace`, field baru di §4.13; atau `traceback.format_exc()` dari `worker`
   untuk exception yang lolos) ditambahkan sebagai entri terakhir. `maxlen=200` membatasi tabel
   ber-chunk ratusan checkpoint supaya tidak membengkakkan DB — yang tersimpan adalah 200 event
   TERAKHIR (ujung cerita yang menarik untuk post-mortem). Thread-safety: tiap deque hanya di-append
   oleh worker thread tabel itu sendiri, dan hanya dibaca SETELAH future-nya selesai
   (happens-before via `future.result()`). Ditampilkan di tab "Log" drilldown (§5.13). Test:
   `tests/test_error_logging.py`.

**`_persist_table_result`, `_persist_aggregate_findings`, `_persist_rowlevel_findings`,
`_build_column_type_details`** — 4 fungsi yang menerjemahkan objek Python (`TableRunResult` dari
`validation_core`) menjadi baris SQL:
```python
def _persist_aggregate_findings(rt, result):
    ...
    cd = agg.column_details
    if "validate_completeness" in cd.columns:
        for _, row in cd[cd["validate_completeness"] == False].iterrows():
            if row["column_name"] in META_COLUMNS:
                continue
            rt.aggregate_findings.append(models.FindingAggregate(
                category="completeness", column_name=row["column_name"], ...
            ))
```
Pola yang berulang di ketiga fungsi ini: filter DataFrame hasil validation_core untuk baris yang
`False` (artinya MISMATCH — nama kolomnya `validate_completeness`, jadi `== False` berarti "TIDAK
valid"/berbeda), lewati kalau kolomnya termasuk `META_COLUMNS`, lalu `.append()` objek ORM baru ke
relationship list (`rt.aggregate_findings`) — SQLAlchemy otomatis akan meng-INSERT baris baru ini
begitu `db.commit()` dipanggil di pemanggilnya, TANPA perlu `db.add()` eksplisit (karena sudah
terhubung lewat relationship `back_populates` ke `rt` yang sudah ada di Session).

`_persist_rowlevel_findings` memotong list ke `[:cap]` (default 10.000, dari
`settings.rowlevel_sample_cap`) — SAMPEL yang disimpan ke database dibatasi supaya tabel
`findings_rowlevel` tidak membengkak untuk tabel dengan JUTAAN mismatch; TOTAL yang sebenarnya
(`rl.missing_in_source_count`, dst — dihitung LENGKAP tanpa dipotong oleh `validation_core`) tetap
disimpan utuh di `rt.rl_metrics` (kolom JSON), jadi angka ringkasan yang ditampilkan selalu akurat
meski detail baris yang bisa dilihat dibatasi.

```python
def _build_column_type_details(agg) -> list[dict]:
    cd = agg.column_details
    if cd.empty or "column_name" not in cd.columns:
        return []
    rows = []
    for _, row in cd.iterrows():
        src_type = row.get("source_column_type")
        tgt_type = row.get("target_column_type")
        src_type = None if pd.isna(src_type) else str(src_type)
        tgt_type = None if pd.isna(tgt_type) else str(tgt_type)
        category_match = None
        if src_type and tgt_type:
            category_match = get_category(src_type) == get_category(tgt_type)
        rows.append({"column": row["column_name"], "source_type": src_type,
                     "target_type": tgt_type, "category_match": category_match})
    return rows
```
Ini fungsi baru untuk tab "Tipe Kolom" (§5.10, README). Bedanya dari 3 fungsi `_persist_*` di atas:
mereka semua hanya mencatat MISMATCH (filter `== False`, cuma baris yang beda yang jadi
`FindingAggregate`/`FindingRowLevel`), sedangkan ini mencatat SEMUA kolom apa adanya — termasuk yang
cocok, dan termasuk kolom yang HANYA ada di satu sisi (`src_type`/`tgt_type` jadi `None`, bukan
dilewati). `category_match` sengaja bisa bernilai 3 kondisi (`True`/`False`/`None`, bukan cuma
boolean) — `None` berarti "tidak ada yang bisa dibandingkan" (kolom itu tidak ada di salah satu
sisi), beda maknanya dari `False` ("ADA di kedua sisi tapi kategorinya beda" — inilah sinyal yang
sebelumnya cuma dipakai diam-diam untuk skip Report 4/5, lihat `_shared_stat_cols` §4.8). `agg`
adalah `AggregateResult` (dari `result.aggregate`, sama objek yang dipakai `_persist_aggregate_findings`
untuk `investigate_query`), `cd` (`column_details`) adalah DataFrame Report 2 yang SUDAH punya
`source_column_type`/`target_column_type` per kolom sejak `gen_report_column_details` (§4.8) — tidak
perlu query baru, cukup dibaca ulang dari struktur yang sudah ada.

### 5.9 `services/export_service.py`

Berbeda dari `validation_core/excel_export.py` (§4.14) yang bekerja dari objek LIVE di memory,
`export_run_to_excel(db, run)` di sini membangun ulang laporan Excel dari data yang SUDAH tersimpan
di database (bisa dipanggil kapan saja, bahkan berhari-hari setelah run selesai):
```python
agg_rows = [
    {"category": f.category, "column": f.column_name, "metric": f.metric, ...}
    for f in rt.aggregate_findings
]
if agg_rows:
    pd.DataFrame(agg_rows).to_excel(writer, sheet_name=_sheet_name(rt.target_table, "_findings"), index=False)
```
Perhatikan `if agg_rows:` — sheet HANYA dibuat kalau ADA temuan (`aggregate_findings` tidak
kosong). Kalau semua tabel PASS, file Excel-nya hanya berisi 1 sheet `Summary` tanpa sheet detail
apa pun — desain minimal, tidak membuat sheet kosong percuma.

### 5.10 `routers/ui.py` — Semua Halaman & Form

**RBAC + data scoping (2026-07-21)** — setiap route di file ini sekarang di-anotasi salah satu dari
tiga pola (lihat §9.1 untuk matriks lengkap per-route, §5.4 untuk definisi helper-nya):
- `user: models.User = Depends(require_login)` — viewer ke atas boleh, cuma BACA.
- `user: models.User = Depends(require_role("editor"))` — editor/admin boleh, aksi MENGUBAH data.
- Route LIST (`connections_list`, `configs_list`, `dashboard`, dst) memanggil
  `scope_query(db.query(Model), Model, user)` alih-alih `db.query(Model)` polos.
- Route SATU-OBJEK (`connection_edit_form`, `config_detail`, `run_detail`, dst) memanggil
  `check_owner(obj, user)` tepat setelah `db.get(...)`, SEBELUM baris kode lain menyentuh objek itu.

Satu route yang SEBELUMNYA tidak punya dependency auth apa pun — `run_tables_fragment`
(`GET /runs/{run_id}/tables-fragment`, dipanggil oleh polling JS di §5.13) — ditemukan saat audit
RBAC ini dan diperbaiki (ditambah `require_login` + `check_owner` pada `Run`-nya). Regression test:
`tests/test_rbac.py::test_unauthorized_tables_fragment_now_requires_login`.

**`asset_version` (Jinja global, di-set sekali saat import modul ini)**:
```python
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
templates.env.globals["asset_version"] = int((_STATIC_DIR / "app.css").stat().st_mtime)
```
`templates.env` adalah `jinja2.Environment` di balik `Jinja2Templates` — apa pun yang ditaruh di
`.globals` otomatis tersedia di SEMUA template yang dirender lewat `templates` ini, tanpa perlu
dioper manual lewat context dict di setiap route (ada lusinan route di file ini, tidak realistis
menambah 1 key yang sama ke semuanya satu-satu). `base.html` memakainya di `<link rel="stylesheet"
href="/static/app.css?v={{ asset_version }}">`.

**Insiden nyata yang melahirkan ini**: `StaticFiles` (dipakai untuk `/static`, §5.12) tidak mengirim
header `Cache-Control` apa pun — browser bebas cache `app.css` seagresif apa pun berdasarkan
heuristiknya sendiri. Setelah deploy CSS baru (popup modal untuk fitur copy-key, §5.13), user
melaporkan tampilannya TETAP polos/tidak ter-styling — padahal server sudah dikonfirmasi (curl
langsung ke `/static/app.css`) menyajikan file yang BENAR. Browser user-nya yang meng-cache versi
lama. Karena mtime file diambil SEKALI saat modul ini di-import (= setiap kali server start), dan
server HANYA restart ketika ada perubahan kode/asset yang perlu dideploy (tidak ada hot-reload di
app ini) — URL stylesheet otomatis berubah persis di saat asset-nya benar-benar berubah, membuat
cache lama di browser manapun jadi tidak relevan (URL lama tidak pernah diminta lagi). Test:
`tests/test_asset_versioning.py`.

**`config_create()`** — cek nama duplikat SEBELUM insert:
```python
if db.query(models.ValidationConfig).filter_by(name=name).first():
    return RedirectResponse(url=f"/configs/new?error=Nama config \"{name}\" sudah dipakai, pilih nama lain", status_code=303)
```
`ValidationConfig.name` punya `unique=True` (§5.2) — insiden nyata: sebelum cek ini ada, membuat
config dengan nama yang sudah dipakai (mis. lewat form) langsung crash `sqlalchemy.exc.IntegrityError:
UNIQUE constraint failed` sebagai Internal Server Error mentah, bukan pesan yang bisa dimengerti user.
Pola yang sama seperti `connection_delete()` di §5.10 (baris di bawah, kasus serupa untuk FK
constraint): cek DULU lewat query biasa, bukan menangkap exception dari database — lebih murah dan
hasilnya pasti (tidak tergantung timing/race antara cek dan insert, meski untuk aplikasi single-user
seperti ini race itu sendiri bukan risiko nyata).

**`config_table_status()` + `config_rerun_table()`** — halaman "Status Tabel" (`/configs/{id}/status`)
dan re-run per tabel. Matriks status TERKINI per tabel digabung lintas run: query `RunTable` untuk
≤15 run terakhir config itu, diurutkan run terbaru dulu, lalu di-group per `source_table` — kemunculan
PERTAMA per tabel = status terkininya, sisanya jadi riwayat (chip per run di UI). Lahir dari keluhan
usability nyata: re-run parsial (scope resume / per tabel) membuat Run baru yang hanya berisi tabel
yang di-re-run, jadi tidak ada satu run pun yang menunjukkan posisi terkini SEMUA tabel.
`config_rerun_table()` membuat run `table_filter=[source_table]` lalu redirect BALIK ke halaman
status (bukan ke run 1-tabel-nya) — halaman status auto-refresh 5 detik selagi ada tabel berjalan.
Tabel yang ada di riwayat tapi sudah dihapus dari config tetap ditampilkan (ditandai "dihapus dari
config") supaya riwayat tidak diam-diam hilang. Test: `tests/test_config_status.py`.

**`_parse_indexed_rows(form)`** — parser untuk form editor tabel di `config_detail.html`:
```python
_ROW_KEY_RE = re.compile(r"^row_(\d+)_(\w+)$")

def _parse_indexed_rows(form) -> list[dict]:
    rows: dict[int, dict] = {}
    for key in set(form.keys()):
        m = _ROW_KEY_RE.match(key)
        if not m:
            continue
        idx, field = int(m.group(1)), m.group(2)
        rows.setdefault(idx, {})[field] = form.getlist(key)
    return [rows[i] for i in sorted(rows.keys())]
```
Ini solusi untuk masalah HTML yang cukup halus: form dengan banyak BARIS (tiap baris = 1 pasangan
tabel), tiap baris punya beberapa field (`source_table`, `target_table`, `enabled` [checkbox],
dst). Kalau field diberi nama SAMA di semua baris (`name="source_table"` berulang) dan diandalkan
urutan array paralel, itu RAWAN BUG: checkbox yang TIDAK DICENTANG **tidak mengirim value APA PUN**
ke server (perilaku standar HTML form) — jadi kalau baris ke-3 checkbox-nya tidak dicentang, array
`enabled[]` akan punya elemen LEBIH SEDIKIT daripada array `source_table[]`, dan index-nya jadi
tidak sejajar (bug klasik). Solusinya di sini: SETIAP field diberi nama unik ber-indeks
(`row_0_source_table`, `row_0_enabled`, `row_1_source_table`, ...) — jadi TIDAK ADA ketergantungan
pada urutan/jumlah kemunculan, tiap baris berdiri sendiri sepenuhnya.

`form.getlist(key)` (bukan `form.get(key)`) — SETIAP field disimpan sebagai LIST, bukan nilai
tunggal. Ini perlu karena `key_columns`/`exclude_columns` sekarang bisa dirender sebagai
`<select multiple>` (§6.13) yang mengirim SATU value PER option yang dipilih — form dengan 2 kolom
composite key ter-centang mengirim 2 pasang `row_0_key_columns=order_id` &
`row_0_key_columns=material_id` dengan NAMA SAMA. `form.get()` hanya mengembalikan value PERTAMA
(kehilangan `material_id`); `form.getlist()` mengembalikan keduanya. Untuk field yang tetap berupa
`<input>` teks biasa (baris yang ditambah manual sebelum kolomnya di-load — §6.13), hasilnya cuma
list berisi SATU string (boleh jadi berisi koma, mis. `["order_id,material_id"]`) — dua bentuk ini
(banyak value polos vs satu value ber-koma) disatukan lagi oleh `_flatten_csv()` di bawah, jadi
logic penyimpanan TIDAK PERLU tahu widget mana yang menghasilkan data itu. `set(form.keys())` (BUKAN
`form.keys()` langsung) memastikan tiap NAMA key hanya diproses SEKALI oleh loop utama — kalau
tidak, key yang punya banyak value (seperti `key_columns` composite) akan muncul BERULANG di
`form.keys()` (sekali per value), dan `rows.setdefault(idx, {})[field] = form.getlist(key)`
dipanggil berkali-kali secara REDUNDAN untuk field yang sama (tidak salah hasilnya, cuma boros).

```python
def _first(row: dict, field: str, default: str = "") -> str:
    values = row.get(field) or []
    return values[0] if values else default

def _flatten_csv(row: dict, field: str) -> list[str]:
    out: list[str] = []
    for v in row.get(field) or []:
        out.extend(c.strip() for c in v.split(",") if c.strip())
    return out
```
Dua helper kecil yang menyeragamkan cara membaca `row` (yang sekarang SEMUA field-nya list) sesuai
kebutuhan tiap kolom database: `_first()` untuk field SINGLE-value (`source_table`, `target_table`,
`chunk_column`, `date_column`, `mode_override`, `enabled`) — ambil elemen pertama saja, aman baik
dari `<input>` maupun `<select>` (non-multiple) yang keduanya selalu mengirim TEPAT satu value per
field. `_flatten_csv()` untuk field MULTI-value (`key_columns`, `exclude_columns`) — iterasi SETIAP
value yang terkirim (bisa 1 atau banyak), `.split(",")` masing-masing (menangani kasus `<input>`
teks berisi `"order_id,material_id"` SEKALIGUS kasus `<select multiple>` yang tiap value-nya sudah
satu nama kolom polos tanpa koma — `.split(",")` pada `"order_id"` polos cuma menghasilkan
`["order_id"]`, tidak merusak apa pun), lalu `.strip()` semuanya jadi satu list datar.

**`config_save_tables()`**:
```python
for t in list(cfg.tables):
    db.delete(t)
db.flush()
for row in rows:
    source_table = _first(row, "source_table").strip()
    target_table = _first(row, "target_table").strip()
    if not source_table or not target_table:
        continue
    db.add(models.ConfigTable(
        config_id=config_id, source_table=source_table, target_table=target_table,
        key_columns=_flatten_csv(row, "key_columns") or ["id"],
        chunk_column=_first(row, "chunk_column").strip() or None,
        date_column=_first(row, "date_column").strip() or None,
        exclude_columns=_flatten_csv(row, "exclude_columns"),
        mode_override=_first(row, "mode_override").strip() or None,
        enabled="on" in (row.get("enabled") or []),
    ))
db.commit()
```
Strategi **replace-all**: SEMUA `ConfigTable` lama untuk config ini dihapus dulu, baru dibuat ulang
dari data form yang baru disubmit. Ini lebih sederhana daripada mencoba mencocokkan baris mana yang
"diedit" vs "baru" vs "dihapus" satu-satu (butuh ID tersembunyi per baris + logic diff) — cocok
untuk skala pemakaian tool ini (puluhan-ratusan tabel per config, bukan jutaan), meski berarti ID
`ConfigTable` selalu berubah tiap kali mapping disimpan ulang (yang untuk saat ini tidak masalah
karena tidak ada yang mereferensikan `config_table_id` dari luar config itu sendiri kecuali
`RunTable.config_table_id`, yang toh dibuat ulang tiap run baru). `"on" in (row.get("enabled") or
[])` — checkbox HTML mengirim string literal `"on"` kalau tercentang, TIDAK ADA APA PUN kalau
tidak (bukan `"off"`/`"false"`) — jadi `row.get("enabled")` untuk baris yang checkbox-nya kosong
adalah `None`/`[]`, dan `"on" in []` otomatis `False` tanpa perlu pengecekan `is None` eksplisit.

**`_table_columns_for(cfg, tables, suggestions)`** — dipanggil oleh SEMUA route yang merender
`config_detail.html` (`config_detail()`, `config_suggest()`, `config_copy_mappings()`):
```python
def _table_columns_for(cfg, tables, suggestions):
    names = [t.source_table for t in tables] + [s["source_table"] for s in suggestions]
    return discovery_service.columns_by_table(cfg.source_connection, names)
```
Mengumpulkan nama SEMUA tabel yang akan tampil di halaman (baik yang SUDAH tersimpan di
`config_tables` maupun suggestion yang BELUM tersimpan), lalu satu kali panggil
`columns_by_table()` (§5.6) untuk keduanya SEKALIGUS — bukan dipanggil terpisah untuk `tables` dan
`suggestions`, supaya tabel yang KEBETULAN muncul di kedua list (jarang, tapi mungkin) tidak
di-query dua kali (`columns_by_table` sendiri sudah dedup by name, tapi menggabungkan nama di sini
dulu berarti hanya SATU koneksi yang perlu dibuka untuk seluruh render halaman, bukan dua).

**`config_suggest()` dan `config_copy_mappings()`** — dua route POST yang MENGISI `suggestions`
tapi TIDAK MENYIMPAN apa pun ke database; keduanya cuma me-render ULANG `config_detail.html` dengan
baris tambahan yang belum tersimpan, menunggu user klik "Simpan Pemetaan Tabel" (`config_save_tables()`
di atas) untuk benar-benar commit. `config_copy_mappings()`:
```python
@router.post("/configs/{config_id}/copy-from", response_class=HTMLResponse)
def config_copy_mappings(config_id, request, source_config_id: int = Form(...), ...):
    cfg = db.get(models.ValidationConfig, config_id)
    other = db.get(models.ValidationConfig, source_config_id)
    existing = {t.source_table for t in cfg.tables}
    suggestions = discovery_service.suggest_from_config(other, existing) if other else []
    ...
    return templates.TemplateResponse(request, "config_detail.html", {..., "suggestions": suggestions, ...})
```
Pola ini SENGAJA dibuat identik dengan `config_suggest()` (source-nya beda — satu dari
`suggest_mappings()`/koneksi database, satu dari `suggest_from_config()`/config lain — tapi
KEDUANYA menghasilkan list dict berbentuk sama yang dirender oleh blok `{% for s in suggestions %}`
YANG SAMA di template, lihat §6.13). `_other_configs(db, config_id)` (helper kecil di atas kedua
route ini) mengisi dropdown "Salin dari config lain" — hanya config LAIN yang belum di-archive
(`config_id != models.ValidationConfig.id`, `is_archived == False`), dipanggil di SEMUA route yang
merender `config_detail.html` (GET biasa, POST suggest, POST copy-from) supaya dropdown itu selalu
terisi terlepas dari aksi mana yang barusan dilakukan user.

**Route dashboard, config list, run detail** — sudah dibahas alurnya di §2. Yang belum disebut:
```python
trend_runs = (
    db.query(models.Run)
    .filter(models.Run.status == "completed")
    .order_by(desc(models.Run.id)).limit(12).all()
)
trend = []
for r in reversed(trend_runs):
    ...
```
`order_by(desc(...)).limit(12)` mengambil 12 run TERBARU (urutan menurun), lalu `reversed(...)`
membalik urutan itu jadi TERLAMA→TERBARU — supaya bar chart tren di dashboard terbaca kiri (lama)
ke kanan (baru), sesuai konvensi bacaan grafik pada umumnya.

**`table_drilldown()`** — selain merender 7 tab (Ringkasan, Temuan Agregat, **Tipe Kolom**, Periode,
Missing Keys, Value Diffs, SQL), route ini menghitung filter kolom untuk tab Value Diffs DAN (sejak
insiden halaman-lambat, lihat README) mem-paginasi tab Missing Keys/Value Diffs alih-alih memuat
semuanya lewat ORM. Tab "Tipe Kolom" TIDAK butuh perubahan apa pun di route ini — datanya
(`rt.column_type_details`, §5.2/§5.8) sudah tersedia langsung di objek `rt` yang SUDAH dioper ke
template untuk tab lain, jadi template-nya cukup baca `rt.column_type_details` langsung (lihat
§5.13):
```python
FRL = models.FindingRowLevel
base_q = db.query(FRL).filter(FRL.run_table_id == rt.id)
missing_count = base_q.filter(FRL.finding_type.in_(("missing_in_source", "missing_in_target"))).count()
total_diff_count = base_q.filter(FRL.finding_type == "value_diff").count()

diff_column_counts: dict[str, int] = dict(
    db.query(FRL.column_name, func.count(FRL.id))
    .filter(FRL.run_table_id == rt.id, FRL.finding_type == "value_diff")
    .group_by(FRL.column_name).all()
)
diff_columns = sorted(diff_column_counts.keys())

missing_findings: list = []
diff_findings: list = []
if tab == "missing":
    missing_findings = (
        base_q.filter(FRL.finding_type.in_(("missing_in_source", "missing_in_target")))
        .order_by(FRL.id).offset((page - 1) * ROWLEVEL_PAGE_SIZE).limit(ROWLEVEL_PAGE_SIZE).all()
    )
elif tab == "diffs":
    diff_q = base_q.filter(FRL.finding_type == "value_diff")
    if column:
        diff_q = diff_q.filter(FRL.column_name == column)
    diff_findings = diff_q.order_by(FRL.id).offset((page - 1) * ROWLEVEL_PAGE_SIZE).limit(ROWLEVEL_PAGE_SIZE).all()
```
Sebelumnya route ini memuat `rt.rowlevel_findings` (relationship ORM) secara PENUH — sampai
`rowlevel_sample_cap` (default 10.000) baris PER jenis finding, jadi bisa 20.000+ baris untuk satu
tabel — lalu MEREK-render semuanya jadi `<tr>` HTML dalam satu response, dan ini terjadi di SETIAP
tab dibuka, termasuk tab "Ringkasan" yang sama sekali tidak menampilkannya. Untuk tabel dengan
puluhan ribu diff, ini membuat halaman drilldown sangat lambat (parah lagi kalau server sedang
menjalankan run besar lain secara bersamaan — lihat README untuk insiden nyata & pengukurannya).

Perbaikannya membagi kerja jadi dua kategori:
1. **Badge/dropdown** (jumlah per tab, jumlah per kolom di filter dropdown) — cukup `COUNT`/
   `GROUP BY` di level SQL, TIDAK PERNAH menghidrasi baris jadi objek ORM Python, jadi murah
   terlepas dari berapa banyak baris yang cocok.
2. **Baris sungguhan untuk ditampilkan** — HANYA di-query untuk tab yang SEDANG dibuka
   (`tab == "missing"` atau `tab == "diffs"`), dan HANYA satu halaman (`ROWLEVEL_PAGE_SIZE = 200`)
   lewat `.offset()/.limit()` langsung di query, bukan slice Python dari list yang sudah lengkap.
   Tab lain (termasuk "ringkasan") tidak menyentuh `findings_rowlevel` sama sekali.

`diff_column_counts` (dan `diff_columns`) selalu dihitung dari SEMUA baris `value_diff` (bukan yang
sudah terfilter kolom/halaman) supaya dropdown filter SELALU menampilkan semua kolom yang punya diff
beserta jumlah aslinya — sama seperti sebelumnya, cuma sekarang lewat `GROUP BY` SQL, bukan iterasi
Python atas list yang sudah dimuat penuh. Parameter `column` & `page` datang dari query string
(`?tab=diffs&column=yearly_need&page=2`) lewat form `<select>`/link `<a>` — reload halaman penuh via
GET, bukan AJAX, konsisten dengan pola server-rendered di seluruh aplikasi ini. Mengubah filter kolom
selalu reset ke halaman 1 (form filter tidak mengirim `page`).

Query di atas butuh index pada `(run_table_id, finding_type)` — lihat §5.1 & §5.2 untuk
`ix_findings_rowlevel_run_table_type` dan kenapa dia harus di-backfill manual, bukan otomatis lewat
`create_all()`.

**`table_drilldown_keys()` + `_format_keys_for_sql()`** — `GET /runs/{run_id}/tables/{run_table_id}/keys`,
backing tombol "📋 Copy key" (§5.13, README). Beda dari route di atas: TIDAK dipaginasi sama sekali —
ini dipanggil sekali per klik tombol (bukan otomatis tiap render halaman), jadi mengambil SEMUA key
yang cocok (via `.distinct()` di kolom `row_key` saja, bukan seluruh baris/kolom) memang sengaja,
bukan mengulang kesalahan insiden pagination di atas.
```python
q = db.query(FRL.row_key).filter(FRL.run_table_id == rt.id)
if kind == "value_diff":
    q = q.filter(FRL.finding_type == "value_diff")
    if column:
        q = q.filter(FRL.column_name == column)
elif kind in ("missing_in_source", "missing_in_target"):
    q = q.filter(FRL.finding_type == kind)
keys = sorted({row[0] for row in q.distinct().all()})
```
`.distinct()` PENTING untuk `kind="value_diff"` tanpa filter kolom: satu row_key bisa muncul di
banyak baris `FindingRowLevel` (satu per kolom yang beda untuk baris itu) — tanpa distinct, key yang
sama akan terulang sebanyak jumlah kolom yang beda untuknya, padahal user butuh daftar ID UNIK untuk
di-re-insert, bukan satu entri per kolom.

`_format_keys_for_sql(keys, key_columns)` menyiapkan tampilan siap-tempel: angka polos dipisah koma
kalau SEMUA key match `^-?\d+(\.\d+)?$` (regex numerik), di-quote (`'...'`) kalau tidak — heuristik
sederhana yang menutupi kasus umum (auto-increment id) sekaligus tetap aman untuk id string. Untuk
`len(key_columns) > 1` (composite key), key ditampilkan APA ADANYA (nilai gabungan `_` dari
`composite_key()` di `rowlevel/comparator.py`) plus baris komentar yang menyebutkan urutan kolomnya —
SENGAJA TIDAK di-split balik jadi tuple per kolom: penggabungan dengan `_` itu lossy (kalau ada value
asli yang mengandung `_`, split balik bisa salah tanpa ketahuan), jadi menampilkan nilai asli apa
adanya lebih jujur daripada menebak split yang bisa diam-diam keliru.

`key_columns` diambil dari `rt.rl_metrics.get("key_columns")` (disimpan saat run selesai, §5.8) —
BUKAN dari `ConfigTable.key_columns` — supaya tetap benar untuk run LAMA meski config-nya sudah
diubah sejak itu. Regression test: `tests/test_copy_keys.py`.

### 5.11 `routers/api.py`

2 endpoint JSON:
```python
@router.get("/runs/{run_id}/status")
def run_status(run_id, user=Depends(require_login), db=Depends(get_db)):
    run = db.get(models.Run, run_id)
    check_owner(run, user)
    return {"id": run.id, "status": run.status, "summary": run.summary or {}}
```
FastAPI otomatis meng-encode dictionary Python ini jadi JSON (tidak perlu `jsonify()` manual
seperti di Flask) — cukup `return` dictionary biasa. Endpoint ini dipanggil oleh JavaScript
`fetch()` di `run_detail.html` setiap 2 detik selagi status masih `"running"`/`"queued"`.
`check_owner()` (§5.4/§9.2) di sini mengembalikan `404` JSON kalau `run_id` bukan milik user yang
sedang polling — mencegah user B melihat status run milik user A hanya dengan menebak-nebak ID di
URL polling-nya sendiri.

```python
@router.get("/configs/{config_id}/table-columns")
def config_table_columns(config_id, table: str, side: str = "source",
                          user=Depends(require_role("editor")), db=Depends(get_db)):
    cfg = db.get(models.ValidationConfig, config_id)
    if not cfg:
        return {"columns": [], "error": "config not found"}
    check_owner(cfg, user)
    conn = cfg.target_connection if side == "target" else cfg.source_connection
    if not table:
        return {"columns": []}
    try:
        cols = discovery_service.list_columns(conn, table)
        return {"columns": [c["name"] for c in cols]}
    except Exception as exc:
        return {"columns": [], "error": str(exc)}
```
`require_role("editor")` (bukan `require_login` polos) karena endpoint ini HANYA dipanggil dari
alur config-builder (§5.13) yang sudah editor-only di sisi UI-nya — konsisten dengan
`config_new_form`/`config_save_tables` yang jadi sumber form-nya.
Endpoint KEDUA ini adalah versi AJAX dari mekanisme yang sudah dijalankan SERVER-SIDE untuk
tabel yang dikenal saat render (`_table_columns_for()`, §5.10) — bedanya, dipanggil dari
JAVASCRIPT (bukan Jinja), untuk baris yang ditambah manual lewat tombol "+ Tambah baris" di
`config_detail.html`, di mana `source_table` BELUM diketahui saat halaman pertama kali dirender
(baris itu masih kosong). `try/except` DI DALAM route (bukan cuma di `discovery_service`) memastikan
kegagalan APA PUN (koneksi mati, tabel tidak ada) selalu balik sebagai JSON `{"columns": [],
"error": "..."}` dengan status `200`, BUKAN `500` — supaya JS pemanggilnya (§5.13) selalu bisa
`.json()` responsnya dengan aman tanpa perlu menangani exception HTTP terpisah.

### 5.12 `main.py`

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    _bootstrap_admin()
    yield

app = FastAPI(..., lifespan=lifespan)
```
Pola **lifespan context manager** (API modern FastAPI, menggantikan `@app.on_event("startup")`
yang sudah deprecated) — kode SEBELUM `yield` (`_bootstrap_admin()`) jalan SEKALI saat aplikasi
pertama kali start, kode SESUDAH `yield` (tidak ada di sini) akan jalan saat aplikasi shutdown.

```python
def _bootstrap_admin():
    init_db()
    db = SessionLocal()
    try:
        if db.query(models.User).count() == 0:
            email = os.environ.get("LIDVALID_ADMIN_EMAIL")
            password = os.environ.get("LIDVALID_ADMIN_PASSWORD")
            if not email or not password:
                if os.environ.get("LIDVALID_ENV", "development") == "production":
                    raise RuntimeError("LIDVALID_ADMIN_EMAIL/PASSWORD wajib di production...")
                email = email or "admin@lidvalid.local"
                password = password or "admin123"
                print("WARNING: bootstrapping with DEV-ONLY demo admin credentials.")
            admin = models.User(email=email, password_hash=security.hash_password(password), role="admin", ...)
            db.add(admin)
            db.commit()
    finally:
        db.close()
```
`init_db()` (dari `database.py`) memanggil `Base.metadata.create_all(bind=engine)` — SQLAlchemy
otomatis membuat SEMUA tabel yang belum ada berdasarkan class model yang sudah didefinisikan (lihat
§5.2). Kalau tabel `users` MASIH KOSONG (`count() == 0` — hanya terjadi di run PERTAMA aplikasi
ini di database yang benar-benar baru), dibuat 1 user admin bootstrap.

**Env-driven (2026-07-21)** — sebelumnya email/password admin bootstrap HARDCODED
(`admin@lidvalid.local`/`admin123`) tanpa syarat. Sekarang: kalau `LIDVALID_ADMIN_EMAIL` DAN
`LIDVALID_ADMIN_PASSWORD` di-set (mis. di VPS produksi lewat `.env`), itu yang dipakai untuk admin
pertama — kredensial demo tidak pernah tersentuh. Kalau salah satu/keduanya TIDAK di-set: di
`LIDVALID_ENV=production` langsung `RuntimeError` (gagal start, bukan diam-diam pakai kredensial
demo di produksi); di dev/lokal, jatuh ke kredensial demo seperti sebelumnya (dengan warning
tercetak) supaya `uvicorn app.main:app --reload` tanpa `.env` apa pun tetap langsung bisa dipakai.
Perhatikan bahwa blok ini HANYA jalan kalau `users` masih kosong — pada database yang SUDAH punya
user (mis. hasil migrasi, §9.3), env var ini tidak dibaca sama sekali, jadi aman di-set atau tidak.

### 5.12b `scripts/create_user.py` (ditambahkan 2026-07-21)

Belum ada halaman user-management di UI sama sekali (tidak ada `/users`, tidak ada tombol "Tambah
User" di mana pun) — jadi ini satu-satunya cara membuat akun KEDUA, ketiga, dst:
```bash
.venv/Scripts/python.exe scripts/create_user.py --email a@b.com --password secret --role editor --name "Jane Doe"
```
Pola file-nya sama seperti `seed_demo.py` (§5.7-ish) — `sys.path.insert(0, ...)` lalu import
langsung `app.database`/`app.models`/`app.security`, TANPA lewat HTTP/TestClient. Kalau email yang
diberikan SUDAH ADA, akun itu di-UPDATE (password/role/nama) alih-alih membuat duplikat — inilah
cara mengganti kredensial demo admin (`admin@lidvalid.local`) setelah deploy: jalankan script ini
lagi dengan email yang sama, password baru. Dipakai juga untuk membuat akun viewer/editor
sungguhan di produksi, dan (di `tests/test_rbac.py`, lewat pola serupa langsung ke ORM, bukan
script ini) untuk membuat akun uji RBAC.

```python
reaped = run_service.reap_orphaned_runs(db)
if reaped:
    print(f"LidValid — {reaped} run dari proses sebelumnya ditandai 'failed' ...")
```
Baris ini yang menutup celah "run terlihat hidup selamanya" yang dibahas di §5.8 — dipanggil di
SETIAP startup (bukan cuma run pertama seperti pembuatan admin di atasnya), karena orphaned run bisa
terjadi kapan saja server berhenti tak terduga, bukan cuma sekali di awal.

```python
app.add_middleware(SessionMiddleware, secret_key=security._load_or_create_key().decode("ascii"))
```
`SessionMiddleware` dari Starlette menyediakan `request.session` (dictionary yang persist antar
request LEWAT COOKIE terenkripsi-tersigned di browser — bukan disimpan di server). Kuncinya
memakai FUNGSI YANG SAMA (`_load_or_create_key`) dengan yang dipakai enkripsi password koneksi di
`security.py` — jadi hanya ADA SATU kunci rahasia untuk seluruh aplikasi (disimpan di
`data/secret.key`), dipakai untuk dua tujuan berbeda (enkripsi Fernet & signing session cookie).

### 5.13 Frontend — Templates & JavaScript (tanpa build step)

Semua halaman adalah file `.html` biasa di `app/templates/`, dirender server-side oleh Jinja2 (tag
`{{ variabel }}`, `{% for %}`, `{% if %}`). TIDAK ADA React/Vue/build step — ini pilihan yang
dijelaskan di README (tidak ada Node/npm di environment pembuatan awal).

Interaktivitas dicapai lewat 2 pola JavaScript sederhana yang ditulis langsung di `<script>` HTML
biasa (tanpa framework):

**Pola 1 — Polling live progress** (`run_detail.html`):
```javascript
async function poll() {
  const statusRes = await fetch('/api/runs/{{ run.id }}/status');
  const status = await statusRes.json();
  document.getElementById('run-status-pill').textContent = status.status.toUpperCase();
  const fragRes = await fetch('/runs/{{ run.id }}/tables-fragment');
  document.getElementById('tables-fragment').innerHTML = await fragRes.text();
  if (status.status === 'running' || status.status === 'queued') {
    setTimeout(poll, 2000);
  } else {
    setTimeout(() => location.reload(), 500);
  }
}
setTimeout(poll, 2000);
```
`poll()` memanggil dirinya sendiri lewat `setTimeout` (bukan `setInterval`) — supaya SETIAP
pemanggilan menunggu response SEBELUMNYA selesai dulu (mencegah request menumpuk kalau server lagi
lambat). Endpoint kedua (`tables-fragment`) mengembalikan POTONGAN HTML (bukan JSON) yang langsung
ditempel via `innerHTML` — pendekatan ini disebut "HTML-over-the-wire", menghindari perlu menulis
logika rendering tabel dua kali (sekali di Jinja server-side untuk load awal, sekali lagi di
JavaScript untuk update) — cukup 1 template Jinja (`_run_tables_fragment.html`) dipakai baik untuk
render halaman awal MAUPUN untuk hasil polling.

**Pola 2 — Baris tabel bisa diklik** (`base.html`, berlaku di semua halaman):
```javascript
document.addEventListener('click', function (e) {
  const tr = e.target.closest('tr[data-href]');
  if (!tr) return;
  if (e.target.closest('a, button, input, select, textarea, form')) return;
  window.location.href = tr.dataset.href;
});
```
SATU event listener global di `<body>` (event delegation — bukan 1 listener per baris tabel, lebih
efisien) yang berlaku untuk SEMUA halaman. `e.target.closest('tr[data-href]')` mencari elemen
`<tr>` terdekat (dari titik yang diklik, naik ke atas DOM tree) yang punya atribut `data-href`.
Baris kedua (`e.target.closest('a, button, ...')`) adalah PENGAMANnya: kalau klik jatuh pada
elemen interaktif DI DALAM baris (link "Test", tombol "Hapus", checkbox), fungsi berhenti TANPA
navigasi — supaya tombol aksi di dalam baris tetap berfungsi normal, hanya AREA KOSONG baris yang
memicu navigasi ke `tr.dataset.href`.

**Pola 3 — Macro Jinja + AJAX untuk dropdown kolom** (`config_detail.html`):

Editor pemetaan tabel punya DUA cara berbeda mengisi field key/chunk/date/exclude column,
tergantung apakah nama tabelnya SUDAH diketahui saat halaman dirender:

```jinja
{% macro column_cells(idx, source_table, key_columns, chunk_column, date_column, exclude_columns, table_columns) %}
{% set cols = table_columns.get(source_table, []) %}
<td data-role="key_columns">
  {% if cols %}
  <select name="row_{{ idx }}_key_columns" multiple size="3">
    {% for c in cols %}
    <option value="{{ c }}" {{ 'selected' if c in (key_columns or ['id']) else '' }}>{{ c }}</option>
    {% endfor %}
  </select>
  {% else %}
  <input name="row_{{ idx }}_key_columns" value="{{ (key_columns or ['id'])|join(',') }}">
  {% endif %}
</td>
...
{% endmacro %}
```
`{% macro %}` di Jinja2 adalah fungsi templating biasa — dipanggil dua kali, sekali per baris di
`{% for t in tables %}` (tabel yang SUDAH tersimpan) dan sekali per baris di `{% for s in
suggestions %}` (hasil auto-suggest ATAU copy-from-config), dengan argumen berbeda tapi LOGIKA
render yang SAMA PERSIS — tanpa macro, blok `<td>` sepanjang ini harus disalin-tempel dua kali dan
gampang tidak sinkron kalau salah satu diubah tapi yang lain lupa. `table_columns.get(source_table,
[])` — kalau kosong (tabel gagal di-introspeksi, lihat §5.6), `{% if cols %}` jatuh ke cabang
`{% else %}` yang me-render `<input>` teks biasa dengan value comma-joined seperti sebelum fitur
ini ada — DEGRADASI YANG AMAN, bukan field kosong/error.

`data-role="key_columns"` (dan sejenisnya per `<td>`) BUKAN dipakai CSS — ini "kait" yang dicari
oleh JavaScript di bawah untuk MENGGANTI isi sel itu, tanpa perlu tahu index kolom ke berapa di
tabel (lebih tahan perubahan struktur tabel daripada `tr.children[2]` dst).

Untuk baris yang ditambah manual (tombol **"+ Tambah baris"**), nama tabelnya belum diketahui saat
JS membuat baris itu, jadi field-nya SELALU mulai sebagai `<input>` teks polos. Tombol 🔍 di
sampingnya memicu:
```javascript
async function loadColumnsForRow(btn) {
  const tr = btn.closest('tr');
  const idx = btn.dataset.idx;
  const tableName = tr.querySelector(`input[name="row_${idx}_source_table"]`).value.trim();
  const res = await fetch(`/api/configs/{{ config.id }}/table-columns?table=${encodeURIComponent(tableName)}&side=source`);
  const data = await res.json();
  const cols = data.columns;
  const opts = (selected) => cols.map(c => `<option value="${c}" ${selected.includes(c) ? 'selected' : ''}>${c}</option>`).join('');
  tr.querySelector('td[data-role="key_columns"]').innerHTML =
    `<select name="row_${idx}_key_columns" multiple size="3">${opts(['id'])}</select>...`;
  ...
}
```
Ini MENIRU persis apa yang `column_cells` macro lakukan di server, tapi di sisi klien: fetch
kolom via endpoint AJAX (§5.11), lalu `innerHTML` ke-4 sel (`key_columns`, `chunk_column`,
`date_column`, `exclude_columns`) diganti dari `<input>` jadi `<select>`/`<select multiple>` yang
baru dibangun. `tr.querySelector('td[data-role="..."]')` — inilah gunanya atribut `data-role` yang
disebut di atas: JS bisa menemukan sel yang tepat TANPA hardcode posisi kolom. Karena elemen HTML
dibuat lewat template string JS (bukan macro Jinja), kedua implementasi (server & klien) HARUS
dijaga tetap sinkron secara manual kalau strukturnya berubah — trade-off yang diterima demi tetap
"tanpa build step, tanpa framework" (lihat README "Deviasi dari arsitektur target").

**Pola 4 — Indikator loading global** (`base.html`, berlaku di semua halaman):
```javascript
(function () {
  var bar = document.getElementById('nav-loading-bar');
  function startLoading() {
    bar.classList.add('active');
    document.body.classList.add('nav-loading');
  }
  document.addEventListener('click', function (e) {
    var a = e.target.closest('a[href]');
    if (!a) return;
    var href = a.getAttribute('href');
    if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
    if (a.target === '_blank' || a.hasAttribute('download')) return;
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    startLoading();
  });
  document.addEventListener('submit', function (e) {
    if (e.defaultPrevented) return;
    startLoading();
    var btn = e.target.querySelector('button[type="submit"], input[type="submit"]');
    if (btn && !btn.disabled) {
      btn.disabled = true;
      var loadingText = '⏳ Memuat...';
      if (btn.tagName === 'BUTTON') { btn.innerHTML = loadingText; } else { btn.value = loadingText; }
    }
  });
  window.addEventListener('pageshow', function () {
    bar.classList.remove('active');
    document.body.classList.remove('nav-loading');
  });
})();
```
Latar belakang (lihat README): aplikasi ini TIDAK PUNYA SPA — setiap navigasi (klik link, submit
form) adalah full page reload biasa. Tanpa penanda apa pun, halaman yang lambat (drilldown tabel
besar, config dengan banyak tabel, dll) terlihat SAMA PERSIS dengan aplikasi yang macet, terutama
buat user non-teknis yang tidak tahu harus menunggu. Fix-nya deliberately sederhana, bukan spinner
per-komponen: sebuah progress bar tipis (`#nav-loading-bar`, CSS di `app.css`) langsung muncul
begitu ADA klik link ATAU submit form APAPUN, dan animasi CSS-nya (`transition: width 10s ...`)
membuatnya bergerak PELAN menuju 85% tanpa pernah benar-benar tahu kapan selesai — filosofinya sama
seperti progress bar YouTube/GitHub: memberi SINYAL GERAKAN, bukan progress yang akurat. Baru
berhenti kalau `pageshow` (halaman BARU selesai dimuat, ATAU halaman lama muncul lagi dari
back/forward-cache browser) — TIDAK PERNAH di-`false`-kan secara eksplisit saat sukses, karena
seluruh DOM (termasuk state JS ini) akan hilang begitu halaman baru benar-benar tiba.

Beberapa pengecualian di listener `click` supaya tidak salah trigger: `target="_blank"`/`download`
(tab baru, bukan navigasi halaman ini), `href="#..."`/`javascript:...` (bukan navigasi sungguhan),
klik dengan modifier key (Ctrl/Cmd/Shift/Alt — user sengaja mau buka di tab baru), dan
`e.button !== 0` (bukan klik kiri, mis. klik tengah/kanan).

Untuk form submit, tombol submit-nya JUGA di-disable dan teksnya diganti `"⏳ Memuat..."` — feedback
yang lebih eksplisit dari sekadar progress bar, sekaligus mencegah double-submit. Dropdown filter
kolom di tab Value Diffs (`table_drilldown.html`) sengaja diubah dari `this.form.submit()` ke
`this.form.requestSubmit()` supaya listener `submit` di atas ikut ter-trigger — pemanggilan
`HTMLFormElement.submit()` langsung (beda dari `requestSubmit()`) SENGAJA melewati event `submit`
sesuai spesifikasi DOM, jadi tanpa perubahan ini dropdown itu tidak akan memicu indikator loading.

**Tab "Tipe Kolom"** (`table_drilldown.html`) — satu-satunya tab yang TIDAK butuh variabel baru dari
route (§5.10): datanya (`rt.column_type_details`) sudah ada langsung di objek `rt` yang SUDAH dioper
ke semua tab lain. Template cukup:
```jinja
{% set column_type_details = rt.column_type_details or [] %}
{% set type_mismatch_count = column_type_details|selectattr('category_match', 'equalto', false)|list|length %}
```
lalu iterasi `column_type_details` biasa di dalam blok `{% if tab == 'tipekolom' %}`. Badge angka di
tab nav (`Tipe Kolom (N)`) HANYA menghitung `category_match == False` (kolom yang ADA di kedua sisi
tapi kategorinya BEDA) — bukan seluruh baris — karena itulah sinyal yang benar-benar berarti
"masalah", beda dari `category_match is none` (kolom cuma ada di satu sisi, sudah ditangani terpisah
oleh `extra_source_columns`/`extra_target_columns` di tab Ringkasan).

---

## 6. Skema Database (ERD Ringkas)

```
users
  id, email, password_hash, display_name, role, is_active

connections
  id, owner_id → users.id (nullable, lihat §9), name, engine, host, port, database, username,
  secret_encrypted (BLOB terenkripsi), params (JSON), status, last_tested_at

validation_configs
  id, owner_id → users.id (nullable, lihat §9), name, description,
  source_connection_id → connections.id, target_connection_id → connections.id,
  default_mode, settings (JSON)

config_tables
  id, config_id → validation_configs.id, source_table, target_table,
  key_columns (JSON list), chunk_column, date_column, exclude_columns (JSON list),
  mode_override, enabled

runs
  id, owner_id → users.id (nullable, lihat §9 — DIWARISKAN dari config.owner_id saat run dibuat,
  BUKAN dari user yang menekan tombol "Run" — supaya kalau admin men-trigger run milik user lain,
  hasilnya tetap muncul di dashboard PEMILIK config, bukan admin),
  config_id → validation_configs.id, trigger_type, mode, status,
  started_at, finished_at, summary (JSON: {tables_total, pass, fail, error})

run_tables
  id, run_id → runs.id, config_table_id → config_tables.id,
  source_table, target_table, status, tier_reached, mode,
  source_rows, target_rows, row_diff, agg_metrics (JSON), rl_metrics (JSON),
  investigate_query, queries (JSON: semua SQL yang dieksekusi)

findings_aggregate
  id, run_table_id → run_tables.id, category, column_name, metric, period,
  source_value, target_value, difference

findings_rowlevel
  id, run_table_id → run_tables.id, finding_type, row_key, column_name,
  source_value, target_value
```

---

## 7. Testing — Strategi & Cara Kerja

155 test di `tests/`, SEMUA jalan terhadap SQLite lokal (tidak ada dependensi jaringan/VPN):

| File | Yang diuji |
|---|---|
| `test_categories.py` | `get_category()` untuk semua tipe termasuk edge-case (`YEAR`, `Nullable(...)`, `Array(...)`, `Date32`); `values_match()` untuk presisi angka & normalisasi string tanggal |
| `test_rowlevel_comparator.py` | `composite_key`, `column_diff_mask` (termasuk toleransi presisi numerik & integer exactness), `compare_chunk_multi` — pakai DataFrame pandas buatan tangan, TANPA koneksi DB sama sekali |
| `test_aggregate_validator.py` | `AggregateValidator` end-to-end lewat fixture `conftest.py` yang membuat 2 file SQLite sungguhan (`identical_pair`, `orders_pair` dengan mismatch yang DISENGAJA disisipkan); `TestDateCeilingBounds` menguji `_date_ceiling_bounds()` lewat dialect palsu ringan (tanpa koneksi DB) — neither-side-clickhouse, salah satu sisi, kedua sisi (ambil batas yang lebih ketat) |
| `test_tiered_runner.py` | `run_table()` — semua kombinasi mode (`tiered`/`aggregate`/`rowlevel_missing`/`rowlevel_full`), eskalasi Tier 1→2, override mode per tabel, penanganan error; plus `TestTier2OverridesFalsePositiveTier1Fail` (fake `AggregateValidator`/`RowLevelValidator`) — Tier 2 bersih total meng-override FAIL Tier 1 jadi PASS di KEDUA mode (`full` maupun `missing` — keputusan user, lihat §4.13), Tier 2 dengan temuan nyata tetap FAIL; plus `TestTier2ErrorKeepsTier1Results` — Tier 2 crash tetap membawa hasil Tier 1 yang sudah selesai (§4.13/§5.8) |
| `test_period_findings.py` | Detail mismatch tab Periode (§4.8, README) — `mismatch_detail` terhitung benar pakai fixture nyata `orders_pair` (periode Δ=0 yang tetap ke-flag karena stat kolom lain beda, DAN periode dengan row count beda), `_parse_period_alias` untuk semua bentuk alias (`sum_x`, `min_len_x`, dst — termasuk kasus prefix yang lebih spesifik harus dicoba duluan), dan `_persist_aggregate_findings` memecah tiap periode jadi finding row-count (hanya kalau row count beda) + finding per metrik terpisah |
| `test_copy_keys.py` | Fitur copy key bermasalah (§5.10, README) — `_format_keys_for_sql` (angka polos vs di-quote, header composite key tanpa split-balik); endpoint `/keys` untuk missing_in_target/missing_in_source independen, value_diff DISTINCT lintas kolom (key yang sama di 2 kolom cuma muncul sekali) dan filter per-kolom, kind tak dikenal → 400, run_id salah → 404, hasil kosong → placeholder jelas |
| `test_app_run_service.py` | Regression test untuk bug threading (§5.8) — menjalankan 4 tabel KONKUREN berulang lewat `TestClient` FastAPI sungguhan untuk memastikan race condition benar-benar hilang |
| `test_connectors.py` | `get_primary_key()` SQLite end-to-end (single & composite PK, tabel tanpa PK eksplisit); `parse_sorting_key()` ClickHouse murni; `clickhouse_date_max()` untuk semua varian tipe (`Date`/`Date32`/`DateTime`/`DateTime64`, `Nullable(...)`, case-insensitive, fallback) — inilah yang menangkap bug urutan prefix `datetime` vs `date` (§4.5); `date_floor_1970`/`date_ceiling`/`date_max_bound` per dialect (MySQL, SQLite, ClickHouse), termasuk regression test khusus untuk guard `if(isNull(...), NULL, ...)` di ClickHouse (insiden `deleted_at` jadi `1970-01-01` — §4.5, README) — MySQL/ClickHouse `get_primary_key()` butuh server sungguhan jadi hanya logika parsing/klampingnya yang dites di sini |
| `test_discovery_service.py` | `list_columns()` (termasuk regression untuk bug `df.columns[-1]` pada tabel yang tidak ada), `columns_by_table()` (batching, dedup nama tabel, degradasi aman saat koneksi gagal total) |
| `test_run_service.py` | Regression test untuk insiden `database is locked` / Internal Server Error (§5.1, §5.8, README) — (1) WAL mode: thread writer nge-hold transaksi terbuka 2 detik sementara thread reader konkuren query di tengahnya, pastikan reader TIDAK error/blok; (2) `reap_orphaned_runs()`: Run dengan campuran status tabel (`pass`/`fail`/`running`/`pending`) di-reap jadi `failed`/`error` dengan pesan yang jelas, tabel yang sudah `pass`/`fail` TIDAK disentuh; (3) `reap_orphaned_runs()` no-op kalau tidak ada run yang macet |
| `test_table_drilldown.py` | Regression test untuk insiden halaman drilldown lambat (§5.10, README) — seed 250 baris value-diff / 210 baris missing-key lalu pastikan route HANYA mengembalikan 1 halaman (200 baris) per request, badge jumlah tetap benar (dari COUNT, bukan `len()` hasil terpotong), filter kolom + paginasi bekerja bersamaan, tab "Ringkasan" tidak memuat baris rowlevel findings sama sekali, dan tab "Tipe Kolom" merender perbandingan tipe + badge mismatch dengan benar (termasuk saat `column_type_details` masih kosong) |
| `test_error_logging.py` | Regression test untuk insiden tabel ERROR tanpa alasan (§5.8, README) — run sungguhan (1 tabel valid + 1 tabel tidak ada): tabel error HARUS punya `rt.error` terisi (bug: `_persist_table_result` dulu tidak menyalin `result.error`) dan `rt.event_log` yang berakhir dengan entri `traceback`; tabel pass punya trail fase tanpa traceback dan `error` NULL; tab "Log" me-render banner error + traceback lengkap |
| `test_config_status.py` | Halaman "Status Tabel" per config (§5.10, README) — status TERKINI per tabel digabung lintas run parsial (run 1 semua tabel, run 2 re-run parsial → latest tiap tabel dari run yang benar, riwayat FAIL lama tetap kelihatan); re-run per tabel membuat run berisi PERSIS 1 tabel itu (`table_filter`, `trigger_type=revalidate`) lalu redirect balik ke halaman status; tabel tak dikenal ditolak dengan flash error tanpa membuat run |
| `test_rowlevel_chunking.py` | Chunking sadar-kepadatan (§4.10, README insiden `datamart_logger_monitoring`) — tabel dense composite-key (100 id × 200 baris/id, target 5.000 baris/chunk) terpecah jadi 4 chunk row-bounded dengan hasil validasi tetap bersih; tabel sparse/normal (~1 baris/id) tetap 1 chunk legacy tanpa pesan "dense chunk column" |
| `test_run_cancel.py` | Regression test untuk 2 bug independen di insiden Cancel (§5.8, README) — `vc_run_table` di-fake jadi `time.sleep` (tanpa data sungguhan): (1) `table_concurrency=2` + 20 tabel, cancel diminta 0,2 detik setelah start, memverifikasi run selesai jauh lebih cepat daripada durasi broken-nya dan tabel yang belum sempat jalan berstatus `cancelled` bukan `pass`; (2) cancel diminta SEBELUM `start_run_async` dipanggil sama sekali (deterministik, tidak bergantung timing) — memverifikasi SEMUA tabel jadi `cancelled` (bukan tertinggal `running` selamanya, bug `_summarize_run` yang terlewat) |
| `test_column_type_details.py` | `_build_column_type_details()` murni — flag kategori mismatch untuk kolom shared, kolom yang cuma ada di satu sisi dapat `category_match=None` bukan `False`, `column_details` kosong menghasilkan list kosong |
| `test_asset_versioning.py` | Insiden cache CSS basi (§5.10, README) — `asset_version` (Jinja global, mtime `app.css`) ikut dirender di `<link>` stylesheet tiap halaman (`href="/static/app.css?v=<mtime>"`) sehingga perubahan CSS di masa depan otomatis dapat URL baru dan tidak lagi disajikan dari cache browser lama; nilai `asset_version` dicocokkan langsung ke mtime file `app.css` sungguhan |
| `test_rbac.py` | RBAC + data scoping (§9, ditambahkan 2026-07-21) — viewer bisa baca dashboard/configs/connections tapi dibalik ke `/login` (303) untuk route editor+ (bikin koneksi, bikin config); editor bisa bikin koneksi & config, `owner_id` ke-stamp; regression test route `tables-fragment` yang dulu tanpa auth sama sekali sekarang wajib login; editor A tidak bisa lihat/akses config-connection-run milik editor B (hilang dari list, `404` di akses langsung termasuk endpoint JSON `/api/runs/{id}/status`); admin bisa lihat DAN edit data milik user lain (bypass ownership, bukan cuma lihat) |

`tests/conftest.py` menyediakan fixture `orders_pair` & `identical_pair` — masing-masing membuat 2
file `.sqlite` sungguhan di direktori temp pytest (`tmp_path`), lalu membuka `Connector` sungguhan
ke keduanya. Ini DISENGAJA bukan mock — memastikan test benar-benar mengeksekusi jalur SQL asli
(lewat `SqliteDialect`), bukan hanya menguji logika Python di sekitarnya.

**Gotcha (ditemukan bertahap, dua bug independen) — sekarang dipusatkan di
`reload_app_with_fresh_db()` di `tests/conftest.py`**: beberapa test file butuh instance `app.*`
yang benar-benar terisolasi (DB sendiri, tidak nyampur dengan test lain) — caranya lewat
`importlib.reload(app.database)` untuk mengganti `DATABASE_URL` di tengah proses test (lihat §5.8
untuk KENAPA reload ini perlu). Reload ini ternyata rawan pecah dengan dua cara berbeda tergantung
urutan pytest mengimpor file test:

1. *Bug pertama (model registration)* — ditemukan saat menambah `test_discovery_service.py`.
   Awalnya `test_app_run_service.py` satu-satunya file yang mengimpor `app.*`, jadi aman. Begitu
   `test_discovery_service.py` (yang mengimpor `from app import models` di level modul) ditambahkan,
   pytest MENGIMPOR SEMUA file test lebih dulu saat fase *collection*, SEBELUM ada test yang benar-
   benar dijalankan — jadi `app.models` sudah ter-import (dengan `Base` yang LAMA) SEBELUM
   `test_app_run_service.py` sempat me-reload `app.database` (yang membuat `Base` BARU dan kosong).
   Reload `app.database` doang tidak menyeret `app.models` ikut reload (Python men-cache modul,
   `import app.models` sesudahnya adalah no-op) — akibatnya class ORM tetap terdaftar di `Base`
   LAMA, `Base.metadata.create_all()` di `init_db()` membuat database TANPA TABEL SAMA SEKALI, baru
   gagal belakangan sebagai `OperationalError: no such table: ...` yang membingungkan.

   Perbaikan pertama (reload `app.models` TANPA SYARAT setiap kali) ternyata malah membuat bug BARU:
   kalau `test_run_service.py` dijalankan sendirian/duluan, `app.models` belum pernah ter-import sama
   sekali sebelumnya — `import app.models` yang pertama itu SUDAH mengeksekusi definisi class
   terhadap `Base` yang baru saja di-reload, jadi me-reload LAGI persis sesudahnya adalah kesalahan:
   SQLAlchemy melempar `InvalidRequestError: Table 'users' is already defined for this MetaData
   instance`. Perbaikan final: cek `"app.models" in sys.modules` DULU sebelum memutuskan reload —
   hanya reload kalau modul itu SUDAH ter-import sebelumnya (oleh test file lain), bukan reload buta.

2. *Bug kedua (stale `SessionLocal` di background thread)* — ditemukan setelah bug pertama beres,
   saat menjalankan `test_run_service.py` lalu `test_app_run_service.py` lalu
   `test_discovery_service.py` berurutan: sebuah run macet di status `queued` selamanya
   (`AssertionError: run did not complete cleanly: queued / None`). Penyebabnya:
   `app/services/run_service.py` melakukan `from ..database import SessionLocal` di level modul —
   sebuah binding nama LANGSUNG ke objek `SessionLocal` yang ada SAAT ITU. Me-reload `app.database`
   membuat `SessionLocal` BARU, tapi modul lain yang sudah lebih dulu meng-import nama lama itu
   (`run_service`) TETAP memakai yang lama selama tidak ikut di-reload. Thread background run
   (`_execute_run`) jadi membuka Session ke file database test SEBELUMNYA, di mana run_id yang
   dicari tidak ada — thread itu diam-diam no-op, dan run tidak pernah ter-update.

Kedua bug ini punya pola yang sama (module-level state yang di-cache Python, hanya benar kalau
di-reload di urutan/kondisi yang tepat), jadi solusinya digabung jadi satu helper
`reload_app_with_fresh_db(database_url, secret_key=None)` di `conftest.py` yang menangani ketiganya
sekaligus: reload `app.database` (selalu), reload `app.models` (HANYA kalau sudah ter-import
sebelumnya), `init_db()`, lalu reload `app.services.run_service` (HANYA kalau sudah ter-import
sebelumnya, dengan alasan yang sama seperti `app.models`). `test_app_run_service.py` dan
`test_run_service.py` sekarang sama-sama memakai helper ini lewat `from conftest import
reload_app_with_fresh_db`, bukan reload manual masing-masing. Diverifikasi lewat 5+ urutan file
berbeda dan 3x run full-suite berturut-turut — konsisten 61/61 lulus. Pelajarannya: kalau ada test
file baru yang mengimpor/reload modul `app.*` di level modul, JANGAN menulis ulang logika reload
sendiri — pakai helper ini, dan kalau ada modul BARU yang melakukan `from ..database import X` di
level modul (binding langsung, bukan lookup dinamis), tambahkan modul itu ke helper dengan pola
"reload hanya kalau sudah ter-import" yang sama.

Jalankan: `.venv\Scripts\python.exe -m pytest -v`

---

## 8. Cara Menambah Fitur Umum

**Menambah engine database baru (mis. PostgreSQL)**:
1. Buat `validation_core/connectors/postgres.py` — `PostgresDialect(Dialect)` +
   `PostgresConnector(Connector)`, isi method yang perlu di-override (lihat tabel di §4.3).
2. Tambah 1 baris di `validation_core/connectors/registry.py::_REGISTRY`.
3. Selesai — `AggregateValidator`, `RowLevelValidator`, dan seluruh `app/` TIDAK perlu diubah sama
   sekali, karena semuanya hanya bicara lewat interface `Connector`/`Dialect`.

**Menambah field setting baru** (mis. toleransi baru):
1. Tambah field di `validation_core/models.py::RunSettings`.
2. Tambah nama field itu ke tuple di `app/services/run_service.py::build_run_settings()`.
3. (Opsional) tambah input-nya di `config_detail.html` bagian "⚙ Lanjutan".

**Menambah kategori temuan baru di drilldown**:
1. Tambah logika ekstraksi di `app/services/run_service.py::_persist_aggregate_findings()` (atau
   `_persist_rowlevel_findings()`), simpan dengan `category` baru.
2. Filter kategori itu di `app/routers/ui.py::table_drilldown()`.
3. Tambah tab baru di `table_drilldown.html`.

---

## 9. RBAC & Per-User Data Scoping (2026-07-21)

Ditambahkan sebagai bagian dari migrasi ke VPS produksi (§10) — sebelumnya aplikasi ini SATU-USER
saja secara efektif (semua orang yang login melihat SEMUA data yang sama, `require_role` didefinisikan
tapi tidak dipasang di mana pun). Dua pertanyaan dari pemilik produk yang menentukan desainnya:
"kalau ada user baru, dia lihat config/hasil validasi yang lama tidak?" → TIDAK (setiap user cuma
lihat miliknya sendiri) — dan "connection (koneksi database) juga per-user, bukan cuma
config/run?" → YA, ketiganya (Connection, ValidationConfig, Run) di-scope sama.

### 9.1 Matriks Role × Route

Konvensi (didefinisikan di `auth.py`, §5.4): **viewer** = `require_login` polos (baca saja);
**editor** = `require_role("editor")` (kelola MILIKNYA SENDIRI); **admin** selalu bypass — baik
role check (`require_role` manapun) MAUPUN ownership check (`check_owner`/`scope_query`). Tidak ada
route yang admin-only murni — pembeda admin bukan "bisa akses route yang lain tidak bisa", tapi
"bisa akses/ubah DATA yang lain tidak bisa".

| Area | Route | Role minimum | Scoping |
|---|---|---|---|
| Dashboard | `GET /dashboard` | viewer | `scope_query` di 3 query Run (recent/running/trend) + JOIN ke Run di query problem-tables |
| Connections | `GET /connections` | viewer | `scope_query` |
| | `GET /connections/new`, `POST /connections` | **editor** | `owner_id=user.id` di-stamp saat create |
| | `GET .../{id}/edit`, `POST .../{id}` | **editor** | `check_owner` |
| | `POST .../{id}/test` | **editor** | `check_owner` |
| | `POST .../{id}/delete` | **editor** | `check_owner` |
| Configs | `GET /configs` | viewer | `scope_query` + `filter_by(is_archived=False)` |
| | `GET /configs/new`, `POST /configs` | **editor** | `owner_id=user.id` di-stamp; connection sumber/target divalidasi `check_owner` juga (cegah config nempel ke connection user lain lewat form yang di-tamper) |
| | `GET /configs/{id}`, `POST .../suggest`, `POST .../copy-from`, `POST .../tables` | **editor**\* | `check_owner` pada config (dan pada config LAIN yang jadi sumber "copy-from") |
| | `GET /configs/{id}/status`, `POST .../rerun-table` | **editor**\* | `check_owner` |
| | `POST /configs/{id}/run` | **editor** | `check_owner`; Run baru mewarisi `owner_id` dari config (lihat §9.2) |
| Runs | `GET /runs/{id}`, `GET .../export.xlsx`, `GET .../tables/{id}`, `GET .../tables/{id}/keys` | viewer\*\* | `check_owner` pada Run |
| | `GET /runs/{id}/tables-fragment` | viewer | `check_owner` — **route ini dulu TANPA auth sama sekali**, ditemukan & diperbaiki saat audit ini |
| | `POST .../cancel`, `POST .../resume` | **editor** | `check_owner` |
| API | `GET /api/runs/{id}/status` | viewer | `check_owner` |
| | `GET /api/configs/{id}/table-columns` | **editor** | `check_owner` (dipakai HANYA dari alur config-builder yang editor-only) |

\* `config_detail`/`config_table_status` sendiri (GET, lihat isinya) sebenarnya `require_login`
(viewer boleh LIHAT), tapi aksi form di halaman yang sama (suggest/copy-from/save tables/run/rerun)
semuanya editor-only — jadi viewer bisa membuka halaman config, tapi setiap tombol aksinya akan
memantulkannya ke `/login` kalau ditekan.
\*\* Sama untuk halaman run detail — semua tab/lihat adalah `require_login`, cuma cancel/resume yang
editor-only.

Route publik TANPA auth sama sekali (tidak berubah): `GET/POST /login`, `POST /logout`, `GET /`
(redirect ke dashboard).

### 9.2 Mekanisme Scoping

```python
def scope_query(query, model, user):
    if is_admin(user):
        return query
    return query.filter(model.owner_id == user.id)

def check_owner(obj, user):
    if obj is None or (not is_admin(user) and obj.owner_id != user.id):
        raise HTTPException(status_code=404)
```
Dua fungsi ini (`auth.py`, §5.4) menutup SEMUA jalur akses: `scope_query` untuk daftar (list), 
`check_owner` untuk objek tunggal yang diambil lewat `db.get(Model, id)`. `check_owner` melempar
`404` — BUKAN `403` — supaya user B tidak bisa membedakan "id ini tidak ada" dari "id ini ada tapi
bukan punya saya" hanya dari kode status; keduanya terlihat identik dari luar.

**Tabel turunan (`ConfigTable`, `RunTable`, `FindingAggregate`, `FindingRowLevel`) TIDAK punya
`owner_id` sendiri** — kepemilikannya implisit lewat parent yang SUDAH di-`check_owner` sebelum kode
menyentuh anak-anaknya. Contoh: `table_drilldown()` memanggil `check_owner(run, user)` pada `Run`
di awal fungsi, BARU KEMUDIAN mem-query `FindingRowLevel` lewat `run_table_id` — kalau `run` bukan
milik user, fungsi sudah berhenti (404) sebelum baris `FindingRowLevel` mana pun ter-query. Pola ini
konsisten di semua route yang menyentuh data turunan.

**`Run.owner_id` diwariskan dari `ValidationConfig.owner_id`, bukan dari user yang menekan tombol
"Run"** (`run_service.create_run()`, §5.8):
```python
run = models.Run(owner_id=config.owner_id, config_id=config.id, ...)
```
Alasannya: karena editor cuma bisa men-trigger run pada config MILIKNYA SENDIRI (`check_owner`
sudah menjamin ini SEBELUM `create_run` dipanggil), `config.owner_id` dan `user.id`-nya editor
SELALU sama untuk kasus editor. Bedanya baru terlihat kalau **admin** yang men-trigger run pada
config milik user lain (admin bypass ownership check, jadi BISA) — dalam kasus itu, hasil run tetap
harus muncul di dashboard PEMILIK ASLI config-nya (yang mungkin sedang login bersamaan), bukan
"menghilang" ke akun admin. Mewarisi dari config, bukan dari user pemicu, menjamin ini.

### 9.3 Migrasi Data Lama (Backfill)

Database yang sudah berjalan LAMA sebelum fitur `owner_id` ada (§10.7 — dipindah dari laptop
pengembangan ke VPS) sudah punya baris `connections`/`validation_configs`/`runs` dengan `owner_id`
kosong. `database.py::init_db()` (§5.1) menangani ini otomatis di setiap startup:
```python
for table in ("connections", "validation_configs", "runs"):
    cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
    if "owner_id" not in cols:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN owner_id INTEGER"))
admin_row = conn.execute(text("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")).fetchone()
if admin_row:
    admin_id = admin_row[0]
    for table in ("connections", "validation_configs", "runs"):
        conn.execute(text(f"UPDATE {table} SET owner_id = :aid WHERE owner_id IS NULL"), {"aid": admin_id})
```
`ALTER TABLE ADD COLUMN` (kalau kolomnya belum ada — cek `PRAGMA table_info`, sama seperti backfill
`column_type_details`/`event_log` di §5.1) lalu `UPDATE ... WHERE owner_id IS NULL` meng-assign
SEMUA baris yang belum punya pemilik ke admin PERTAMA yang ditemukan. Ini **no-op yang aman** di dua
skenario berbeda: (1) database FRESH (instalasi baru) — belum ada baris `connections`/dst sama
sekali saat `init_db()` jalan (dipanggil SEBELUM `_bootstrap_admin()` di `main.py`, §5.12, jadi juga
belum ada admin untuk di-assign-i — query `admin_row` kosong, blok `if admin_row:` dilewati), (2)
database yang SUDAH pernah di-backfill sebelumnya — `WHERE owner_id IS NULL` tidak match apa pun.
`scripts/seed_demo.py` (§5.7) juga diperbarui untuk men-stamp `owner_id` pada Connection/Config yang
dibuatnya, konsisten dengan pola ini.

---

## 10. Deployment — Docker, Caddy, dan VPS (2026-07-21)

Aplikasi ini SEBELUMNYA hanya dijalankan lewat `uvicorn app.main:app --reload` langsung di laptop
pengembangan — tidak ada `Dockerfile`, tidak ada `docker-compose.yml`, tidak ada `git` sama sekali
(lihat README "Deviasi dari arsitektur target"). Bagian ini mendokumentasikan containerization dan
migrasi ke VPS produksi (`43.134.129.64`, direktori `~/lidvalid`), TERMASUK dua gotcha jaringan
nyata yang perlu waktu untuk didiagnosis.

### 10.1 `Dockerfile`

```dockerfile
FROM python:3.12-slim
...
RUN useradd --create-home --uid 1000 lidvalid && mkdir -p /app/data && chown -R lidvalid:lidvalid /app
USER lidvalid
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```
Single-stage (bukan multi-stage build) — aplikasi ini kecil (tidak ada langkah kompilasi/bundling
terpisah dari `pip install`), jadi kompleksitas multi-stage tidak sepadan manfaatnya di skala ini.
`useradd --uid 1000` dipilih SPESIFIK karena user `ubuntu` di VPS target juga `uid=1000` — volume
`./data:/app/data` (compose, di bawah) jadi otomatis punya kepemilikan yang cocok tanpa perlu
`chown` manual tambahan di host tiap kali container di-recreate. `build-essential`/`libffi-dev`
di-install (lalu TIDAK dihapus di layer yang sama, sengaja — single-stage, bukan multi-stage) untuk
jaga-jaga `cryptography` butuh kompilasi dari source di platform tertentu (jarang terjadi, image
`python:3.12-slim` biasanya sudah dapat manylinux wheel, tapi murah untuk disiapkan).

`.dockerignore` mengecualikan `.venv/`, `.git/`, `data/` (runtime, di-mount lewat volume — BUKAN
di-bake ke image), `tests/`, dan dua file dokumentasi besar (README/TECHNICAL) yang tidak perlu ada
di image produksi.

### 10.2 `docker-compose.yml` — app + Caddy

```yaml
services:
  app:
    build: .
    env_file: .env
    environment:
      - LIDVALID_ENV=production
    volumes:
      - ./data:/app/data
    networks: [internal]
    extra_hosts:
      - "host.docker.internal:172.28.1.1"

  caddy:
    image: caddy:2-alpine
    ports: ["80:80", "443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    networks: [internal]

networks:
  internal:
    driver: bridge
    ipam:
      config:
        - subnet: 172.28.1.0/24
          gateway: 172.28.1.1
```
`app` **tidak pernah mem-publish port ke host** (tidak ada `ports:` di service `app`) — satu-satunya
jalan masuk adalah lewat `caddy`, yang meneruskan ke `app:8000` lewat network Docker internal
(`reverse_proxy app:8000` di Caddyfile, §10.3). Ini diverifikasi langsung: `curl` ke
`http://<ip>:8000` dari LUAR VPS timeout/refused, sementara `https://<ip>.sslip.io` (lewat Caddy)
normal — port 8000 memang tidak pernah ter-expose ke internet.

`networks.internal.ipam` (subnet+gateway EKSPLISIT, bukan dibiarkan Docker meng-auto-assign) adalah
perbaikan dari masalah nyata — lihat §10.6 untuk cerita lengkapnya (kenapa subnet harus tetap,
bukan auto).

### 10.3 `Caddyfile` — HTTPS Otomatis Tanpa Domain

```
{
    email raffi.ainul@badr-interactive.com
}

43.134.129.64.sslip.io {
    reverse_proxy app:8000
    encode gzip
}
```
Domain sungguhan belum ada saat deploy pertama — **sslip.io** dipakai sebagai gantinya: layanan DNS
publik yang meng-embed alamat IP langsung di hostname-nya (`<ip>.sslip.io` selalu resolve ke `<ip>`
itu sendiri), jadi berperilaku PERSIS seperti domain asli untuk keperluan ACME (Let's Encrypt)
tanpa perlu membeli/mendaftarkan apa pun. Caddy otomatis meminta & memperbarui sertifikat TLS lewat
tantangan HTTP-01 di port 80 — tidak ada langkah manual sama sekali (`docker compose logs caddy`
menunjukkan `"certificate obtained successfully"` pada deploy pertama). Kalau nanti ada domain
sungguhan: cukup ganti baris hostname di Caddyfile, `docker compose restart caddy` — Caddy akan
meminta sertifikat baru untuk domain itu otomatis, tidak ada perubahan lain yang dibutuhkan.

### 10.4 Environment Variables Produksi (`.env`, dari `.env.example`)

```
LIDVALID_ENV=production
LIDVALID_SECRET_KEY=<harus SAMA dengan kunci yang dulu mengenkripsi database yang dimigrasi>
LIDVALID_ADMIN_EMAIL=      # kosong OK -- database migrasi sudah punya admin, bootstrap tidak jalan
LIDVALID_ADMIN_PASSWORD=
```
`.env` **tidak** ikut ke git (`.gitignore`) — dibuat manual di VPS. Untuk deployment INI spesifik
(migrasi database yang SUDAH ADA, bukan instalasi baru kosong), `LIDVALID_SECRET_KEY` diisi dengan
kunci yang SAMA seperti `data/secret.key` di laptop pengembangan (lihat §5.3 kenapa ini kritis —
kunci yang beda = semua `Connection.secret_encrypted` yang ter-migrasi jadi sampah tak
terdekripsi). `LIDVALID_ADMIN_EMAIL/PASSWORD` boleh dibiarkan kosong karena `_bootstrap_admin()`
(§5.12) hanya jalan kalau tabel `users` kosong — database yang dimigrasi sudah punya baris admin.

### 10.5 VPS — Hardening di Server *Shared*

VPS target (`43.134.129.64`, Ubuntu 24.04) BUKAN server khusus untuk aplikasi ini — sudah menjalankan
beberapa project lain milik pemiliknya (di port 3111, 3121, 8090, plus Tailscale-nya sendiri untuk
keperluan tidak terkait). Konsekuensinya, hardening di sini LEBIH HATI-HATI daripada server kosong:

- **UFW**: `default deny incoming`, TAPI dengan `allow` eksplisit untuk SEMUA port yang sudah dipakai
  project lain (22 SSH, 80/443 buat LidValid, PLUS 3111/3121/8090 project lain) — supaya menyalakan
  firewall tidak diam-diam mematikan akses ke project lain yang sedang berjalan.
- **fail2ban**: jail `sshd` default saja diaktifkan (belum ada jail khusus Caddy/`/login` — itu
  butuh access log JSON Caddy dengan format tertentu, belum disiapkan di iterasi ini).
- **Password SSH SENGAJA TIDAK DIMATIKAN** (`PasswordAuthentication yes` tetap) — keputusan eksplisit
  pemilik server, karena dia kadang login dari device lain yang belum tentu punya key terdaftar.
  Ini penyimpangan dari praktik "matikan password auth, key-only" yang lazim direkomendasikan,
  tapi konsisten dengan pola dokumen ini (§8, README) untuk mencatat keputusan yang MEMANG disengaja
  meski berbeda dari default yang "lebih aman di atas kertas".

### 10.6 Konektivitas ke Database Staging — SSH Reverse Tunnel

ClickHouse (`clickhouse-data.smile5.xyz`) dan MySQL RDS staging cuma bisa diakses lewat VPN privat
(OpenVPN, terpisah dari Tailscale yang ada di VPS untuk keperluan lain). VPS tidak (belum) punya
profile OpenVPN sendiri — solusi SEMENTARA yang dipilih: **SSH reverse tunnel** dari laptop
pengembang (yang OpenVPN-nya aktif) ke VPS:
```bash
ssh -i ~/.ssh/lidvalid_vps -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
  -R 0.0.0.0:8123:clickhouse-data.smile5.xyz:8123 \
  -R 0.0.0.0:3306:app-smile5-uat.cxeycqoo6axz.ap-southeast-3.rds.amazonaws.com:3306 \
  ubuntu@43.134.129.64
```
`-N` (tanpa remote command, murni port forwarding), `-R <port>:<host>:<port>` (reverse — port itu
dibuka DI SISI SERVER/VPS, diteruskan lewat tunnel balik ke laptop, yang lalu meneruskannya lagi
lewat OpenVPN-nya ke host aslinya). **Trade-off yang disadari dan diterima**: koneksi ke database
staging HANYA hidup selama proses SSH ini jalan DAN OpenVPN laptop aktif — kalau laptop mati/sleep
atau SSH terputus, VPS kehilangan akses ke staging (tapi aplikasi tetap bisa diakses publik dan
menampilkan data yang SUDAH tersimpan — cuma tidak bisa test/jalankan validasi baru terhadap
database staging sungguhan sampai tunnel dinyalakan lagi). Jalur permanen (OpenVPN langsung di VPS,
profile terpisah dari milik laptop) direncanakan sebagai langkah lanjutan — kalau itu terjadi,
`Connection` di UI tinggal diganti host-nya dari `host.docker.internal` (di bawah) ke hostname asli
(`clickhouse-data.smile5.xyz` dst) TANPA perlu ubah kode/redeploy apa pun.

**Dua gotcha jaringan yang ditemukan & diperbaiki saat setup ini:**

1. **`host.docker.internal` resolve ke gateway bridge yang SALAH.** Docker Compose punya fitur
   `extra_hosts: ["host.docker.internal:host-gateway"]` yang, secara teori, membuat hostname itu
   otomatis menunjuk ke gateway network Docker milik CONTAINER itu sendiri. Di host Linux ini,
   nilai `host-gateway` ternyata resolve ke gateway bridge Docker DEFAULT (`172.17.0.1`, `docker0`)
   — BUKAN gateway network custom `internal` yang container `app` ini benar-benar terhubung ke
   sana (yang saat itu auto-assigned Docker ke `172.18.0.1`). Container tidak punya rute apa pun ke
   `172.17.0.1` (tidak terhubung ke bridge itu sama sekali), jadi `host.docker.internal:8123` selalu
   timeout. **Perbaikan**: pin subnet+gateway network `internal` secara MANUAL di
   `docker-compose.yml` (`ipam.config`, §10.2) jadi `172.28.1.0/24` / gateway `172.28.1.1` yang
   stabil, lalu set `extra_hosts` ke IP gateway itu SECARA EKSPLISIT (bukan `host-gateway` lagi).
2. **UFW memblokir traffic dari container ke host lewat bridge**, bahkan setelah gateway-nya benar.
   `ufw default deny incoming` ternyata berlaku juga untuk paket yang datang dari network bridge
   Docker menuju proses HOST (di sini: listener `sshd` untuk reverse tunnel, bind ke `0.0.0.0:8123`)
   — bukan cuma untuk trafik dari internet luar. Docker mengelola iptables-nya SENDIRI (biasanya
   untuk *port publishing* container keluar), tapi TIDAK otomatis meng-exempt trafik container→host
   yang masuk lewat jalur ini dari kebijakan UFW. **Perbaikan**: `ufw allow from 172.28.1.0/24 to
   any port 8123 proto tcp` (dan port 3306) — mengizinkan HANYA dari subnet bridge itu (bukan dari
   internet), jadi tunnel tetap tidak ter-expose publik.

Kedua perbaikan ini diverifikasi dengan test koneksi langsung dari DALAM container:
```python
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(5)
s.connect(("host.docker.internal", 8123))  # ClickHouse — REACHABLE setelah fix
```

### 10.7 Migrasi Data — Dari Laptop ke VPS

Database SQLite yang sudah berisi data sungguhan (17 config, 19 koneksi, 30 run, ~138 MB) dipindah
UTUH ke VPS, bukan mulai dari kosong:
1. `tar -czf ... --exclude .venv --exclude __pycache__ --exclude .env .` di laptop (termasuk `data/`
   DAN `.git/` — TIDAK di-exclude, supaya riwayat commit ikut pindah).
2. `scp` tarball ke VPS, `tar -xzf` ke `~/lidvalid`.
3. `.env` dibuat manual di VPS (§10.4) dengan `LIDVALID_SECRET_KEY` yang SAMA seperti sumbernya.
4. `docker compose up -d --build` — `_bootstrap_admin()` (§5.12) mendeteksi tabel `users` SUDAH
   berisi 1 baris (admin lama), TIDAK membuat admin baru — kredensial & seluruh data ter-migrasi
   apa adanya.
5. Akun admin yang ter-migrasi masih memakai email brand LAMA (`admin@validahub.local`, dari
   SEBELUM rebrand ke LidValid, §1/README) — email SATU baris ini diupdate manual lewat query ORM
   langsung (bukan lewat `create_user.py`, karena itu match by-email dan email lamanya belum
   diketahui sebagai "LidValid" oleh siapa pun sampai diperiksa manual) jadi `admin@lidvalid.local`,
   password direset lewat `scripts/create_user.py` (§5.12b) supaya ada kredensial yang diketahui.

**Catatan mtime WAL**: SQLite dalam mode WAL (§5.1) bisa punya file pendamping `lidvalid.sqlite-wal`
/`-shm` yang berisi data BELUM ter-checkpoint ke file utama. Sebelum tar dibuat, pastikan proses
aplikasi yang memegang WAL itu sudah berhenti bersih (`docker compose down` bukan `kill -9`) —
kalau tidak, database yang di-transfer bisa kehilangan write terbaru yang masih di WAL. Di migrasi
ini, `docker compose down` sebelum tar sudah cukup men-checkpoint WAL kembali ke file utama (file
`-wal`/`-shm` tidak ada lagi setelahnya, ukuran file utama justru bertambah — tanda checkpoint
berhasil), jadi tidak perlu langkah manual tambahan.
