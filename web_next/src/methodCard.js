/** 把方法卡 JSON 收成给人看的条目。禁止把机器字段 stringify 进侧栏。 */

export const METHOD_FIELDS = [
  ["problem", "要解决什么"],
  ["claims", "结论"],
  ["methods", "怎么做"],
  ["datasets", "用了什么数据"],
  ["metrics", "看哪些数字"],
  ["baselines", "和谁比"],
  ["limitations", "局限"],
  ["reusable", "可以拿来用的"],
];

const HIDDEN_KEYS = new Set([
  "warning",
  "source",
  "related_in_lab",
  "chunks",
  "card_quality",
  "is_best",
  "title",
  "abstract",
  "used_method_card",
  "used_reproduce",
  "needs_gpu",
  "session_id",
  "trace_id",
  "steps",
  "followups",
  "degraded",
  "degraded_to",
  "available_tools",
  "status",
  "schema",
  "coverage",
  "plain",
  "quote",
  ...METHOD_FIELDS.map(([k]) => k),
]);

function looksLikeJsonDump(s) {
  const t = String(s || "").trim();
  return t.startsWith("{") || t.startsWith("[");
}

export function itemText(item) {
  if (item == null) return "";
  if (typeof item === "string") return looksLikeJsonDump(item) ? "" : item.trim();
  if (typeof item !== "object" || Array.isArray(item)) return "";
  const text = String(item.text || "").trim();
  const said = String(item.said || "").trim();
  if ((!text || looksLikeJsonDump(text)) && said && !looksLikeJsonDump(said)) return said;
  if (text && !looksLikeJsonDump(text)) return text;
  const claim = String(item.claim || "").trim();
  const support = String(item.support || item.evidence || "").trim();
  const desc = String(item.description || item.raw || "").trim();
  const name = String(item.name || item.metric || item.key || "").trim();
  const val = item.value ?? item.claimed ?? item.score;
  if (name && val != null && String(val).trim() !== "") return `${name} = ${val}`;
  const head = claim || said || desc;
  if (head && support && support !== head) return `${head}（依据：${support}）`;
  return head || support || name;
}

export function itemPages(item) {
  if (!item || typeof item !== "object") return [];
  const raw = Array.isArray(item) ? item.flatMap((x) => (x && typeof x === "object" ? x.pages || [] : [])) : item.pages || [];
  return [...new Set((raw || []).map((n) => Number(n)).filter((n) => Number.isFinite(n) && n > 0))];
}

export function itemUngrounded(item) {
  return Boolean(item && typeof item === "object" && item.ungrounded);
}

export function itemQuote(item) {
  if (!item || typeof item !== "object") return "";
  return String(item.quote || "").trim();
}

export function itemUnverified(item) {
  return Boolean(item && typeof item === "object" && item.unverified && !item.ungrounded);
}

export function methodItems(block) {
  if (!block) return [];
  if (typeof block === "string") {
    const text = itemText(block);
    return text ? [{ text, pages: [], ungrounded: false }] : [];
  }
  const rows = Array.isArray(block) ? block : [block];
  return rows
    .map((row) => {
      const text = itemText(row);
      if (!text) return null;
      return {
        text,
        pages: itemPages(row),
        ungrounded: itemUngrounded(row),
        unverified: itemUnverified(row),
        quote: itemQuote(row),
      };
    })
    .filter(Boolean);
}

export function methodSections(analysis) {
  if (!analysis || typeof analysis !== "object") return [];
  return METHOD_FIELDS.map(([key, label]) => {
    const items = methodItems(analysis[key]);
    return items.length ? { key, label, items } : null;
  }).filter(Boolean);
}

export function methodCardText(analysis) {
  return methodSections(analysis)
    .map((sec) => `${sec.label}\n${sec.items.map((it) => `- ${it.text}`).join("\n")}`)
    .join("\n\n");
}

export function leftoverMethodKeys(analysis) {
  if (!analysis || typeof analysis !== "object") return [];
  return Object.keys(analysis).filter((k) => !HIDDEN_KEYS.has(k));
}
