import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
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
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCreateConnection, useEngines, useUpdateConnection } from "@/hooks/use-connections";
import { ApiError } from "@/lib/api";
import type { Connection, Engine } from "@/lib/types";

const schema = z.object({
  name: z.string().min(1, "Nama wajib diisi"),
  engine: z.string().min(1, "Pilih engine"),
  host: z.string(),
  port: z.number().int().min(0),
  database: z.string(),
  username: z.string(),
  password: z.string(),
  use_tunnel: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

const EMPTY_VALUES: FormValues = {
  name: "",
  engine: "mysql",
  host: "",
  port: 0,
  database: "",
  username: "",
  password: "",
  use_tunnel: false,
};

export function ConnectionFormDialog({
  open,
  onOpenChange,
  connection,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connection?: Connection | null;
}) {
  const isEdit = !!connection;
  const { data: engineData } = useEngines();
  const createMutation = useCreateConnection();
  const updateMutation = useUpdateConnection();
  const pending = createMutation.isPending || updateMutation.isPending;

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    reset,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: EMPTY_VALUES,
  });

  useEffect(() => {
    if (open) {
      reset(
        connection
          ? {
              name: connection.name,
              engine: connection.engine,
              host: connection.host,
              port: connection.port,
              database: connection.database,
              username: connection.username,
              password: "",
              use_tunnel: connection.use_tunnel,
            }
          : EMPTY_VALUES,
      );
    }
  }, [open, connection, reset]);

  const engine = watch("engine");
  const useTunnel = watch("use_tunnel");

  async function onSubmit(values: FormValues) {
    try {
      if (isEdit && connection) {
        await updateMutation.mutateAsync({ id: connection.id, body: values });
        toast.success("Koneksi diperbarui");
      } else {
        await createMutation.mutateAsync(values);
        toast.success("Koneksi dibuat");
      }
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal menyimpan koneksi");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit Koneksi" : "Tambah Koneksi"}</DialogTitle>
          <DialogDescription>
            {isEdit ? "Perbarui detail koneksi ini." : "Buat koneksi database baru."}
          </DialogDescription>
        </DialogHeader>
        <form className="grid gap-4" onSubmit={handleSubmit(onSubmit)}>
          <div className="grid gap-2">
            <Label htmlFor="name">Nama</Label>
            <Input id="name" placeholder="mis. MySQL Staging" {...register("name")} />
            {errors.name && <p className="text-xs text-destructive">{errors.name.message}</p>}
          </div>

          <div className="grid gap-2">
            <Label>Engine</Label>
            <Select value={engine} onValueChange={(v) => setValue("engine", v as Engine)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(engineData?.engines ?? []).map((e) => (
                  <SelectItem key={e} value={e}>
                    {e}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="host">Host (diabaikan untuk engine sqlite)</Label>
            <Input id="host" placeholder="10.x.x.x atau clickhouse-host.example.com" {...register("host")} />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="grid gap-2">
              <Label htmlFor="port">Port</Label>
              <Input
                id="port"
                type="number"
                placeholder="3306 / 8123"
                {...register("port", { valueAsNumber: true })}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="username">Username</Label>
              <Input id="username" {...register("username")} />
            </div>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="database">Database (untuk sqlite: path file .sqlite)</Label>
            <Input
              id="database"
              placeholder="staging_db atau /path/ke/demo.sqlite"
              {...register("database")}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="password">
              Password {isEdit && <span className="text-muted-foreground">(kosongkan bila tidak diubah)</span>}
            </Label>
            <Input id="password" type="password" autoComplete="new-password" {...register("password")} />
          </div>

          <div className="flex items-start gap-2">
            <Checkbox
              id="use_tunnel"
              checked={useTunnel}
              onCheckedChange={(v) => setValue("use_tunnel", v === true)}
            />
            <div className="grid gap-1">
              <Label htmlFor="use_tunnel" className="font-normal">
                Akses lewat tunnel VPS
              </Label>
              <p className="text-xs text-muted-foreground">
                Aktifkan kalau host di atas cuma bisa diakses lewat VPN dan server ini tidak sedang
                konek VPN — koneksi akan lewat SSH reverse tunnel yang sudah dibuka ke server ini.
              </p>
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Batal
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "Menyimpan…" : "Simpan"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
