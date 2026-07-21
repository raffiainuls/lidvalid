import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { UserPlus, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useMe, useInvalidateMe } from "@/hooks/use-me";
import { api, ApiError } from "@/lib/api";
import type { RegisterInput, User } from "@/lib/types";

const schema = z
  .object({
    display_name: z.string(),
    username: z.string().min(1, "Username wajib diisi"),
    password: z.string().min(8, "Minimal 8 karakter"),
    confirm_password: z.string().min(1, "Wajib diisi"),
  })
  .refine((v) => v.password === v.confirm_password, {
    message: "Konfirmasi password tidak cocok",
    path: ["confirm_password"],
  });
type FormValues = z.infer<typeof schema>;

export default function RegisterPage() {
  const navigate = useNavigate();
  const invalidateMe = useInvalidateMe();
  const { data: me, isLoading: meLoading } = useMe();

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  const registerMutation = useMutation({
    mutationFn: (body: RegisterInput) => api.post<User>("/register", body),
    onSuccess: () => {
      invalidateMe();
      navigate("/dashboard", { replace: true });
    },
  });

  if (!meLoading && me) {
    return <Navigate to="/dashboard" replace />;
  }

  function onSubmit(values: FormValues) {
    registerMutation.mutate({
      username: values.username,
      password: values.password,
      display_name: values.display_name,
    });
  }

  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-6 bg-muted/30 p-6">
      <Link to="/" className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Kembali ke beranda
      </Link>
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <div className="mx-auto mb-2 flex size-10 items-center justify-center rounded-full bg-primary/10">
            <UserPlus className="size-5 text-primary" />
          </div>
          <CardTitle>Buat Akun LidValid</CardTitle>
          <CardDescription>
            Akun baru langsung aktif -- tapi belum ada data apa pun, mulai dari kosong dan cuma
            bisa mengelola Connection/Config/Run milik sendiri.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="grid gap-4" onSubmit={handleSubmit(onSubmit)}>
            <div className="grid gap-2">
              <Label htmlFor="display_name">Nama Tampilan</Label>
              <Input id="display_name" autoComplete="name" {...register("display_name")} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="username">Username</Label>
              <Input id="username" autoComplete="username" {...register("username")} />
              {errors.username && <p className="text-xs text-destructive">{errors.username.message}</p>}
            </div>
            <div className="grid gap-2">
              <Label htmlFor="password">Password</Label>
              <Input id="password" type="password" autoComplete="new-password" {...register("password")} />
              {errors.password && <p className="text-xs text-destructive">{errors.password.message}</p>}
            </div>
            <div className="grid gap-2">
              <Label htmlFor="confirm_password">Konfirmasi Password</Label>
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
            {registerMutation.isError && (
              <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {registerMutation.error instanceof ApiError
                  ? registerMutation.error.message
                  : "Gagal membuat akun. Coba lagi."}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={registerMutation.isPending}>
              {registerMutation.isPending ? "Membuat akun…" : "Buat Akun"}
            </Button>
          </form>
          <p className="mt-4 text-center text-sm text-muted-foreground">
            Sudah punya akun?{" "}
            <Link to="/login" className="font-medium text-primary hover:underline">
              Masuk
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
