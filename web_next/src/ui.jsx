import { useState } from "react";
import { Alert, Breadcrumb, Button, Checkbox, Collapse, Dropdown, Input, Modal, Popover, Tag, Typography, message } from "antd";
import { Link } from "react-router-dom";
import { api } from "./api.js";
import { MS_STATUS, READ_STATUS, RUN_STATUS, TASK_STATUS, VERB_LABEL, noticeHref, nodeLabel, literatureHref, formatError, humanize, stripModelReasoning } from "./err.js";
import { METHOD_FIELDS, methodCardText, methodItems, methodSections } from "./methodCard.js";
import { fmtWhen, parseWhen } from "./when.js";

export { fmtWhen, parseWhen };

const { Title, Paragraph } = Typography;

export const MS_STATUS_COLOR = {
  draft: "default",
  internal_review: "blue",
  submitted: "processing",
  revision: "gold",
  published: "green",
  rejected: "red",
};

export const RUN_STATUS_COLOR = {
  pending: "default",
  queued: "default",
  running: "processing",
  paused: "gold",
  succeeded: "green",
  failed: "red",
  cancelled: "default",
  blocked: "gold",
  awaiting_gpu: "gold",
};

export function StatusTag({ status, map }) {
  const labels = map || { ...MS_STATUS, ...RUN_STATUS, ...TASK_STATUS };
  return <Tag color={MS_STATUS_COLOR[status] || RUN_STATUS_COLOR[status]}>{labels[status] || status}</Tag>;
}

export function RoleTag({ role }) {
  return <Tag>{role === "teacher" ? "导师" : role === "student" ? "学生" : role || "—"}</Tag>;
}

export function HintTip({ title, children }) {
  if (children == null || children === false || children === "") return null;
  return (
    <Popover
      trigger="click"
      placement="bottomLeft"
      overlayClassName="fs-hint-overlay"
      title={title || "说明"}
      content={<div className="fs-hint-pop">{children}</div>}
    >
      <button
        type="button"
        className="fs-hint-btn"
        aria-label="说明"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
      >
        ?
      </button>
    </Popover>
  );
}

export function hintTitle(label, hint) {
  return (
    <span className="fs-col-title">
      {label}
      <HintTip title={label}>{hint}</HintTip>
    </span>
  );
}

export function VerbTag({ verb }) {
  return <Tag>{VERB_LABEL[verb] || verb}</Tag>;
}

export function jobResumeKind(row) {
  const t = String(row?.kind || row?.object_type || row?.type || "");
  if (t === "reproduce" || t === "reproduce_run") return "reproduce";
  if (t === "research" || t === "research_run") return "research";
  if (row?.question) return "research";
  return "";
}

export function tellFeedback(data, fallback = "已处理") {
  const fb = data?.feedback;
  const text = humanize(fb ? [fb.title, fb.next_step].filter(Boolean).join(" · ") : fallback);
  const tone = fb?.tone;
  if (tone === "error") message.error(text || fallback);
  else if (tone === "warning") message.warning(text || fallback);
  else if (tone === "success") message.success(text || fallback);
  else message.info(text || humanize(fallback));
}

export async function copyText(text) {
  const value = String(text ?? "");
  if (!value.trim()) {
    message.info("没有可复制的内容");
    return false;
  }
  try {
    if (!navigator.clipboard?.writeText) throw new Error("clipboard");
    await navigator.clipboard.writeText(value);
    message.success("已复制");
    return true;
  } catch {
    message.error("没复制上，请再试一次");
    return false;
  }
}

export async function press(fn, { ok = "已处理", fail = "没做成" } = {}) {
  try {
    const raw = await fn();
    const payload = raw && typeof raw === "object" && raw.data && typeof raw.data === "object" ? raw.data : raw;
    if (payload && typeof payload === "object") tellFeedback(payload, ok);
    else message.success(ok);
    return raw;
  } catch (err) {
    message.error(formatError(err, fail));
    throw err;
  }
}

export function PressButton({
  onPress,
  ok = "已处理",
  fail = "没做成",
  blocked = false,
  blockedReason = "现在不能做",
  pendingLabel,
  children,
  loading: loadingProp,
  disabled,
  ...rest
}) {
  const [loading, setLoading] = useState(false);
  const busy = !!(loading || loadingProp);
  const locked = !!(disabled || blocked);
  const click = async (e) => {
    e?.stopPropagation?.();
    if (busy) return;
    setLoading(true);
    try {
      await press(() => onPress?.(e), { ok, fail });
    } catch {
      /* press already told the user */
    } finally {
      setLoading(false);
    }
  };
  const button = (
    <Button
      {...rest}
      loading={busy}
      disabled={locked || busy}
      onClick={locked ? undefined : click}
      style={locked ? { ...(rest.style || {}), pointerEvents: "none" } : rest.style}
    >
      {busy && pendingLabel ? pendingLabel : children}
    </Button>
  );
  if (!locked) return button;
  return (
    <span
      className="fs-press-wrap"
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        message.info(blockedReason);
      }}
    >
      {button}
    </span>
  );
}

export function whenBlocked(disabled, reason = "现在不能做") {
  return (e) => {
    if (!disabled) return;
    e?.preventDefault?.();
    e?.stopPropagation?.();
    message.info(reason);
  };
}

export function jobBlockedReason(run, action) {
  const st = run?.status || "";
  if (action === "pause") {
    if (st === "awaiting_gpu") return "还在等机器，不用暂停";
    if (st === "pending" || st === "queued") return "还没开始跑，不用暂停";
    if (st === "succeeded" || st === "failed" || st === "cancelled") return "这一次已经结束，不用暂停";
    return "现在不能暂停";
  }
  if (action === "resume") {
    if (st === "running") return "正在跑，不用再恢复";
    if (st === "succeeded") return "已经跑完，不用恢复";
    if (st === "cancelled") return "已取消，不用恢复";
    return "现在不能恢复";
  }
  if (action === "cancel") {
    if (st === "succeeded" || st === "failed" || st === "cancelled") return "这一次已经结束";
    return "现在不能取消";
  }
  if (action === "improve") {
    if (st === "running") return "正在跑，先等这一轮结束";
    if (st === "cancelled") return "已取消，不能接着改";
    return "现在不能改进";
  }
  return "现在不能做";
}

export function RowMore({ items }) {
  const menu = (items || []).filter((it) => it && (it.label || it.to));
  if (!menu.length) return null;
  return (
    <Dropdown
      trigger={["click"]}
      menu={{
        items: menu.map((it, i) => ({
          key: it.key || String(i),
          label: it.to ? <Link to={it.to} onClick={(e) => e.stopPropagation()}>{it.label}</Link> : it.label,
          onClick: (info) => {
            info?.domEvent?.stopPropagation?.();
            it.onClick?.(info);
          },
        })),
      }}
    >
      <Button size="small" type="link" onClick={(e) => e.stopPropagation()}>
        更多
      </Button>
    </Dropdown>
  );
}

export function feedbackAlertType(row, fallback = "info") {
  const tone =
    row?.feedback?.tone ||
    (row?.status === "awaiting_gpu" || row?.error_class === "awaiting_gpu"
      ? "warning"
      : row?.status === "failed"
        ? "error"
        : row?.status === "succeeded"
          ? "success"
          : "");
  if (tone === "error" || tone === "warning" || tone === "success") return tone;
  return fallback;
}

export function FeedbackStrip({ row }) {
  const fb = row?.feedback || {};
  const step = typeof row?.next_step === "object" && row.next_step ? row.next_step : null;
  const href = step?.href || fb.href;
  const chip = step?.label || fb.label;
  const text = (typeof row?.next_step === "string" ? row.next_step : step?.text) || fb.next_step || "";
  const title = fb.title || "";
  const tone =
    fb.tone ||
    (row?.status === "awaiting_gpu" || row?.error_class === "awaiting_gpu" ? "warning" : row?.status === "failed" ? "error" : "info");
  const unusedBibTitles = row?.diagnosis?.unused_bib_titles || row?.unused_bib_titles || [];
  const unusedInjTitles = row?.diagnosis?.unused_injected_titles || row?.unused_injected_titles || [];
  const unusedBib = unusedBibTitles.length;
  const unusedInj = unusedInjTitles.length;
  const firstUnused = String(unusedBibTitles[0] || unusedInjTitles[0] || "").trim();
  const hitN = row?.diagnosis?.search_hit_count || row?.search_hit_count || 0;
  const shortBits = [];
  if (firstUnused) shortBits.push(`「${firstUnused.length > 24 ? `${firstUnused.slice(0, 23)}…` : firstUnused}」`);
  if (unusedBib) shortBits.push(`综述没提到 ${unusedBib} 篇`);
  if (unusedInj) shortBits.push(`组内未交代 ${unusedInj} 篇`);
  const long = title && text && !String(text).includes(title) ? `${title} · ${text}` : text || title;
  const explain = humanize(shortBits.length ? shortBits.join(" · ") : long);
  if (!href && !explain && !chip && !hitN) return null;
  return (
    <span className={`fs-feedback is-${tone}`}>
      {href ? <ActionChip to={href}>{chip || "打开"}</ActionChip> : null}
      {explain ? <span>{href ? ` ${explain}` : explain}</span> : null}
      {hitN && !shortBits.length ? <span className="fs-muted">{explain ? " · " : ""}命中 {hitN}</span> : null}
    </span>
  );
}

