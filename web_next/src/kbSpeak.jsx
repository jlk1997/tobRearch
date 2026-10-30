function isHeadingLine(line) {
  const raw = String(line || "").trim();
  const plain = raw.replace(/\*\*/g, "").trim();
  if (!plain) return true;
  if (/^#{1,6}\s/.test(raw)) return true;
  if (/^\*\*[^*]{1,40}\*\*$/.test(raw)) return true;
  if (plain.length <= 24 && /[：:]$/.test(plain)) return true;
  return false;
}

export function kbSpoken(text, { hasPapers = false } = {}) {
  const raw = String(text || "").trim();
  const leaked = /每条主张必须带来源页码|无法综述|你收到的规则|禁止解释|先把文献入库|已建索引/;
  const dump = /Looking at the observations|Direction tree|Key observations|Knowledge base:/i;
  if (dump.test(raw)) return "";
  if (!raw || leaked.test(raw)) {
    return hasPapers ? "这些论文的正文还不能问。" : "先把论文放进这个方向。";
  }
  return raw;
}

function rowName(row) {
  return String(row?.filename || row?.title || row?.heading_path || "").trim();
}

function rowText(row) {
  return String(row?.citation || row?.content || row?.excerpt || "");
}

export function isRosterHit(row) {
  const kind = row?.kind || "";
  if (kind === "member" || kind === "direction") return true;
  const id = String(row?.source_id || row?.chunk_id || row?.id || "");
  return id.startsWith("member:") || id.startsWith("dir:");
}

export function rosterLines(hits) {
  const rows = (hits || []).filter(isRosterHit);
  const dirs = rows.filter((h) => h.kind === "direction" || String(h.source_id || h.chunk_id || "").startsWith("dir:"));
  const people = rows.filter((h) => h.kind === "member" || String(h.source_id || h.chunk_id || "").startsWith("member:"));
  const lines = [];
  if (dirs.length) {
    lines.push(`这个组有 ${dirs.length} 个方向：${dirs.map(rowName).filter(Boolean).join("、")}。`);
  }
  if (people.length) {
    const teachers = people.filter((p) => /导师/.test(rowText(p)));
    const students = people.filter((p) => /学生/.test(rowText(p)));
    const bits = [`成员 ${people.length} 人。`];
    if (teachers.length) bits.push(`导师 ${teachers.map(rowName).filter(Boolean).join("、")}。`);
    if (students.length) bits.push(`学生 ${students.map(rowName).filter(Boolean).join("、")}。`);
    lines.push(bits.join(""));
  }
  if (rows.length && !(hits || []).some((h) => h.literature_item_id || h.kind === "kb_chunk")) {
    lines.push("这是组里的人和方向，不是某一页论文，不要写进论文。");
  }
  return lines;
}

export function rosterSpoken(text, hits) {
  const lines = rosterLines(hits);
  const raw = String(text || "");
  const dump = /Looking at the observations|Direction tree|Key observations|Knowledge base:/i.test(raw);
  if (!dump) return "";
  if (!lines.length) return "查到了组里的人和方向，但回答没写成中文。展开下面的人，直接点名字。";
  return lines.join("");
}

export function personLine(row) {
  const name = rowName(row);
  const raw = rowText(row);
  const role = /导师/.test(raw) ? "导师" : /学生/.test(raw) ? "学生" : "";
  const dir = (raw.match(/方向：([^｜|]*)/) || [])[1] || "";
  const hang = /未挂靠/.test(dir) || /未挂靠/.test(raw) ? "还没挂方向" : dir.trim();
  const leave = /on_leave|休学/.test(raw) ? "休学" : "";
  return [name, role, hang, leave].filter(Boolean).join(" · ");
}

export function CitedAnswer({ text, plain = false }) {
  const lines = String(text || "")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) return null;
  if (plain) {
    return lines.map((line, i) => (
      <p key={`p-${i}`}>{line.replace(/\*\*/g, "")}</p>
    ));
  }
  const shown = [];
  const loose = [];
  lines.forEach((line) => {
    const plain = /先把论文放进这个方向|正文还不能问/.test(line);
    const hasPage = /\[p\.\d+\]|p\.\d+/i.test(line);
    if (plain || isHeadingLine(line) || hasPage) shown.push(line);
    else loose.push(line);
  });
  return (
    <>
      {shown.map((line, i) => (
        <p key={`s-${i}`}>{line.replace(/\*\*/g, "")}</p>
      ))}
      {loose.length ? (
        <>
          <p className="fs-muted">还有几句没有页码。</p>
          {loose.map((line, i) => (
            <p key={`l-${i}`} className="fs-muted">
              {line.replace(/\*\*/g, "")}
            </p>
          ))}
        </>
      ) : null}
    </>
  );
}
