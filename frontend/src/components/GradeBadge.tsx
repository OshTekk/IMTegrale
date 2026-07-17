import clsx from "clsx";

export function GradeBadge({ grade, description }: { grade: string | null; description?: string | null }) {
  return <span className={clsx("grade-badge", grade && `grade-${grade.toLowerCase()}`)} title={description ?? undefined}>{grade ?? "—"}</span>;
}
