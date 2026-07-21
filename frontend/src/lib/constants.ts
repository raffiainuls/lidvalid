import type { ValidationMode } from "@/lib/types";

export const MODE_OPTIONS: { value: ValidationMode; label: string }[] = [
  { value: "tiered", label: "tiered (agregat, lalu row-level untuk yang FAIL)" },
  { value: "aggregate", label: "aggregate saja" },
  { value: "rowlevel_missing", label: "row-level — missing keys saja" },
  { value: "rowlevel_full", label: "row-level — missing + value diff" },
];

export const MODE_OVERRIDE_OPTIONS: { value: ValidationMode; label: string }[] = [
  { value: "tiered", label: "tiered" },
  { value: "aggregate", label: "aggregate" },
  { value: "rowlevel_missing", label: "rowlevel_missing" },
  { value: "rowlevel_full", label: "rowlevel_full" },
];
