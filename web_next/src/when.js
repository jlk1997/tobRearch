/** 后端时间按 UTC 存。展示用浏览器本地时区，精确到分钟。 */

function pad(n) {
  return String(n).padStart(2, "0");
}

function ymd(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function parseWhen(v) {
  if (v == null || v === "") return null;
  if (v instanceof Date) return Number.isNaN(v.getTime()) ? null : v;
  const raw = String(v).trim();
  if (!raw) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    const [y, m, d] = raw.split("-").map(Number);
    return new Date(y, m - 1, d);
  }
  let iso = raw.replace(" ", "T");
  iso = iso.replace(/(\.\d{3})\d+/, "$1");
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)) iso += "Z";
  const dt = new Date(iso);
  return Number.isNaN(dt.getTime()) ? null : dt;
}

export function fmtWhen(v) {
  if (!v) return "—";
  const raw = String(v).trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  const d = parseWhen(v);
  if (!d) return raw.replace("T", " ").replace("Z", "").slice(0, 16) || "—";
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const day = ymd(d);
  const now = new Date();
  if (day === ymd(now)) return `今天 ${hm}`;
  const yest = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (day === ymd(yest)) return `昨天 ${hm}`;
  return `${day} ${hm}`;
}
