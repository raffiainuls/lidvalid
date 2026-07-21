import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/hooks/use-me";
import { useChangePassword, useUpdateProfile } from "@/hooks/use-profile";
import { ApiError } from "@/lib/api";

const profileSchema = z.object({
  display_name: z.string(),
  username: z.string().min(1, "Username wajib diisi"),
});
type ProfileValues = z.infer<typeof profileSchema>;

const passwordSchema = z
  .object({
    current_password: z.string().min(1, "Wajib diisi"),
    new_password: z.string().min(8, "Minimal 8 karakter"),
    confirm_password: z.string().min(1, "Wajib diisi"),
  })
  .refine((v) => v.new_password === v.confirm_password, {
    message: "Konfirmasi password baru tidak cocok",
    path: ["confirm_password"],
  });
type PasswordValues = z.infer<typeof passwordSchema>;

function AccountInfoCard() {
  const { data: user, isLoading } = useMe();
  const updateMutation = useUpdateProfile();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<ProfileValues>({ resolver: zodResolver(profileSchema), defaultValues: { display_name: "", username: "" } });

  useEffect(() => {
    if (user) reset({ display_name: user.display_name, username: user.username });
  }, [user, reset]);

  async function onSubmit(values: ProfileValues) {
    try {
      await updateMutation.mutateAsync(values);
      toast.success("Profil diperbarui");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal memperbarui profil");
    }
  }

  if (isLoading || !user) {
    return (
      <Card>
        <CardContent>
          <Skeleton className="h-40" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <form onSubmit={handleSubmit(onSubmit)}>
        <CardHeader>
          <CardTitle>Informasi Akun</CardTitle>
          <CardDescription>Nama tampilan dan username untuk akun ini.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="display_name">Nama Tampilan</Label>
            <Input id="display_name" {...register("display_name")} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="username">Username</Label>
            <Input id="username" type="text" {...register("username")} />
            {errors.username && <p className="text-xs text-destructive">{errors.username.message}</p>}
          </div>
          <div className="grid gap-2">
            <Label>Role</Label>
            <Input value={user.role} disabled className="capitalize" />
          </div>
        </CardContent>
        <CardFooter>
          <Button type="submit" disabled={updateMutation.isPending}>
            {updateMutation.isPending ? "Menyimpan…" : "Simpan"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

function ChangePasswordCard() {
  const changePasswordMutation = useChangePassword();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<PasswordValues>({ resolver: zodResolver(passwordSchema) });

  async function onSubmit(values: PasswordValues) {
    try {
      await changePasswordMutation.mutateAsync(values);
      toast.success("Password diperbarui");
      reset();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Gagal memperbarui password");
    }
  }

  return (
    <Card>
      <form onSubmit={handleSubmit(onSubmit)}>
        <CardHeader>
          <CardTitle>Ubah Password</CardTitle>
          <CardDescription>Minimal 8 karakter.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="current_password">Password Saat Ini</Label>
            <Input
              id="current_password"
              type="password"
              autoComplete="current-password"
              {...register("current_password")}
            />
            {errors.current_password && (
              <p className="text-xs text-destructive">{errors.current_password.message}</p>
            )}
          </div>
          <div className="grid gap-2">
            <Label htmlFor="new_password">Password Baru</Label>
            <Input id="new_password" type="password" autoComplete="new-password" {...register("new_password")} />
            {errors.new_password && <p className="text-xs text-destructive">{errors.new_password.message}</p>}
          </div>
          <div className="grid gap-2">
            <Label htmlFor="confirm_password">Konfirmasi Password Baru</Label>
            <Input
              id="confirm_password"
              type="password"
              autoComplete="new-password"
              {...register("confirm_password")}
            />
            {errors.confirm_password && (
              <p className="text-xs text-destructive">{errors.confirm_password.message}</p>
            )}
          </div>
        </CardContent>
        <CardFooter>
          <Button type="submit" disabled={changePasswordMutation.isPending}>
            {changePasswordMutation.isPending ? "Menyimpan…" : "Ubah Password"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

export default function ProfilePage() {
  return (
    <div className="grid max-w-lg gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Profil</h1>
      <AccountInfoCard />
      <ChangePasswordCard />
    </div>
  );
}
