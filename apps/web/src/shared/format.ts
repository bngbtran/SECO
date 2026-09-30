const pad2 = (n: number) => String(n).padStart(2, "0");

const toDate = (value: string | number | Date) => (value instanceof Date ? value : new Date(value));

export const vnd = (n: number | null | undefined) =>
  `${(Math.round(Number(n)) || 0).toLocaleString("vi-VN")}đ`;

/** Decimal fields arrive as strings ("0.00375") or numbers from WebSocket events. */
export const num = (value: string | number | null | undefined) => Number(value ?? 0);

export const kwh = (value: string | number | null | undefined, digits = 4) => num(value).toFixed(digits);

export const fTime = (t: string | number | Date) => {
  const d = toDate(t);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
};

export const fTimeS = (t: string | number | Date) => `${fTime(t)}:${pad2(toDate(t).getSeconds())}`;

export const fDate = (t: string | number | Date) => {
  const d = toDate(t);
  return `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)} ${fTime(d)}`;
};

export const fDay = (t: string | number | Date) => {
  const d = toDate(t);
  return `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)}/${d.getFullYear()}`;
};

export const rel = (t: string | number | Date) => {
  const s = Math.max(0, (Date.now() - toDate(t).getTime()) / 1000);
  if (s < 45) return "vừa xong";
  if (s < 3600) return `${Math.round(s / 60)} phút trước`;
  if (s < 86400) return `${Math.round(s / 3600)} giờ trước`;
  return `${Math.round(s / 86400)} ngày trước`;
};

export const dur = (seconds: number) => {
  const m = Math.floor(seconds / 60);
  const h = Math.floor(m / 60);
  if (h) return `${h}g ${pad2(m % 60)}p`;
  if (m) return `${m} phút`;
  return `${Math.max(0, Math.floor(seconds))} giây`;
};

export const shortId = (id: string | null | undefined) => (id ? id.slice(0, 8) : "—");

export const initials = (name: string) =>
  name
    .split(" ")
    .filter(Boolean)
    .slice(-2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();

export const compactVnd = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(Math.round(v)));
