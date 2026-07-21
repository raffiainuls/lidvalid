import { ChevronLeft, ChevronRight, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useRunTableRowlevel } from "@/hooks/use-runs";
import type { MissingFindingRow } from "@/lib/types";

export function MissingTab({
  runId,
  runTableId,
  page,
  onPageChange,
  onCopyKeys,
}: {
  runId: number;
  runTableId: number;
  page: number;
  onPageChange: (page: number) => void;
  onCopyKeys: (kind: string) => void;
}) {
  const { data, isLoading } = useRunTableRowlevel(runId, runTableId, "missing", "", page);
  const rows = (data?.rows as MissingFindingRow[]) ?? [];

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => onCopyKeys("missing_in_target")}>
          <Copy className="size-3.5" /> Copy key: hilang di TARGET
        </Button>
        <Button size="sm" variant="outline" onClick={() => onCopyKeys("missing_in_source")}>
          <Copy className="size-3.5" /> Copy key: hilang di SOURCE
        </Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Arah</TableHead>
            <TableHead>Key</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {isLoading ? (
            <TableRow>
              <TableCell colSpan={2} className="text-center text-sm text-muted-foreground">
                Memuat…
              </TableCell>
            </TableRow>
          ) : rows.length ? (
            rows.map((f, i) => (
              <TableRow key={i}>
                <TableCell className="font-mono text-xs">
                  {f.finding_type === "missing_in_target"
                    ? "target tidak punya (hilang di target)"
                    : "source tidak punya (hilang di source)"}
                </TableCell>
                <TableCell className="font-mono text-xs">{f.row_key}</TableCell>
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell colSpan={2} className="text-center text-sm text-muted-foreground">
                Tidak ada missing key.
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
