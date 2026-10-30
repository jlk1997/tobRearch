function focusKey(title) {
  const t = String(title || "").trim();
  if (/相关工作|related work/i.test(t)) return "相关工作";
  const exp = /实验|experiments?/i.test(t);
  const method = /方法|methods?/i.test(t);
  if (exp && !method) return "实验";
  if (method && !exp) return "方法";
  if (exp && method) {
    const e = t.search(/实验|experiments?/i);
    const m = t.search(/方法|methods?/i);
    return e <= m ? "实验" : "方法";
  }
  return "";
}

export const PAPER_SECTIONS = ["相关工作", "方法", "实验"];

export function nextEmptySection(parsed, activeKey) {
  const sections = parsed?.sections || [];
  const gaps = PAPER_SECTIONS.filter((name) => {
    const hit = sections.find((s) => s.key === name);
    return !hit || !String(hit.body || "").trim();
  });
  if (!gaps.length || gaps[0] === activeKey) return null;
  const name = gaps[0];
  const hit = sections.find((s) => s.key === name);
  return { name, exists: Boolean(hit), id: hit?.id || "" };
}

export function parseManuscript(md) {
  const text = String(md || "");
  const hits = [];
  const re = /^(#{1,3})\s+(.+)$/gm;
  let m;
  while ((m = re.exec(text))) {
    hits.push({ index: m.index, title: m[2].trim() });
  }
  if (!hits.length) return { preamble: text, sections: [], focused: false };
  const sections = hits.map((h, i) => {
    const end = i + 1 < hits.length ? hits[i + 1].index : text.length;
    const block = text.slice(h.index, end);
    const nl = block.indexOf("\n");
    const headingLine = nl >= 0 ? block.slice(0, nl) : block;
    let body = nl >= 0 ? block.slice(nl + 1) : "";
    body = body.replace(/^\n/, "").replace(/\n+$/, "");
    const key = focusKey(h.title);
    return {
      id: `${i}:${h.title}`,
      title: h.title,
      key: key || h.title,
      headingLine,
      body,
      focus: Boolean(key),
    };
  });
  return {
    preamble: text.slice(0, hits[0].index),
    sections,
    focused: sections.some((s) => s.focus),
  };
}

export function joinManuscript(parsed, id, body) {
  if (!parsed?.sections?.length) return String(body ?? "");
  const chunks = [parsed.preamble];
  for (const s of parsed.sections) {
    const b = s.id === id ? String(body ?? "") : s.body;
    const tail = b.endsWith("\n") ? b : `${b}\n`;
    chunks.push(`${s.headingLine}\n\n${tail}`);
  }
  return chunks.join("");
}

export function ensureHeading(md, title) {
  const name = String(title || "").trim();
  const text = String(md || "");
  if (!name) return text;
  const parsed = parseManuscript(text);
  if (parsed.sections.some((s) => s.key === name || s.title === name)) return text;
  const base = text.replace(/\s*$/, "");
  return `${base}\n\n## ${name}\n\n`;
}

export function sectionBody(md, title) {
  const parsed = parseManuscript(md);
  const hit = parsed.sections.find((s) => s.key === title || s.title === title);
  return hit ? hit.body : "";
}

export function writingDeskHref(href, labId, manuscriptId, heading = "相关工作", lit) {
  let base = href || "";
  if (!base && labId && manuscriptId) {
    base = `/labs/${labId}/writing?ms=${encodeURIComponent(manuscriptId)}`;
  }
  if (!base) return "/";
  if (heading && !/[?&]heading=/.test(base)) {
    base += `${base.includes("?") ? "&" : "?"}heading=${encodeURIComponent(heading)}`;
  }
  const litText = Array.isArray(lit) ? lit.filter(Boolean).join(",") : lit || "";
  if (litText && !/[?&]lit=/.test(base)) {
    base += `${base.includes("?") ? "&" : "?"}lit=${encodeURIComponent(litText)}`;
  }
  return base;
}

export function suggestCiteKey(doc, existing = []) {
  const year = String(doc?.year || doc?.publish_year || "").replace(/\D/g, "").slice(0, 4);
  const authors = Array.isArray(doc?.authors) ? doc.authors.join(", ") : String(doc?.authors || doc?.author || "");
  const first = authors.split(/[,;，]/)[0].trim();
  const last = first.split(/\s+/).filter(Boolean).pop() || "ref";
  const stemFromTitle = String(doc?.title || "").replace(/\s+/g, "").slice(0, 8);
  const stem = last.replace(/[^A-Za-z0-9]/g, "").slice(0, 24) || stemFromTitle || "ref";
  const base = `${stem}${year}`.slice(0, 40);
  const used = new Set((existing || []).map((c) => c.citation_key).filter(Boolean));
  if (!used.has(base)) return base;
  let n = 2;
  while (used.has(`${base}${n}`) && n < 20) n += 1;
  return `${base}${n}`.slice(0, 40);
}

export function manuscriptSectionHref(manuscriptId, heading) {
  if (!manuscriptId) return "/";
  if (!heading) return `/manuscripts/${manuscriptId}`;
  return `/manuscripts/${manuscriptId}?section=${encodeURIComponent(heading)}`;
}
