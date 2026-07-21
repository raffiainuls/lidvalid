import { useState } from "react";
import { toast } from "sonner";
import { Plus, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useCopyFromConfig,
  useSaveConfigTables,
  useSuggestMappings,
  useTableColumns,
} from "@/hooks/use-configs";
import { MODE_OVERRIDE_OPTIONS } from "@/lib/constants";
import { ApiError } from "@/lib/api";
import type { ConfigDetail, ConfigTableRowInput } from "@/lib/types";

const NONE = "__none__";
const DEFAULT_MODE = "__default__";

interface EditableRow {
  clientId: string;
  source_table: string;
  target_table: string;
  key_columns: string[];
  chunk_column: string;
  date_column: string;
  exclude_columns: string[];
  mode_override: string;
  enabled: boolean;
  match_rule?: string;
  key_source?: string;
}

function fromSaved(t: ConfigDetail["tables"][number]): EditableRow {
  return {
    clientId: `saved-${t.id}`,
    source_table: t.source_table,
    target_table: t.target_table,
    key_columns: t.key_columns.length ? t.key_columns : ["id"],
    chunk_column: t.chunk_column ?? "",
    date_column: t.date_column ?? "",
    exclude_columns: t.exclude_columns,
    mode_override: t.mode_override ?? "",
    enabled: t.enabled,
  };
}

let seq = 0;
function blankRow(): EditableRow {
  seq += 1;
  return {
    clientId: `new-${seq}`,
    source_table: "",
    target_table: "",
    key_columns: ["id"],
    chunk_column: "",
    date_column: "",
    exclude_columns: [],
    mode_override: "",
    enabled: true,
  };
}

