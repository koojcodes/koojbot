"use client";

interface MetricCardProps {
  label: string;
  value: string;
  change?: string;
  positive?: boolean;
  sublabel?: string;
}

export function MetricCard({ label, value, change, positive, sublabel }: MetricCardProps) {
  const hasChange = change !== undefined && positive !== undefined;

  return (
    <div
      className={`
        bg-card border rounded-xl p-4 transition-all duration-200
        ${hasChange
          ? positive
            ? "border-accent/30 shadow-[0_0_12px_rgba(34,197,94,0.08)]"
            : "border-accent-red/30 shadow-[0_0_12px_rgba(239,68,68,0.08)]"
          : "border-card-border"
        }
      `}
    >
      <p className="text-xs text-muted uppercase tracking-widest font-medium">{label}</p>
      <p className="text-2xl font-bold mt-1.5 font-mono tabular-nums">{value}</p>
      {hasChange && (
        <div className="flex items-center gap-1.5 mt-1.5">
          <span className={positive ? "text-accent" : "text-accent-red"}>
            {positive ? "▲" : "▼"}
          </span>
          <p className={`text-sm font-mono tabular-nums ${positive ? "text-accent" : "text-accent-red"}`}>
            {change}
          </p>
        </div>
      )}
      {sublabel && (
        <p className="text-xs text-muted mt-1">{sublabel}</p>
      )}
    </div>
  );
}
