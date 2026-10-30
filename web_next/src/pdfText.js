/** Join pdf.js text items into readable page text. Do not space-join in paint order. */

const JUNK_RE =
  /copyright|ieee|all rights reserved|authorized licensed|downloaded from|personal use(?: only)?|doi:\s*10\.|https?:\/\/doi\.org|xxx\.xxx|not for redistribution|©|\(c\)\s*\d{4}/i;

export const LANG_DRAFT_KEY = "fs.lang.draft";

/** 用这一页文字块的位置，把摘录标成相对页高的矩形。对不上就空着，不猜一块。 */
export function rectsForQuote(items, quote) {
  const needle = String(quote || "").replace(/\s+/g, "").slice(0, 24);
  if (needle.length < 4 || !Array.isArray(items) || !items.length) return [];
  let acc = "";
  const spans = [];
  for (const item of items) {
    const text = String(item?.str || "").replace(/\s+/g, "");
    const start = acc.length;
    acc += text;
    spans.push({ item, start, end: acc.length });
  }
  const at = acc.indexOf(needle);
  if (at < 0) return [];
  const hit = spans.filter((span) => span.end > at && span.start < at + needle.length && span.item?.transform);
  if (!hit.length) return [];
  const xs = [];
  const ys = [];
  for (const item of items) {
    const t = item?.transform;
    if (!t) continue;
    xs.push(t[4] || 0);
    ys.push(t[5] || 0);
  }
  if (!xs.length) return [];
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const width = Math.max(1, maxX - minX);
  const height = Math.max(1, maxY - minY);
  return hit.map((span) => {
    const t = span.item.transform;
    const itemWidth = span.item.width || 8;
    const itemHeight = span.item.height || 10;
    return {
      x: Math.max(0, (t[4] - minX) / width),
      y: Math.max(0, 1 - (t[5] - minY) / height - itemHeight / height),
      w: Math.min(1, itemWidth / width),
      h: Math.max(0.012, Math.min(0.08, itemHeight / height)),
    };
  });
}

export function setLangDraft(text, mode = "translate", context = "") {
  const body = String(text || "").trim();
  if (!body) return;
  try {
    sessionStorage.setItem(
      LANG_DRAFT_KEY,
      JSON.stringify({
        text: body,
        mode: mode || "translate",
        context: String(context || "").trim().slice(0, 4000),
      })
    );
  } catch {
    /* ignore quota */
  }
}

export function takeLangDraft() {
  try {
    const raw = sessionStorage.getItem(LANG_DRAFT_KEY);
    if (!raw) return null;
    sessionStorage.removeItem(LANG_DRAFT_KEY);
    if (raw.startsWith("{")) {
      const pack = JSON.parse(raw);
      const text = String(pack?.text || "").trim();
      if (!text) return null;
      return {
        text,
        mode: pack.mode || "translate",
        context: String(pack.context || "").trim(),
      };
    }
    return { text: raw, mode: "translate", context: "" };
  } catch {
    return null;
  }
}

export function clipPreview(text, n = 72) {
  const s = String(text || "").replace(/\s+/g, " ").trim();
  if (!s) return "";
  if (s.length <= n) return s;
  return `${s.slice(0, Math.max(1, n - 1))}…`;
}

export function clampPopover(x, y, w = 360, h = 280) {
  const width = typeof window === "undefined" ? 1200 : window.innerWidth;
  const height = typeof window === "undefined" ? 800 : window.innerHeight;
  return {
    left: Math.max(8, Math.min(Number(x) || 8, width - w - 8)),
    top: Math.max(8, Math.min(Number(y) || 8, height - h - 8)),
  };
}

