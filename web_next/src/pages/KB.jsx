import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Form, Input, Select, Space, Table, Tag, message, Popconfirm, Switch, Upload } from "antd";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, asItems } from "../api.js";
import { formatError, literatureHref, readingPctLabel } from "../err.js";
import { ActionChip, AgentRuntimeNotice, AgentTraceBox, AnalyzeChip, AskEmptyNext, AskFollowChips, AskSourceChips, AskToolSteps, CreateDialog, DirectionFilterHint, DirectionSelectEmpty, EmptyHint, HintTip, LoadFailed, MdPreview, ModelKeyHint, PageHeader, UnusedResearchAlert, copyText, followupsFromCitations, useComposeOpen, whenBlocked } from "../ui.jsx";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";
import { CitedAnswer, isRosterHit, kbSpoken, personLine, rosterSpoken } from "../kbSpeak.jsx";

function directedCites(cites, directionId) {
  return (cites || []).map((c) => {
    const item = c.literature_item_id || c.item_id;
    if (!item) return c;
    return {
      ...c,
      href: literatureHref(item, c.page_no || c.page || c.last_page_no, directionId ? { direction: directionId } : undefined),
    };
  });
}

const ASK_QUICK = {
  onboarding: ["这个组在做什么方向？", "谁负责什么，哪位同学能帮我？", "我该从哪几篇文献读起？", "组里有什么数据？"],
  lab: ["组里有哪些文献？", "组里还没做完什么？", "我的任务有哪些？", "组里有什么数据？"],
  kb: ["组里术语怎么译？", "知识库有哪些文档？"],
};

function unpagedLines(text) {
  return String(text || "")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter((line) => line && !/\[p\.\d+\]|p\.\d+/i.test(line) && !isHeading(line));
}

function isHeading(line) {
  const plain = String(line || "").replace(/\*\*/g, "").trim();
  return plain.length <= 24 && /[：:]$/.test(plain);
}

const STATUS_LABEL = {
  pending: "还没整理",
  indexing: "正在整理",
  indexed: "可以问",
  failed: "没整理好",
};

