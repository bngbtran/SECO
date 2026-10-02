/** SVG charts ported from the prototype (areaChart, barChart, donut). */

import { useId } from "react";

export function AreaChart({
  values,
  height = 200,
  color = "#437066",
  labels = [],
  format = (v: number) => String(v),
  yTicks = 4,
}: {
  values: number[];
  height?: number;
  color?: string;
  labels?: string[];
  format?: (v: number) => string;
  yTicks?: number;
}) {
  const gradientId = useId();
  const vals = values.length > 1 ? values : [0, values[0] ?? 0];
  const W = 640;
  const H = height;
  const pl = 58;
  const pr = 10;
  const pt = 10;
  const pb = labels.length ? 26 : 10;
  const min = 0;
  const max = Math.max(...vals, 1e-9) * 1.12;
  const n = Math.max(vals.length - 1, 1);
  const X = (i: number) => pl + ((W - pl - pr) * i) / n;
  const Y = (v: number) => pt + (H - pt - pb) * (1 - (v - min) / (max - min));
  const points = vals.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const step = Math.max(1, Math.ceil(labels.length / 8));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" preserveAspectRatio="none" style={{ display: "block", height }}>
      <defs>
        <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity=".22" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {Array.from({ length: yTicks + 1 }, (_, k) => {
        const v = min + ((max - min) * k) / yTicks;
        const y = Y(v);
        return (
          <g key={k}>
            <line x1={pl} x2={W - pr} y1={y} y2={y} stroke="var(--border)" strokeDasharray={k ? "3 4" : undefined} />
            <text x={pl - 8} y={y + 4} textAnchor="end" fontSize="10.5" fill="var(--muted-fg)">
              {format(v)}
            </text>
          </g>
        );
      })}
      <polygon points={`${X(0)},${Y(min)} ${points} ${X(vals.length - 1)},${Y(min)}`} fill={`url(#${gradientId})`} />
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      {labels.map((label, i) =>
        i % step === 0 ? (
          <text key={i} x={X(i)} y={H - 8} textAnchor="middle" fontSize="10.5" fill="var(--muted-fg)">
            {label}
          </text>
        ) : null,
      )}
    </svg>
  );
}

export function BarChart({
  values,
  labels,
  height = 220,
  color = "#437066",
  format = (v: number) => String(v),
  highlight = -1,
}: {
  values: number[];
  labels: string[];
  height?: number;
  color?: string;
  format?: (v: number) => string;
  highlight?: number;
}) {
  const W = 640;
  const H = height;
  const pl = 50;
  const pr = 8;
  const pt = 12;
  const pb = 26;
  const max = Math.max(...values, 0) * 1.15 || 1;
  const bw = (W - pl - pr) / Math.max(values.length, 1);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", height: "auto" }}>
      {Array.from({ length: 5 }, (_, k) => {
        const y = pt + (H - pt - pb) * (1 - k / 4);
        return (
          <g key={k}>
            <line x1={pl} x2={W - pr} y1={y} y2={y} stroke="var(--border)" strokeDasharray={k ? "3 4" : undefined} />
            <text x={pl - 8} y={y + 4} textAnchor="end" fontSize="10.5" fill="var(--muted-fg)">
              {format((max * k) / 4)}
            </text>
          </g>
        );
      })}
      {values.map((v, i) => {
        const bh = ((H - pt - pb) * v) / max;
        const x = pl + bw * i + bw * 0.22;
        const y = H - pb - bh;
        return (
          <g key={i}>
            <rect x={x} y={y} width={bw * 0.56} height={bh} rx="5" fill={color} opacity={i === highlight ? 1 : 0.55}>
              <title>{`${labels[i]}: ${format(v)}`}</title>
            </rect>
            <text x={x + bw * 0.28} y={H - 8} textAnchor="middle" fontSize="10.5" fill="var(--muted-fg)">
              {labels[i]}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function Donut({
  segments,
  size = 150,
  label,
  sub,
}: {
  segments: { value: number; color: string }[];
  size?: number;
  label: string | number;
  sub: string;
}) {
  const r = 56;
  const c = 2 * Math.PI * r;
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  let offset = 0;

  return (
    <svg viewBox="0 0 150 150" width={size} height={size} style={{ flex: "none" }}>
      <circle cx="75" cy="75" r={r} fill="none" stroke="var(--muted-2)" strokeWidth="16" />
      {segments.map((s, i) => {
        const len = (c * s.value) / total;
        const arc = (
          <circle
            key={i}
            cx="75"
            cy="75"
            r={r}
            fill="none"
            stroke={s.color}
            strokeWidth="16"
            strokeDasharray={`${Math.max(len - 2, 0)} ${c}`}
            strokeDashoffset={-offset}
            transform="rotate(-90 75 75)"
          />
        );
        offset += len;
        return arc;
      })}
      <text x="75" y="74" textAnchor="middle" fontSize="24" fontWeight="700" fill="var(--fg)">
        {label}
      </text>
      <text x="75" y="93" textAnchor="middle" fontSize="10.5" fill="var(--muted-fg)">
        {sub}
      </text>
    </svg>
  );
}

/** Bar that shows how much of a session budget is used. */
export function BudgetBar({ cost, budget }: { cost: number; budget: number | null }) {
  if (!budget) return <span className="muted">Không giới hạn</span>;
  const pct = Math.min(100, Math.max(0, (cost / budget) * 100));
  return (
    <div style={{ width: 120 }}>
      <div className="between" style={{ fontSize: 11.5 }}>
        <span className="num">{Math.round(cost).toLocaleString("vi-VN")}đ</span>
        <span className="muted num">{budget.toLocaleString("vi-VN")}đ</span>
      </div>
      <div className="meter" style={{ marginTop: 4, height: 5 }}>
        <i style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
