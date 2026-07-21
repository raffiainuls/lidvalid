import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { Badge } from "@/components/ui/badge";
import { ConfigFormDialog } from "@/components/configs/config-form-dialog";
import { useConfigs } from "@/hooks/use-configs";

const PERMISSION_LABEL = { view: "Lihat Saja", run: "Lihat & Jalankan", edit: "Edit Penuh" } as const;

export default function ConfigsPage() {
  const { data: configs, isLoading } = useConfigs();
  const [formOpen, setFormOpen] = useState(false);
  const navigate = useNavigate();

  return (
    <div className="grid gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Validation Configs</h1>
        <Button onClick={() => setFormOpen(true)}>
          <Plus className="size-4" /> Buat Config
        </Button>
      </div>

      <Card>
        <CardContent>
          {isLoading ? (
            <div className="grid gap-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-10" />
              ))}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Nama</TableHead>
                  <TableHead>Source → Target</TableHead>
                  <TableHead>Tabel</TableHead>
                  <TableHead>Mode Default</TableHead>
                  <TableHead>Run Terakhir</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {configs && configs.length ? (
                  configs.map((cfg) => (
                    <TableRow
                      key={cfg.id}
                      className="cursor-pointer"
                      onClick={() => navigate(`/configs/${cfg.id}`)}
                    >
                      <TableCell className="font-medium">
                        <div className="flex items-center gap-1.5">
                          {cfg.name}
                          {!cfg.is_mine && cfg.shared_permission && (
                            <Badge variant="outline" className="text-[10px] font-normal">
                              Dibagikan oleh {cfg.owner_username} · {PERMISSION_LABEL[cfg.shared_permission]}
                            </Badge>
                          )}
                          {cfg.is_mine && cfg.share_count > 0 && (
                            <Badge variant="secondary" className="text-[10px] font-normal">
                              Dibagikan ke {cfg.share_count} orang
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {cfg.source_connection_name} → {cfg.target_connection_name}
                      </TableCell>
                      <TableCell>{cfg.table_count}</TableCell>
                      <TableCell className="font-mono text-xs">{cfg.default_mode}</TableCell>
                      <TableCell>
                        {cfg.last_run ? (
                          <span className="inline-flex items-center gap-1.5">
                            <StatusBadge status={cfg.last_run.status} /> #{cfg.last_run.id}
                          </span>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button size="sm" variant="outline">
                          Buka
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center text-sm text-muted-foreground">
                      Belum ada config.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ConfigFormDialog open={formOpen} onOpenChange={setFormOpen} />
    </div>
  );
}
