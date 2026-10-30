import { useEffect, useRef, useState } from "react";
import { Button, Input, message } from "antd";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { AskFollowChips, copyText, kindLabel } from "../ui.jsx";

const DIGEST_KIND = new Set(["digest", "literature", "kb_chunk", "page"]);

const LEAK = /i should |the rule says|don't read|do not read|mark sources|like talking to a lab mate|so the lab has|i'll present|i will present|回答要求|不要把编号|作为助手|综上所述|looking at |key observations/i;

function papersToBibtex(sources) {
  return (sources || [])
    .filter((row) => row?.title && (row.doi || row.kind === "external" || row.kind === "scholar" || row.kind === "literature"))
    .slice(0, 20)
    .map((row, index) => {
      const authors = Array.isArray(row.authors)
        ? row.authors.map((a) => (typeof a === "string" ? a : a?.name)).filter(Boolean).join(" and ")
        : "";
      const key = String(row.doi || row.title || index).replace(/[^\w]+/g, "_").slice(0, 40) || `p${index}`;
      return `@article{${key},\n  title={${row.title}},\n  author={${authors}},\n  year={${row.year || ""}},\n  doi={${row.doi || ""}}\n}`;
    })
    .join("\n\n");
}

function plainSpeak(text) {
  const kept = String(text || "")
    .split(/\n+/)
    .filter((line) => line.trim() && !LEAK.test(line) && !/·\s*成员\s*$/.test(line.trim()) && !/^the materials\b/i.test(line.trim()));
  return kept
    .join("\n")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/ +([，。；、])/g, "$1")
    .trim();
}

