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
import { useMe } from "@/hooks/use-me";
import { useCreateUser, useUpdateUser } from "@/hooks/use-users";
import { ApiError } from "@/lib/api";
import type { Role, UserAccount } from "@/lib/types";

const ROLE_OPTIONS: { value: Role; label: string }[] = [
  { value: "admin", label: "Admin — bisa lihat & kelola semua data" },
  { value: "editor", label: "Editor — kelola data milik sendiri" },
  { value: "viewer", label: "Viewer — cuma bisa lihat" },
];

const createSchema = z.object({
  username: z.string().min(1, "Username wajib diisi"),
  password: z.string().min(8, "Minimal 8 karakter"),
  display_name: z.string(),
  role: z.enum(["admin", "editor", "viewer"] as const),
});

const editSchema = z.object({
  username: z.string().min(1, "Username wajib diisi"),
  display_name: z.string(),
  role: z.enum(["admin", "editor", "viewer"] as const),
  is_active: z.boolean(),
});

type CreateValues = z.infer<typeof createSchema>;
type EditValues = z.infer<typeof editSchema>;

export function UserFormDialog({
  open,
  onOpenChange,
  user,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user?: UserAccount | null;
}) {
  const isEdit = !!user;
  const { data: me } = useMe();
  const isSelf = isEdit && me?.id === user?.id;
  const createMutation = useCreateUser();
  const updateMutation = useUpdateUser();
  const pending = createMutation.isPending || updateMutation.isPending;

  const createForm = useForm<CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: { username: "", password: "", display_name: "", role: "editor" },
  });
  const editForm = useForm<EditValues>({
    resolver: zodResolver(editSchema),
    defaultValues: { username: "", display_name: "", role: "editor", is_active: true },
  });

  useEffect(() => {
    if (!open) return;
    if (isEdit && user) {
      editForm.reset({
        username: user.username,
        display_name: user.display_name,
        role: user.role,
        is_active: user.is_active,
      });
    } else {
      createForm.reset({ username: "", password: "", display_name: "", role: "editor" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isEdit, user]);

  async function onSubmitCreate(values: CreateValues) {
    try {
      await createMutation.mutateAsync(values);
      toast.success("User dibuat");
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal membuat user");
    }
  }

  async function onSubmitEdit(values: EditValues) {
    if (!user) return;
    try {
      await updateMutation.mutateAsync({ id: user.id, body: values });
      toast.success("User diperbarui");
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal memperbarui user");
    }
  }

  const roleValue = isEdit ? editForm.watch("role") : createForm.watch("role");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit User" : "Buat User"}</DialogTitle>
          <DialogDescription>
            {isEdit ? "Perbarui detail akun ini." : "Buat akun baru untuk anggota tim."}
          </DialogDescription>
        </DialogHeader>

        {isEdit ? (
          <form className="grid gap-4" onSubmit={editForm.handleSubmit(onSubmitEdit)}>
            <div className="grid gap-2">
              <Label htmlFor="display_name">Nama Tampilan</Label>
              <Input id="display_name" {...editForm.register("display_name")} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="username">Username</Label>
              <Input id="username" type="text" {...editForm.register("username")} />
              {editForm.formState.errors.username && (
                <p className="text-xs text-destructive">{editForm.formState.errors.username.message}</p>
              )}
            </div>
            <div className="grid gap-2">
              <Label>Role</Label>
              <Select
                value={roleValue}
                onValueChange={(v) => editForm.setValue("role", v as Role)}
                disabled={isSelf}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROLE_OPTIONS.map((r) => (
                    <SelectItem key={r.value} value={r.value}>
                      {r.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-start gap-2">
              <Checkbox
                id="is_active"
                checked={editForm.watch("is_active")}
                onCheckedChange={(v) => editForm.setValue("is_active", v === true)}
                disabled={isSelf}
              />
              <Label htmlFor="is_active" className="font-normal">
                Akun aktif
              </Label>
            </div>
            {isSelf && (
              <p className="text-xs text-muted-foreground">
                Tidak bisa menurunkan role atau menonaktifkan akun sendiri — supaya tidak ada yang
                terkunci keluar tanpa admin lain yang bisa membatalkannya.
              </p>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Batal
              </Button>
              <Button type="submit" disabled={pending}>
                {pending ? "Menyimpan…" : "Simpan"}
              </Button>
            </DialogFooter>
          </form>
        ) : (
          <form className="grid gap-4" onSubmit={createForm.handleSubmit(onSubmitCreate)}>
            <div className="grid gap-2">
              <Label htmlFor="c-display_name">Nama Tampilan</Label>
              <Input id="c-display_name" {...createForm.register("display_name")} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="c-username">Username</Label>
              <Input id="c-username" type="text" {...createForm.register("username")} />
              {createForm.formState.errors.username && (
                <p className="text-xs text-destructive">{createForm.formState.errors.username.message}</p>
              )}
            </div>
            <div className="grid gap-2">
              <Label htmlFor="c-password">Password</Label>
              <Input id="c-password" type="password" autoComplete="new-password" {...createForm.register("password")} />
              {createForm.formState.errors.password && (
                <p className="text-xs text-destructive">{createForm.formState.errors.password.message}</p>
              )}
            </div>
            <div className="grid gap-2">
              <Label>Role</Label>
              <Select value={roleValue} onValueChange={(v) => createForm.setValue("role", v as Role)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROLE_OPTIONS.map((r) => (
                    <SelectItem key={r.value} value={r.value}>
                      {r.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Batal
              </Button>
              <Button type="submit" disabled={pending}>
                {pending ? "Menyimpan…" : "Buat User"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
