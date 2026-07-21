# LidValid

Satu platform web validasi data — gabungan dua tool lama:

- **`validation-data`** ([d:\data-pipeline-batch\validation-data](../../data-pipeline-batch/validation-data)) — validasi agregat/statistik (row count, completeness, uniqueness, statistik kolom, breakdown periode)
- **`validation_database`** ([d:\Project\validation_database](../validation_database)) — validasi row-level (missing ID dua arah, value diff per-row via chunked-by-id)

Ini adalah implementasi kerja dari paket dokumen perencanaan di
[`d:\data-pipeline-batch\docs\validation-platform\`](../../data-pipeline-batch/docs/validation-platform/)
(PRD, arsitektur, data model, API spec, wireframe, roadmap). Baca dokumen itu dulu untuk konteks visi
lengkap — dokumen ini menjelaskan **apa yang sudah benar-benar berjalan** di implementasi ini.

> Untuk penjelasan teknis mendalam — arsitektur, alur data, dan pembahasan tiap file/fungsi/baris
> kode — baca **[TECHNICAL.md](TECHNICAL.md)**. Dokumen ini (README) fokus ke "apa & cara pakai";
> TECHNICAL.md fokus ke "bagaimana cara kerjanya di dalam".

Fitur intinya: **Tiered Validation** — jalankan agregat (murah) untuk semua tabel, lalu otomatis
jalankan row-level (presisi, lebih mahal) hanya untuk tabel yang FAIL. Satu run bisa langsung
menjawab "tabel mana yang beda" *dan* "row/kolom mana persisnya" tanpa pindah tool.

## Coba sekarang (2 menit)

```powershell
cd d:\Project\lidvalid
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# buat 2 database SQLite contoh + config + 1 run lengkap, siap dilihat
.venv\Scripts\python.exe scripts\seed_demo.py

# jalankan server
.venv\Scripts\uvicorn app.main:app --reload
```

Buka **http://127.0.0.1:8000** → login `admin@lidvalid.local` / `admin123` (dicetak juga di konsol saat
pertama kali jalan). Config **"Demo: Contoh Validasi"** sudah berisi 1 run selesai — tabel
`ws_materials` PASS, `ws_orders` FAIL (5 row sengaja dihilangkan + 2 value diff) dengan drilldown
lengkap sampai ke row & kolom yang beda.

Untuk memvalidasi data **sungguhan** (MySQL/ClickHouse asli): buat Connection baru di `/connections`
dengan engine `mysql`/`clickhouse`, lalu buat Config seperti biasa. Server perlu akses jaringan yang
sama seperti Dagster (VPN ke ClickHouse K8s bila perlu).

## Menjalankan test

```powershell
.venv\Scripts\python.exe -m pytest -v
```

40 test, semuanya terhadap SQLite lokal (tidak butuh MySQL/ClickHouse nyata) — mencakup edge-case
lintas-engine yang di-port dari kedua tool lama (lihat `tests/test_categories.py`,
`tests/test_rowlevel_comparator.py`, `tests/test_aggregate_validator.py`, `tests/test_tiered_runner.py`),
plus satu regression test (`tests/test_app_run_service.py`) untuk bug threading nyata yang ditemukan
saat membangun ini — lihat catatan di bawah.

## Struktur

```
lidvalid/
├── README.md             # dokumen ini — apa & cara pakai
├── TECHNICAL.md           # arsitektur, alur data, penjelasan kode per-file/fungsi/baris
├── validation_core/      # Engine — port murni Python, tidak tahu web/DB metadata sama sekali
│   ├── categories.py      # get_category, values_match, META_COLUMNS (fix bug YEAR→UInt16)
│   ├── models.py           # TableSpec, RunSettings (dataclass biasa)
│   ├── events.py           # ProgressEvent
│   ├── connectors/         # Dialect (mysql/clickhouse/sqlite) + Connector — 1 implementasi 5 report
│   ├── aggregate/          # AggregateValidator — port db_validator.py (Report 1-5)
│   ├── rowlevel/           # compare_chunk_multi + RowLevelValidator — port chunked-by-id
│   ├── runner/             # run_table() — Tiered Validation (aggregate → rowlevel utk FAIL) + retry
│   └── excel_export.py     # export .xlsx kompatibel format lama (fitur transisi, bukan output utama)
├── app/                  # FastAPI backend + UI server-rendered
│   ├── main.py             # entry point, bootstrap admin user
│   ├── database.py         # SQLAlchemy engine/session (SQLite default)
│   ├── models.py           # ORM (13 tabel, versi SQLite-friendly dari data-model.md)
│   ├── security.py         # enkripsi Fernet + hashing password
│   ├── auth.py             # session auth
│   ├── services/           # connections/discovery/run/export service — jembatan ORM ↔ validation_core
│   ├── routers/            # ui.py (halaman + form POST), api.py (JSON polling)
│   └── templates/          # Jinja2 — dashboard, connections, configs, run detail/report, drilldown
├── tests/                # pytest — 40 test, semua jalan lokal via SQLite, tanpa DB nyata
└── scripts/seed_demo.py  # bikin demo end-to-end sekali klik
```

## Yang sudah nyata jalan

- **Tiered validation** end-to-end: aggregate 5-report, eskalasi otomatis ke row-level chunked-by-id
  untuk tabel FAIL, hasil tergabung dalam satu run.
- **3 engine**: MySQL, ClickHouse (paritas produksi kedua tool lama), plus SQLite (baru — untuk
  demo/testing lokal tanpa VPN).
- **Semua edge-case fix dari kedua tool lama dipertahankan** (lihat
  `docs/validation-platform/01-analisa-existing.md` §2.3 di project asal): floor tanggal pre-1970,
  `BINARY LOWER(TRIM())` untuk collation MySQL vs ClickHouse, `_ceil_stat` untuk presisi AVG,
  `toString()` untuk sentinel date, normalisasi `.000`, backtick reserved keyword, `FINAL` untuk
  ReplacingMergeTree, escaping `%%` khusus MySQL+SQLAlchemy (lihat komentar di
  `validation_core/connectors/mysql.py`).
- **Clamp tanggal 2 arah (floor 1970 + ceiling max ClickHouse) di SEMUA validasi terkait date**:
  setiap kolom date/timestamp yang dibandingkan (Report 2 uniqueness, Report 3 min/max/datediff,
  Report 4/5 period breakdown, investigate query) di-floor ke `1970-01-01` DAN di-cap ke batas
  maksimum yang benar-benar bisa disimpan ClickHouse untuk tipe kolom itu (`Date` → `2149-06-06`,
  `Date32` → `2299-12-31`, `DateTime` → `2106-02-07 06:28:15`, `DateTime64` → `2299-12-31`) — pada
  KEDUA sisi perbandingan, bukan cuma sisi yang kebetulan MySQL, supaya nilai yang di luar jangkauan
  itu (yang di ClickHouse pasti sudah ke-clamp diam-diam saat ingestion) tidak muncul sebagai false
  mismatch di sisi lain yang masih punya nilai asli. Lihat `validation_core/connectors/clickhouse.py::clickhouse_date_max()`.
- **1 bug lama diperbaiki**: MySQL `YEAR` sekarang dipetakan ke kategori numeric (dulu fallback ke
  string, menyebabkan crash `length(UInt16)` di ClickHouse — `validation-data/CLAUDE.md` known issue #1).
- **Config builder** via web: koneksi terenkripsi (Fernet), pemetaan tabel dengan editor baris
  (tambah/hapus tanpa reload), auto-suggest mapping by prefix. Import YAML config lama belum ada
  (lihat tabel "Deviasi dari arsitektur target" di bawah).
- **Auto-suggest key columns dari DDL (termasuk composite key)**: saat "Auto-suggest dari koneksi"
  dipakai, `key_columns` otomatis diisi dari PRIMARY KEY tabel (MySQL, lewat
  `INFORMATION_SCHEMA.KEY_COLUMN_USAGE`) atau sorting key (ClickHouse, lewat
  `system.tables.sorting_key`) — kalau PK/sorting key-nya composite (mis. `order_id, material_id`),
  otomatis kepakai sebagai composite key, urutan kolom dijaga sesuai definisi DDL. Fallback ke `id`
  kalau tidak terdeteksi.
- **Key/chunk/date/exclude column jadi dropdown, bukan ketik manual**: untuk tabel yang sudah ada di
  config dan untuk hasil suggestion (auto-suggest maupun salin dari config lain), field "Key
  columns" & "Exclude" jadi `<select multiple>` (Ctrl+klik untuk pilih lebih dari satu — composite
  key tinggal klik, bukan ketik dipisah koma) dan "Chunk col"/"Date col" jadi `<select>` — semuanya
  diisi dari kolom ASLI tabel itu di source connection. Untuk baris yang ditambah manual ("+ Tambah
  baris"), ada tombol 🔍 di sebelah nama tabel yang memuat daftar kolomnya via AJAX begitu nama
  tabelnya diketik.
- **Salin pemetaan tabel dari config lain**: di halaman config, ada dropdown untuk memilih config
  lain lalu tombol "Salin pemetaan" — menyalin `key_columns`/`chunk_column`/`date_column`/
  `exclude_columns`/`mode_override` yang sudah pernah diisi untuk tabel yang sama, tabel yang sudah
  ada di config saat ini dilewati. Hasilnya masuk sebagai baris belum-tersimpan untuk direview dulu
  sebelum "Simpan Pemetaan Tabel" — bukan langsung menimpa.
- **Run**: trigger dari UI, eksekusi background (thread pool, konkurensi per-tabel konfigurabel),
  retry otomatis 3× untuk error transient, cancel, resume (skip tabel yang sudah pass/fail).
  Progres dipoll tiap 2 detik (lihat "Live progress" di bawah).
- **Drilldown**: per tabel — Ringkasan, Temuan Agregat, Tipe Kolom (Report 2/3's perbandingan tipe
  mentah source vs target per kolom, termasuk kolom yang KATEGORI-nya beda — sebelumnya cuma dipakai
  diam-diam untuk skip stat comparison, sekarang kelihatan), Periode (mismatch saja), Missing Keys,
  Value Diffs, SQL (semua query yang dieksekusi, untuk audit).
- **Indikator loading global**: setiap navigasi (klik link, submit form) langsung menampilkan progress
  bar tipis di atas halaman + tombol submit berubah jadi "⏳ Memuat..." — aplikasi ini full page-reload
  tanpa SPA, jadi tanpa penanda ini halaman yang lambat (drilldown tabel besar, dll) terlihat sama
  persis dengan aplikasi yang macet buat user non-teknis.
- **Export Excel** kompatibel format lama (Summary + sheet temuan per tabel).
- **Auth** sederhana (session, 1 admin bootstrap otomatis di run pertama).
- **Dashboard interaktif**: seluruh baris tabel (run terbaru, daftar config, riwayat run, daftar
  tabel per-run) bisa diklik langsung ke halaman detailnya — bukan cuma teks link kecil. Ada widget
  tren pass-rate (bar chart dari run-run terakhir) dan "Tabel Paling Bermasalah" (pasangan
  source/target yang paling sering FAIL/ERROR lintas riwayat run).
- **Filter kolom di tab Value Diffs**: dropdown berisi semua kolom yang kena value diff + jumlahnya,
  supaya tidak perlu scroll ratusan baris untuk cek kolom tertentu.
- **Toleransi presisi angka di row-level value diff**: kolom numerik (float/decimal) dibandingkan
  dengan toleransi relatif+absolut (mirip `math.isclose`), bukan exact-compare — menghilangkan
  false-positive akibat dua engine menyerialisasi nilai float yang SAMA dengan presisi desimal
  berbeda (mis. `482.437346437` vs `482.43734643734643`). Kolom integer murni (tanpa NULL) tetap
  dibandingkan exact — lihat `validation_core/rowlevel/comparator.py::column_diff_mask`. Berlaku
  untuk pasangan engine apa pun (MySQL vs ClickHouse, ClickHouse vs ClickHouse, dst) karena
  perbandingannya bekerja di atas data yang sudah ditarik ke pandas, bukan di level SQL.

## Bug nyata yang ditemukan & diperbaiki saat membangun ini

Saat menjalankan 2 tabel **konkuren** lewat `run_service.py`, salah satu tabel kadang gagal dengan
`IndexError: tuple index out of range` yang membingungkan (dari dalam kode SQLAlchemy sendiri, bukan
dari validation_core). Akar masalahnya: kode awal membaca `source_conn.database` dan `run.mode`
(atribut ORM) dari **dalam worker thread**. SQLAlchemy meng-*expire* semua atribut setelah
`db.commit()`, jadi baca berikutnya memicu lazy-reload lewat Session yang sama — dan satu `Session`
tidak aman dipakai bersamaan dari banyak thread. Race ini kadang merusak data row di level cursor.

Perbaikannya: semua nilai yang dibutuhkan worker thread (`source_db_name`, `target_db_name`,
`run_mode`) ditangkap sebagai **string biasa** di thread utama, sebelum thread pool dibuka — worker
thread sekarang tidak pernah menyentuh objek SQLAlchemy sama sekali, hanya `validation_core` +
koneksi DB mentah. Regression test-nya ada di `tests/test_app_run_service.py` (menjalankan 4 tabel
konkuren berulang untuk memastikan racenya benar-benar hilang, bukan cuma "biasanya lolos").

### Insiden: `database is locked` — Internal Server Error saat run panjang berjalan

Saat sebuah run **68 tabel ClickHouse asli** berjalan lama (mode `tiered`, jam-an) di background,
request HTTP LAIN yang cuma butuh baca (mis. cek login) ikut gagal dengan
`sqlite3.OperationalError: database is locked`. Penyebabnya: SQLite secara default (*rollback
journal* / mode `DELETE`) mengambil lock EKSKLUSIF atas seluruh file selama transaksi tulis
berlangsung — commit demi commit dari worker validasi cukup untuk membuat request baca lain
(halaman mana pun) ikut ter-block sampai timeout driver (5 detik) lalu gagal.

**Perbaikan**: `app/database.py` sekarang mengaktifkan **WAL mode** (`PRAGMA journal_mode=WAL`) +
`busy_timeout=30000` lewat SQLAlchemy connect event — WAL membiarkan pembaca jalan terus SELAGI
satu penulis aktif (persis pola beban tool ini: banyak baca pendek, satu validasi background yang
menulis). Sudah diverifikasi lewat test (`tests/test_run_service.py`) yang benar-benar membuka
transaksi tulis lalu mencoba baca bersamaan — sebelum fix ini gagal, sesudahnya lolos instan.

**Kesalahan tambahan yang terjadi saat investigasi** (dicatat biar tidak terulang): melihat
`run_tables.progress = 0.0` di database untuk tabel yang sudah berjam-jam berjalan sempat disalah
tafsir sebagai "proses macet". Padahal `progress` **cuma di-update saat tabel SELESAI**, bukan
selagi berjalan — jadi tabel yang lagi diproses lambat (menunggu ClickHouse) SELALU tampil
`progress: 0.0` sampai benar-benar tuntas, sama persis tampilannya dengan tabel yang proses-nya
sudah mati. Salah baca ini sempat menyebabkan proses server yang SEDANG memvalidasi asli
dihentikan paksa — pekerjaan komputasi yang sedang berjalan di memory saat itu **hilang, tidak bisa
di-resume**. Perbaikan pencegahan: `run_service.reap_orphaned_runs()` sekarang dipanggil sekali di
setiap startup server — kalau ada Run yang statusnya masih `"running"`/`"queued"` padahal proses
BARU saja mulai (berarti thread pemiliknya sudah mati bersama proses sebelumnya, entah karena
restart/crash), langsung ditandai `"failed"` dengan pesan jelas alih-alih diam-diam terlihat
"hidup" selamanya. ***Progress tabel per-chunk yang akurat secara real-time*** sayangnya tetap
belum ada di database (cuma ada di event bus in-memory, hilang begitu server restart) — item lanjutan
yang masuk akal untuk fase berikutnya adalah mengalirkan checkpoint chunk ke `run_tables` juga, bukan
cuma ke event bus, supaya "masih jalan wajar" vs "macet" bisa dibedakan tanpa perlu tebak-tebakan.

### Insiden: halaman detail tabel (Value Diffs / Missing Keys) sangat lambat dibuka

User melaporkan tab "Value Diffs"/tab lain di halaman detail hasil validasi lama banget dibuka.
Investigasi menemukan **dua penyebab independen yang saling menumpuk**:

1. **Bug arsitektur (permanen, selalu ada)**: route `table_drilldown` (`app/routers/ui.py`) memuat
   **SELURUH** relationship `rt.rowlevel_findings` lewat ORM — bisa sampai 10.000-20.000+ baris per
   tabel (dibatasi `rowlevel_sample_cap`, default 10.000 per jenis finding) — lalu me-render SEMUA
   baris itu jadi `<tr>` HTML dalam SATU response, dan ini terjadi di SETIAP tab dibuka (termasuk tab
   "Ringkasan" yang sama sekali tidak menampilkan data itu). Contoh nyata: tabel
   `dashboard_delivery_time` di database production punya 20.117 baris value-diff.
2. **Kontensi resource (kondisional, saat itu terjadi)**: pada saat dilaporkan, ada run 68 tabel
   ClickHouse asli (config "All table datamart") yang sedang aktif berjalan di proses server yang
   SAMA — proses uvicorn memakai ~12,9 GB RAM (dari total 22GB sistem, sisa cuma ~2,7GB free), pola
   yang sama dengan insiden OOM yang pernah ditangani sebelumnya (lihat commit
   `dashbaord_stock_sufficiency_monthly`). Dibuktikan langsung: request test ke tab Value Diffs untuk
   tabel di atas makan waktu **lebih dari 5 menit** sebelum akhirnya dihentikan paksa — dibandingkan
   query DB murni untuk baca 20 ribu baris findings itu sendiri yang cuma 0,3 detik saat server idle.

**Perbaikan (untuk bug #1)**: `table_drilldown` sekarang HANYA menghitung COUNT/`GROUP BY` (murah,
tidak menghidrasi objek ORM) untuk badge jumlah di setiap tab, dan HANYA mengambil satu HALAMAN (200
baris) findings sungguhan untuk tab yang SEDANG dibuka — tab lain tidak menyentuh tabel
`findings_rowlevel` sama sekali. Ditambahkan juga index komposit
`ix_findings_rowlevel_run_table_type` pada `(run_table_id, finding_type)` (kolom yang selalu dipakai
di `WHERE`) — sebelumnya tabel ini (120 ribuan baris dan terus tumbuh) di-scan penuh tanpa index sama
sekali. Karena `init_db()` cuma `create_all()` (tidak ada Alembic/migration di project ini,
`create_all` tidak pernah mengubah tabel yang sudah ada), index ini di-backfill lewat
`CREATE INDEX IF NOT EXISTS` eksplisit di `app/database.py` — aman dijalankan berkali-kali, no-op
kalau sudah ada. Regression test-nya di `tests/test_table_drilldown.py`.

**Bug #2 (kontensi resource) belum diperbaiki** — butuh investigasi terpisah tentang kenapa satu run
68 tabel bisa memakai ~13GB RAM (kandidat: ukuran chunk per tabel, tidak ada limit memory per worker,
DataFrame pandas yang tidak dilepas setelah tabel selesai) sebelum bisa diputuskan perbaikannya.

### Insiden: tombol "Cancel" tidak menghentikan run yang sedang jalan

User melaporkan run yang sudah diklik Cancel tetap jalan terus. Penyebabnya ada di
`run_service.py::_execute_run()`: SEMUA tabel di-submit ke `ThreadPoolExecutor` di awal (non-blocking,
selesai dalam hitungan milidetik meski ada 68 tabel), lalu kode menunggu lewat
`as_completed(futures)` — yang **BLOCKING sampai SETIAP future selesai**, tanpa peduli status cancel
sama sekali. Flag cancel (`bus.is_cancel_requested()`) cuma dicek di 2 tempat: (1) sebelum submit
tiap tabel — window ini sudah lama tertutup begitu run berjalan beberapa menit, karena submit tidak
menunggu apa pun; dan (2) SETELAH `as_completed()` return — yaitu setelah SEMUA tabel selesai,
membuat pengecekan itu tidak berguna. `validation_core` sendiri juga tidak punya checkpoint
cancellation di dalam loop chunk-nya, jadi tabel yang SEDANG diproses memang tidak bisa dihentikan
paksa di tengah jalan (ThreadPoolExecutor tidak bisa membunuh thread yang sudah berjalan).

**Perbaikan**: cek flag cancel dipindah ke DALAM loop `as_completed`, dan begitu pertama kali
terdeteksi, panggil `future.cancel()` untuk SEMUA future — ini cuma berhasil untuk tabel yang
BELUM mulai dieksekusi (masih antre di belakang batch yang sedang jalan, dibatasi
`table_concurrency`). Tabel yang sudah mulai tetap harus selesai secara alami (tidak bisa dipaksa
berhenti), tapi semua yang masih antre langsung dilewati alih-alih ditunggu — mengubah "tunggu semua
68 tabel selesai" jadi "tunggu cuma batch yang sedang jalan (biasanya `table_concurrency`, default 4)
selesai". Regression test-nya di `tests/test_run_cancel.py` (mem-fake `vc_run_table` supaya lambat
tanpa perlu data sungguhan, lalu memverifikasi run berhenti jauh lebih cepat daripada durasi semua
tabel, dan tabel yang belum sempat jalan benar-benar berstatus `cancelled` bukan `pass`).

**Bug KEDUA yang ketemu saat mengetes perbaikan di atas**: begitu Cancel beneran punya efek, jalur
kode LAMA yang menangani "tabel yang belum sempat di-submit sama sekali ke executor" (relevan kalau
cancel diminta SEBELUM tabel manapun mulai jalan) ternyata sudah lama rusak juga, cuma tidak pernah
ketahuan karena sebelumnya Cancel nyaris tidak pernah benar-benar berefek. Baris
`run.summary = _summarize_run(run_tables, db)` memanggil `db.refresh(rt)` PER TABEL — tapi flip
status ke `"cancelled"` yang baru saja dilakukan di blok cleanup SEBELUMNYA belum di-commit, jadi
`refresh()` diam-diam MEMBUANG perubahan itu dan memuat ulang status LAMA (`"running"`) dari
database. Hasilnya: run selesai dengan status `"cancelled"`, tapi SEMUA tabelnya tetap tertulis
`"running"` selamanya — persis pola bug yang sudah pernah dicatat & dihindari di
`reap_orphaned_runs()` (lihat komentarnya), cuma terlewat di sini. Perbaikan: `_summarize_run()`
TIDAK PERLU `db.refresh(rt)` sama sekali — objek `run_tables` yang diterimanya SUDAH up-to-date
di memory (baik dari loop `as_completed` maupun cleanup fallback), refresh di situ cuma berisiko,
tidak pernah perlu. Regression test khusus (deterministik, tidak bergantung timing):
`tests/test_run_cancel.py::test_cancel_before_any_table_starts_marks_everything_cancelled` — minta
cancel SEBELUM `start_run_async` dipanggil sama sekali, supaya jalur "belum sempat submit" ini SELALU
kena, bukan cuma kadang-kadang tergantung seberapa cepat scheduler OS kebetulan jalan.

### Insiden: `deleted_at` (NULL di kedua sisi) terdeteksi mismatch — floor tanggal ternyata merusak NULL

User melaporkan (dengan screenshot run production sungguhan): kolom `deleted_at` yang NULL di KEDUA
sisi (source & target) tetap muncul sebagai mismatch di tab Temuan Agregat — `min`/`max` menunjukkan
source `None` (benar, NULL) tapi target `1970-01-01 00:00:00.000` (SALAH — harusnya juga `None`), dan
`uniqueness` beda (`0.0` vs `0.1667`).

Penyebabnya persis di fix clamp tanggal yang baru ditambahkan (lihat bagian "Yang sudah nyata jalan"):
`ClickHouseDialect.date_floor_1970()` membungkus kolom dengan `greatest(expr, toDateTime('1970-01-01
00:00:00'))`. Sejak **ClickHouse 24.12**, `greatest()`/`least()` **MENGABAIKAN argumen NULL** (kalau
salah satu argumen NULL, hasilnya adalah argumen yang LAIN — bukan NULL) — kebalikan dari
MySQL/SQLite yang benar mengikuti standar SQL (`GREATEST(NULL, x)` = `NULL`). Akibatnya:
`greatest(NULL, toDateTime('1970-01-01 00:00:00'))` di ClickHouse mengembalikan `1970-01-01
00:00:00`, BUKAN `NULL` — NULL asli diam-diam diubah jadi tanggal floor, merusak `MIN()`/`MAX()` (yang
sekarang menghitung nilai palsu, bukan mengabaikannya seperti NULL asli) dan `COUNT(DISTINCT ...)`
(yang jadi menghitung 1 nilai distinct tambahan yang seharusnya tidak ada, karena `COUNT(DISTINCT)`
memang mengabaikan NULL — begitu NULL "disamarkan" jadi nilai konkret, ia ikut terhitung).

**Diverifikasi langsung ke ClickHouse production** (bukan cuma dugaan) — query pembanding pada
`raw_ws_orders` (3.331.669 baris): ekspresi LAMA (`greatest` polos) mengembalikan **0 NULL** meski
**3.331.100 baris genuinely NULL** di kolom itu; ekspresi BARU (dengan guard) mengembalikan **persis
3.331.100 NULL** — cocok 100% dengan jumlah NULL asli.

**Perbaikan**: bungkus `greatest`/`least` dengan `if(isNull(expr), NULL, ...)` di
`ClickHouseDialect.date_floor_1970()`/`date_ceiling()` — secara eksplisit menegaskan ulang semantik
NULL alih-alih bergantung pada versi ClickHouse yang kebetulan dipakai (perilaku ini SUDAH pernah
berubah sekali sebelumnya di 24.12, bisa berubah lagi). MySQL/SQLite tidak perlu perubahan — versi
`GREATEST`/`MAX` scalar mereka memang sudah benar dari awal. Regression test:
`tests/test_connectors.py::TestDialectDateClamping` (menguji bentuk SQL persis, termasuk guard
`if(isNull(...))`-nya).

### Insiden: Internal Server Error saat membuat config dengan nama yang sudah dipakai

User melaporkan Internal Server Error saat membuat config baru. Log server menunjukkan
`sqlalchemy.exc.IntegrityError: UNIQUE constraint failed: validation_configs.name` — route
`config_create()` (`app/routers/ui.py`) langsung `db.add()` + `db.commit()` tanpa cek dulu apakah
nama config itu sudah dipakai (`ValidationConfig.name` punya `unique=True`), jadi begitu ada
duplikat, exception dari database mental sebagai 500 mentah alih-alih pesan error yang jelas.
**Perbaikan**: cek `db.query(...).filter_by(name=name).first()` DULU sebelum insert (pola yang sama
seperti `connection_delete()` sudah pakai untuk kasus serupa) — kalau nama sudah ada, redirect balik
ke form dengan flash error "Nama config sudah dipakai, pilih nama lain", bukan crash.

### Perubahan perilaku: Tier 2 yang bersih total sekarang meng-override FAIL palsu dari Tier 1

Diminta user langsung setelah insiden `deleted_at`/NULL di atas: kalau Tier 1 (aggregate stats)
menunjukkan mismatch tapi Tier 2 (row-level, lebih presisi — bandingkan per baris) menemukan **NOL**
missing key DAN **NOL** differing value, tabel itu seharusnya PASS, bukan tetap FAIL. Sebelumnya,
`validation_core/runner/tiered.py::run_table()` memperlakukan Tier 2 di mode `tiered` murni sebagai
"drill-down" — begitu Tier 1 bilang FAIL, status TETAP FAIL apa pun hasil Tier 2, row-level cuma
buat menunjukkan DI MANA letak masalahnya, bukan buat mengoreksi verdict. Ini jadi masalah nyata:
insiden `deleted_at` di atas MEMBUKTIKAN Tier 1 bisa false-positive (bug versi ClickHouse, presisi
angka, dll — lihat juga insiden toleransi presisi & floor tanggal sebelumnya), dan kalau Tier 2 sudah
membuktikan data sebenarnya identik, FAIL yang "menempel" dari Tier 1 jadi alarm palsu yang
membingungkan.

**Perbaikan (versi final, setelah 2 iterasi)**: kalau Tier 2 hasilnya bersih total (0 missing di
kedua sisi + 0 differing value), status akhir jadi PASS — meng-override FAIL dari Tier 1, di **KEDUA
mode Tier 2** (`full` maupun `missing`).

Versi pertama perbaikan ini membatasi override hanya untuk mode `full`, dengan alasan mode `missing`
(otomatis dipakai tabel besar di atas `full_mode_row_threshold`, 5 juta baris) cuma cek keberadaan
key dan TIDAK PERNAH membandingkan isi kolom — `differing_values_count == 0` di mode itu trivially
true, bukan bukti data cocok. Batasan itu langsung kena kasus nyata: `dim_ws_entity_material_activities`
(71 JUTA baris → otomatis mode `missing`) tetap FAIL meski Tier 2-nya bersih, dan user secara
eksplisit memutuskan (dua kali): **Tier 2 bersih = PASS, titik, apa pun modenya**. Trade-off yang
diterima secara sadar: tabel besar yang FAIL Tier 1-nya disebabkan murni oleh perbedaan NILAI kolom
(bukan baris hilang) sekarang akan terbaca PASS — dianggap layak karena dalam praktiknya Tier 1
terbukti berkali-kali menghasilkan false positive (insiden NULL→1970, presisi float, dsb), sementara
kasus "nilai drift tapi jumlah baris persis sama" jauh lebih jarang. Regression test:
`tests/test_tiered_runner.py::TestTier2OverridesFalsePositiveTier1Fail` (4 skenario: `full`+bersih →
PASS, `full`+beda nyata → FAIL, `missing`+bersih → PASS, `missing`+ada baris hilang → FAIL).

### Fitur: halaman "Status Tabel" per config + re-run per tabel

Masalah usability nyata dari alur re-run parsial: setiap re-run membuat Run BARU yang hanya berisi
tabel yang di-re-run — jadi tidak ada satu tempat pun yang menunjukkan "posisi terkini SEMUA tabel"
begitu run mulai parsial; user harus menggabungkan beberapa run di kepala. **Solusi**: halaman
`/configs/{id}/status` ("Status Tabel", tombolnya ada di halaman config, halaman run, dan drilldown
tabel) yang menampilkan matriks per-tabel: status TERKINI lintas semua run (bukan cuma run terakhir),
run mana yang menghasilkannya, riwayat status per-run (chip kecil, terbaru di kiri, maks. 15 run,
klik untuk buka drilldown-nya), KPI ringkas (pass/fail/error), dan **tombol ↻ Re-run per baris**.
Tombol re-run per tabel juga ada di header drilldown tabel. Setelah re-run per tabel, redirect
kembali KE HALAMAN STATUS (bukan ke halaman run barunya yang cuma 1 tabel) — dan halaman ini
auto-refresh tiap 5 detik selagi masih ada tabel yang berjalan. Tabel yang sudah dihapus dari config
tapi masih punya riwayat tetap ditampilkan (ditandai), supaya riwayatnya tidak diam-diam hilang.
Test: `tests/test_config_status.py`.

### Perubahan perilaku: Resume = pilihan scope re-run (all / fail / error / non-PASS)

Berevolusi dua kali atas permintaan user. Awalnya tombol "Resume" hanya menjalankan ulang tabel yang
BELUM SELESAI (status error/pending/running sisa run terputus) — tabel `fail` dianggap "selesai" dan
dilewati. Lalu diubah jadi "re-run semua non-PASS". Bentuk finalnya sekarang: **dropdown pilihan
scope di sebelah tombol Re-run** di halaman run — "Semua non-PASS (fail + error + cancelled)"
(default), "Hanya FAIL", "Hanya ERROR", atau "Semua tabel". Kalau scope yang dipilih tidak cocok
dengan tabel mana pun (mis. "Hanya ERROR" padahal tidak ada tabel error), TIDAK ada run baru yang
dibuat — muncul flash message, bukan diam-diam menjalankan semua tabel (fallback berbahaya yang ada
di kode lama: `table_filter=remaining or None` berarti list kosong → `None` → SEMUA tabel).
Implementasi: `run_service.resume_run(db, run, scope)` + `RESUME_SCOPES`, test:
`tests/test_run_service.py::test_resume_scopes_select_the_right_tables` &
`test_resume_with_empty_scope_selection_creates_nothing`.

### Insiden: tabel ERROR tanpa alasan apa pun — dan lahirnya tab "Log"

User meminta log kenapa sebuah tabel bisa ERROR. Investigasi menemukan bug yang lebih dalam dari
sekadar "belum ada fiturnya": **pesan errornya memang tidak pernah disimpan sama sekali**.
`tiered.run_table()` menangkap semua exception level-tabel sendiri dan mengembalikan objek hasil
NORMAL dengan `status="ERROR"` dan alasannya di `.error` — tapi `_persist_table_result()` tidak
pernah menyalin `.error` itu ke kolom `run_tables.error` (jalur `err is not None` di `_execute_run`
yang MENYIMPAN error hampir tidak pernah aktif, karena exception sudah keburu ditangkap di lapisan
bawah). Hasilnya: tabel ERROR tersimpan dengan status `error` tapi pesan NULL — tidak ada petunjuk
apa pun di UI.

**Perbaikan** (dua lapis):
1. `rt.error = result.error` di `_persist_table_result()` — alasan singkat sekarang tersimpan dan
   tampil di header drilldown.
2. **Tab "Log" baru** di drilldown tabel: jejak proses per-tabel (fase Tier 1/Tier 2, checkpoint
   chunk, retry) yang sebelumnya cuma hidup di event bus in-memory (hilang begitu run selesai/server
   restart) sekarang direkam per tabel — dibatasi 200 event terakhir (`collections.deque(maxlen=200)`)
   supaya tabel besar dengan ratusan checkpoint chunk tidak membengkakkan DB — dan dipersist ke kolom
   JSON baru `run_tables.event_log` saat tabel selesai. Kalau tabel ERROR, **traceback Python
   lengkap** ditambahkan sebagai entri terakhir (field baru `TableRunResult.error_trace`), jadi
   akar masalah bisa dilihat langsung di UI tanpa buka log server. Regression test:
   `tests/test_error_logging.py` (run sungguhan dengan 1 tabel valid + 1 tabel tidak ada → tabel
   error harus punya `error` terisi + trail berakhir dengan traceback; tabel pass punya trail tanpa
   traceback; tab Log me-render semuanya).

### Insiden: `boolean value of NA is ambiguous` — tabel ERROR karena kolom satu-sisi

Tabel `datamart_orders_smdv` ERROR dengan pesan `boolean value of NA is ambiguous`. Berkat tab "Log"
yang baru ditambahkan, traceback lengkapnya langsung kelihatan di UI: crash di
`_date_ceiling_bounds()` pada `if not col_type` — kolom `master_updated_at` cuma ada di SATU sisi
(target), jadi tipe sisi satunya di merged-schema DataFrame adalah missing value. Masalahnya: missing
value pandas bisa datang sebagai `np.nan` (float) ATAU `pd.NA` tergantung dtype DataFrame-nya, dan
`not pd.NA` melempar TypeError (pandas sengaja menolak konversi NA ke boolean) — guard
`isinstance(col_type, float)` yang ada tidak pernah sempat jalan karena `not col_type` di KIRI-nya
dievaluasi duluan. **Perbaikan**: cek `pd.isna()` DULU sebelum test truthiness apa pun
(`if col_type is None or pd.isna(col_type): continue`). Diverifikasi langsung terhadap schema
production tabel itu (34 kolom termasuk yang satu-sisi) — lolos semua. Regression test:
`tests/test_aggregate_validator.py::test_pandas_na_missing_type_does_not_crash` (ketiga varian
missing: `pd.NA`, `np.nan`, `float("nan")`).

**Lapisan kedua dari insiden yang sama** (ketemu setelah fix di atas jalan): kolom satu-sisi itu
(`master_updated_at`) ternyata juga adalah **`date_column` yang dikonfigurasi** untuk tabel ini —
dan breakdown bulanan/tahunan memakai `date_column` di query KEDUA sisi, jadi sisi target (yang
tidak punya kolom itu) langsung gagal `UNKNOWN_IDENTIFIER` dan meng-ERROR-kan seluruh tabel.
**Perbaikan**: sebelum laporan apa pun jalan, `AggregateValidator.run()` sekarang mengecek
`date_column` ada di KEDUA schema — kalau tidak, semua fitur berbasis tanggal (breakdown
bulanan/tahunan, investigate query, filter rentang tanggal) dilewati untuk tabel itu (Report 1-3
tetap jalan penuh), dengan catatan jelas di tab SQL ("Period Breakdown SKIPPED" + alasan + saran
perbaiki config). Memfilter hanya di sisi yang PUNYA kolomnya bukan opsi — dua sisi akan
dibandingkan atas himpunan baris yang berbeda. Diverifikasi terhadap tabel production yang sama:
sekarang selesai tanpa crash dengan verdict FAIL yang GENUINE (13.036 vs 16.012 baris — selisih
nyata ~3.000 baris yang memang seharusnya ketahuan). Regression test:
`tests/test_aggregate_validator.py::TestDateColumnMissingOnOneSide`.

### Insiden: `StreamFailureError` kosong — chunk fetch besar terputus di 600 detik

Tabel `datamart_logger_monitoring` ERROR dengan `StreamFailureError:` yang pesannya KOSONG. Dari tab
Log kelihatan timeline-nya: fetch chunk mulai 10:38:12, mati 10:48:48 — **636 detik, persis melewati
`send_receive_timeout=600`** di connector ClickHouse. Akar masalah gandanya: (1) chunking row-level
membagi berdasarkan RENTANG id (`asset_rtmd_id` 0–22827, jauh di bawah `id_chunk_size` 2 juta) —
tapi tabel ini composite-key dengan BANYAK baris per id × 105 kolom, jadi seluruh tabel jatuh ke
SATU chunk yang fetch-nya >10 menit; (2) begitu timeout memutus koneksi di tengah stream,
`clickhouse_connect` melempar `StreamFailureError` berisi teks apa pun yang sempat diterima dari
server — yaitu KOSONG, karena servernya tidak sempat bilang apa-apa.

**Perbaikan**: (1) `send_receive_timeout` dinaikkan 600 → 3600 detik, konsisten dengan timeout
read/write 3600 yang sudah dipakai connector MySQL, dan bisa di-override per koneksi lewat
`params.send_receive_timeout`; (2) `StreamFailureError` kosong sekarang diganti pesan yang bisa
ditindaklanjuti (kemungkinan penyebab + saran perkecil `id_chunk_size` + potongan query-nya), dan
sengaja mengandung kata "connection" supaya diklasifikasikan transient oleh retry runner — blip
jaringan/VPN di tengah stream akan di-retry otomatis seperti error koneksi lain. Regression test:
`tests/test_connectors.py::TestClickHouseStreamFailureMessage`.

**Babak kedua insiden yang sama — timeout bukan akar masalahnya.** Dengan timeout 3600 pun, tabel
yang sama gagal lagi: fetch-nya jalan hampir 2 JAM lalu stream-nya rusak di tengah
(`unrecognized data found in stream`). Akar masalah sesungguhnya: **chunking row-level membagi
berdasarkan RENTANG id** (`id_chunk_size`, default 2 juta), yang asumsinya ~1 baris per id
(auto-increment PK). Tabel composite-key mematahkan asumsi itu: `asset_rtmd_id` cuma 0–22827 tapi
tiap id punya ~139 baris (3,17 juta baris total × 105 kolom) → SELURUH tabel jatuh ke SATU chunk.
**Perbaikan fundamental: chunking sadar-kepadatan** — query MIN/MAX kini juga mengambil COUNT(*),
dan kalau kepadatannya > 1,5 baris/id, rentang id per chunk dikecilkan supaya satu chunk membawa
±`rowlevel_target_chunk_rows` BARIS (default 500 ribu; bisa dioverride per config lewat `settings`).
Tabel normal (~1 baris/id) tidak berubah sama sekali — shrink hanya pernah MENGECILKAN chunk, tidak
pernah membesarkan. Untuk tabel insiden: 3,17 juta baris → 7 chunk × ±453 ribu baris (fetch
beberapa menit per chunk, jauh di bawah timeout, memory bounded) alih-alih satu fetch 2 jam.
Regression test: `tests/test_rowlevel_chunking.py` (tabel dense composite-key → terpecah jadi
beberapa chunk row-bounded dengan hasil validasi tetap benar; tabel sparse/normal → tetap 1 chunk
legacy tanpa pesan "dense").

**Babak ketiga — desync stream `unrecognized data found in stream`.** Setelah chunking benar (7
chunk, 3 chunk pertama lancar), chunk 4 masih gagal: fetch macet ~14 menit lalu parser
`clickhouse_connect` kehilangan posisi di stream — hex yang dilaporkan jelas-jelas data float64
mentah yang terbaca di offset yang salah. Ini pola desync klasik **stream HTTP terkompresi** yang
lewat proxy/LB (host deployment ini di belakang domain/proxy): satu frame boundary yang di-rechunk/
terganggu, dan semua byte setelahnya salah baca. **Perbaikan** (dua lapis): (1) **kompresi respons
dimatikan default-nya** (`compress=False` di `get_client`; bisa dinyalakan lagi per koneksi lewat
`params {"compress": true}`) — bandwidth naik tapi failure mode-nya hilang; (2) **self-heal di
connector**: `StreamFailureError` meninggalkan sesi HTTP client dalam keadaan tak tentu, jadi retry
di client yang sama percuma — connector sekarang MEMBANGUN ULANG client (koneksi baru) dan
menjalankan ulang query-nya sampai 2×, baru menyerah dengan pesan jelas; plus marker "stream"
ditambahkan ke klasifikasi transient supaya sisa kegagalan masih dapat retry level-tabel.
**Diverifikasi langsung**: chunk 4 yang gagal dua kali itu di-refetch dengan connector baru —
1,16 juta baris × 110 kolom selesai bersih dalam 4,5 menit tanpa desync. Regression test:
`tests/test_connectors.py::TestClickHouseStreamSelfHeal`.

**Insiden: `NO_COMMON_TYPE` — kolom chunk String berisi digit dikira numerik.** Tabel
`datamart_wms_report_waste_bags` ERROR: `waste_bag_id` bertipe `Nullable(String)` di kedua sisi,
tapi isinya digit semua (mis. `101012026`). Deteksi "kolom numerik atau bukan" lama memakai
`int(min_value)` — string digit lolos konversi, jadi runner salah mengira kolom itu numerik:
(1) query range `WHERE waste_bag_id >= 101012026` memakai literal integer, yang MySQL toleransi
tapi ClickHouse tolak (`NO_COMMON_TYPE` String vs UInt32); (2) MIN/MAX string itu urutan
LEKSIKOGRAFIS, jadi rentang id-nya ngawur — insiden ini menghitung 499.912 chunk untuk tabel 254
ribu baris. **Perbaikan**: cek dtype nilai MIN/MAX yang dikembalikan driver — kalau `str`/`bytes`
(kolomnya string, apa pun isinya), jatuh ke jalur single full-table scan yang memang sudah ada untuk
kolom non-numerik, dengan pesan log yang menjelaskan alasannya. Tabel insiden cuma 254 ribu baris ×
35 kolom — full scan aman. Regression test:
`tests/test_rowlevel_chunking.py::test_digit_string_chunk_column_falls_back_to_full_scan`.

**Bug ikutan yang ketahuan dari insiden ini** (pertanyaan user: "kenapa validasi aggregate-nya ikut
hilang padahal errornya di Tier 2?"): tabel yang ERROR di Tier 2 menampilkan `rows: — / —` dan tab
Temuan Agregat kosong — padahal Tier 1 SUDAH selesai penuh sebelum Tier 2 gagal. Penyebabnya: blok
`except` di `tiered.run_table()` membangun `TableRunResult` ERROR polos tanpa membawa hasil apa pun —
`aggregate_result` cuma variabel lokal di dalam `_do()` yang ikut mati bersama exception-nya.
**Perbaikan**: hasil yang SUDAH selesai di-stash ke dict `partial` begitu tiap fase tuntas (hasil
Tier 1, tier yang tercapai, semua query yang sempat dieksekusi) — jalur error sekarang melampirkan
semua itu, jadi tabel ERROR tetap menampilkan row count, temuan agregat, tipe kolom, periode, dan
tab SQL dari Tier 1-nya, plus error + traceback Tier 2-nya di tab Log. Regression test:
`tests/test_tiered_runner.py::TestTier2ErrorKeepsTier1Results`.

### Fitur: tab Periode menunjukkan kolom/metrik yang mismatch, bukan cuma "periode ini beda"

User bingung (wajar): tab Periode cuma menampilkan Source rows/Target rows/Δ, jadi periode dengan
**Δ = 0** (jumlah baris IDENTIK di kedua sisi) tetap muncul di daftar mismatch tanpa alasan yang
kelihatan — seperti alarm palsu. Penyebabnya bukan bug: `match` di `gen_report_period_breakdown`
memang bukan cuma soal row count —
```python
merged["match"] = merged["row_match"] & (merged["stat_mismatch"] == 0)
```
`stat_mismatch` dihitung dari SUM/MIN/MAX/datediff kolom-kolom shared LAIN untuk periode itu — kalau
row count sama tapi salah satu statistik itu beda, periode tetap dianggap mismatch. Masalahnya:
detail KOLOM/METRIK mana yang beda tidak pernah disimpan, cuma dihitung sekilas lalu dibuang.

**Perbaikan**: `gen_report_period_breakdown` sekarang menyimpan `mismatch_detail` — daftar
`{column, metric, source, target}` per periode, bukan cuma angka `stat_mismatch`.
`_persist_aggregate_findings` memecahnya jadi finding TERPISAH per alasan: satu finding "row count"
(HANYA kalau row count-nya memang beda), PLUS satu finding per (kolom, metrik) yang mismatch — jadi
periode Δ=0 yang tetap ke-flag sekarang punya baris eksplisit yang bilang "sum kolom X beda", bukan
kosong. Tab Periode menampilkan kolom baru **Jenis** (row count / nama metrik: sum, min, max,
datediff, sum_len, dst.) dan **Kolom**. Badge navigasi tetap menghitung jumlah PERIODE yang
mismatch (bukan jumlah baris finding, yang sekarang bisa lebih banyak dari jumlah periode).
Regression test: `tests/test_period_findings.py` (komputasi `mismatch_detail` pakai fixture nyata
`orders_pair`, parsing alias metrik, persistensi jadi finding terpisah) +
`tests/test_table_drilldown.py::test_periode_tab_shows_metric_detail_for_zero_delta_periods`.

### Fitur: copy key bermasalah (Missing Keys / Value Diffs) siap-tempel untuk `WHERE id IN (...)`

Permintaan user: mereka punya script manual untuk insert ulang ke pipeline, dan perlu daftar ID yang
missing/differing untuk dipakai di `WHERE id IN (...)`. Tombol **📋 Copy** di tab Missing Keys (2
tombol: hilang di TARGET / hilang di SOURCE) dan Value Diffs (mengikuti filter kolom yang aktif, atau
semua kolom sekaligus jika tidak difilter) — mengambil SEMUA key yang cocok (bukan cuma halaman yang
sedang ditampilkan, karena kedua tab ini dipaginasi) lewat endpoint baru
`GET /runs/{run_id}/tables/{run_table_id}/keys?kind=...&column=...`, menampilkannya di textarea yang
otomatis ter-select (supaya Ctrl+C manual selalu berhasil) sambil mencoba auto-copy ke clipboard
(`navigator.clipboard` butuh HTTPS/localhost — kalau server diakses lewat HTTP biasa di jaringan
internal, auto-copy bisa gagal diam-diam; makanya fallback textarea-nya WAJIB ada, bukan sekadar
nice-to-have).

Format hasil: angka polos dipisah koma kalau SEMUA key numerik (siap tempel langsung ke `IN (...)`),
di-quote (`'...'`) kalau ada yang bukan angka. Untuk key yang differing di Value Diffs, key yang
sama TIDAK diulang walau muncul di beberapa kolom berbeda (distinct) — user butuh daftar row id unik
untuk re-insert, bukan satu baris per kolom yang beda. Untuk composite key (lebih dari 1 kolom key),
key ditampilkan APA ADANYA (gabungan dengan `_`, sesuai `composite_key()` di comparator) dengan
header yang menjelaskan urutan kolomnya — SENGAJA tidak dipecah balik jadi tuple per kolom, karena
penggabungan dengan `_` itu lossy (kalau salah satu value aslinya mengandung `_`, tebak-tebakan
pemisahannya bisa salah) — lebih jujur menampilkan nilai asli daripada diam-diam salah parse.
Regression test: `tests/test_copy_keys.py`.

### Fitur: polish tampilan (dropdown, editor tabel config, popup copy-key) + insiden cache CSS basi

Permintaan user langsung dari screenshot: dropdown terlihat polos/tidak sesuai tema, editor pemetaan
tabel di halaman config berantakan, dan panel "Copy key" seharusnya jadi popup di tengah layar.
**Perbaikan** (murni CSS/template, tidak menyentuh logika backend):
- **Semua `<select>`** di seluruh app sebelumnya TIDAK PUNYA styling custom sama sekali (mengandalkan
  tampilan default browser/OS) — sekarang dapat border/radius konsisten, chevron custom (beda warna
  untuk light/dark), efek hover/focus — otomatis di SEMUA halaman tanpa perlu ubah template satu pun
  (perbaikan CSS global murni). `<select multiple>` dikecualikan dari chevron (browser me-render-nya
  sebagai listbox selalu-terbuka, bukan dropdown tertutup) tapi tetap dapat border/warna yang serasi.
- **Editor pemetaan tabel** (`config_detail.html`): semua sel `vertical-align: top`, padding seragam,
  ukuran select/multi-select/input konsisten, tombol hapus (✕) jadi lingkaran kecil yang lebih rapi.
- **Panel Copy Key**: diubah dari textarea inline di bawah tombol jadi **modal popup di tengah**
  (overlay gelap, kartu dengan judul+tombol tutup, tombol "Salin ke Clipboard" yang bisa diklik
  ulang) — CSS generik (`.modal-backdrop`/`.modal-card`) supaya dipakai ulang kalau ada modal lain
  nanti.

**Insiden ikutan**: setelah deploy, modal-nya TETAP tampil polos/tidak ter-styling di browser user
meski server sudah dikonfirmasi (lewat curl) menyajikan CSS yang baru — **browser meng-cache
`app.css` versi lama**. `StaticFiles` (dipakai untuk `/static`) tidak mengirim `Cache-Control`
apa pun, jadi browser bebas cache seagresif apa pun berdasarkan heuristiknya sendiri; setiap kali CSS
berubah, user yang browser-nya sudah pernah buka app ini akan tetap melihat versi lama sampai
hard-refresh manual. **Perbaikan**: `<link>` stylesheet di `base.html` sekarang punya query string
`?v={{ asset_version }}` — `asset_version` adalah Jinja *global* (di-set SEKALI saat startup di
`ui.py`, dari mtime file `app.css` itu sendiri) yang otomatis tersedia di SEMUA template lewat
`base.html`. Begitu file CSS berubah DAN server di-restart (satu-satunya momen assetnya benar-benar
berubah, given tidak ada hot-reload di app ini), mtime-nya berubah, URL stylesheet-nya berubah —
copy lama di cache browser jadi tidak relevan sama sekali karena tidak pernah diminta lagi lewat URL
lama itu. Tidak perlu bump versi manual, tidak bergantung pada user mau hard-refresh atau tidak.
Regression test: `tests/test_asset_versioning.py`.

## Deviasi dari arsitektur target (dan kenapa)

Dokumen [03-arsitektur.md](../../data-pipeline-batch/docs/validation-platform/03-arsitektur.md)
menspesifikasikan FastAPI + Celery/Redis + PostgreSQL + React. Environment saat implementasi ini
dibuat **tidak punya Node/npm terpasang** dan **Docker daemon tidak jalan**, jadi beberapa keputusan
disesuaikan supaya project ini bisa langsung dicoba tanpa setup tambahan:

| Arsitektur target | Implementasi ini | Kenapa | Cara upgrade nanti |
|---|---|---|---|
| PostgreSQL | **SQLite** (default) | Zero-setup, `pip install` saja | `DATABASE_URL` env var → connection string Postgres; skema SQLAlchemy sudah portable (JSON type dsb) |
| Celery + Redis worker | **`threading.Thread` + `ThreadPoolExecutor`** in-process | Redis/Celery butuh service terpisah, tak bisa diinstal offline dengan mudah di sesi ini | Ganti `run_service.start_run_async` jadi `.delay()` Celery task; struktur event/progress sudah dipisah di `events_bus.py` supaya gampang diganti jadi Redis pub/sub |
| React SPA | **Server-rendered Jinja2 + vanilla JS** (fetch polling, tanpa framework) | Tidak ada Node/npm untuk build step | UI sudah terpisah rapi dari engine (routers hanya panggil services); bisa dibangun ulang sebagai SPA yang manggil endpoint JSON yang sama pola-nya dengan `/api/runs/{id}/status` |
| SSE untuk live progress | **Polling `fetch()` tiap 2 detik** | Lebih sederhana & robust tanpa perlu Redis pub/sub lintas proses | `events_bus.py` sudah in-memory pub/sub — tinggal bungkus jadi `StreamingResponse` SSE bila mau |
| Drilldown "semua kolom" (match + mismatch) | **Hanya temuan/mismatch** yang disimpan & ditampilkan | Lebih ringan di DB, dan lebih fokus ke "apa yang salah" — tapi memang bukan tabel perbandingan lengkap seperti Excel lama | Tambah kolom JSON di `RunTable` untuk simpan `column_details`/`src_type_details` penuh bila suatu saat dibutuhkan |
| Import YAML config lama | **Belum ada** | Di luar scope sesi ini | `validation_core.models.TableSpec` sudah persis semantiknya — tinggal tulis parser YAML → `ConfigTable` rows |
| RBAC penuh (admin/editor/viewer ditegakkan di semua endpoint) | **Auth ada, role dicatat, tapi enforcement per-endpoint belum lengkap** | Fokus waktu ke engine + alur inti | `app/auth.py::require_role` sudah ada, tinggal dipasang di endpoint yang butuh |
| Notifikasi Slack/email, scheduler cron | **Belum ada** | Fase 2 di roadmap, di luar scope MVP ini | — |
| Backfill trigger dari UI | **Belum ada** | Fase 3 di roadmap | — |

CLI standalone (`validation_core.runner.run_table` dipanggil langsung dari Python) tetap bisa dipakai
tanpa web sama sekali — lihat `tests/conftest.py` untuk contoh pemakaian langsung ke connector.

## Kredensial demo

- Login: `admin@lidvalid.local` / `admin123` — **ganti setelah demo**, atau hapus `data/lidvalid.sqlite`
  untuk mulai bersih.
- Kunci enkripsi koneksi (`data/secret.key`) auto-dibuat saat pertama jalan. **Jangan commit file ini**
  (sudah di `.gitignore`). Untuk produksi, set `LIDVALID_SECRET_KEY` sebagai env var alih-alih file lokal.