function speakLines(text) {
  return plainSpeak(text)
    .split(/\n+/)
    .flatMap((line) => line.split(/(?<=。)(?=\d+[.、])/))
    .map((line) => line.replace(/^#{1,6}\s+/, "").replace(/^>\s?/, "").trim())
    .filter(Boolean);
}

const GROUP_TITLE = {
  person: "这位作者",
  own_work: "这位作者自己的工作",
  cited_by: "引用了它的",
  cites: "它引用的",
  same_author: "同一作者",
  co_cited: "常一起被引",
  bib_coupled: "参考文献相近",
  topic: "主题相近的",
  same_direction: "同一方向",
  direction: "组里的方向",
  in_lab: "组里的文献",
  outside: "组外的论文",
  patent: "专利",
  web: "网页",
  "": "其他出处",
};
const GROUP_ORDER = Object.keys(GROUP_TITLE);

function previewState(preview) {
  if (preview?.already) return "组里已有";
  if (preview?.oa && preview.oa.pdf_url) return "能拿到公开 PDF";
  return "只有题录";
}

function isOutside(row) {
  const kind = row.kind || "";
  if (kind === "external" || kind === "web" || kind === "web_page" || kind === "scholar" || row.external) return true;
  return kind === "citation" && !row.literature_item_id && String(row.source_id || "").startsWith("ref:");
}

function groupOf(row) {
  if (row.kind === "direction") return "direction";
  if (row.kind === "literature" || row.kind === "kb_chunk") return "in_lab";
  const host = hostOf(row);
  const title = row.title || row.filename || "";
  if (/patent/i.test(host) || /专利/.test(title)) return "patent";
  if (row.kind === "web" || row.kind === "web_page") return "web";
  if (isOutside(row)) return "outside";
  if (row.kind === "author") return "person";
  const kind = (row.relations || [])[0] || row.relation || "";
  return GROUP_TITLE[kind] ? kind : "";
}

function hostOf(row) {
  const site = String(row?.site || "").replace(/^www\./, "");
  if (site) return site;
  const raw = row?.url || row?.href || row?.open_href || "";
  try {
    return new URL(raw).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function roleWord(row) {
  if (row?.role === "teacher") return "导师";
  if (row?.role === "student") return "学生";
  return "";
}

const READ_WORD = { reading: "在读", want_read: "想读", read: "已读", finished: "已读", dropped: "放下" };
const TASK_WORD = { todo: "待办", doing: "进行中", done: "已完成", open: "待办" };

function sourceMark(row) {
  if ((row?.kind || "") === "member") {
    return [roleWord(row), row.member_title, row.member_dirs].filter(Boolean).join(" · ");
  }
  const kind = row?.kind_label || kindLabel(row?.kind);
  if ((row?.kind || "") === "literature") {
    return [kind, READ_WORD[row.my_reading]].filter(Boolean).join(" · ");
  }
  if ((row?.kind || "") === "task") {
    return [kind, TASK_WORD[row.task_status], row.assignee].filter(Boolean).join(" · ");
  }
  if ((row?.kind || "") === "work_log" || (row?.kind || "") === "activity") {
    return [kind, row.actor].filter(Boolean).join(" · ");
  }
  if ((row?.kind || "") === "glossary") {
    const term = row.locked ? "锁定不译" : row.term_tgt ? "已定译" : "未定译";
    return [kind, term].filter(Boolean).join(" · ");
  }
  if ((row?.kind || "") === "meeting") {
    return [kind, row.held_at].filter(Boolean).join(" · ");
  }
  return kind;
}

function SourceChip({ labId, row, onIngested }) {
  const kind = row.kind || "";
  const title = row.filename || row.title || row.heading_path || "来源";
  const [added, setAdded] = useState(false);
  const [preview, setPreview] = useState(null);
  const host = hostOf(row);
  const year = row.year || row.publish_year || "";
  const meta = [
    year ? String(year) : "",
    host,
    isOutside(row) ? "还没放进这个组" : sourceMark(row),
    row.sweep_id && kind === "literature" ? "组里已有" : "",
    row.why || "",
    row.page_no || row.page ? `第 ${row.page_no || row.page} 页` : "",
  ].filter(Boolean);
  const href = kind === "web" || kind === "web_page" ? row.url || row.href || "" : row.open_href || row.href || (row.literature_item_id ? `/literature/${row.literature_item_id}` : "");
  const externalLink = href && /^https?:/i.test(href);
  const internalLink = href && !externalLink && (DIGEST_KIND.has(kind) || kind === "member" || kind === "direction" || href.startsWith("/"));
  const titleNode = externalLink ? (
    <a className="fs-ask-paper-title" href={href} target="_blank" rel="noreferrer">
      {title}
    </a>
  ) : internalLink ? (
    <Link className="fs-ask-paper-title" to={href}>
      {title}
    </Link>
  ) : (
    <span className="fs-ask-paper-title">{title}</span>
  );
  return (
    <article className="fs-ask-paper">
      {titleNode}
      {meta.length ? <p className="fs-ask-paper-meta">{meta.join(" · ")}</p> : null}
      {isOutside(row) && canIngest(row) ? (
        <div className="fs-ask-paper-act">
          {added ? (
            <span className="fs-muted">已放进组里</span>
          ) : (
            <Button
              size="small"
              type="link"
              onClick={async () => {
                const ref = {
                  title,
                  authors: row.authors || [],
                  abstract: kind === "citation" ? "" : row.content || row.excerpt || "",
                  doi: row.doi || null,
                  url: row.open_href || row.href || row.url || null,
                  arxiv_id: row.arxiv_id || null,
                  openalex_id: row.openalex_id || null,
                  pdf_url: row.pdf_url || null,
                  cluster_id: row.cluster_id || null,
                  cnki_id: row.cnki_id || null,
                  venue: row.venue || null,
                  publish_year: row.year || null,
                };
                try {
                  const checked = await api.importCheck(labId, { refs: [ref] });
                  setPreview((checked.data?.items || [])[0] || { title, oa: {} });
                } catch {
                  setPreview(null);
                  message.error("这次没预览成。过一会儿再收。");
                }
              }}
            >
              收进组里
            </Button>
          )}
          {preview ? (
            <span className="fs-muted">
              {previewState(preview)}
              {preview.capture ? " · 订阅全文用文献捕手" : ""}
              {!preview.already ? (
                <Button
                  size="small"
                  type="link"
                  onClick={async () => {
                    await api.addLiterature(labId, {
                      title: preview.title || title,
                      authors: preview.authors || row.authors || [],
                      abstract: preview.meta?.abstract || row.excerpt || "",
                      doi: preview.doi || row.doi || null,
                      url: preview.url || row.url || null,
                      arxiv_id: preview.arxiv_id || row.arxiv_id || null,
                      openalex_id: preview.openalex_id || row.openalex_id || null,
                      cnki_id: preview.cnki_id || row.cnki_id || null,
                      venue: preview.meta?.venue || row.venue || null,
                      publish_year: preview.year || row.year || null,
                      fetch_oa_pdf: Boolean(preview.oa?.pdf_url),
                      oa_pdf_url: preview.oa?.pdf_url || null,
                    });
                    setAdded(true);
                    setPreview(null);
                    onIngested?.(title);
                  }}
                >
                  确认收进
                </Button>
              ) : null}
            </span>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function asksRoster(question) {
  const q = question || "";
  if (/我的导师|我导师/.test(q)) return false;
  if (/导师/.test(q) && /谁|哪|有没有|是不是/.test(q)) return true;
  return /成员有谁|有哪些人|组里有谁|都有谁/.test(q);
}

function canIngest(row) {
  if (!isOutside(row)) return false;
  const host = hostOf(row);
  if (/patent|genius\.|deepwiki\.|zhihu\.|csdn\./i.test(host)) return false;
  const hasId = Boolean(row.doi || row.arxiv_id || row.openalex_id || row.pdf_url || row.cluster_id || row.cnki_id);
  if (/scholar\.google/i.test(host) && !hasId) return false;
  if (row.kind === "web" || row.kind === "web_page") return hasId;
  return Boolean(row.title) || hasId;
}

function PeopleList({ sources, question }) {
  if (!asksRoster(question)) return null;
  const people = (sources || []).filter((row) => row.kind === "member");
  const aboutAdvisor = /导师/.test(question || "") && !/我的导师|我导师/.test(question || "");
  const shown = aboutAdvisor ? people.filter((row) => row.role === "teacher") : people.slice(0, 8);
  if (!shown.length) return null;
  return (
    <ul className="fs-ask-people">
      {shown.map((row) => {
        const mark = sourceMark(row);
        return (
          <li key={row.id || row.source_id || row.title}>
            <strong>{row.title || row.filename || "这位"}</strong>
            {mark ? <span className="fs-muted"> · {mark}</span> : null}
          </li>
        );
      })}
      {shown.length < people.length ? <li className="fs-muted">还有 {people.length - shown.length} 人</li> : null}
    </ul>
  );
}

const PAPER_CAP = 6;

function SourceGroup({ title, rows, labId, onIngested }) {
  const [open, setOpen] = useState(false);
  const shown = open ? rows : rows.slice(0, PAPER_CAP);
  const rest = rows.length - shown.length;
  const keyOf = (row) => row.source_id || row.chunk_id || row.open_href || row.title;
  return (
    <section className="fs-ask-sources is-grouped">
      <h3 className="fs-ask-group-title">
        {title}
        <span className="fs-muted"> {rows.length}</span>
      </h3>
      {shown.map((row) => (
        <SourceChip key={keyOf(row)} labId={labId} row={row} onIngested={onIngested} />
      ))}
      {rest > 0 ? (
        <button type="button" className="fs-link-btn" onClick={() => setOpen(true)}>
          还有 {rest} 条
        </button>
      ) : null}
    </section>
  );
}

export function SourceList({ labId, sources, onIngested }) {
  const seen = new Set();
  const rows = (sources || []).filter((row) => {
    if (row.kind === "member" || row.kind === "identity" || row.kind === "sweep") return false;
    const title = row.filename || row.title || row.heading_path || "来源";
    if (seen.has(title)) return false;
    seen.add(title);
    return true;
  });
  if (!rows.length) return null;
  const kept = rows.filter((row) => row.used !== false);
  const dropped = rows.filter((row) => row.used === false);
  const buckets = new Map();
  for (const row of kept) {
    const g = groupOf(row);
    if (!buckets.has(g)) buckets.set(g, []);
    buckets.get(g).push(row);
  }
  const named = [...buckets.keys()].filter((g) => g && g !== "person");
  const droppedBlock = dropped.length ? (
    <details className="fs-ask-gap">
      <summary>查过但没采用 {dropped.length} 条</summary>
      <ul>
        {dropped.slice(0, 12).map((row) => (
          <li key={row.source_id || row.title}>
            {row.title || row.filename || "来源"}
            {row.drop_reason ? <span className="fs-muted"> · {row.drop_reason}</span> : null}
          </li>
        ))}
      </ul>
    </details>
  ) : null;
  if (!kept.length) return droppedBlock;
  if (!named.length) {
    return (
      <>
        <SourceGroup title="出处" rows={kept} labId={labId} onIngested={onIngested} />
        {droppedBlock}
      </>
    );
  }
  return (
    <div className="fs-ask-groups">
      {GROUP_ORDER.filter((g) => buckets.has(g)).map((g) => (
        <SourceGroup key={g || "rest"} title={GROUP_TITLE[g]} rows={buckets.get(g)} labId={labId} onIngested={onIngested} />
      ))}
      {droppedBlock}
    </div>
  );
}

function MissingCite({ items }) {
  const rows = (Array.isArray(items) ? items : [])
    .map((s) => String(s || "").replace(/\[S\d+\]/g, "").trim())
    .filter((s) => s.length >= 8);
  if (!rows.length) return null;
  return (
    <details className="fs-ask-gap">
      <summary>有 {rows.length} 句没有标来源</summary>
      <p>这几句没有指向下面列出的论文或网页，先别当成已经查实的。</p>
      <ul>
        {rows.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>
    </details>
  );
}

function AskBackChoices({ pack, disabled, onPick }) {
  const options = pack?.options || [];
  if (!options.length) return null;
  const outside = pack.where !== "lab";
  return (
    <div className="fs-ask-choices">
      <span className="fs-muted">{outside ? "还不是这个组的成员" : "论文署名里的作者"}</span>
      {options.map((opt) => {
        const bits = String(opt.label || "").split(" · ").filter(Boolean);
        const name = bits[0] || opt.label;
        const rest = bits.slice(1);
        return (
          <button
            key={opt.author_id || opt.label}
            type="button"
            className="fs-ask-choice is-person"
            disabled={disabled}
            onClick={() => onPick(opt.question)}
          >
            <strong>{name}</strong>
            {rest.length ? <span>{rest.join(" · ")}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

function sourceAt(sources, n) {
  const rows = sources || [];
  const i = Number(n) - 1;
  return rows[i] || null;
}

function Inline({ text, sources, onCite }) {
  const bits = String(text || "").split(/\[S(\d+)\]/g);
  return bits.map((bit, i) => {
    if (i % 2 === 1) {
      const row = sourceAt(sources, bit);
      const title = row?.title || row?.filename || `出处 ${bit}`;
      return (
        <button key={i} type="button" className="fs-cite" title={title} onClick={() => row && onCite?.(row)}>
          {bit}
        </button>
      );
    }
    const strong = bit.split(/(\*\*[^*]+\*\*)/g);
    return strong.map((part, j) =>
      part.startsWith("**") && part.endsWith("**") ? <strong key={`${i}-${j}`}>{part.slice(2, -2)}</strong> : <span key={`${i}-${j}`}>{part}</span>
    );
  });
}

function answerBlocks(text) {
  const blocks = [];
  let list = null;
  for (const line of speakLines(text)) {
    const item = line.match(/^(?:[-*]|\d+[.、])\s+(.*)$/);
    if (item) {
      if (!list) {
        list = { type: "list", items: [] };
        blocks.push(list);
      }
      list.items.push(item[1]);
      continue;
    }
    list = null;
    blocks.push({ type: "p", text: line });
  }
  return blocks;
}

function AnswerBody({ text, sources, onCite, streaming }) {
  const blocks = answerBlocks(text);
  if (!blocks.length) return null;
  return (
    <div className={streaming ? "fs-ask-stream fs-ask-answer" : "fs-ask-answer"}>
      {blocks.map((block, n) =>
        block.type === "list" ? (
          <ul key={n} className="fs-ask-list">
            {block.items.map((item, i) => (
              <li key={i}>
                <Inline text={item} sources={sources} onCite={onCite} />
              </li>
            ))}
          </ul>
        ) : (
          <p key={n}>
            <Inline text={block.text} sources={sources} onCite={onCite} />
          </p>
        )
      )}
    </div>
  );
}

function SendMark({ stop }) {
  if (stop) {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect x="7" y="7" width="10" height="10" rx="1.5" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 19V6M12 6l-5 5M12 6l5 5" />
    </svg>
  );
}

function IdentityCards({ sources, labId }) {
  const rows = (sources || []).filter((row) => row.kind === "identity" && row.identity);
  if (!rows.length) return null;
  return (
    <div className="fs-ask-sources">
      {rows.map((row) => {
        const idn = row.identity || {};
        const bits = [idn.affiliations?.[0], idn.topics?.[0], idn.h_index ? `h ${idn.h_index}` : ""].filter(Boolean);
        return (
          <div key={row.id || row.title} className="fs-ask-source">
            <strong>{row.title}</strong>
            <span className="fs-muted"> · {idn.confirmed ? "已确认" : "待确认"}</span>
            {bits.length ? <span className="fs-muted"> · {bits.join(" · ")}</span> : null}
            {idn.user_id && !idn.confirmed ? (
              <>
                <Button
                  size="small"
                  type="link"
                  onClick={async () => {
                    try {
                      await api.confirmIdentity(labId, idn.user_id, {
                        openalex_id: idn.openalex_id,
                        orcid: idn.orcid,
                        scholar_id: idn.scholar_id,
                        name: row.title,
                      });
                      message.success("已写进档案");
                    } catch {
                      message.error("没写进档案。本人或导师才能确认。");
                    }
                  }}
                >
                  是这位，写进档案
                </Button>
                <Button
                  size="small"
                  type="link"
                  onClick={async () => {
                    try {
                      await api.confirmIdentity(labId, idn.user_id, {
                        reject: true,
                        openalex_id: idn.openalex_id,
                        scholar_id: idn.scholar_id,
                      });
                      message.success("已记下：不是这位");
                    } catch {
                      message.error("没记下。本人或导师才能确认。");
                    }
                  }}
                >
                  不是这位
                </Button>
              </>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function SweepCards({ labId, sources }) {
  const rows = (sources || []).filter((row) => row.kind === "sweep" && row.sweep);
  const [batch, setBatch] = useState(null);
  const [busyBatch, setBusyBatch] = useState(false);
  if (!rows.length) return null;
  return rows.map((row) => {
    const papers = (sources || []).filter((item) => item.sweep_id && item.sweep_id === row.sweep_id && item.kind === "external" && item.title);
    return (
      <details key={row.id || row.sweep_id} className="fs-inline-more" open>
        <summary>检索说明</summary>
        <p className="fs-muted">{(row.sweep.queries || []).join("；")}</p>
        {(row.sweep.themes || []).length ? <p>主题：{row.sweep.themes.map((t) => t.name).join("、")}</p> : null}
        {(row.sweep.missing || []).length ? (
          <p className="fs-muted">可能漏了：{row.sweep.missing.map((m) => m.title).filter(Boolean).join("、")}</p>
        ) : null}
        {papers.length ? (
          <p>
            <Button
              size="small"
              disabled={busyBatch}
              onClick={async () => {
                setBusyBatch(true);
                try {
                  const checked = await api.importCheck(labId, {
                    refs: papers.map((item) => ({
                      title: item.title,
                      authors: item.authors || [],
                      doi: item.doi || null,
                      url: item.url || item.href || null,
                      arxiv_id: item.arxiv_id || null,
                      publish_year: item.year || null,
                    })),
                  });
                  setBatch(checked.data?.items || []);
                } catch {
                  message.error("这次没预览成。过一会儿再收。");
                } finally {
                  setBusyBatch(false);
                }
              }}
            >
              把留下的收进组里
            </Button>
          </p>
        ) : null}
        {batch?.length ? (
          <div>
            {batch.map((item) => (
              <p key={item.title} className="fs-muted">
                {item.title} · {previewState(item)}
              </p>
            ))}
            <Button
              size="small"
              type="primary"
              disabled={busyBatch}
              onClick={async () => {
                setBusyBatch(true);
                try {
                  const pending = batch.filter((item) => !item.already);
                  const results = await Promise.allSettled(
                    pending.map((item) =>
                      api.addLiterature(labId, {
                        title: item.title,
                        authors: item.authors || [],
                        doi: item.doi || null,
                        url: item.url || null,
                        arxiv_id: item.arxiv_id || null,
                        publish_year: item.year || null,
                        fetch_oa_pdf: Boolean(item.oa?.pdf_url),
                        oa_pdf_url: item.oa?.pdf_url || null,
                      }),
                    ),
                  );
                  const failed = results.filter((item) => item.status === "rejected").length;
                  if (!pending.length) message.info("这些组里已经有了");
                  else if (failed) message.warning(`${pending.length - failed} 篇已收进组里，${failed} 篇这次没写上。`);
                  else message.success("已按预览收进组里");
                  setBatch(null);
                } catch {
                  message.error("有的没收进去。可以再试一次。");
                } finally {
                  setBusyBatch(false);
                }
              }}
            >
              确认收进这批
            </Button>
          </div>
        ) : null}
      </details>
    );
  });
}

export function AssistantChat({ labId, turns, busy, onAsk, onStop, placeholder, welcome, quiet, scopeSlot, starters, prefill, actions, onOpenSource, pendingConfirm, onConfirm }) {
  const [text, setText] = useState(prefill || "");
  useEffect(() => {
    if (prefill) setText(prefill);
  }, [prefill]);
  const [note, setNote] = useState("");
  const [panel, setPanel] = useState(null);
  const [vote, setVote] = useState("");
  const [pinned, setPinned] = useState(true);
  const threadRef = useRef(null);
  const stickRef = useRef(true);
  const onThreadScroll = () => {
    const el = threadRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    stickRef.current = near;
    setPinned(near);
  };
  useEffect(() => {
    const el = threadRef.current;
    const last = turns[turns.length - 1];
    if (last && !last.answer) stickRef.current = true;
    if (!el || !stickRef.current) return;
    const frame = requestAnimationFrame(() => {
      if (!stickRef.current || !threadRef.current) return;
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [turns, busy]);
  const send = () => {
    const q = text.trim();
    if (!q || busy) return;
    setText("");
    onAsk(q);
  };
  const mark = async (turn, useful) => {
    const chunk = (turn.sources || []).find((s) => s.chunk_id)?.chunk_id;
    setVote(useful ? "up" : "down");
    if (!chunk) {
      setNote(useful ? "已记下：有用" : "已记下：没用");
      return;
    }
    try {
      await api.kbFeedback(labId, { chunk_id: chunk, useful, q: turn.q });
      setNote(useful ? "已记下：有用" : "已记下：没用");
    } catch {
      setNote("这次没记下");
    }
  };
  return (
    <div className={quiet ? "fs-ask-desk is-quiet" : "fs-ask-desk"}>
      <div className="fs-ask-main">
        {welcome}
        {!welcome && quiet && starters?.length ? (
          <div className="fs-ask-starters">
            {starters.map((q) => (
              <button key={q} type="button" className="fs-ask-suggest" onClick={() => onAsk(q)}>
                {q}
              </button>
            ))}
          </div>
        ) : null}
        {turns.length ? (
          <div className="fs-ask-thread-wrap">
          <div className="fs-tutor-thread" ref={threadRef} onScroll={onThreadScroll}>
            {turns.map((turn, i) => {
              const last = i === turns.length - 1;
              const streaming = busy && last;
              const waiting = streaming && !turn.answer;
              const looking = turn.steps?.[turn.steps.length - 1]?.label;
              const seen = [...new Set((turn.steps || []).map((s) => s.label || s.tool).filter(Boolean))];
              return (
                <div className="fs-tutor-turn" key={`${turn.q}-${i}`}>
                  <div className="fs-tutor-bubble is-user">{turn.q}</div>
                  <div className="fs-tutor-bubble is-assistant">
                    {waiting ? <p className="fs-ask-looking">{looking ? `正在查：${looking}` : "正在看组里的文献"}</p> : null}
                    {!streaming && seen.length ? (
                      <details className="fs-inline-more">
                        <summary>{`查了 ${seen.length} 处`}</summary>
                        <ul className="fs-ask-seen">
                          {seen.map((label) => (
                            <li key={label}>{label}</li>
                          ))}
                        </ul>
                      </details>
                    ) : null}
                    <AnswerBody
                      text={turn.answer}
                      sources={turn.sources}
                      onCite={onOpenSource || setPanel}
                      streaming={streaming && !!turn.answer}
                    />
                    {!waiting && !turn.askBack ? <MissingCite items={turn.ungrounded} /> : null}
                    {!waiting ? <PeopleList sources={turn.sources} question={turn.q} /> : null}
                    {turn.stopped ? <p className="fs-ask-stopped">已停止</p> : null}
                    {turn.error && !waiting && !turn.stopped ? (
                      <p className="fs-muted">
                        {turn.answer || "这次没问成。"}{" "}
                        <button type="button" className="fs-link-btn" onClick={() => onAsk(turn.q)}>
                          再问一次
                        </button>
                      </p>
                    ) : null}
                    {turn.askBack && !waiting ? (
                      <AskBackChoices pack={turn.askBack} disabled={busy} onPick={(q) => onAsk(q)} />
                    ) : null}
                    {!waiting && !turn.askBack ? <IdentityCards labId={labId} sources={turn.sources} /> : null}
                    {!waiting && !turn.askBack ? <SweepCards labId={labId} sources={turn.sources} /> : null}
                    {!turn.askBack ? <SourceList labId={labId} sources={turn.sources} onIngested={(title) => setNote(`已把《${title}》收进组里`)} /> : null}
                    {!turn.askBack && !waiting && (turn.sources || []).some((s) => s?.title) ? (
                      <div className="fs-ask-tools">
                        <button
                          type="button"
                          className="fs-link-btn"
                          onClick={() => {
                            const text = papersToBibtex(turn.sources);
                            if (!text) return;
                            const blob = new Blob([text], { type: "application/x-bibtex" });
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement("a");
                            a.href = url;
                            a.download = "shortlist.bib";
                            a.click();
                            URL.revokeObjectURL(url);
                          }}
                        >
                          导出 BibTeX
                        </button>
                        <button
                          type="button"
                          className="fs-link-btn"
                          onClick={async () => {
                            const ids = [...new Set((turn.sources || []).map((s) => s.item_id || s.literature_item_id).filter(Boolean))];
                            if (!ids.length) {
                              message.info("这些还没在组里，先收进组里再加入待读。");
                              return;
                            }
                            let ok = 0;
                            for (const itemId of ids.slice(0, 12)) {
                              try {
                                await api.markRead(itemId, "want_read");
                                ok += 1;
                              } catch {
                                /* 下一篇继续 */
                              }
                            }
                            message.success(ok ? `已把 ${ok} 篇放进你的待读` : "没放进待读");
                          }}
                        >
                          全部加入待读
                        </button>
                      </div>
                    ) : null}
                    {last && !waiting && pendingConfirm?.length ? (
                      <div className="fs-ask-sources">
                        <span className="fs-muted">写入前再确认一次</span>
                        {pendingConfirm.map((item, n) => (
                          <span key={`${item.tool || "write"}-${n}`} className="fs-ask-source">
                            {item.label || item.tool}
                            {item.preview_text ? <span className="fs-muted"> · {item.preview_text}</span> : null}
                          </span>
                        ))}
                        <Button size="small" type="primary" disabled={busy} onClick={onConfirm}>
                          确认执行
                        </Button>
                      </div>
                    ) : null}
                    {!waiting && turn.answer ? (
                      <div className="fs-ask-tools">
                        <span className="fs-ask-tools-hover">
                          <button type="button" onClick={() => copyText(plainSpeak(turn.answer).replace(/\[S\d+\]/g, ""))}>
                            复制
                          </button>
                          {last ? (
                            <button type="button" onClick={() => onAsk(turn.q, { replace: true })} disabled={busy}>
                              重新回答
                            </button>
                          ) : null}
                        </span>
                        <button type="button" className={vote === "up" ? "is-on" : ""} onClick={() => mark(turn, true)}>
                          有用
                        </button>
                        <button type="button" className={vote === "down" ? "is-on" : ""} onClick={() => mark(turn, false)}>
                          没用
                        </button>
                      </div>
                    ) : null}
                    {actions ? actions(turn) : null}
                    {last && turn.followups?.length ? (
                      <AskFollowChips items={turn.followups} disabled={busy} hint="接着可以" onPick={(item) => onAsk(item.q || item.label || item)} />
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
          {pinned ? null : (
            <button
              type="button"
              className="fs-ask-jump"
              onClick={() => {
                const el = threadRef.current;
                stickRef.current = true;
                if (el) el.scrollTop = el.scrollHeight;
                setPinned(true);
              }}
            >
              回到最新
            </button>
          )}
          </div>
        ) : null}
        {note ? <p className="fs-muted">{note}</p> : null}
        <form
          className="fs-ask-compose"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <Input.TextArea
            value={text}
            placeholder={placeholder || "问这个组"}
            autoFocus
            autoSize={{ minRows: 1, maxRows: 6 }}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && busy) {
                e.preventDefault();
                onStop?.();
                return;
              }
              if (e.key === "ArrowUp" && !text && turns.length) {
                e.preventDefault();
                setText(turns[turns.length - 1].q || "");
                return;
              }
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <div className="fs-ask-compose-bar">
            <span className="fs-ask-compose-side">
              {scopeSlot}
              <span className="fs-ask-keyhint">Enter 发送</span>
            </span>
            <button
              type={busy ? "button" : "submit"}
              className="fs-ask-send"
              aria-label={busy ? "停止" : "发送"}
              disabled={!busy && !text.trim()}
              onClick={
                busy
                  ? (e) => {
                      e.preventDefault();
                      onStop?.();
                    }
                  : undefined
              }
            >
              <SendMark stop={busy} />
              <span>{busy ? "停止" : "发送"}</span>
            </button>
          </div>
        </form>
      </div>
      {panel ? (
        <aside className="fs-source-panel">
          <div className="fs-source-panel-head">
            <strong>出处</strong>
            <button type="button" className="fs-link-btn" onClick={() => setPanel(null)}>
              收起
            </button>
          </div>
          <p className="fs-source-title">{panel.title || panel.filename || "这篇"}</p>
          {panel.page_no || panel.page ? <p className="fs-muted">第 {panel.page_no || panel.page} 页</p> : null}
          {panel.excerpt || panel.content ? <p>{panel.excerpt || panel.content}</p> : null}
          {panel.open_href || panel.href || panel.literature_item_id ? (
            <Link to={panel.open_href || panel.href || `/literature/${panel.literature_item_id}`}>打开这一页</Link>
          ) : null}
        </aside>
      ) : null}
    </div>
  );
}