export function TableMappingEditor({ config }: { config: ConfigDetail }) {
  const [rows, setRows] = useState<EditableRow[]>(() => config.tables.map(fromSaved));
  const [tableColumns, setTableColumns] = useState<Record<string, string[]>>(config.table_columns);
  const [prefix, setPrefix] = useState("");
  const [copyFromId, setCopyFromId] = useState<string>("");

  const saveMutation = useSaveConfigTables(config.id);
  const suggestMutation = useSuggestMappings(config.id);
  const copyFromMutation = useCopyFromConfig(config.id);
  const tableColumnsMutation = useTableColumns();

  function updateRow(clientId: string, patch: Partial<EditableRow>) {
    setRows((prev) => prev.map((r) => (r.clientId === clientId ? { ...r, ...patch } : r)));
  }
  function removeRow(clientId: string) {
    setRows((prev) => prev.filter((r) => r.clientId !== clientId));
  }

  async function handleSuggest() {
    try {
      const result = await suggestMutation.mutateAsync(prefix);
      if (!result.suggestions.length) {
        toast.info("Tidak ada saran mapping baru (semua tabel sudah dipetakan atau tidak ada yang cocok)");
        return;
      }
      const existing = new Set(rows.map((r) => r.source_table));
      const newRows = result.suggestions
        .filter((s) => !existing.has(s.source_table))
        .map((s) => {
          seq += 1;
          return {
            clientId: `sugg-${seq}`,
            source_table: s.source_table,
            target_table: s.target_table,
            key_columns: s.key_columns.length ? s.key_columns : ["id"],
            chunk_column: s.chunk_column || "",
            date_column: s.date_column || "",
            exclude_columns: s.exclude_columns,
            mode_override: s.mode_override || "",
            enabled: true,
            match_rule: s.match_rule,
            key_source: s.key_source,
          };
        });
      setRows((prev) => [...prev, ...newRows]);
      setTableColumns((prev) => ({ ...prev, ...result.table_columns }));
      toast.success(`${newRows.length} saran mapping ditambahkan — tinjau lalu simpan`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal mengambil saran mapping");
    }
  }

  async function handleCopyFrom() {
    if (!copyFromId) return;
    try {
      const result = await copyFromMutation.mutateAsync(Number(copyFromId));
      const existing = new Set(rows.map((r) => r.source_table));
      const newRows = result.suggestions
        .filter((s) => !existing.has(s.source_table))
        .map((s) => {
          seq += 1;
          return {
            clientId: `copy-${seq}`,
            source_table: s.source_table,
            target_table: s.target_table,
            key_columns: s.key_columns.length ? s.key_columns : ["id"],
            chunk_column: s.chunk_column || "",
            date_column: s.date_column || "",
            exclude_columns: s.exclude_columns,
            mode_override: s.mode_override || "",
            enabled: true,
            match_rule: s.match_rule,
            key_source: s.key_source,
          };
        });
      setRows((prev) => [...prev, ...newRows]);
      setTableColumns((prev) => ({ ...prev, ...result.table_columns }));
      toast.success(`${newRows.length} baris disalin — tinjau lalu simpan`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal menyalin pemetaan");
    }
  }

  async function handleLoadColumns(row: EditableRow) {
    const tableName = row.source_table.trim();
    if (!tableName) {
      toast.error("Isi nama source table dulu");
      return;
    }
    try {
      const data = await tableColumnsMutation.mutateAsync({ configId: config.id, table: tableName, side: "source" });
      if (!data.columns.length) {
        toast.error(`Kolom tidak ditemukan untuk "${tableName}"${data.error ? `: ${data.error}` : ""}`);
        return;
      }
      setTableColumns((prev) => ({ ...prev, [tableName]: data.columns }));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal memuat kolom");
    }
  }

  async function handleSave() {
    const payload: ConfigTableRowInput[] = rows
      .filter((r) => r.source_table.trim() && r.target_table.trim())
      .map((r) => ({
        source_table: r.source_table.trim(),
        target_table: r.target_table.trim(),
        key_columns: r.key_columns.length ? r.key_columns : ["id"],
        chunk_column: r.chunk_column || null,
        date_column: r.date_column || null,
        exclude_columns: r.exclude_columns,
        mode_override: r.mode_override || null,
        enabled: r.enabled,
      }));
    try {
      const result = await saveMutation.mutateAsync(payload);
      setRows(result.tables.map(fromSaved));
      setTableColumns(result.table_columns);
      toast.success("Pemetaan tabel disimpan");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal menyimpan pemetaan tabel");
    }
  }

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder="prefix target, mis. raw_"
          value={prefix}
          onChange={(e) => setPrefix(e.target.value)}
          className="h-8 w-56"
        />
        <Button size="sm" variant="outline" onClick={handleSuggest} disabled={suggestMutation.isPending}>
          <Search className="size-3.5" /> Auto-suggest dari koneksi
        </Button>
        <span className="text-xs text-muted-foreground">
          key columns (termasuk composite) otomatis diisi dari PRIMARY KEY (MySQL) / sorting key (ClickHouse)
        </span>
      </div>

      {config.configs_for_copy.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <Select value={copyFromId} onValueChange={setCopyFromId}>
            <SelectTrigger className="h-8 w-64">
              <SelectValue placeholder="Pilih config sumber" />
            </SelectTrigger>
            <SelectContent>
              {config.configs_for_copy.map((c) => (
                <SelectItem key={c.id} value={String(c.id)}>
                  {c.name} ({c.table_count} tabel)
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button size="sm" variant="outline" onClick={handleCopyFrom} disabled={copyFromMutation.isPending || !copyFromId}>
            ⧉ Salin pemetaan dari config ini
          </Button>
          <span className="text-xs text-muted-foreground">
            tabel yang sudah ada dilewati, sisanya masuk sebagai baris baru untuk direview
          </span>
        </div>
      )}

      <div className="overflow-x-auto rounded-md border">
        <table className="w-full min-w-[1100px] border-collapse text-sm">
          <thead>
            <tr className="border-b bg-muted/50 text-left text-xs text-muted-foreground">
              <th className="sticky left-0 z-10 min-w-40 bg-muted/50 p-2">Source table</th>
              <th className="sticky left-40 z-10 min-w-40 bg-muted/50 p-2">Target table</th>
              <th className="min-w-36 p-2">Key columns</th>
              <th className="min-w-32 p-2">Chunk col</th>
              <th className="min-w-32 p-2">Date col</th>
              <th className="min-w-32 p-2">Exclude</th>
              <th className="min-w-32 p-2">Mode override</th>
              <th className="p-2">Aktif</th>
              <th className="p-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const cols = tableColumns[row.source_table];
              const known = !!cols?.length;
              return (
                <tr key={row.clientId} className="border-b align-top last:border-0">
                  <td className="sticky left-0 z-10 bg-background p-2">
                    <Input
                      value={row.source_table}
                      onChange={(e) => updateRow(row.clientId, { source_table: e.target.value })}
                      className="h-8"
                    />
                    {row.match_rule && <p className="mt-1 text-[10px] text-muted-foreground">{row.match_rule}</p>}
                    {!known && row.clientId.startsWith("new-") && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="mt-1 h-6 px-1.5 text-[11px]"
                        onClick={() => handleLoadColumns(row)}
                        disabled={tableColumnsMutation.isPending}
                        title="Muat daftar kolom dari source connection"
                      >
                        <Search className="size-3" /> Muat kolom
                      </Button>
                    )}
                  </td>
                  <td className="sticky left-40 z-10 bg-background p-2">
                    <Input
                      value={row.target_table}
                      onChange={(e) => updateRow(row.clientId, { target_table: e.target.value })}
                      className="h-8"
                    />
                  </td>
                  <td className="p-2">
                    {known ? (
                      <select
                        multiple
                        size={3}
                        className="w-full rounded-md border bg-background px-2 py-1 text-xs"
                        value={row.key_columns}
                        onChange={(e) =>
                          updateRow(row.clientId, {
                            key_columns: Array.from(e.target.selectedOptions, (o) => o.value),
                          })
                        }
                      >
                        {cols!.map((c) => (
                          <option key={c} value={c}>
                            {c}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <Input
                        className="h-8"
                        value={row.key_columns.join(",")}
                        onChange={(e) =>
                          updateRow(row.clientId, {
                            key_columns: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                          })
                        }
                      />
                    )}
                    {row.key_source && (
                      <p
                        className={`mt-1 rounded px-1.5 py-0.5 text-[10px] ${
                          row.key_source.includes("TIDAK terdeteksi")
                            ? "bg-status-fail-bg text-status-fail"
                            : "bg-status-pass-bg text-status-pass"
                        }`}
                      >
                        {row.key_source}
                      </p>
                    )}
                  </td>
                  <td className="p-2">
                    {known ? (
                      <Select
                        value={row.chunk_column || NONE}
                        onValueChange={(v) => updateRow(row.clientId, { chunk_column: v === NONE ? "" : v })}
                      >
                        <SelectTrigger className="h-8 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={NONE}>(pakai key pertama)</SelectItem>
                          {cols!.map((c) => (
                            <SelectItem key={c} value={c}>
                              {c}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <Input
                        className="h-8"
                        value={row.chunk_column}
                        onChange={(e) => updateRow(row.clientId, { chunk_column: e.target.value })}
                      />
                    )}
                  </td>
                  <td className="p-2">
                    {known ? (
                      <Select
                        value={row.date_column || NONE}
                        onValueChange={(v) => updateRow(row.clientId, { date_column: v === NONE ? "" : v })}
                      >
                        <SelectTrigger className="h-8 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={NONE}>(kosongkan)</SelectItem>
                          {cols!.map((c) => (
                            <SelectItem key={c} value={c}>
                              {c}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <Input
                        className="h-8"
                        value={row.date_column}
                        onChange={(e) => updateRow(row.clientId, { date_column: e.target.value })}
                      />
                    )}
                  </td>
                  <td className="p-2">
                    {known ? (
                      <select
                        multiple
                        size={3}
                        className="w-full rounded-md border bg-background px-2 py-1 text-xs"
                        value={row.exclude_columns}
                        onChange={(e) =>
                          updateRow(row.clientId, {
                            exclude_columns: Array.from(e.target.selectedOptions, (o) => o.value),
                          })
                        }
                      >
                        {cols!.map((c) => (
                          <option key={c} value={c}>
                            {c}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <Input
                        className="h-8"
                        value={row.exclude_columns.join(",")}
                        onChange={(e) =>
                          updateRow(row.clientId, {
                            exclude_columns: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                          })
                        }
                      />
                    )}
                  </td>
                  <td className="p-2">
                    <Select
                      value={row.mode_override || DEFAULT_MODE}
                      onValueChange={(v) => updateRow(row.clientId, { mode_override: v === DEFAULT_MODE ? "" : v })}
                    >
                      <SelectTrigger className="h-8 text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={DEFAULT_MODE}>(default config)</SelectItem>
                        {MODE_OVERRIDE_OPTIONS.map((m) => (
                          <SelectItem key={m.value} value={m.value}>
                            {m.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </td>
                  <td className="p-2 text-center">
                    <Checkbox
                      checked={row.enabled}
                      onCheckedChange={(v) => updateRow(row.clientId, { enabled: v === true })}
                    />
                  </td>
                  <td className="p-2">
                    <Button
                      size="icon"
                      variant="ghost"
                      className="size-6 text-muted-foreground hover:text-destructive"
                      onClick={() => removeRow(row.clientId)}
                    >
                      <X className="size-3.5" />
                    </Button>
                  </td>
                </tr>
              );
            })}
            {!rows.length && (
              <tr>
                <td colSpan={9} className="p-4 text-center text-sm text-muted-foreground">
                  Belum ada pemetaan tabel. Pakai auto-suggest atau tambah baris manual.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex gap-2">
        <Button variant="outline" onClick={() => setRows((prev) => [...prev, blankRow()])}>
          <Plus className="size-4" /> Tambah baris
        </Button>
        <Button onClick={handleSave} disabled={saveMutation.isPending}>
          {saveMutation.isPending ? "Menyimpan…" : "Simpan Pemetaan Tabel"}
        </Button>
      </div>
    </div>
  );
}
