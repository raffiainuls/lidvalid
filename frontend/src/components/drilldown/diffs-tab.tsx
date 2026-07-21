import { ChevronLeft, ChevronRight, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useRunTableRowlevel } from "@/hooks/use-runs";
import type { DiffFindingRow } from "@/lib/types";

const ALL_COLUMNS = "__all__";

export function DiffsTab({
  runId,
  runTableId,
  diffColumns,
  diffColumnCounts,
  totalDiffCount,
  selectedColumn,
  onColumnChange,
  page,
  onPageChange,
  onCopyKeys,
}: {
  runId: number;
  runTableId: number;
  diffColumns: string[];
  diffColumnCounts: Record<string, number>;
  totalDiffCount: number;
  selectedColumn: string;
  onColumnChange: (column: string) => void;
  page: number;
  onPageChange: (page: number) => void;
  onCopyKeys: (kind: string, column?: string) => void;
}) {
  const { data, isLoading } = useRunTableRowlevel(runId, runTableId, "diffs", selectedColumn, page);
  const rows = (data?.rows as DiffFindingRow[]) ?? [];

  return (
    <div className="grid gap-3">
      {diffColumns.length > 0 && (
        <div className="flex items-center gap-2">
          <label className="text-sm text-muted-foreground">Filter kolom:</label>
          <Select value={selectedColumn || ALL_COLUMNS} onValueChange={(v) => onColumnChange(v === ALL_COLUMNS ? "" : v)}>
            <SelectTrigger className="w-64">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_COLUMNS}>Semua kolom ({totalDiffCount})</SelectItem>
              {diffColumns.map((col) => (
                <SelectItem key={col} value={col}>
                  {col} ({diffColumnCounts[col] ?? 0})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selectedColumn && (
            <Button size="sm" variant="ghost" onClick={() => onColumnChange("")}>
              ✕ Hapus filter
            </Button>
          )}
        </div>
      )}
      <div>
        <Button size="sm" variant="outline" onClick={() => onCopyKeys("value_diff", selectedColumn || undefined)}>
          <Copy className="size-3.5" />
          Copy key yang differing{selectedColumn ? ` (kolom: ${selectedColumn})` : " (semua kolom, distinct)"}
        </Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Key</TableHead>
            <TableHead>Kolom</TableHead>
            <TableHead>Nilai Source</TableHead>
            <TableHead>Nilai Target</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {isLoading ? (
            <TableRow>
              <TableCell colSpan={4} className="text-center text-sm text-muted-foreground">
                Memuat…
              </TableCell>
            </TableRow>
          ) : rows.length ? (
            rows.map((f, i) => (
              <TableRow key={i}>
                <TableCell className="font-mono text-xs">{f.row_key}</TableCell>
                <TableCell className="font-mono text-xs">{f.column_name}</TableCell>
                <TableCell className="font-mono text-xs">{f.source_value}</TableCell>
                <TableCell className="font-mono text-xs">{f.target_value}</TableCell>
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell colSpan={4} className="text-center text-sm text-muted-foreground">
                {selectedColumn ? "Tidak ada value diff untuk kolom ini." : "Tidak ada value diff."}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      {data && data.total_pages > 1 && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>
            Halaman {data.page} / {data.total_pages} ({data.page_size} baris/halaman)
          </span>
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
            <ChevronLeft className="size-3.5" /> Sebelumnya
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= data.total_pages}
            onClick={() => onPageChange(page + 1)}
          >
            Berikutnya <ChevronRight className="size-3.5" />
          </Button>
        </div>
      )}
    </div>
  );
}