export function ResumeJob({ row, onDone }) {
  const kind = jobResumeKind(row);
  const id = row?.object_id || row?.id;
  const [busy, setBusy] = useState(false);
  if (!row?.can_resume || !id || !kind) return null;
  return (
    <Button
      size="small"
      type="link"
      className="fs-resume"
      loading={busy}
      disabled={busy}
      onClick={async (e) => {
        e?.stopPropagation?.();
        e?.preventDefault?.();
        if (busy) return;
        setBusy(true);
        try {
          const r = kind === "reproduce" ? await api.resumeReproduce(id) : await api.resumeRun(id);
          tellFeedback(r.data, "已恢复");
          onDone?.(r.data);
        } catch (err) {
          message.error(formatError(err, "无法恢复"));
        } finally {
          setBusy(false);
        }
      }}
    >
      恢复
    </Button>
  );
}

export function ActivityLine({ event, labId }) {
  const href = noticeHref({ ...event, lab_id: event.lab_id || labId });
  return (
    <span>
      <VerbTag verb={event.verb} />{" "}
      <Link to={href}>{humanize(event.summary || VERB_LABEL[event.verb] || event.verb)}</Link>{" "}
      {event.next_step || event.feedback ? (
        <>
          {" "}
          <FeedbackStrip row={event} />
        </>
      ) : null}
      <ResumeJob row={event} />
      {event.latest_reproduce?.measured ? (
        <span className="fs-muted"> · 已经跑出结果</span>
      ) : event.latest_reproduce ? (
        <span className="fs-muted"> · 还没跑出结果</span>
      ) : null}
      {event.ungrounded_reproduce ? <span className="fs-muted"> · 写了结果，组里还没跑过</span> : null}
      {event.reproduce_href ? (
        <ActionChip to={event.latest_reproduce?.href || event.reproduce_href} tone={event.latest_reproduce?.measured ? "" : "gold"}>
          {event.latest_reproduce ? "查看复现" : "去复现"}
        </ActionChip>
      ) : null}{" "}
      <span className="fs-muted">{fmtWhen(event.created_at)}</span>
    </span>
  );
}

export function ReadTag({ status }) {
  const color = { reading: "green", want_read: "blue", finished: "default", dropped: "red" }[status];
  return <Tag color={color}>{READ_STATUS[status] || status || "未开始"}</Tag>;
}

export function PdfGapLink({ row }) {
  const kind = row?.card_empty?.kind;
  if (!kind) return null;
  const label =
    kind === "running"
      ? "正在提炼"
      : kind === "unusable"
        ? "扫描件"
        : kind === "ungrounded"
          ? "摘录弱"
          : kind === "abstract_only"
            ? row.has_pdf
              ? "仅摘要"
              : "无全文"
            : "无全文";
  return (
    <Link to={row.card_href || literatureHref(row.id, row.last_page_no, { panel: "card" })}>
      {label}
    </Link>
  );
}

export function AnalyzeChip({ row }) {
  if (!row || row.has_method_card) return null;
  const to = row.card_href || literatureHref(row.id || row.literature_item_id, row.last_page_no, { panel: "card" });
  if (!to || to === "/literature") return null;
  return (
    <ActionChip to={to} tone="gold">
      去提炼
    </ActionChip>
  );
}

export function WeekPaperChips({ items, prefix }) {
  if (!items || !items.length) return null;
  return (
    <span className="fs-muted">
      {prefix ? `${prefix} ` : ""}
      {items.map((p) => (
        <Link
          key={p.id || p.href || p.title}
          to={p.href || `/literature/${p.id}`}
          onClick={(e) => e.stopPropagation()}
          style={{ marginRight: 8 }}
        >
          {(p.title || "").slice(0, 22)}
          {p.degraded || p.ground_weak ? (
            <span className="fs-muted"> {p.why || (p.degraded ? "降级" : "接地弱")}</span>
          ) : p.why ? (
            <span className="fs-muted"> {String(p.why).slice(0, 10)}</span>
          ) : null}
        </Link>
      ))}
    </span>
  );
}

export function EmptyHint({ to, children }) {
  if (to) {
    return (
      <span className="fs-muted">
        <Link to={to}>{children}</Link>
      </span>
    );
  }
  return <span className="fs-muted">{children}</span>;
}

export function StreamDropped({ onReconnect, message }) {
  return (
    <Alert
      type="warning"
      showIcon
      message={message || "实时流已断开，已改为定时刷新。可重连。"}
      action={
        <Button size="small" onClick={onReconnect}>
          重连
        </Button>
      }
    />
  );
}

export function dueLabel(m) {
  if (m?.due_label) return m.due_label;
  if (m?.overdue) return "已过期";
  if (m?.due_at) return fmtWhen(m.due_at);
  return "";
}

export function clipUnusedTitle(title, n = 36) {
  const s = String(title || "").trim();
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export function unusedTitleRunHref(rows, title, fallback) {
  const name = String(title || "").trim();
  if (!name) return fallback;
  for (const row of rows || []) {
    const blob = [
      ...(row?.diagnosis?.unused_bib_titles || []),
      ...(row?.unused_bib_titles || []),
      ...(row?.diagnosis?.unused_injected_titles || []),
      ...(row?.unused_injected_titles || []),
    ];
    if (blob.some((t) => String(t || "").trim() === name)) {
      return row.id ? `/runs/${row.id}` : fallback;
    }
  }
  return fallback;
}

export function NotRunTitleChips({ titles, href, more = 0 }) {
  const items = (titles || [])
    .map((t) =>
      typeof t === "string"
        ? { title: t, href }
        : { title: t?.title || "", href: t?.href || href }
    )
    .filter((t) => String(t.title || "").trim());
  if (!items.length) return null;
  return (
    <p className="fs-unused-titles">
      {items.map((it) => {
        const label = clipUnusedTitle(it.title);
        return it.href ? (
          <ActionChip key={it.title} to={it.href} tone="gold">
            {label}
          </ActionChip>
        ) : (
          <Tag key={it.title}>{label}</Tag>
        );
      })}
      {more > 0 ? <span className="fs-muted">另有 {more} 篇</span> : null}
      <HintTip title="还没做复现">还没对着这些篇做复现。和综述没提到不是一回事。</HintTip>
    </p>
  );
}

export function LeftoverTitleChips({ titles, href, more = 0, note }) {
  const items = (titles || [])
    .map((t) =>
      typeof t === "string"
        ? { title: t, href }
        : { title: t?.title || "", href: t?.href || href }
    )
    .filter((t) => String(t.title || "").trim());
  if (!items.length) return null;
  return (
    <p className="fs-unused-titles">
      {items.map((it) => {
        const label = clipUnusedTitle(it.title);
        return it.href ? (
          <ActionChip key={`${it.title}-${it.href}`} to={it.href}>
            {label}
          </ActionChip>
        ) : (
          <Tag key={it.title}>{label}</Tag>
        );
      })}
      {more > 0 ? <span className="fs-muted">另有 {more} 篇</span> : null}
      {note ? <HintTip title="这些题名">{note}</HintTip> : null}
    </p>
  );
}

export function UnreadTitleChips({ titles, href, more = 0, mine = false }) {
  return (
    <LeftoverTitleChips
      titles={titles}
      href={href}
      more={more}
      note={mine ? "这人还没开始读。" : "组里没人在读这些篇。"}
    />
  );
}

export function NoCardTitleChips({ titles, href, more = 0 }) {
  return (
    <LeftoverTitleChips
      titles={titles}
      href={href}
      more={more}
      note="打开一篇，点「提炼方法」。"
    />
  );
}

export function RelatedWorkTitleChips({ titles, href, more = 0 }) {
  return (
    <LeftoverTitleChips
      titles={titles}
      href={href}
      more={more}
      note="论文还没写相关工作。"
    />
  );
}

export function CoreMissingTitleChips({ titles, href, more = 0 }) {
  return (
    <LeftoverTitleChips
      titles={titles}
      href={href}
      more={more}
      note="这几篇还没写进正文。"
    />
  );
}

export function UnusedTitleChips({ titles, href, more = 0, rows }) {
  const names = (titles || []).map((t) => String(t || "").trim()).filter(Boolean);
  if (!names.length) return null;
  return (
    <p className="fs-unused-titles">
      {names.map((title) => {
        const to = rows ? unusedTitleRunHref(rows, title, href) : href;
        const label = clipUnusedTitle(title);
        return to ? (
          <ActionChip key={title} to={to}>
            {label}
          </ActionChip>
        ) : (
          <Tag key={title}>{label}</Tag>
        );
      })}
      {more > 0 ? <span className="fs-muted">另有 {more} 篇</span> : null}
      <HintTip title="综述没提到">综述写完了，但这几篇没写进去。</HintTip>
    </p>
  );
}

export function UnusedResearchAlert({ note }) {
  if (!note?.text && !(note?.titles || []).length) return null;
  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 12 }}
      message={humanize(note.text || "上次综述没提到这些论文。")}
      description={
        <>
          {note.href ? <ActionChip to={note.href}>{note.label || "打开文献调研"}</ActionChip> : null}
          <UnusedTitleChips titles={note.titles || []} href={note.href} more={note.title_more || 0} />
        </>
      }
    />
  );
}

