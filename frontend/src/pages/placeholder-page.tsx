import { Construction } from "lucide-react";

export function PlaceholderPage({ title, note }: { title: string; note?: string }) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <Construction className="size-10 text-muted-foreground" />
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="max-w-sm text-sm text-muted-foreground">
        {note ?? "Halaman ini sedang dibangun di fase berikutnya dari migrasi UI."}
      </p>
    </div>
  );
}