export default function KB() {
  const { id } = useParams();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const me = useSession((s) => s.me);
  const teacher = roleOf(me, id) === "teacher" || !!me?.is_admin;
  const [termForm] = Form.useForm();
  const [askForm] = Form.useForm();
  const [docs, setDocs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [terms, setTerms] = useState([]);
  const [answer, setAnswer] = useState("");
  const [hits, setHits] = useState([]);
  const [summary, setSummary] = useState("");
  const [cites, setCites] = useState([]);
  const [err, setErr] = useState("");
  const [termErr, setTermErr] = useState("");
  const [summarizing, setSummarizing] = useState(false);
  const [asking, setAsking] = useState(false);
  const [kbSpans, setKbSpans] = useState([]);
  const askAbort = useRef(null);
  const [asked, setAsked] = useState(false);
  const [emptyReason, setEmptyReason] = useState("");
  const [emptyNext, setEmptyNext] = useState(null);
  const [docsEmptyNext, setDocsEmptyNext] = useState(null);
  const [embedFallback, setEmbedFallback] = useState("");
  const [kbUngrounded, setKbUngrounded] = useState(false);
  const [reproduceGap, setReproduceGap] = useState("");
  const [reproWarn, setReproWarn] = useState("");
  const [unusedNote, setUnusedNote] = useState(null);
  const [dirs, setDirs] = useState([]);
  const [papers, setPapers] = useState([]);
  const [retrievalMode, setRetrievalMode] = useState("");
  const directionId = params.get("direction_id") || undefined;
  const litId = params.get("lit") || "";
  const [litTitle, setLitTitle] = useState("");
  const askScope = params.get("scope") === "onboarding" || params.get("scope") === "lab" ? params.get("scope") : "kb";
  const presetQ = (params.get("q") || "").trim();
  const [askSession, setAskSession] = useState("");
  const [askSteps, setAskSteps] = useState([]);
  const [askCitations, setAskCitations] = useState([]);
  const [askDegraded, setAskDegraded] = useState("");
  const [askTools, setAskTools] = useState([]);
  const [askPending, setAskPending] = useState([]);
  const [askTrace, setAskTrace] = useState("");
  const [askFollowups, setAskFollowups] = useState([]);
  const [askTurns, setAskTurns] = useState([]);
  const [askSessions, setAskSessions] = useState([]);
  const threadRef = useRef(null);
  const lastAskQ = useRef("");
  const rawTab = (params.get("tab") || "").trim();
  const tab = rawTab === "docs" || rawTab === "ask" || rawTab === "terms" ? rawTab : "docs";
  const setTab = (t) => {
    const next = new URLSearchParams(params);
    next.set("tab", t);
    setParams(next, { replace: true });
  };
  const [composeOpen, setComposeOpen] = useComposeOpen(params, setParams);
  const reload = () => {
    setErr("");
    setTermErr("");
    api
      .kbDocs(id, { page, page_size: 20, direction_id: directionId })
      .then((r) => {
        const data = r.data;
        setDocs(asItems(data));
        setTotal(data.total || asItems(data).length);
        setDocsEmptyNext(data.empty_next || null);
        if (data.embed_fallback) setEmbedFallback(data.embed_fallback);
      })
      .catch((e) => {
        setErr(formatError(e, "知识库加载失败"));
        setDocsEmptyNext(null);
      });
    api
      .glossary(id)
      .then((r) => {
        setTerms(asItems(r.data));
        setTermErr("");
      })
      .catch((e) => {
        setTerms([]);
        setTermErr(formatError(e, "术语表加载失败"));
      });
    api
      .manuscripts(id, directionId ? { direction_id: directionId } : undefined)
      .then((r) => setPapers(asItems(r.data)))
      .catch(() => setPapers([]));
    api
      .directions(id)
      .then((r) => setDirs(asItems(r.data)))
      .catch((e) => {
        setDirs([]);
        message.error(formatError(e, "方向列表没打开"));
      });
  };
  useEffect(() => {
    reload();
  }, [id, page, directionId]);
  useEffect(() => {
    if (!litId) {
      setLitTitle("");
      return undefined;
    }
    let alive = true;
    api
      .getLiterature(litId)
      .then((r) => {
        if (alive) setLitTitle(r.data?.title || "");
      })
      .catch(() => {
        if (alive) setLitTitle("");
      });
    return () => {
      alive = false;
    };
  }, [litId]);
  const autoAsked = useRef("");
  useEffect(() => {
    if (tab !== "ask" || !presetQ || autoAsked.current === `${id}:${presetQ}`) return;
    autoAsked.current = `${id}:${presetQ}`;
    askForm.setFieldsValue({ q: presetQ });
    askForm.submit();
  }, [tab, presetQ, id]);
  const runAsk = async (q, extra = {}) => {
    const text = String(q || "").trim();
    if (!text || asking) return;
    setAsking(true);
    setAskFollowups([]);
    const ac = new AbortController();
    askAbort.current = ac;
    lastAskQ.current = text;
    try {
      const payload = {
        ...(directionId ? { direction_id: directionId } : {}),
        session_id: askSession || undefined,
        ...(askScope !== "kb" ? { scope: askScope } : {}),
        ...(askTurns.length
          ? { history: askTurns.slice(-2).map((t) => ({ q: t.q, a: String(t.answer || "").slice(0, 4000) })) }
          : {}),
        ...extra,
      };
      const ask = askScope !== "kb" ? api.askLab : api.kbQuery;
      const r = await ask(id, text, { signal: ac.signal }, payload);
      const rawAnswer = r.data.answer || "";
      const spoken = rosterSpoken(rawAnswer, r.data.hits || []) || kbSpoken(rawAnswer, { hasPapers: Boolean(directionId) || (r.data.citations || []).length > 0 });
      const paperHits = (r.data.hits || []).filter((h) => !isRosterHit(h));
      setAsked(true);
      setAnswer(spoken);
      setHits(r.data.hits || []);
      setEmptyReason((paperHits.length || (r.data.hits || []).some(isRosterHit)) ? "" : (r.data.empty_reason || ""));
      setEmptyNext(r.data.empty_next || null);
      setEmbedFallback(r.data.embed_fallback || "");
      setKbUngrounded(Boolean(r.data.ungrounded) || (Array.isArray(r.data.ungrounded) && r.data.ungrounded.length > 0));
      setKbSpans(r.data.ungrounded_spans || (Array.isArray(r.data.ungrounded) ? r.data.ungrounded : []));
      setReproduceGap(r.data.reproduce_gap_md || "");
      setReproWarn(r.data.ungrounded_reproduce || "");
      setUnusedNote(r.data.unused_research || null);
      setRetrievalMode(r.data.retrieval_mode || (r.data.agent ? "agent" : ""));
      setAskSteps(r.data.steps || []);
      setAskCitations(r.data.citations || []);
      setAskDegraded(r.data.degraded || r.data.degraded_to || "");
      setAskTools(r.data.available_tools || []);
      setAskPending(r.data.pending_confirm || []);
      setAskTrace(r.data.trace_id || r.data.session_id || "");
      const nextFollows = r.data.followups || followupsFromCitations(r.data.citations || [], r.data.ungrounded);
      setAskFollowups(nextFollows);
      setAskTurns((prev) => [
        ...prev,
        {
          q: text,
          answer: spoken,
          citations: r.data.citations || [],
          sources: r.data.hits || [],
          steps: r.data.steps || [],
          followups: nextFollows,
          trace_id: r.data.trace_id || r.data.session_id || "",
          ungrounded: Boolean(r.data.ungrounded) || (Array.isArray(r.data.ungrounded) && r.data.ungrounded.length > 0),
          emptyNext: r.data.empty_next || null,
        },
      ]);
      if (r.data.session_id) {
        setAskSession(r.data.session_id);
        const next = new URLSearchParams(params);
        next.set("tab", "ask");
        next.set("session", r.data.session_id);
        setParams(next, { replace: true });
      }
      if (r.data.quota_warning) message.warning(r.data.quota_warning);
      setTab("ask");
    } catch (e) {
      if (e?.name === "AbortError" || e?.code === "ERR_CANCELED") return;
      message.error(formatError(e, "问答失败"));
    } finally {
      setAsking(false);
      askAbort.current = null;
    }
  };
  const busy = docs.some((d) => d.status === "pending" || d.status === "indexing");
  const failed = docs.some((d) => d.status === "failed");
  useEffect(() => {
    if (!busy) return undefined;
    const t = window.setInterval(reload, 4000);
    return () => window.clearInterval(t);
  }, [busy, id, page, directionId]);
  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [askTurns, asking, askSteps]);
  const resetAskThread = () => {
    setAskTurns([]);
    setAskSession("");
    setAnswer("");
    setHits([]);
    setAskFollowups([]);
    setAskTrace("");
    setAskSteps([]);
    setAsked(false);
    setAskDegraded("");
    lastAskQ.current = "";
    askForm.setFieldsValue({ q: "" });
    const next = new URLSearchParams(params);
    next.delete("session");
    setParams(next, { replace: true });
    openedSession.current = "";
  };
  const openAskSession = (row) => {
    if (!row?.session_id) return;
    setAskSession(row.session_id);
    setAskTurns(
      row.question
        ? [
            {
              q: row.question,
              answer: rosterSpoken(row.answer || "", []) || kbSpoken(row.answer || "", { hasPapers: false }) || "这次没有写成中文。再问一次。",
              citations: [],
              steps: [],
              followups: [],
              trace_id: row.session_id,
            },
          ]
        : []
    );
    setAsked(true);
    const next = new URLSearchParams(params);
    next.set("tab", "ask");
    next.set("session", row.session_id);
    setParams(next, { replace: true });
  };
  useEffect(() => {
    if (!id || tab !== "ask") return undefined;
    let alive = true;
    api
      .kbMeta(id)
      .then((r) => {
        if (alive) setAskSessions(r.data?.ask_sessions || []);
      })
      .catch((e) => {
        if (!alive) return;
        setAskSessions([]);
        message.warning(formatError(e, "以前的提问没打开"));
      });
    return () => {
      alive = false;
    };
  }, [id, tab, askTurns.length]);
  const urlSession = params.get("session") || "";
  const openedSession = useRef("");
  useEffect(() => {
    if (!urlSession || !askSessions.length || openedSession.current === urlSession) return;
    const row = askSessions.find((s) => s.session_id === urlSession);
    if (!row) return;
    openedSession.current = urlSession;
    setAskSession(row.session_id);
    setAskTurns([
      {
        q: row.question,
        answer: rosterSpoken(row.answer || "", []) || kbSpoken(row.answer || "", { hasPapers: false }) || "这次没有写成中文。再问一次。",
        citations: [],
        steps: [],
        followups: [],
        trace_id: row.session_id,
      },
    ]);
    setAsked(true);
  }, [urlSession, askSessions]);
  if (tab === "ask") {
    const next = new URLSearchParams(params);
    return <Navigate to={`/labs/${id}/assistant?${next.toString()}`} replace />;
  }
  if (me && !teacher) {
    const dest = directionId ? `/labs/${id}/directions/${directionId}` : `/labs/${id}/cockpit?tab=directions`;
    return <Navigate to={dest} replace />;
  }
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        title={
          litTitle
            ? `正在问这篇：${litTitle}`
            : directionId
              ? `正在问：${(dirs.find((d) => d.id === directionId) || {}).name || "这个方向"}`
              : "知识库"
        }
        hint="选了方向就只问这个方向。术语表仍是全实验室的。"
        extra={
          tab === "terms" ? (
            <Button type="primary" onClick={() => setComposeOpen(true)}>
              锁定术语
            </Button>
          ) : null
        }
      >
        对着资料提问
      </PageHeader>
      <CreateDialog
        open={composeOpen}
        onClose={() => setComposeOpen(false)}
        title="锁定术语"
        okText="锁定术语"
        form={termForm}
      >
        <Form
          form={termForm}
          layout="vertical"
          onFinish={async (v) => {
            try {
              await api.addGlossary(id, { src: v.src, tgt: v.tgt, do_not_translate: true });
              termForm.resetFields();
              message.success("已锁定");
              setComposeOpen(false);
              reload();
            } catch (e) {
              message.error(formatError(e, "无法锁定术语"));
            }
          }}
        >
          <Form.Item name="src" label="原文" rules={[{ required: true, message: "请填写原文" }]}>
            <Input placeholder="原文" />
          </Form.Item>
          <Form.Item name="tgt" label="译名">
            <Input placeholder="译名" />
          </Form.Item>
        </Form>
      </CreateDialog>
      <div className="fs-filterbar">
      <Select
        allowClear
        placeholder="按方向"
        style={{ width: 240 }}
        value={directionId}
        notFoundContent={<DirectionSelectEmpty labId={id} teacher={teacher} />}
        options={dirs.map((d) => ({ value: d.id, label: d.parent_id ? `↳ ${d.name}` : d.name }))}
        onChange={(v) => {
          const next = new URLSearchParams(params);
          if (v) next.set("direction_id", v);
          else next.delete("direction_id");
          setParams(next, { replace: true });
          setPage(1);
        }}
      />
      <DirectionFilterHint labId={id} teacher={teacher} dirs={dirs} />
      </div>
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}
      <ModelKeyHint err={err} />
      <div className="fs-side-tabs" role="tablist" aria-label="知识库">
        {[
          ["docs", "文档"],
          ["terms", "术语"],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={`fs-side-tab${tab === key ? " is-on" : ""}`}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "docs" ? (
      <>
      {busy ? (
        <Alert type="info" showIcon message="正在整理文档，完成前可以先去问答。" />
      ) : null}
      {failed && !busy ? (
        <Alert type="warning" showIcon message="有的文档没整理好。换成可复制的 PDF 后再试。" />
      ) : null}
      <p className="fs-muted">{err ? "文档列表没打开" : total ? `已有 ${total} 篇` : "还没有文档"}{busy ? " · 正在整理" : ""}</p>
      {docsEmptyNext && !docs.length && !err ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={docsEmptyNext.title || "还没有文档"}
          description={
            <span>
              {docsEmptyNext.text}{" "}
              {(docsEmptyNext.steps || []).map((s) => (
                <ActionChip key={s.key || s.href} to={s.href}>
                  {s.label}
                </ActionChip>
              ))}
            </span>
          }
        />
      ) : null}
      <Upload.Dragger
        showUploadList={false}
        beforeUpload={async (file) => {
          try {
            await api.kbUpload(id, file, directionId);
            message.success("已上传");
            reload();
          } catch (e) {
            message.error(formatError(e, "上传失败"));
          }
          return false;
        }}
      >
        <p>将 PDF / Markdown / 文本拖到此处入库</p>
      </Upload.Dragger>
      <Table
        rowKey="id"
        dataSource={docs}
        pagination={{ current: page, pageSize: 20, total, onChange: setPage }}
        locale={{
          emptyText: err ? (
            <EmptyHint>文档加载失败，可重试。</EmptyHint>
          ) : (
            <EmptyHint>{docsEmptyNext?.text || "还没有文档。拖入 PDF 或 Markdown 后再提问。"}</EmptyHint>
          ),
        }}
        columns={[
          { title: "文件", dataIndex: "filename" },
          { title: "状态", dataIndex: "status", render: (s, row) => (
            <span>
              <Tag>{STATUS_LABEL[s] || s}</Tag>
              {row.parse_error && s === "failed" ? <span className="fs-muted"> {row.parse_error}</span> : null}
            </span>
          ) },
          { title: "分段", dataIndex: "chunk_count" },
          {
            title: "方向",
            render: (_, row) => {
              const d = dirs.find((x) => x.id === row.direction_id);
              return d ? <Link to={`/labs/${id}/kb?direction_id=${d.id}`}>{d.name}</Link> : row.direction_id ? "已挂方向" : "全库";
            },
          },
          {
            title: "来源文献",
            render: (_, row) =>
              row.literature_item_id ? (
                <span>
                  <Link to={row.literature_item_id ? literatureHref(row.literature_item_id, row.last_page_no, directionId ? { direction: directionId } : undefined) : row.href}>{row.literature_title || "打开文献"}</Link>{" "}
                  <AnalyzeChip row={{ id: row.literature_item_id, has_method_card: row.has_method_card, card_href: row.card_href }} />
                </span>
              ) : (
                "—"
              ),
          },
          {
            title: "",
            render: (_, row) => (
              <Space>
                {row.status === "failed" || row.status === "pending" || row.status === "indexing" ? (
                  <Button
                    size="small"
                    onClick={async () => {
                      try {
                        const r = await api.kbReindex(row.id);
                        message.success(r.data.status === "indexed" ? "已重新索引" : "已重新排队");
                        reload();
                      } catch (e) {
                        message.error(formatError(e, "无法重试索引"));
                      }
                    }}
                  >
                    重试
                  </Button>
                ) : null}
                {row.literature_item_id ? (
                  <ActionChip to={literatureHref(row.literature_item_id)}>打开这篇</ActionChip>
                ) : null}
                {row.literature_item_id ? (
                  <details className="fs-inline-more">
                    <summary>还有</summary>
                    <ActionChip to={row.reproduce_href || `/labs/${id}/reproduces?literature_item_id=${encodeURIComponent(row.literature_item_id)}`}>
                      去复现
                    </ActionChip>
                  </details>
                ) : null}
                <Popconfirm
                  title="删除该文档？"
                  onConfirm={async () => {
                    try {
                      await api.deleteKbDoc(row.id);
                      reload();
                    } catch (e) {
                      message.error(formatError(e, "无法删除"));
                    }
                  }}
                >
                  <Button size="small" danger>
                    删
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      </>
      ) : null}
      {tab === "ask" ? (
      <>
      <div className="fs-ask-desk">
      <Form
        form={askForm}
        className="fs-ask-form"
        initialValues={{ q: presetQ }}
        onFinish={(v) => runAsk(v.q)}
      >
        <p className="fs-muted">{askScope === "onboarding" ? "问这个组在做什么、谁负责。答的是人和方向，不是某一页论文。" : "问这些论文。有页码的句子才能写进论文。"}</p>
        <div className="fs-tutor-chips">
          {(askScope !== "kb"
            ? ASK_QUICK[askScope]
            : directionId
              ? ["这个方向的论文分别怎么做？", "这些论文的结论有什么不同？"]
              : ASK_QUICK.kb
          ).map((q) => (
            <button key={q} type="button" className="fs-chip" disabled={asking || summarizing} onClick={() => runAsk(q)}>
              {q}
            </button>
          ))}
        </div>
        {askSessions.length ? (
          <div className="fs-tutor-chips">
            <span className="fs-muted">以前问过</span>
            {askSessions.map((s) => (
              <button
                key={s.session_id}
                type="button"
                className="fs-chip"
                disabled={asking}
                onClick={() => openAskSession(s)}
              >
                {(s.question || "实验室问").slice(0, 28)}
              </button>
            ))}
          </div>
        ) : null}
        <div className="fs-tutor-thread" ref={threadRef}>
          {askTurns.map((t, i) => {
            const roster = (t.sources || []).filter(isRosterHit);
            const papers = directedCites(t.citations, directionId).filter((c) => !isRosterHit(c));
            return (
            <div key={`${t.trace_id || t.q}-${i}`} className="fs-tutor-turn">
              <div className="fs-tutor-bubble is-user">
                <p>{t.q}</p>
              </div>
              <div className="fs-tutor-bubble is-assistant">
                {t.answer ? <CitedAnswer text={t.answer} plain={askScope !== "kb"} /> : <p className="fs-muted">这次没有写出回答。</p>}
                {roster.length ? (
                  <ul className="fs-roster">
                    {roster.map((row) => {
                      const href = row.open_href || row.href || "";
                      const line = row.kind === "member" || String(row.chunk_id || "").startsWith("member:") ? personLine(row) : (row.filename || row.title || row.citation);
                      return (
                        <li key={row.chunk_id || row.source_id || line}>
                          {href ? <Link to={href}>{line}</Link> : line}
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
                <AskSourceChips citations={papers} />
                {!t.citations?.length && !roster.length ? <AskEmptyNext emptyNext={t.emptyNext} /> : null}
                <div className="fs-tutor-actions">
                  <Button size="small" type="link" disabled={asking} onClick={() => runAsk(t.q)}>
                    再问这一句
                  </Button>
                  <Button
                    size="small"
                    type="link"
                    onClick={() => copyText(t.answer || "")}
                  >
                    复制回答
                  </Button>
                </div>
                <AskFollowChips
                  items={t.followups}
                  disabled={asking}
                  hint="你可以接着问"
                  onPick={(q) => {
                    askForm.setFieldsValue({ q });
                    runAsk(q);
                  }}
                />
                <AgentTraceBox traceId={t.trace_id} stepCount={(t.steps || []).length} steps={t.steps} />
              </div>
            </div>
            );
          })}
          {asking ? (
            <div className="fs-tutor-turn">
              <div className="fs-tutor-bubble is-user is-pending">
                <p>{lastAskQ.current}</p>
              </div>
              <div className="fs-tutor-bubble is-assistant is-pending">
                <p className="fs-muted">{askScope === "onboarding" ? "正在查这个组…" : "正在看这些论文…"}</p>
              </div>
            </div>
          ) : null}
          {!askTurns.length && !asking ? <p className="fs-muted">还没问过。点上面一句，或在下面写一句。</p> : null}
        </div>
        <div className="fs-ask-compose">
        <Form.Item name="q" rules={[{ required: true }]} style={{ flex: 1, marginBottom: 0 }}>
          <Input.TextArea
            rows={2}
            placeholder={askScope === "onboarding" ? "问这个组：谁负责、先读哪几篇" : "接着问。Enter 提问，Shift+Enter 换行。"}
            disabled={asking || summarizing}
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault();
                askForm.submit();
              }
            }}
          />
        </Form.Item>
        <span className="fs-press-wrap" onClick={whenBlocked(asking || summarizing, asking ? "正在问，请稍等" : "正在写综述，请稍等")}>
        <Button htmlType="submit" type="primary" disabled={asking || summarizing} loading={asking}>
          提问
        </Button>
        </span>
        {asking ? (
          <Button onClick={() => askAbort.current?.abort()}>
            停止
          </Button>
        ) : null}
        </div>
        <div className="fs-tutor-actions">
        {lastAskQ.current && !asking ? (
          <Button type="link" size="small" onClick={() => runAsk(lastAskQ.current)}>
            再问上一句
          </Button>
        ) : null}
        <Button type="link" size="small" disabled={asking} onClick={resetAskThread}>
          新开一轮
        </Button>
        </div>
      </Form>
      </div>
      {summary ? (
        <Card
          title="这个方向的归纳"
          extra={
            papers[0] ? (
              <Button
                type="primary"
                disabled={kbUngrounded || /先把文献入库|还没有足够的正文/.test(summary)}
                onClick={async () => {
                  const target = papers[0];
                  const text = (kbSpans || []).reduce((acc, span) => acc.split(String(span)).join(""), summary).trim();
                  if (!text || kbUngrounded) {
                    message.warning("没有页码的句子不能写进相关工作");
                    return;
                  }
                  try {
                    const ms = await api.getManuscript(target.id);
                    await api.writeback(id, {
                      manuscript_id: target.id,
                      text,
                      write_mode: "append_heading",
                      heading: "相关工作",
                      expected_updated_at: ms.data.updated_at,
                      literature_item_ids: [...new Set((cites || []).map((c) => c.literature_item_id).filter(Boolean))],
                    });
                    message.success("已写进相关工作");
                    nav(`/manuscripts/${target.id}?section=${encodeURIComponent("相关工作")}&wrote=1`);
                  } catch (e) {
                    message.error(formatError(e, "没写进相关工作"));
                  }
                }}
              >
                {papers[0]?.title ? `写进《${papers[0].title}》的相关工作` : "写进相关工作"}
              </Button>
            ) : (
              <Link to={`/labs/${id}/manuscripts`}>先建论文</Link>
            )
          }
        >
          {/先把文献入库/.test(summary) ? (
            <p>
              {summary}{" "}
              <Link to={`/labs/${id}/literature${directionId ? `?direction_id=${encodeURIComponent(directionId)}` : ""}`}>去文献库</Link>
            </p>
          ) : (
            <>
              <CitedAnswer text={summary} />
            </>
          )}
          {(cites || []).map((h) => (
            <div key={h.chunk_id} className="fs-cite">
              <div className="fs-cite-row">
                {h.literature_item_id ? (
                  <Link to={literatureHref(h.literature_item_id, h.page_no || h.last_page_no || 1, directionId ? { direction: directionId } : undefined)}>
                    [p.{h.page_no}] {h.citation}
                    {readingPctLabel(h) ? ` · ${readingPctLabel(h)}` : ""}
                  </Link>
                ) : (
                  <span>{h.citation}（无对应 PDF）</span>
                )}
                {h.literature_item_id ? (
                  <details className="fs-inline-more">
                    <summary>还有</summary>
                    <ActionChip
                      to={h.latest_reproduce?.href || h.reproduce_href || `/labs/${id}/reproduces?literature_item_id=${encodeURIComponent(h.literature_item_id)}`}
                    >
                      {h.latest_reproduce ? "查看复现" : "去复现"}
                    </ActionChip>
                  </details>
                ) : null}
                {h.latest_reproduce?.measured ? (
                  <span className="fs-muted"> · 已经跑出结果</span>
                ) : h.latest_reproduce ? (
                  <span className="fs-muted"> · 还没跑出结果</span>
                ) : null}
              </div>
            </div>
          ))}
        </Card>
      ) : null}
      {reproduceGap ? (
        <Card title="组内复现差距">
          <p className="fs-muted">与综述/问答分开，不是把声称写进综述。</p>
          <MdPreview text={reproduceGap} literatureId={(cites[0] || hits[0] || {}).literature_item_id} />
        </Card>
      ) : null}
      {reproWarn ? <Alert type="warning" showIcon message={reproWarn} /> : null}
      <UnusedResearchAlert note={unusedNote} />
      {kbUngrounded && hits.some((h) => !isRosterHit(h) && (h.literature_item_id || h.kind === "kb_chunk")) ? (
        <Alert
          type="warning"
          showIcon
          message="还有几句没有页码，先不要写进论文。"
          description={kbSpans.length ? `对不上原文：${kbSpans.join("、")}` : null}
        />
      ) : null}
      <AgentRuntimeNotice
        degraded={askDegraded}
        degradedTo={askDegraded === "chat" ? "chat" : ""}
        availableTools={askTools}
        pendingConfirm={askPending}
        confirming={asking}
        onConfirm={() =>
          runAsk(lastAskQ.current, {
            confirm: true,
            confirm_tools: (askPending || []).map((p) => ({ tool: p.tool, args: p.args || {} })),
          })
        }
      />
      {!askTurns.length ? <AgentTraceBox traceId={askTrace} stepCount={askSteps.length} steps={askSteps} /> : null}
      {retrievalMode && !(askScope !== "kb" && hits.some(isRosterHit)) ? (
        <p className="fs-muted">
          {retrievalMode === "simple" ? "普通搜索" : retrievalMode === "hybrid" ? "混合检索" : retrievalMode === "agent" ? "实验室助手（可追问）" : "向量检索"}
        </p>
      ) : null}
      {asking ? <p className="fs-muted">正在看这些论文…</p> : null}
      {answer && !askTurns.length ? (
        <Card title="回答">
          <MdPreview text={answer} literatureId={(hits[0] || askCitations[0] || {}).literature_item_id || (askCitations[0] || {}).item_id} />
          <AskSourceChips citations={directedCites(askCitations, directionId)} />
          <AskFollowChips
            items={[
              ...askFollowups,
              ...(askFollowups.some((q) => String(q).includes("依据"))
                ? []
                : kbSpans.slice(0, 2).map((s) => `这段依据在哪：${String(s).slice(0, 24)}`)),
            ]}
            disabled={asking}
            hint="你可以接着问"
            onPick={(q) => {
              askForm.setFieldsValue({ q });
              runAsk(q);
            }}
          />
          {!askCitations.length ? <AskEmptyNext emptyNext={emptyNext} /> : null}
        </Card>
      ) : asked && emptyNext && !askTurns.length ? (
        <AskEmptyNext emptyNext={emptyNext} />
      ) : asked && (emptyReason === "no_indexed" || emptyReason === "no_hits") ? (
        <EmptyHint to={(emptyNext?.steps || [])[0]?.href}>
          {emptyReason === "no_indexed"
            ? "还没有已索引文档。请先上传可复制文字，失败项点重试后再问。"
            : "已索引库中没有匹配片段。换关键词，或确认文档已切块成功。"}
        </EmptyHint>
      ) : null}
      {asked && hits.some((h) => !isRosterHit(h)) ? (
      <Table
        rowKey={(r) => r.chunk_id || `${r.filename}-${r.page_no}`}
        dataSource={hits.filter((h) => !isRosterHit(h))}
        locale={{ emptyText: emptyReason === "no_indexed" ? "没有已索引片段" : "没有匹配片段" }}
        columns={[
          { title: "页码", dataIndex: "page_no", width: 80 },
          { title: "文件", dataIndex: "filename" },
          {
            title: "片段",
            dataIndex: "citation",
            render: (t, row) =>
              row.literature_item_id ? (
                <span>
                  <Link to={row.literature_item_id ? literatureHref(row.literature_item_id, row.page_no || row.last_page_no || 1, directionId ? { direction: directionId } : undefined) : row.href}>
                    {t}
                    {readingPctLabel(row) ? ` · ${readingPctLabel(row)}` : ""}
                  </Link>
                  {" "}
                  <details className="fs-inline-more">
                    <summary>还有</summary>
                    <ActionChip
                      to={row.latest_reproduce?.href || row.reproduce_href || `/labs/${id}/reproduces?literature_item_id=${encodeURIComponent(row.literature_item_id)}`}
                    >
                      {row.latest_reproduce ? "查看复现" : "去复现"}
                    </ActionChip>
                  </details>
                  {row.latest_reproduce?.measured ? (
                    <span className="fs-muted"> · 已经跑出结果</span>
                  ) : row.latest_reproduce ? (
                    <span className="fs-muted"> · 还没跑出结果</span>
                  ) : null}
                </span>
              ) : (
                t || row.content
              ),
          },
          {
            title: "",
            render: (_, row) =>
              row.chunk_id ? (
                <Button
                  size="small"
                  onClick={async () => {
                    try {
                      await api.kbFeedback(id, { chunk_id: row.chunk_id, useful: true });
                      message.success("已记为有用，下次检索会优先");
                    } catch (e) {
                      message.error(formatError(e, "无法记录反馈"));
                    }
                  }}
                >
                  有用
                </Button>
              ) : null,
          },
        ]}
      />
      ) : null}
      </>
      ) : null}
      {tab === "terms" ? (
      <Card
        title={
          <span>
            术语表
            <HintTip title="术语表">
              {directionId
                ? "术语是全实验室的，不跟上面的方向走。问答才按方向。"
                : "锁定后译润和检索都会用到。不是某个方向的词表。"}
            </HintTip>
          </span>
        }
      >
        {termErr ? <LoadFailed message={termErr} onRetry={reload} /> : null}
        <Table
          rowKey="id"
          dataSource={terms}
          locale={{ emptyText: termErr ? "术语表加载失败，可重试。" : "还没有锁定术语。" }}
          columns={[
            { title: "原文", dataIndex: "src" },
            { title: "译名", dataIndex: "tgt" },
            {
              title: "不译",
              dataIndex: "do_not_translate",
              render: (v, row) => (
                <Switch
                  size="small"
                  checked={!!v}
                  onChange={async (on) => {
                    try {
                      await api.patchGlossary(row.id, { do_not_translate: on });
                      reload();
                    } catch (e) {
                      message.error(formatError(e, "无法更新术语"));
                    }
                  }}
                />
              ),
            },
            {
              title: "",
              render: (_, row) =>
                row.created_by === me?.id || teacher ? (
                  <Popconfirm
                    title="删除该术语？"
                    onConfirm={async () => {
                      try {
                        await api.deleteGlossary(row.id);
                        reload();
                      } catch (e) {
                        message.error(formatError(e, "无法删除术语"));
                      }
                    }}
                  >
                    <Button size="small" danger>
                      删
                    </Button>
                  </Popconfirm>
                ) : null,
            },
          ]}
        />
      </Card>
      ) : null}
    </Space>
  );
}
