import { Inbox } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({
  title,
  detail,
  action,
  icon,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span>{icon ?? <Inbox size={22} />}</span>
      <h3>{title}</h3>
      <p>{detail}</p>
      {action}
    </div>
  );
}
