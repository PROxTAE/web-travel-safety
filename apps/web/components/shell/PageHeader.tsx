import type { ReactNode } from "react";

export function PageHeader({ title, subtitle, icon, aside }: { title: string; subtitle?: string; icon?: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="flex items-center gap-3">
        {icon && <span className="sta-icon-tile bg-mint text-primary-deep">{icon}</span>}
        <div>
          <h1 className="text-3xl font-extrabold text-navy">{title}</h1>
          {subtitle && <p className="text-ink-muted">{subtitle}</p>}
        </div>
      </div>
      {aside}
    </div>
  );
}