const TOOL_LABELS = {
  search_lab_kb: "搜知识库",
  search_lab_literature: "搜文献库",
  search_open_sources: "搜外面的论文",
  get_direction_tree: "看方向",
  get_lab_members: "看名册",
  get_member_profile: "看档案",
  get_milestones: "看节点",
  get_my_next_step: "看下一步",
  get_recent_meetings: "看组会",
  get_lab_activity: "看动态",
  get_lab_glossary: "看术语",
  get_lab_tasks: "看任务",
  get_lab_kb_docs: "看知识库目录",
  get_lab_files: "看资料柜",
  get_starter_reading: "看先读",
  get_lab_literature: "看文献目录",
  get_paper_readers: "看谁在读",
  get_my_reading: "看我的进度",
  get_my_tutor_history: "看辅导记录",
  get_lab_manuscripts: "看论文目录",
  get_lab_runs: "看作业",
  get_job_preflight: "看预检",
  get_unused_research: "看没写到的综述",
  get_manuscript_brief: "看论文摘要",
  get_manuscript_comments: "看稿评",
  get_manuscript_versions: "看稿版本",
  get_method_compare: "对照这篇的方法",
  get_core_coverage: "核核心文献",
  get_lab_datasets: "看数据集",
  get_run_detail: "看作业详情",
  get_related_work: "看相关工作",
  get_honesty_brief: "看有没有跑过",
  get_citation_links: "看引用关系",
  get_paper_page_text: "取页正文",
  get_page_translation: "取页译文",
  get_paper_structure: "看章节",
  get_paper_biblio: "看书目",
  get_method_card: "看这篇的方法",
  get_reproduce_evidence: "看复现证据",
  get_my_notes: "看我的笔记",
  write_note: "写笔记",
  write_highlight: "划高亮",
  writeback_manuscript: "写回论文",
  submit_research_run: "提交调研",
  submit_reproduce_run: "提交复现",
};

const KIND_LABELS = {
  page: "正文",
  abstract: "摘要",
  structure: "章节",
  biblio: "书目",
  method_card: "这篇的方法",
  method_card_claim: "对不上原文的句子",
  page_translation: "页译文",
  note: "笔记",
  highlight: "高亮",
  literature: "文献",
  kb_chunk: "知识库",
  kb_doc: "文档",
  kb: "知识库",
  lab_file: "资料",
  reading: "在读",
  tutor: "辅导",
  manuscript: "论文",
  manuscript_comment: "稿评",
  manuscript_version: "稿版本",
  citation: "引用",
  reproduce: "复现",
  reproduce_gaps: "复现差距",
  claimed_metrics: "论文声称",
  research_run: "文献调研",
  reproduce_run: "复现作业",
  queued: "排队",
  job_preflight: "预检",
  unused_research: "没写到",
  dataset: "数据集",
  artifact: "产物",
  honesty: "有没有跑过",
  related: "组内相关",
  direction: "方向",
  member: "成员",
  profile: "档案",
  milestone: "节点",
  next_step: "下一步",
  meeting: "组会",
  work_log: "工作日志",
  activity: "动态",
  glossary: "术语",
  task: "任务",
  external: "外面的论文",
  lab_intro: "组里写的",
  core_mentioned: "已点名核心",
  core_missing: "未点名核心",
};

export function toolLabel(name) {
  return TOOL_LABELS[name] || name || "查证";
}

export function kindLabel(kind) {
  return KIND_LABELS[kind] || "";
}

export function sourcePage(src) {
  const n = src?.page ?? src?.page_no;
  if (n == null || n === "") return null;
  const num = Number(n);
  return Number.isFinite(num) && num > 0 ? num : null;
}

export function sourceItemId(src) {
  return src?.item_id || src?.literature_item_id || "";
}

export function sourceHref(src) {
  if (src?.href || src?.open_href) return src.href || src.open_href;
  const item = sourceItemId(src);
  const page = sourcePage(src);
  if (item) return literatureHref(item, page, { panel: "tutor" });
  return "";
}

export function followupsFromCitations(citations, ungrounded) {
  const out = [];
  const add = (q) => {
    const text = String(q || "").trim();
    if (text && !out.includes(text)) out.push(text.slice(0, 40));
  };
  (citations || []).slice(0, 4).forEach((c) => {
    const kind = c.kind || "";
    const title = String(c.title || "").trim().slice(0, 28);
    if (title.includes("没有") || title.includes("对不上")) return;
    const page = sourcePage(c);
    if ((kind === "page" || kind === "page_translation") && page) add(`第${page}页还讲了什么？`);
    else if (kind === "literature" && title) add(`谁在读《${title.slice(0, 12)}》？`);
    else if (kind === "biblio" && title) add(`${title} 被谁引用？`);
    else if (kind === "dataset" && title) add(`${title} 被哪次作业用了？`);
    else if ((kind === "research_run" || kind === "reproduce_run" || kind === "queued") && title) add(`${title} 卡在哪？`);
    else if (kind === "glossary" && title && !title.includes("没有")) add(`${title} 锁定不译吗？`);
    else if (kind === "method_card") add("哪句对不上原文？");
    else if (kind === "task" && title) add(`${title} 到期了吗？`);
    else if (kind === "milestone" && title) add(`${title} 到期了吗？`);
    else if (kind === "artifact" && title) add(`${title} 里写了什么？`);
    else if (kind === "lab_file" && title) add(`${title} 里写了什么？`);
    else if (kind === "note") add("我还记过什么？");
    else if (kind === "reproduce") add("声称和实测差在哪？");
    else if (kind === "reading") add("这篇我读到哪了？");
  });
  if (ungrounded) add("这段依据在哪一页？");
  return out.slice(0, 3);
}

export function AskFollowChips({ items, onPick, disabled, hint }) {
  const seen = new Set();
  const rows = [];
  for (const x of items || []) {
    const text = String(typeof x === "string" ? x : x?.q || x?.label || "").trim();
    const href = typeof x === "object" && x ? String(x.href || "") : "";
    if (!text || seen.has(text)) continue;
    seen.add(text);
    rows.push({ text: text.slice(0, 40), href });
    if (rows.length >= 3) break;
  }
  if (!rows.length) return null;
  if (!onPick && !rows.some((r) => r.href)) return null;
  return (
    <div className="fs-tutor-chips" style={{ marginTop: 8 }}>
      {hint ? <span className="fs-muted" style={{ marginRight: 8 }}>{hint}</span> : null}
      {rows.map((row) =>
        row.href ? (
          <Link key={row.text} className="fs-chip" to={row.href}>
            {row.text}
          </Link>
        ) : (
          <span key={row.text} className="fs-press-wrap" onClick={whenBlocked(!!disabled, "正在回答，请稍等")}>
          <button type="button" className="fs-chip" disabled={!!disabled} onClick={() => onPick?.(row.text)}>
            {row.text}
          </button>
          </span>
        )
      )}
    </div>
  );
}

export function AiResultBar({
  text,
  copyLabel = "复制",
  onRetry,
  retryLabel = "再跑一次",
  busy,
  traceId,
  steps,
  degraded,
  availableTools,
  followups,
  onPick,
}) {
  return (
    <>
      <AgentRuntimeNotice
        degraded={degraded}
        degradedTo={degraded === "chat" || degraded ? "chat" : ""}
        availableTools={availableTools}
      />
      <AskToolSteps steps={steps} />
      <div className="fs-tutor-actions">
        {onRetry ? (
          <Button size="small" type="link" disabled={!!busy} onClick={onRetry}>
            {retryLabel}
          </Button>
        ) : null}
        {text ? (
          <Button
            size="small"
            type="link"
            onClick={() => copyText(text)}
          >
            {copyLabel}
          </Button>
        ) : null}
      </div>
      <AskFollowChips items={followups} disabled={busy} hint="你可以接着做" onPick={onPick} />
      {traceId || (steps || []).length ? (
        <AgentTraceBox traceId={traceId} stepCount={(steps || []).length} steps={steps} />
      ) : null}
    </>
  );
}

export function AskToolSteps({ steps }) {
  if (!steps?.length) return null;
  return (
    <ul className="fs-muted" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
      {steps.filter((s) => s && typeof s === "object").map((s, i) => (
        <li key={`${s.tool || s.label || "step"}-${i}`}>
          {s.label && s.label !== s.tool ? s.label : toolLabel(s.tool)}
          {s.ok === false ? " · 这篇页上没取到，可换一句或先提炼方法" : ""}
          {s.summary || s.result_summary ? ` · ${s.summary || s.result_summary}` : ""}
        </li>
      ))}
    </ul>
  );
}

export function aiToolsLine(tools) {
  const names = (tools || []).map(toolLabel).filter(Boolean);
  if (!names.length) return null;
  return <span className="fs-muted"> · 用过：{names.join("、")}</span>;
}

const READ_WORD = { reading: "在读", want_read: "想读", read: "已读", finished: "已读", dropped: "放下" };
const TASK_WORD = { todo: "待办", doing: "进行中", done: "已完成", open: "待办" };

function chipMark(c) {
  if ((c?.kind || "") === "member") {
    const role = c.role === "teacher" ? "导师" : c.role === "student" ? "学生" : "";
    const mark = [role, c.member_title, c.member_dirs].filter(Boolean).join(" · ");
    return mark || c.kind_label || kindLabel(c.kind);
  }
  const kind = c?.kind_label || kindLabel(c?.kind);
  if ((c?.kind || "") === "literature") {
    return [kind, READ_WORD[c.my_reading]].filter(Boolean).join(" · ");
  }
  if ((c?.kind || "") === "task") {
    return [kind, TASK_WORD[c.task_status], c.assignee].filter(Boolean).join(" · ");
  }
  if ((c?.kind || "") === "work_log" || (c?.kind || "") === "activity") {
    return [kind, c.actor].filter(Boolean).join(" · ");
  }
  if ((c?.kind || "") === "glossary") {
    const term = c.locked ? "锁定不译" : c.term_tgt ? "已定译" : "未定译";
    return [kind, term].filter(Boolean).join(" · ");
  }
  if ((c?.kind || "") === "meeting") {
    return [kind, c.held_at].filter(Boolean).join(" · ");
  }
  return kind;
}

