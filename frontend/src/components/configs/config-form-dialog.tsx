import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useConnections } from "@/hooks/use-connections";
import { useCreateConfig } from "@/hooks/use-configs";
import { MODE_OPTIONS } from "@/lib/constants";
import { ApiError } from "@/lib/api";
import type { ValidationMode } from "@/lib/types";

const schema = z.object({
  name: z.string().min(1, "Nama wajib diisi"),
  description: z.string(),
  source_connection_id: z.number().int().min(1, "Pilih source connection"),
  target_connection_id: z.number().int().min(1, "Pilih target connection"),
  default_mode: z.enum(["tiered", "aggregate", "rowlevel_missing", "rowlevel_full"] as const),
});
type FormValues = z.infer<typeof schema>;

export function ConfigFormDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate();
  const { data: connections } = useConnections();
  const createMutation = useCreateConfig();

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    reset,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: "", description: "", source_connection_id: 0, target_connection_id: 0, default_mode: "tiered" },
  });

  const sourceId = watch("source_connection_id");
  const targetId = watch("target_connection_id");
  const defaultMode = watch("default_mode");

  async function onSubmit(values: FormValues) {
    try {
      const created = await createMutation.mutateAsync(values);
      toast.success("Config dibuat — lanjutkan pemetaan tabel");
      onOpenChange(false);
      reset();
      navigate(`/configs/${created.id}`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal membuat config");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Buat Validation Config</DialogTitle>
          <DialogDescription>Setelah dibuat, lanjutkan ke pemetaan tabel.</DialogDescription>
        </DialogHeader>
        <form className="grid gap-4" onSubmit={handleSubmit(onSubmit)}>
          <div className="grid gap-2">
            <Label htmlFor="name">Nama</Label>
            <Input id="name" placeholder="mis. RAW: MySQL vs ClickHouse" {...register("name")} />
            {errors.name && <p className="text-xs text-destructive">{errors.name.message}</p>}
          </div>
          <div className="grid gap-2">
            <Label htmlFor="description">Deskripsi</Label>
            <Textarea id="description" rows={2} {...register("description")} />
          </div>
          <div className="grid gap-2">
            <Label>Source Connection</Label>
            <Select
              value={sourceId ? String(sourceId) : undefined}
              onValueChange={(v) => setValue("source_connection_id", Number(v))}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pilih koneksi" />
              </SelectTrigger>
              <SelectContent>
                {connections?.map((c) => (
                  <SelectItem key={c.id} value={String(c.id)}>
                    {c.name} ({c.engine})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errors.source_connection_id && (
              <p className="text-xs text-destructive">{errors.source_connection_id.message}</p>
            )}
          </div>
          <div className="grid gap-2">
            <Label>Target Connection</Label>
            <Select
              value={targetId ? String(targetId) : undefined}
              onValueChange={(v) => setValue("target_connection_id", Number(v))}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pilih koneksi" />
              </SelectTrigger>
              <SelectContent>
                {connections?.map((c) => (
                  <SelectItem key={c.id} value={String(c.id)}>
                    {c.name} ({c.engine})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errors.target_connection_id && (
              <p className="text-xs text-destructive">{errors.target_connection_id.message}</p>
            )}
          </div>
          <div className="grid gap-2">
            <Label>Mode Default</Label>
            <Select value={defaultMode} onValueChange={(v) => setValue("default_mode", v as ValidationMode)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODE_OPTIONS.map((m) => (
                  <SelectItem key={m.value} value={m.value}>
                    {m.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Batal
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? "Menyimpan…" : "Simpan & Lanjut"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