export function visibleTranslation(text) {
  const raw = String(text || "").replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, "").trim();
  if (!raw) return "";
  const markers = [
    "carefully translate",
    "let me translate",
    "only output the translation",
    "do not translate",
    "follow the glossary",
    "the text includes",
    "the translation",
  ];
  const labelRe = /^(?:title|authors?|abstract(?:\s+content)?|index\s+terms?|keywords?|affiliations?)\s*[:：]/i;
  const hasMarker =
    markers.some((m) => raw.toLowerCase().includes(m)) || /^\s*\d+[\.)]\s+/.test(raw) || labelRe.test(raw);
  if (!hasMarker) return raw;
  const kept = [];
  let started = false;
  for (const line of raw.split("\n")) {
    let blob = line.trim();
    if (!blob) {
      if (started && kept.length && kept[kept.length - 1] !== "") kept.push("");
      continue;
    }
    const low = blob.toLowerCase();
    const cjk = [...blob].filter((ch) => ch >= "\u4e00" && ch <= "\u9fff").length;
    const latin = [...blob].filter((ch) => /[A-Za-z]/.test(ch)).length;
    const preamble = markers.some((m) => low.includes(m)) || /^\s*\d+[\.)]\s+/.test(blob);
    const labelOnly = labelRe.test(blob) && cjk < 4;
    const latinOnly = cjk < 2 && latin > 16;
    if (!started) {
      if (preamble && cjk < 8) continue;
      if (latinOnly || labelOnly) continue;
      started = true;
    }
    if ((preamble && cjk < 8) || labelOnly || latinOnly) continue;
    if (cjk >= 8) {
      const first = [...blob].findIndex((ch) => ch >= "\u4e00" && ch <= "\u9fff");
      if (first > 0) {
        const latinBefore = [...blob.slice(0, first)].filter((ch) => /[A-Za-z]/.test(ch)).length;
        if (latinBefore >= 24) blob = blob.slice(first).replace(/^[ \-—:：]+/, "");
      }
    }
    kept.push(blob);
  }
  return kept.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

export function chunkProgressLabel(chunk, chunks, retrying) {
  if (retrying) return "模型跑偏了，正在再译一次…";
  const n = Number(chunks) || 0;
  const i = Number(chunk) || 0;
  if (n <= 1) return "正在译这一页…";
  return `正在译第 ${i || 1}/${n} 段…`;
}

export function pageTextHash(text) {
  let h = 2166136261;
  const s = String(text || "");
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(16);
}

export function zhCacheKey(litId, page, text) {
  return `fs.zh.${litId || "x"}.${page || 0}.${pageTextHash(text)}`;
}

export function selZhKey(litId, page, text) {
  return `fs.selzh.${litId || "x"}.${page || 0}.${pageTextHash(text)}`;
}

export function getSelZh(litId, page, text) {
  try {
    return sessionStorage.getItem(selZhKey(litId, page, text)) || "";
  } catch {
    return "";
  }
}

export function setSelZh(litId, page, text, zh) {
  const body = String(zh || "").trim();
  if (!body) return;
  try {
    sessionStorage.setItem(selZhKey(litId, page, text), body);
  } catch {
    /* ignore quota */
  }
}

export function lastSelKey(litId) {
  return `fs.lastsel.${litId || "x"}`;
}

export function getLastSel(litId) {
  try {
    const raw = sessionStorage.getItem(lastSelKey(litId));
    if (!raw) return null;
    const pack = JSON.parse(raw);
    const text = String(pack?.text || "").trim();
    if (!text) return null;
    return {
      text,
      page: Number(pack.page) || 1,
      context: String(pack.context || "").trim(),
    };
  } catch {
    return null;
  }
}

export function setLastSel(litId, pack) {
  const text = String(pack?.text || "").trim();
  if (!text) return;
  try {
    sessionStorage.setItem(
      lastSelKey(litId),
      JSON.stringify({
        text,
        page: Number(pack.page) || 1,
        context: String(pack.context || "").trim().slice(0, 4000),
      })
    );
  } catch {
    /* ignore quota */
  }
}

function asItem(it) {
  if (!it) return null;
  const str = String(it.str || "");
  if (!str.trim()) return null;
  const t = it.transform || [];
  const x = Number(t.length > 4 ? t[4] : it.x || 0);
  const y = Number(t.length > 5 ? t[5] : it.y || 0);
  const h = Number(it.height || (t.length > 3 ? Math.abs(t[3]) : 8) || 8);
  const w = Number(it.width || 0);
  return { str, x, y, h, w };
}

function isJunk(s) {
  const t = String(s || "").trim();
  if (!t) return true;
  if (/^\d{1,4}$/.test(t)) return true;
  return JUNK_RE.test(t);
}

function joinTokens(parts) {
  let out = "";
  for (const raw of parts) {
    const p = String(raw || "").replace(/\s+/g, " ");
    if (!p) continue;
    if (!out) {
      out = p;
      continue;
    }
    if (/[A-Za-z]-$/.test(out) && /^[a-z]/.test(p.trimStart())) {
      out = out.replace(/-+\s*$/, "") + p.trimStart();
    } else if (/[-/]$/.test(out) || /^[,.;:)\]]/.test(p)) {
      out += p.trimStart();
    } else if (/\s$/.test(out) || /^\s/.test(p)) {
      out += p;
    } else {
      out += ` ${p}`;
    }
  }
  return out.replace(/\s+/g, " ").trim();
}