export function AskSourceChips({ citations, onOpen }) {
  const rows = citations || [];
  if (!rows.length) return null;
  return (
    <div className="fs-tutor-chips" style={{ marginTop: 8 }}>
      {rows.map((c, i) => {
        const page = sourcePage(c);
        const kind = chipMark(c);
        const text = `${c.label || "S"} ${c.title || ""}${page ? ` p.${page}` : ""}${kind ? ` · ${kind}` : ""}`.trim();
        const href = sourceHref(c);
        const key = c.source_id || c.id || `${c.label || "s"}-${i}`;
        if (onOpen) {
          return (
            <button key={key} type="button" className="fs-chip" title={c.excerpt || c.title || ""} onClick={() => onOpen(c)}>
              {text}
            </button>
          );
        }
        if (href) {
          return (
            <Link key={key} className="fs-chip" to={href} title={c.excerpt || c.title || ""}>
              {text}
            </Link>
          );
        }
        return (
          <span key={key} className="fs-chip" title={c.excerpt || ""}>
            {text}
          </span>
        );
      })}
    </div>
  );
}

function stepLine(s) {
  const name = s.label && s.label !== s.tool ? s.label : toolLabel(s.tool || s.kind);
  const summary = s.summary || s.result_summary || "";
  const err = s.error ? ` · ${s.error}` : "";
  const nxt = s.empty_next?.title ? ` · ${s.empty_next.title}` : "";
  return `${name}${summary ? ` · ${summary}` : ""}${err}${nxt}`;
}

export function AgentTraceBox({ traceId, stepCount, steps: liveSteps }) {
  const [fetched, setFetched] = useState(null);
  const [err, setErr] = useState("");
  const ready = liveSteps?.length ? liveSteps : fetched;
  const n = Number(stepCount) || liveSteps?.length || 0;
  if (!traceId && !n) return null;
  return (
    <Collapse
      ghost
      onChange={async (keys) => {
        const open = Array.isArray(keys) ? keys.includes("trace") : keys === "trace";
        if (!open || ready || !traceId) return;
        try {
          const r = await api.agentTrace(traceId);
          setFetched((r.data.steps || []).filter((s) => s.kind !== "answer" || s.tool));
          setErr("");
        } catch (e) {
          setErr(formatError(e, "这次查证记录不可看"));
        }
      }}
      items={[
        {
          key: "trace",
          label: n > 0 ? `用了 ${n} 步查证` : "这次怎么查的",
          children: err ? (
            <p className="fs-muted">{err}</p>
          ) : (
            <ul className="fs-muted" style={{ margin: 0, paddingLeft: 18 }}>
              {(ready || []).filter((s) => s && (s.tool || s.kind === "tool")).map((s, i) => (
                <li key={`${s.tool || s.kind}-${i}`}>{stepLine(s)}</li>
              ))}
            </ul>
          ),
        },
      ]}
    />
  );
}

function degradeReason(degraded) {
  const s = String(degraded || "");
  if (s.includes("budget")) return "步数或工具次数用完了，先按已查到的回答。";
  if (s.includes("tool_failures")) return "连续两次工具没查到，先按已有材料回答。";
  if (s.includes("tool_calling")) return "这次模型不能调工具，先按单轮回答。";
  return s;
}

function pendingPreview(item) {
  const text = item?.preview_text || item?.preview?.preview || item?.preview?.question || "";
  return String(text).slice(0, 180);
}

export function AgentRuntimeNotice({ degraded, degradedTo, pendingConfirm, onConfirm, confirming, availableTools }) {
  const pending = pendingConfirm?.length ? pendingConfirm : [];
  const fell = Boolean(degraded || degradedTo === "chat");
  const tools = (availableTools || []).map((name) => toolLabel(name)).filter(Boolean);
  if (!fell && !pending.length) return null;
  return (
    <>
      {fell ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="这次先按已查到的材料回答，没有走完全部工具。"
          description={
            <>
              {degradeReason(degraded) || degradeReason(degradedTo)}
              {tools.length ? (
                <p className="fs-muted" style={{ margin: "8px 0 0" }}>
                  这次还能用：{tools.join("、")}
                </p>
              ) : null}
            </>
          }
        />
      ) : null}
      {pending.length ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`要改笔记、论文或提交作业，需要你再确认一次（${pending.length} 项）。`}
          description={
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              {pending.map((p, i) => (
                <li key={`${p.tool || "write"}-${i}`}>
                  {p.label || toolLabel(p.tool)}
                  {pendingPreview(p) ? ` · ${pendingPreview(p)}` : ""}
                </li>
              ))}
            </ul>
          }
          action={
            onConfirm ? (
              <Button type="primary" size="small" loading={!!confirming} onClick={onConfirm}>
                确认执行
              </Button>
            ) : null
          }
        />
      ) : null}
    </>
  );
}

export function unusedScopeLead(scope) {
  const who = String(scope?.requester_name || "").trim();
  const dir = String(scope?.direction_name || "").trim();
  if (who && dir) return `${who} · ${dir}`;
  if (who) return who;
  if (dir) return dir;
  if (scope?.requester_id) return "这人的综述没提到这些论文。";
  if (scope?.direction_id) return "这个方向的综述没提到这些论文。";
  return "综述没提到这些论文。";
}

const GLYPH_PATHS = {
  home: "M3 10.5 12 3l9 7.5V21H14v-7H10v7H3z",
  cockpit: "M4 18c2.2-6 5.5-9 8-9s5.8 3 8 9 M12 9v3.5",
  lit: "M5 4h9v16H5z M14 7h5v13h-5 M8 4v16",
  writing: "M4 20h16 M7 16.5 16 7.5l2.5 2.5L9.5 19H7z",
  ms: "M6 3h9l5 5v13H6z M15 3v5h5",
  kb: "M4 6h16v12H4z M8 10h8 M8 14h5",
  lang: "M4 6h16 M12 6c0 4-3 10-8 12 M12 6c0 4 3 10 8 12 M5 14h14",
  runs: "M6 5l13 7-13 7z",
  repro: "M8 8h8v8H8z M12 3v5 M12 16v5 M3 12h5 M16 12h5",
  members: "M8 8c1.4 0 2.4 1 2.4 2.4S9.4 12.8 8 12.8 5.6 11.8 5.6 10.4 6.6 8 8 8z M3.8 19c.6-3 2.2-4.2 4.2-4.2s3.6 1.2 4.2 4.2 M16 9c1.1 0 2 .9 2 2s-.9 2-2 2-2-.9-2-2 .9-2 2-2z M13.4 19c.4-2 1.5-3 2.6-3s2.2 1 2.6 3",
  dirs: "M4 6h7l2 2h7v10H4z",
  digest: "M6 4h12v16H6z M9 8h6 M9 12h6 M9 16h4",
  stats: "M5 18V10 M12 18V6 M19 18v-7",
  keys: "M8 16c-2 0-3.5-1.5-3.5-3.5S6 9 8 9s3.5 1.5 3.5 3.5S10 16 8 16z M11 11.5 19 3.5 21 5.5 19 7.5h-2v2h-2v2",
  ntf: "M6 16h12l-1.2-6.2C16.2 6.8 14.3 5 12 5s-4.2 1.8-4.8 4.8z M10 16v1.2c0 1.1.9 2 2 2s2-.9 2-2V16",
  data: "M4 7c0-1.4 3.6-2.4 8-2.4s8 1 8 2.4v10c0 1.4-3.6 2.4-8 2.4s-8-1-8-2.4z M4 12c0 1.4 3.6 2.4 8 2.4s8-1 8-2.4",
  graph: "M6 16h.01 M12 7h.01 M18 15h.01 M7.2 14.6 11 8.8 M13.1 8.9 16.8 13.7",
  tasks: "M5 7h14 M5 12h14 M5 17h10",
  meet: "M5 8h14v10H5z M5 8l7 5 7-5",
  files: "M5 5h6l2 2h6v12H5z",
  logs: "M6 4h12v16H6z M9 8h6 M9 12h6",
  me: "M12 8c1.7 0 3 1.3 3 3s-1.3 3-3 3-3-1.3-3-3 1.3-3 3-3z M6 19c1.1-3.4 3.4-4.6 6-4.6s4.9 1.2 6 4.6",
  admin: "M12 3l8 4v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z",
  pending: "M12 6v7l4 2 M12 3a9 9 0 1 0 .01 0",
  apply: "M12 4v16 M4 12h16",
  ai: "M7 5h10v14H7z M9 9h6 M9 15h4",
  search: "M10.2 10.2 16 16 M10 4.6a5.4 5.4 0 1 0 .01 0",
};

export function FsGlyph({ name }) {
  const d = GLYPH_PATHS[name] || GLYPH_PATHS.home;
  return (
    <svg className="fs-glyph" viewBox="0 0 24 24" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export function ActionChip({ to, children, tone, onClick }) {
  if (!to) return null;
  return (
    <Link className={`fs-chip${tone ? ` is-${tone}` : ""}`} to={to} onClick={onClick}>
      {children}
    </Link>
  );
}

const WORK_WORD = { direction: "在做", manuscript: "在写", literature: "在读" };

export function spokenName(name) {
  const s = String(name || "").trim();
  if (!s || /^\d+$/.test(s)) return "";
  return s;
}

export function LabReaderBits({ itemId, names, marks, onPage }) {
  const rows = Array.isArray(marks) && marks.length ? marks : (names || []).map((name) => ({ name }));
  const bits = rows
    .map((row) => {
      const name = row?.name || "";
      const page = Number(row?.last_page_no || 0);
      if (!name) return null;
      return { name, page: page >= 2 ? page : 0 };
    })
    .filter(Boolean);
  if (!bits.length) return null;
  return (
    <span className="fs-reader-bits">
      {bits.map((row, i) => {
        const label = row.page ? `${row.name} 第${row.page}页` : row.name;
        const sep = i ? "、" : "";
        if (row.page && onPage) {
          return (
            <span key={`${i}-${row.name}-${row.page}`}>
              {sep}
              <button type="button" className="fs-link-btn" onClick={() => onPage(row.page)}>
                {label}
              </button>
            </span>
          );
        }
        if (row.page && itemId) {
          return (
            <span key={`${i}-${row.name}-${row.page}`}>
              {sep}
              <Link to={literatureHref(itemId, row.page)} onClick={(e) => e.stopPropagation()}>
                {label}
              </Link>
            </span>
          );
        }
        return (
          <span key={`${i}-${row.name}`}>
            {sep}
            {label}
          </span>
        );
      })}
    </span>
  );
}

export function WorkLine({ text, sources }) {
  const rows = (sources || []).filter((s) => s?.href && s?.title);
  if (!rows.length) return text ? <span className="fs-muted">{text}</span> : null;
  return (
    <span className="fs-muted" onClick={(e) => e.stopPropagation()}>
      {rows.map((s, i) => (
        <span key={s.href}>
          {i ? "；" : ""}
          {WORK_WORD[s.kind] || ""}
          <Link to={s.href}>{s.kind === "direction" ? s.title : `《${s.title}》`}</Link>
        </span>
      ))}
    </span>
  );
}

export function ContinueAskChip({ last, meId }) {
  const mine = meId && last?.user_id && String(last.user_id) === String(meId);
  if (!mine || !last?.href || !last?.question) return null;
  return <ActionChip to={last.href}>接着上次问的</ActionChip>;
}

export function AskWeekChip({ meId, userId, labId }) {
  if (!labId || !meId || !userId || String(meId) !== String(userId)) return null;
  // 学生问这些论文；导师知识库仍用 tab=ask
  return <ActionChip to={`/labs/${labId}/assistant`}>问这些论文</ActionChip>;
}

export function DirectionSelectEmpty({ labId, teacher }) {
  return (
    <span className="fs-dir-empty">
      还没有方向。
      {teacher ? (
        <>
          <ActionChip to={`/labs/${labId}/literature`}>从文献归纳</ActionChip>
          <ActionChip to={`/labs/${labId}/directions?compose=1`}>新建方向</ActionChip>
        </>
      ) : (
        " 导师确认后才会出现方向。"
      )}
    </span>
  );
}

export function DirectionFilterHint({ labId, teacher, dirs }) {
  if ((dirs || []).length) return null;
  return (
    <span className="fs-dir-filter-hint">
      <DirectionSelectEmpty labId={labId} teacher={teacher} />
    </span>
  );
}

export async function hangSuggestedPapers(rows) {
  let n = 0;
  for (const row of rows || []) {
    const itemId = row.item_id || row.id;
    const ids = row.direction_ids || [];
    if (!itemId || !ids.length) continue;
    const keep = Array.isArray(row.keep_ids) ? row.keep_ids : [];
    await api.patchLiterature(itemId, { direction_ids: [...new Set([...keep, ...ids])] });
    n += 1;
  }
  return n;
}

export async function adoptDirectionDraft(labId, draft) {
  const name = (draft.name || "").trim();
  if (!name) throw new Error("请填写方向名");
  const created = await api.createDirection(labId, {
    name,
    keywords: draft.keywords || [],
    description: `从 ${draft.paper_count || (draft.item_ids || []).length} 篇题名归纳，可改名。`,
  });
  const did = created.data.id;
  for (const itemId of draft.item_ids || []) {
    const cur = Array.isArray(draft.keep_ids?.[itemId]) ? draft.keep_ids[itemId] : [];
    await api.patchLiterature(itemId, { direction_ids: [...new Set([...cur, did])] });
  }
  return did;
}

export function DirectionDraftList({ drafts, labId, teacher, onAdopted }) {
  const [busy, setBusy] = useState("");
  const [names, setNames] = useState({});
  const items = drafts || [];
  if (!items.length) return null;
  const adoptOne = async (d) => {
    const key = d.key || d.name;
      const name = String(names[key] ?? d.name ?? "").trim();
    await adoptDirectionDraft(labId, { ...d, name });
    return name;
  };
  return (
    <div className="fs-dir-drafts">
      <p className="fs-muted">从题名和这篇的方法归纳，采用后才写入方向并挂上论文。名称可改。</p>
      {items.map((d) => {
        const key = d.key || d.name;
        return (
          <div key={key} className="fs-dir-draft">
            <div className="fs-dir-draft-meta">
              {teacher ? (
                <Input
                  value={names[key] ?? d.name}
                  onChange={(e) => setNames((s) => ({ ...s, [key]: e.target.value }))}
                  maxLength={80}
                  style={{ maxWidth: 280 }}
                />
              ) : (
                <strong>{d.name}</strong>
              )}
              <span className="fs-muted">
                {" "}
                · {d.paper_count || (d.item_ids || []).length} 篇
                {(d.titles || []).length ? ` · ${(d.titles || []).join("；")}` : ""}
              </span>
            </div>
            {teacher ? (
              <Button
                size="small"
                type="primary"
                loading={busy === key}
                disabled={!!busy}
                onClick={async () => {
                  setBusy(key);
                  try {
                    const name = await adoptOne(d);
                    message.success(`已采用「${name}」并挂上 ${(d.item_ids || []).length} 篇`);
                    onAdopted?.();
                  } catch (e) {
                    message.error(formatError(e, "无法采用方向"));
                  } finally {
                    setBusy("");
                  }
                }}
              >
                采用并挂上
              </Button>
            ) : (
              <span className="fs-muted">导师确认后才会出现方向</span>
            )}
          </div>
        );
      })}
      {teacher && items.length > 1 ? (
        <Button
          size="small"
          loading={busy === "*"}
          disabled={!!busy}
          onClick={async () => {
            setBusy("*");
            try {
              for (const d of items) await adoptOne(d);
              message.success(`已采用 ${items.length} 个方向`);
              onAdopted?.();
            } catch (e) {
              message.error(formatError(e, "无法采用方向"));
            } finally {
              setBusy("");
            }
          }}
        >
          全部采用
        </Button>
      ) : null}
    </div>
  );
}

export function DirectionGapAlert({ gap, drafts, labId, teacher, onAdopted }) {
  const [hanging, setHanging] = useState(false);
  const hangable = gap?.hangable || [];
  if (!gap && !(drafts || []).length) return null;
  return (
    <>
      {gap ? (
        <Alert
          className="fs-invite"
          type="info"
          showIcon
          message={gap.title}
          description={
            <>
              {gap.text ? <span>{gap.text} </span> : null}
              {(gap.steps || []).map((s) => (
                <ActionChip key={s.key || s.href} to={s.href}>
                  {s.label}
                </ActionChip>
              ))}
              {(gap.hang_names || []).length ? (
                <span className="fs-muted"> 将挂到{(gap.hang_names || []).join("、")}。</span>
              ) : null}
              {(gap.keyword_hints || []).length ? (
                <span className="fs-muted"> 可作关键词：{(gap.keyword_hints || []).join("、")}</span>
              ) : null}
              {gap.scan_window && gap.scan_capped && !String(gap.text || "").includes("只看最近") ? (
                <span className="fs-muted">
                  {" "}
                  只看最近 {gap.scan_window} 篇没挂方向的，挂完再看下一批。
                </span>
              ) : null}
              {hangable.length ? (
                <ul className="fs-muted" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                  {hangable.slice(0, 8).map((row) => (
                    <li key={row.item_id || row.title}>
                      {row.title || "未命名"}
                      {row.direction_name ? ` → ${row.direction_name}` : ""}
                      {(row.matched || []).length ? `（${(row.matched || []).slice(0, 3).join("、")}）` : ""}
                      {row.score != null && row.score !== "" ? ` ${Number(row.score).toFixed ? Number(row.score).toFixed(2) : row.score}` : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
              {teacher && hangable.length ? (
                <div className="fs-dir-hang-batch">
                  <Button
                    size="small"
                    type="primary"
                    loading={hanging}
                    onClick={async () => {
                      setHanging(true);
                      try {
                        const n = await hangSuggestedPapers(hangable);
                        message.success(`已按题名挂上 ${n} 篇`);
                        onAdopted?.();
                      } catch (e) {
                        message.error(formatError(e, "无法按题名挂方向"));
                      } finally {
                        setHanging(false);
                      }
                    }}
                  >
                    按题名挂上 {hangable.length} 篇
                  </Button>
                </div>
              ) : null}
            </>
          }
        />
      ) : null}
      <DirectionDraftList drafts={drafts} labId={labId} teacher={teacher} onAdopted={onAdopted} />
    </>
  );
}

export function DirectionHangChips({ suggestions, onHang, busyId, verb = "挂到" }) {
  const items = suggestions || [];
  if (!items.length) return null;
  return (
    <span className="fs-dir-hang" onClick={(e) => e.stopPropagation()}>
      {items.map((s) => (
        <Button
          key={s.id}
          size="small"
          type="link"
          loading={busyId === s.id}
          onClick={(e) => {
            e.stopPropagation();
            onHang?.(s);
          }}
        >
          {verb}{verb === "放进来" ? "" : s.name}
          {verb === "放进来" ? "" : (s.matched || []).length ? `（${(s.matched || []).slice(0, 2).join("、")}）` : ""}
        </Button>
      ))}
    </span>
  );
}

export function AskEmptyNext({ emptyNext }) {
  if (!emptyNext) return null;
  const steps = emptyNext.steps || [];
  if (!emptyNext.title && !emptyNext.text && !steps.length) return null;
  return (
    <Alert
      type="info"
      showIcon
      style={{ marginTop: 8 }}
      message={emptyNext.title || "下一步"}
      description={
        <>
          {emptyNext.text ? <span>{emptyNext.text} </span> : null}
          {steps.filter((s) => s && typeof s === "object").map((s) => (
            <ActionChip key={s.key || s.href} to={s.href}>
              {s.label}
            </ActionChip>
          ))}
        </>
      }
    />
  );
}

export function ReproduceHonest({ ev, none = "" }) {
  if (!ev) return none ? <span className="fs-muted">{none}</span> : null;
  return ev.measured ? <span className="fs-muted"> · 已经跑出结果</span> : <span className="fs-muted"> · 还没跑出结果</span>;
}

export function ReproduceChip({ to, ev, onClick }) {
  if (!to) return null;
  return (
    <ActionChip to={to} tone={ev?.measured ? "" : "gold"} onClick={onClick}>
      {ev ? "查看复现" : "去复现"}
    </ActionChip>
  );
}

export function AttentionBar({ labId, milestones, queue, unassigned, onResume, spine }) {
  const ms = milestones || [];
  const q = queue || {};
  const gpu = q.awaiting_gpu || 0;
  const blocked = q.blocked || 0;
  const researchGpu = q.research_awaiting_gpu || 0;
  const researchGpuHref = q.research_awaiting_href || (labId ? `/labs/${labId}/runs?status=awaiting_gpu` : "");
  const u = unassigned || {};
  const uCount = u.count || 0;
  const uHref = u.href || (labId ? `/labs/${labId}/tasks?assignee=none` : "");
  const itemN = (q.items || []).length;
  const notRun = (spine && spine.not_run) || 0;
  const notRunHref = (spine && spine.not_run_href) || (labId ? `/labs/${labId}/reproduces?status=failed` : "");
  if (!ms.length && !gpu && !blocked && !uCount && !researchGpu && !itemN && !notRun) return null;
  const urgent = ms.some((m) => m.overdue) || gpu || blocked || researchGpu;
  return (
    <section className={`fs-attention${urgent ? " is-urgent" : ""}`} aria-label="待处理">
      <header className="fs-attention-head">
        <strong>待处理</strong>
        <span className="fs-muted">只列出需要看一眼的事</span>
      </header>
      {gpu || blocked || uCount || researchGpu || notRun ? (
        <div className="fs-attention-chips">
          {gpu ? <ActionChip to={q.awaiting_gpu_href} tone="gold">在等机器 {gpu}</ActionChip> : null}
          {researchGpu ? <ActionChip to={researchGpuHref} tone="gold">文献调研在等机器 {researchGpu}</ActionChip> : null}
          {blocked ? <ActionChip to={q.blocked_href} tone="gold">缺数据 {blocked}</ActionChip> : null}
          {uCount ? <ActionChip to={uHref}>待认领 {uCount}</ActionChip> : null}
          {notRun ? <ActionChip to={notRunHref}>还没做复现 {notRun}</ActionChip> : null}
        </div>
      ) : null}
      <NotRunTitleChips
        titles={spine?.not_run_titles}
        href={notRunHref}
        more={spine?.not_run_title_more || 0}
      />
      {(q.items || []).slice(0, 6).map((it) => (
        <p key={`${it.kind}-${it.id}`}>
          <Link to={it.href}>
            {it.kind === "reproduce" ? "复现" : "文献调研"} · {it.title || it.id}
          </Link>
          {it.next_step || it.feedback ? (
            <>
              {" "}
              <FeedbackStrip row={it} />
            </>
          ) : null}
          <ResumeJob row={it} onDone={() => onResume?.(it)} />
        </p>
      ))}
      {ms.map((m) => {
        const when = dueLabel(m);
        return (
          <p key={m.id} className={`fs-attention-ms${m.overdue ? " is-overdue" : ""}`}>
            <Link to={m.direction_href || (labId && m.direction_id ? `/labs/${labId}/directions/${m.direction_id}` : "#")}>
              {m.kind_label || m.title || m.kind}
              {m.title && m.kind_label && m.title !== m.kind_label ? ` · ${m.title}` : ""}
            </Link>
            {when ? <span className="fs-muted"> · {when}</span> : null}
            {m.overdue ? <Tag color="red">已过期</Tag> : null}
          </p>
        );
      })}
    </section>
  );
}

export function LoadFailed({ message, onRetry }) {
  return (
    <Alert
      type="error"
      showIcon
      message={message || "加载失败"}
      action={
        onRetry ? (
          <Button size="small" onClick={onRetry}>
            重试
          </Button>
        ) : null
      }
    />
  );
}

export function PageLoading({ label = "正在打开…" }) {
  return (
    <div className="fs-page-loading" role="status" aria-live="polite" aria-busy="true">
      <span className="fs-muted">{label}</span>
      <div className="fs-skeleton" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
    </div>
  );
}

export function ModelKeyHint({ err }) {
  const text = String(err || "");
  if (!/密钥|API Key|未配置|permission/i.test(text)) return null;
  return (
    <p className="fs-hint">
      先到 <Link to="/settings/keys">个人模型</Link> 填写密钥。未填则用实验室或平台模型。文献入库不需要这项。
    </p>
  );
}

export function SituationBar({ situation }) {
  const lines = situation?.lines || [];
  if (!lines.length) return null;
  const nexts = lines.map((l) => (l.empty ? l.next_step || l.empty_text : l.next_step)).filter(Boolean);
  const workerHint = situation?.worker_off && lines.some((l) => l.key === "risk" && (l.count || 0) > 0);
  return (
    <div>
      <div className="fs-toolbar">
        {lines.map((line) => (
          <Link
            key={line.key}
            className={`fs-metric${line.empty ? " is-empty" : ""}`}
            to={line.href || "#"}
            title={line.text || line.empty_text || ""}
          >
            <b>{line.count ?? 0}</b>
            <span>{humanize(line.label || line.text)}</span>
          </Link>
        ))}
      </div>
      {nexts.length ? <p className="fs-muted">{nexts.join(" ")}</p> : null}
      {workerHint && !nexts.some((t) => /还没开始跑|工人未开/.test(String(t))) ? (
        <p className="fs-muted">还没开始跑。点恢复接着做。</p>
      ) : null}
    </div>
  );
}

export function AiOverviewCard({ overview }) {
  if (!overview) return null;
  const feats = overview.usage_by_feature || [];
  const members = overview.usage_by_member || [];
  const empty = overview.empty || (!overview.tokens && !feats.length);
  const keysHref = overview.keys_href || "/settings/keys";
  const message = empty
    ? overview.empty_text || "本月尚无模型调用。配置个人或实验室模型后才会记账。"
    : overview.peer_usage_hidden
      ? `本月共用 ${overview.tokens ?? 0}。这里只显示你自己的。`
      : `本月共用 ${overview.tokens ?? 0} / ${overview.quota_limit ?? 0}。下面按姓名。`;
  return (
    <Alert
      type={overview.quota_near ? "warning" : "info"}
      showIcon
      icon={<FsGlyph name="ai" />}
      style={{ marginBottom: 12 }}
      message={message}
      description={
        <>
          {empty ? <ActionChip to={overview.empty_href || keysHref}>去配置模型</ActionChip> : null}
          {overview.failed_attempts ? (
            <ActionChip to={keysHref}>失败尝试 {overview.failed_attempts} 次已记账</ActionChip>
          ) : null}
          {overview.quota_near ? (
            <ActionChip to={keysHref}>{overview.quota_next || "额度将满。不会伪造指标。"}</ActionChip>
          ) : null}
          {feats.map((f) => (
            <ActionChip key={f.feature} to={f.href || overview.href}>
              {f.label || f.feature} {f.calls} 次
            </ActionChip>
          ))}
          {members.map((m) => (
            <ActionChip key={m.user_id} to={m.href || overview.href}>
              {m.display_name || m.user_id} {m.tokens ?? 0}
            </ActionChip>
          ))}
          <HintTip title="用量">{humanize(overview.direction_note || "这是实验室一起用的量，不按方向拆。")}</HintTip>
        </>
      }
    />
  );
}

export function StuckBriefBar({ brief }) {
  if (!brief?.text) return null;
  return (
    <>
      <p>
        {humanize(brief.text)}{" "}
        <HintTip title="还没做完">这些是还没做完的事。</HintTip>{" "}
        <ActionChip to={brief.href || "/"}>打开研究方向</ActionChip>
        {brief.unread ? <ActionChip to={brief.unread_href}>无人在读</ActionChip> : null}
        {brief.no_card ? <ActionChip to={brief.no_card_href}>还没写方法</ActionChip> : null}
        {brief.related_work_missing ? <ActionChip to={brief.related_work_href}>未写相关工作</ActionChip> : null}
        {brief.core_missing ? <ActionChip to={brief.core_missing_href}>这几篇还没写进正文</ActionChip> : null}
        {brief.not_run ? <ActionChip to={brief.not_run_href}>还没做复现</ActionChip> : null}
        {brief.ungrounded ? <ActionChip to={brief.ungrounded_href}>写了结果，组里还没跑过</ActionChip> : null}
        {brief.unused_research ? <ActionChip to={brief.unused_research_href}>综述没提到</ActionChip> : null}
      </p>
      <UnreadTitleChips titles={brief.unread_titles} href={brief.unread_href} more={brief.unread_title_more || 0} />
      <NoCardTitleChips titles={brief.no_card_titles} href={brief.no_card_href} more={brief.no_card_title_more || 0} />
      <RelatedWorkTitleChips
        titles={brief.related_work_titles}
        href={brief.related_work_href}
        more={brief.related_work_title_more || 0}
      />
      <CoreMissingTitleChips
        titles={brief.core_missing_titles}
        href={brief.core_missing_href}
        more={brief.core_missing_title_more || 0}
      />
    </>
  );
}

export function NextStepList({ steps, title = "下一步", labId, canSearch }) {
  const rows = (steps || []).filter((s) => s && (s.text || s.label || s.href));
  if (!rows.length) return null;
  return (
    <Alert
      type="info"
      showIcon
      style={{ marginBottom: 12 }}
      message={title}
      description={
        <ul className="fs-next-steps">
          {rows.map((s) => (
            <li key={s.key || s.href || s.label}>
              {s.href ? <ActionChip to={s.href}>{s.label || "打开"}</ActionChip> : <b>{s.label}</b>}
              {s.text || s.empty_text ? <span className="fs-muted"> {humanize(s.text || s.empty_text)}</span> : null}
              {s.evidence_text ? <p className="fs-muted">{s.evidence_text}</p> : null}
              {(s.evidence || []).slice(0, 4).map((ev) => (
                <span key={ev.item_id || ev.title}>
                  {" "}
                  <Link to={ev.href || literatureHref(ev.item_id)}>{ev.title}</Link>
                  {ev.gap ? <span className="fs-muted"> · {ev.gap}</span> : null}
                  {ev.one_line ? <span className="fs-muted"> · {ev.one_line}</span> : null}
                </span>
              ))}
              {canSearch && labId && s.search?.question ? (
                <button
                  type="button"
                  className="fs-link-btn"
                  onClick={async () => {
                    try {
                      const r = await api.createRun(labId, {
                        question: s.search.question,
                        literature_item_ids: s.search.literature_item_ids || [],
                      });
                      message.success("已转成文献调研，到调研列表能看到。");
                      if (r.data?.id) window.location.assign(`/labs/${labId}/runs/${r.data.id}`);
                    } catch (e) {
                      message.error(formatError(e, "没转成文献调研"));
                    }
                  }}
                >
                  转成文献调研
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      }
    />
  );
}

export function WritebackSuccess({ summary, href, kind = "manuscript", next }) {
  if (!summary && !href && !next) return null;
  return (
    <Alert
      type="success"
      showIcon
      style={{ marginBottom: 8 }}
      message={summary || (kind === "note" ? "已写回笔记。" : "已写回论文。")}
      description={
        <>
          {href ? <ActionChip to={href}>{kind === "note" ? "打开阅读器对应页" : "打开论文"}</ActionChip> : null}
          {next?.href ? (
            <p className="fs-writeback-next">
              下一步：<ActionChip to={next.href}>{next.label || "继续"}</ActionChip>
              {next.text ? <span className="fs-muted"> {humanize(next.text)}</span> : null}
            </p>
          ) : null}
        </>
      }
    />
  );
}

export function PageHeader({ title, extra, children, hint, tone }) {
  const name = typeof title === "string" ? humanize(title) : title;
  const lead = typeof children === "string" ? humanize(children) : children;
  const tip = typeof hint === "string" ? humanize(hint) : hint;
  return (
    <header className={`fs-pagehead${tone ? ` is-${tone}` : ""}`}>
      <div className="fs-pagehead-row">
        <Title level={3} className="fs-pagehead-title">
          <span className="fs-pagehead-name">{name}</span>
          {tip ? <HintTip title={typeof name === "string" ? name : "说明"}>{tip}</HintTip> : null}
        </Title>
        {extra ? <div className="fs-pagehead-extra">{extra}</div> : null}
      </div>
      {lead ? <Paragraph className="fs-lead">{lead}</Paragraph> : null}
    </header>
  );
}

export function LibraryProgress({ status, labId }) {
  const p = status?.intake;
  if (!p?.received) return null;
  const lv = p.levels || {};
  const total = p.received;
  const abstract = (lv.abstract || 0) + (lv.fulltext || 0) + (lv.deep || 0);
  const full = (lv.fulltext || 0) + (lv.deep || 0);
  const deep = lv.deep || 0;
  const pct = (n) => `${Math.min(100, Math.round((100 * (n || 0)) / total))}%`;
  const left = status.month_left;
  const cells = [
    { key: "search", label: "能搜到", n: p.searchable || 0, to: `/labs/${labId}/literature` },
    { key: "abstract", label: "读过摘要", n: abstract, to: `/labs/${labId}/literature?read_level=abstract` },
    { key: "full", label: "读过全文", n: full, to: `/labs/${labId}/literature?read_level=full` },
    { key: "deep", label: "精读", n: deep, to: `/labs/${labId}/literature?read_level=deep` },
  ];
  return (
    <section className="fs-libprog" aria-label="论文库进度">
      <div className="fs-libprog-head">
        <b>{total} 篇里</b>
        {cells.map((c) => (
          <Link key={c.key} to={c.to} className={`fs-libprog-cell is-${c.key}`}>
            <span>{c.label}</span>
            <strong>{c.n}</strong>
          </Link>
        ))}
        <span className="fs-libprog-cell is-left">
          <span>本月还能读</span>
          <strong>{left == null ? "不限" : `约 ${left}`}</strong>
        </span>
      </div>
      <div className="fs-libprog-bar" role="img" aria-label={`能搜 ${p.searchable || 0}，摘要 ${abstract}，全文 ${full}，精读 ${deep}`}>
        <i className="is-search" style={{ width: pct(p.searchable) }} />
        <i className="is-abstract" style={{ width: pct(abstract) }} />
        <i className="is-full" style={{ width: pct(full) }} />
        <i className="is-deep" style={{ width: pct(deep) }} />
      </div>
      {p.parsing || p.unreadable || p.paused ? (
        <p className="fs-muted fs-libprog-note">
          {p.parsing ? `还有 ${p.parsing} 篇在拆字。` : ""}
          {p.unreadable ? `${p.unreadable} 篇是扫描件或加密件，读不出字。` : ""}
          {p.paused || ""}
        </p>
      ) : null}
    </section>
  );
}

export function useComposeOpen(params, setParams, key = "compose") {
  const open = (params?.get?.(key) || "") === "1";
  const setOpen = (next) => {
    const copy = new URLSearchParams(params);
    if (next) copy.set(key, "1");
    else copy.delete(key);
    setParams(copy, { replace: true });
  };
  return [open, setOpen];
}

export function CreateDialog({
  open,
  onClose,
  title,
  okText = "确定",
  form,
  children,
  extra,
  width = 520,
  loading = false,
  footer,
}) {
  return (
    <Modal
      className="fs-create-modal"
      open={open}
      title={title}
      okText={okText}
      onOk={() => form?.submit?.()}
      onCancel={onClose}
      confirmLoading={loading}
      destroyOnClose
      width={width}
      afterClose={() => form?.resetFields?.()}
      footer={footer}
    >
      {extra}
      {children}
    </Modal>
  );
}

export function Crumbs({ items }) {
  const filtered = (items || []).filter((x) => x && x.label);
  const trail = filtered.filter((x, i) => x.to || i === filtered.length - 1);
  if (trail.length < 2) return null;
  return (
    <Breadcrumb
      className="fs-crumb"
      items={trail.map((x, i) => ({
        title: x.to && i < trail.length - 1 ? <Link to={x.to}>{x.label}</Link> : x.label,
      }))}
    />
  );
}

export function GuideList({ items }) {
  return (
    <ul className="fs-guide">
      {(items || []).map((g) => (
        <li key={g.key} className={g.done ? "is-done" : ""}>
          <Checkbox checked={!!g.done} disabled>
            {g.href && !g.done ? <Link to={g.href}>{g.label}</Link> : g.label}
          </Checkbox>
        </li>
      ))}
    </ul>
  );
}

export function AuthFrame({ title, subtitle, children }) {
  return (
    <div className="fs-auth">
      <div className="fs-auth-card">
        <div className="fs-brand fs-brand-lg">
          <span className="fs-mark" aria-hidden>
            蜂
          </span>
          <div>
            <strong>蜂算科研</strong>
            <p>实验室科研操作系统</p>
          </div>
        </div>
        <Title level={4}>{title}</Title>
        {subtitle ? <Paragraph type="secondary">{subtitle}</Paragraph> : null}
        {children}
      </div>
    </div>
  );
}

function mostlyEnglish(text) {
  const s = String(text || "").trim();
  if (!s) return false;
  const cjk = (s.match(/[\u4e00-\u9fff]/g) || []).length;
  if (cjk >= 6) return false;
  const letters = (s.match(/[A-Za-z]/g) || []).length;
  return letters >= 12 && letters > cjk * 2;
}

function SpeedLine({ item, onPage, onAsk }) {
  if (!item || typeof item !== "object") return null;
  const pages = Array.isArray(item.pages) ? item.pages : [];
  return (
    <li>
      <span>{item.text}</span>
      {pages.map((p) => (
        <button key={p} type="button" className="fs-link-btn" onClick={() => onPage?.(p)}>
          {" "}
          第 {p} 页
        </button>
      ))}
      {onAsk ? (
        <button type="button" className="fs-link-btn" onClick={() => onAsk(item)}>
          {" "}
          问这句
        </button>
      ) : null}
      {item.unverified ? <span className="fs-muted"> 出处没核到</span> : null}
      {item.quote ? (
        <details className="fs-inline-more">
          <summary>原句</summary>
          <p className="fs-muted">{item.quote}</p>
        </details>
      ) : null}
    </li>
  );
}

function SpeedBlock({ title, items, onPage, onAsk }) {
  if (!items?.length) return null;
  const head = items.slice(0, 3);
  const rest = items.slice(3);
  return (
    <section className="fs-method-sec">
      <h4>{title}</h4>
      <ul>
        {head.map((it, i) => (
          <SpeedLine key={i} item={it} onPage={onPage} onAsk={onAsk} />
        ))}
      </ul>
      {rest.length ? (
        <details>
          <summary>展开</summary>
          <ul>
            {rest.map((it, i) => (
              <SpeedLine key={i} item={it} onPage={onPage} onAsk={onAsk} />
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

export function MethodCard({ analysis, onPage, compact, onRetry, retryBusy, onAsk, onAskPaper }) {
  if (!analysis || typeof analysis !== "object" || !Object.keys(analysis).length) {
    return (
      <p className="fs-muted">
        还没有速读。{" "}
        {onRetry ? (
          <button type="button" className="fs-link-btn" disabled={retryBusy} onClick={onRetry}>
            生成速读
          </button>
        ) : null}
      </p>
    );
  }
  const running = analysis.status === "running";
  const one = methodItems(analysis.plain?.one_line)[0];
  const rawClaims = [...methodItems(analysis.claims), ...methodItems(analysis.metrics)];
  const rawProblem = methodItems(analysis.problem);
  const rawMethods = methodItems(analysis.methods);
  const rawLimits = methodItems(analysis.limitations);
  const claims = rawClaims.filter((it) => !mostlyEnglish(it.text));
  const problem = rawProblem.filter((it) => !mostlyEnglish(it.text));
  const methods = rawMethods.filter((it) => !mostlyEnglish(it.text));
  const limits = rawLimits.filter((it) => !mostlyEnglish(it.text));
  const readable = (one?.text && !mostlyEnglish(one.text)) || problem.length || methods.length || claims.length || limits.length;
  const hadEnglish = [one, ...rawProblem, ...rawMethods, ...rawClaims, ...rawLimits].some((it) => mostlyEnglish(it?.text));
  return (
    <div className="fs-method">
      {running ? (
        <p className="fs-muted">
          {analysis.fast ? "快卡 · " : ""}
          {analysis.chunks_done ? "已写出一部分，还在写。" : "正在生成速读。"}
        </p>
      ) : null}
      {analysis.upgrading && !readable ? (
        <p className="fs-muted">
          中文速读正在写。
          {onAskPaper ? (
            <button type="button" className="fs-link-btn" onClick={onAskPaper}>
              {" "}
              先问这篇
            </button>
          ) : null}
        </p>
      ) : analysis.upgrading ? (
        <p className="fs-muted">正在换成中文速读。</p>
      ) : null}
      {analysis.source === "abstract_only" ? <p className="fs-muted">这版只根据摘要。</p> : null}
      {one?.text && !mostlyEnglish(one.text) ? (
        <p className="fs-method-lead">
          {one.text}
          {onAsk ? (
            <button type="button" className="fs-link-btn" onClick={() => onAsk(one)}>
              {" "}
              问这句
            </button>
          ) : null}
        </p>
      ) : null}
      {!readable && hadEnglish && !running && !analysis.upgrading ? (
        <p className="fs-muted">
          这版还是原文条目，读起来不顺。{" "}
          {onRetry ? (
            <button type="button" className="fs-link-btn" disabled={retryBusy} onClick={onRetry}>
              生成速读
            </button>
          ) : (
            "点「生成速读」换成中文。"
          )}
        </p>
      ) : null}
      <SpeedBlock title="要解决什么" items={problem} onPage={onPage} onAsk={onAsk} />
      <SpeedBlock title="怎么做" items={methods} onPage={onPage} onAsk={onAsk} />
      <SpeedBlock title="结论和数字" items={claims} onPage={onPage} onAsk={onAsk} />
      <SpeedBlock title="局限" items={limits} onPage={onPage} onAsk={onAsk} />
      {analysis.coverage ? (
        <details className="fs-inline-more">
          <summary>这次读了哪些部分</summary>
          <p className="fs-muted">{analysis.coverage}</p>
        </details>
      ) : null}
      {!compact && (Array.isArray(analysis.related_in_lab) ? analysis.related_in_lab : []).filter((x) => x && typeof x === "object").length ? (
        <p>
          <b>组内相关</b>{" "}
          {(Array.isArray(analysis.related_in_lab) ? analysis.related_in_lab : []).filter((x) => x && typeof x === "object").map((x) => (
            <Link key={x.literature_item_id || x.title} to={x.href || literatureHref(x.literature_item_id, x.last_page_no)} style={{ marginRight: 8 }}>
              {x.title || x.literature_item_id}
              {x.reason ? <span className="fs-muted"> {x.reason}</span> : null}
            </Link>
          ))}
        </p>
      ) : null}
      {!compact && !running ? (
        <AiResultBar
          text={methodCardText(analysis)}
          copyLabel="复制速读"
          onRetry={onRetry}
          retryLabel="再生成一次"
          busy={retryBusy}
          traceId={analysis.session_id || analysis.trace_id}
          steps={analysis.steps}
          degraded={analysis.degraded}
          availableTools={analysis.available_tools}
          followups={analysis.followups}
        />
      ) : null}
    </div>
  );
}

function renderPageCites(text, { literatureId, extra, onPage } = {}) {
  const src = String(text || "");
  const re = /\[p\.(\d+)\]/gi;
  const parts = [];
  let last = 0;
  let m;
  while ((m = re.exec(src))) {
    if (m.index > last) parts.push(src.slice(last, m.index));
    const n = Number(m[1]);
    if (literatureId) {
      parts.push(
        <Link
          key={`${m.index}-${n}`}
          className="fs-page-cite"
          to={literatureHref(literatureId, n, extra)}
          onClick={(e) => {
            if (onPage) {
              e.preventDefault();
              onPage(n);
            }
          }}
        >
          [p.{n}]
        </Link>
      );
    } else if (onPage) {
      parts.push(
        <button type="button" key={`${m.index}-${n}`} className="fs-page-cite" onClick={() => onPage(n)}>
          [p.{n}]
        </button>
      );
    } else {
      parts.push(m[0]);
    }
    last = m.index + m[0].length;
  }
  if (last < src.length) parts.push(src.slice(last));
  return parts.length ? parts : src;
}

function emphasize(node, keyPrefix) {
  if (typeof node !== "string" || !node.includes("**")) return node;
  const bits = node.split(/(\*\*[^*]+\*\*)/g);
  return bits.map((part, j) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
      <strong key={`${keyPrefix}-b${j}`}>{part.slice(2, -2)}</strong>
    ) : (
      <span key={`${keyPrefix}-t${j}`}>{part}</span>
    )
  );
}

function renderRich(text, cite, keyPrefix) {
  const parts = renderPageCites(text, cite);
  const list = Array.isArray(parts) ? parts : [parts];
  return list.map((part, i) => emphasize(part, `${keyPrefix}-${i}`));
}

export function MdPreview({ text, literatureId, extra, onPage }) {
  const raw = stripModelReasoning(String(text || "").replace(/\r\n/g, "\n"));
  if (!raw.trim()) return <p className="fs-muted">暂无正文。</p>;
  const cite = { literatureId, extra, onPage };
  const blocks = raw.split(/\n{2,}/);
  return (
    <div className="fs-md">
      {blocks.map((block, i) => {
        const line = block.trim();
        if (line.startsWith("### ")) return <h4 key={i}>{renderRich(line.slice(4), cite, `h-${i}`)}</h4>;
        if (line.startsWith("## ")) return <h3 key={i}>{renderRich(line.slice(3), cite, `h-${i}`)}</h3>;
        if (line.startsWith("# ")) return <h3 key={i}>{renderRich(line.slice(2), cite, `h-${i}`)}</h3>;
        if (line.startsWith("- ")) {
          return (
            <ul key={i}>
              {line.split("\n").map((row, j) => (
                <li key={j}>{renderRich(row.replace(/^- /, ""), cite, `li-${i}-${j}`)}</li>
              ))}
            </ul>
          );
        }
        return <p key={i}>{renderRich(line, cite, `p-${i}`)}</p>;
      })}
    </div>
  );
}

export function EventFeed({ items }) {
  const rows = [];
  const push = (node, text) => {
    const t = stripModelReasoning(String(text || "")).trim();
    if (!t) return;
    rows.push({ node: node || "", text: t.length > 600 ? `${t.slice(0, 600)}…` : t });
  };
  const walk = (x) => {
    if (!x) return;
    if (typeof x === "string") {
      push("", x);
      return;
    }
    if (Array.isArray(x)) {
      x.forEach(walk);
      return;
    }
    if (typeof x === "object") {
      if (Array.isArray(x.messages)) {
        walk(x.messages);
        return;
      }
      const node = x.node_name || x.subgraph || x.node || x.role || x.type || "";
      const text = x.content || x.message || x.text || x.summary || "";
      if (text) push(node, text);
      else if (node) push(node, JSON.stringify(x).slice(0, 240));
    }
  };
  walk(items);
  if (!rows.length) return <p className="fs-muted">还没有进度消息。任务开始后会出现阶段日志，而不是原始 JSON。</p>;
  return (
    <ol className="fs-feed">
      {rows.map((row, i) => (
        <li key={i}>
          {row.node ? <b>{nodeLabel(row.node)}</b> : null}
          <span>{row.text}</span>
        </li>
      ))}
    </ol>
  );
}