function median(nums) {
  if (!nums.length) return 8;
  const a = [...nums].sort((x, y) => x - y);
  const mid = Math.floor(a.length / 2);
  return a.length % 2 ? a[mid] : (a[mid - 1] + a[mid]) / 2;
}

function clusterLines(items) {
  if (!items.length) return [];
  const tol = Math.max(3, median(items.map((i) => i.h).filter((h) => h > 0)) * 0.45);
  const ordered = [...items].sort((a, b) => b.y - a.y || a.x - b.x);
  const lines = [];
  for (const it of ordered) {
    const last = lines[lines.length - 1];
    if (last && Math.abs(last.y - it.y) <= tol) {
      last.items.push(it);
      last.y = (last.y * (last.items.length - 1) + it.y) / last.items.length;
    } else {
      lines.push({ y: it.y, items: [it] });
    }
  }
  return lines
    .map((ln) => {
      ln.items.sort((a, b) => a.x - b.x);
      return joinTokens(ln.items.map((i) => i.str));
    })
    .filter(Boolean);
}

function dehyphenateLines(lines) {
  const merged = [];
  for (const line of lines) {
    const t = String(line || "").trim();
    if (!t) continue;
    if (merged.length && /[A-Za-z]-$/.test(merged[merged.length - 1]) && /^[a-z]/.test(t)) {
      merged[merged.length - 1] = merged[merged.length - 1].replace(/-$/, "") + t;
    } else {
      merged.push(t);
    }
  }
  return merged.join("\n");
}

function splitColumns(items) {
  if (items.length < 12) return [items];
  const xs = items.map((i) => i.x).sort((a, b) => a - b);
  const lo = xs[0];
  const hi = xs[xs.length - 1];
  if (hi - lo < 200) return [items];
  let bestGap = 0;
  let splitX = null;
  for (let i = 1; i < xs.length; i += 1) {
    const gap = xs[i] - xs[i - 1];
    const mid = (xs[i] + xs[i - 1]) / 2;
    const rel = (mid - lo) / (hi - lo);
    if (rel > 0.28 && rel < 0.72 && gap > bestGap) {
      bestGap = gap;
      splitX = mid;
    }
  }
  if (splitX == null || bestGap < 18) return [items];
  const left = items.filter((i) => i.x < splitX);
  const right = items.filter((i) => i.x >= splitX);
  if (left.length < 4 || right.length < 4) return [items];
  return [left, right];
}

export function surroundingContext(pageText, selection, radius = 280) {
  const page = String(pageText || "");
  const sel = String(selection || "").trim();
  if (!page || !sel) return "";
  let hay = page;
  let needle = sel;
  let idx = hay.indexOf(needle);
  if (idx < 0) {
    hay = page.replace(/\s+/g, " ");
    needle = sel.replace(/\s+/g, " ");
    idx = hay.indexOf(needle);
    if (idx < 0) return "";
  }
  const start = Math.max(0, idx - radius);
  const end = Math.min(hay.length, idx + needle.length + radius);
  return hay.slice(start, end).trim();
}

export function glossaryHitsOnPage(text, terms) {
  const blob = String(text || "");
  const hits = [];
  const seen = new Set();
  for (const term of terms || []) {
    const src = String(term.src || "").trim();
    if (!src || seen.has(src.toLowerCase())) continue;
    if (blob.includes(src) || blob.toLowerCase().includes(src.toLowerCase())) {
      seen.add(src.toLowerCase());
      hits.push({
        src,
        tgt: term.tgt || "",
        do_not_translate: !!term.do_not_translate,
      });
    }
  }
  return hits.slice(0, 24);
}

export function joinPdfTextItems(items) {
  const raw = (items || []).map(asItem).filter(Boolean);
  if (!raw.length) return "";
  const ys = raw.map((i) => i.y);
  const yMin = Math.min(...ys);
  const yMax = Math.max(...ys);
  const span = Math.max(yMax - yMin, 1);
  const body = raw.filter((i) => {
    const rel = (i.y - yMin) / span;
    const edge = rel < 0.045 || rel > 0.955;
    if (isJunk(i.str) && (edge || /copyright|authorized licensed|all rights reserved/i.test(i.str))) {
      return false;
    }
    return true;
  });
  const cols = splitColumns(body.length ? body : raw);
  return cols
    .map((col) => dehyphenateLines(clusterLines(col)))
    .filter(Boolean)
    .join("\n\n")
    .trim();
}
