import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Collapse, Input, Radio, Select, Space, message } from "antd";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, asItems } from "../api.js";
import { formatError, literatureHref, manuscriptHref, readingPctLabel } from "../err.js";
import { takeLangDraft, visibleTranslation } from "../pdfText.js";
import { joinManuscript, manuscriptSectionHref, parseManuscript, sectionBody } from "../manuscriptSections.js";
import { ActionChip, AgentTraceBox, AiResultBar, DirectionFilterHint, DirectionSelectEmpty, EmptyHint, LoadFailed, MdPreview, ModelKeyHint, PageHeader, UnusedResearchAlert, WritebackSuccess, copyText, whenBlocked } from "../ui.jsx";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";

function parsePage(params) {
  const p = Number(params.get("page"));
  return Number.isFinite(p) && p >= 1 ? Math.trunc(p) : undefined;
}

function noteLabel(n) {
  const body = (n.content_md || "空笔记").replace(/\s+/g, " ").slice(0, 24);
  return n.page_no ? `p.${n.page_no} · ${body}` : body;
}

export default function Language() {
  const { id } = useParams();
  const me = useSession((s) => s.me);
  const teacher = roleOf(me, id) === "teacher" || !!me?.is_admin;
  const [params, setParams] = useSearchParams();
  const textRef = useRef(null);
  const loadedNoteRef = useRef("");
  const loadedMsRef = useRef("");
  const loadedSectionRef = useRef("");
  const runAbort = useRef(null);
  const skipRestore = useRef(false);
  const [text, setText] = useState("");
  const [out, setOut] = useState("");
  const [langNote, setLangNote] = useState("");
  const [mode, setMode] = useState("translate");
  const [target, setTarget] = useState("zh");
  const [manuscripts, setManuscripts] = useState([]);
  const [manuscriptId, setManuscriptId] = useState(null);
  const [papers, setPapers] = useState([]);
  const [litId, setLitId] = useState();
  const [notes, setNotes] = useState([]);
  const [noteId, setNoteId] = useState();
  const [writeTarget, setWriteTarget] = useState("none");
  const [terms, setTerms] = useState([]);
  const [polishLevel, setPolishLevel] = useState("academic");
  const [expandGoal, setExpandGoal] = useState("related_work");
  const [venueNotes, setVenueNotes] = useState("");
  const [writeMode, setWriteMode] = useState("");
  const [selection, setSelection] = useState("");
  const [running, setRunning] = useState(false);
  const [landing, setLanding] = useState(false);
  const [err, setErr] = useState("");
  const [runErr, setRunErr] = useState("");
  const [termErr, setTermErr] = useState("");
  const [noteErr, setNoteErr] = useState("");
  const [wroteHref, setWroteHref] = useState("");
  const [wroteKind, setWroteKind] = useState("");
  const [wroteSummary, setWroteSummary] = useState("");
  const [wroteNext, setWroteNext] = useState(null);
  const [unusedNote, setUnusedNote] = useState(null);
  const [conflictHref, setConflictHref] = useState("");
  const [conflictMsg, setConflictMsg] = useState("");
  const [reproWarn, setReproWarn] = useState("");
  const [draftContext, setDraftContext] = useState("");
  const [langTrace, setLangTrace] = useState("");
  const [langSteps, setLangSteps] = useState([]);
  const [langDegraded, setLangDegraded] = useState("");
  const [langTools, setLangTools] = useState([]);
  const [langFollowups, setLangFollowups] = useState([]);
  const [dirs, setDirs] = useState([]);
  const directionId = params.get("direction_id") || undefined;
  const reloadLists = () => {
    setErr("");
    setTermErr("");
    api
      .manuscripts(id)
      .then((r) => setManuscripts(asItems(r.data)))
      .catch((e) => setErr(formatError(e, "论文列表加载失败")));
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
    api.literature(id, { page: 1, page_size: 100 }).then((r) => setPapers(asItems(r.data))).catch((e) => setErr(formatError(e, "文献列表加载失败")));
    api.directions(id).then((r) => setDirs(asItems(r.data))).catch((e) => {
      setDirs([]);
      message.error(formatError(e, "方向列表没打开"));
    });
  };
  const syncQuery = (patch) => {
    const next = new URLSearchParams(params);
    Object.entries(patch).forEach(([k, v]) => {
      if (v === undefined) return;
      if (v === null || v === "") next.delete(k);
      else next.set(k, String(v));
    });
    if (next.toString() !== params.toString()) setParams(next, { replace: true });
  };
  useEffect(() => {
    reloadLists();
  }, [id]);
  useEffect(() => {
    if (!id) return undefined;
    if (params.get("note") || params.get("ms") || params.get("lit")) return undefined;
    let alive = true;
    api
      .languageLatest(id)
      .then((r) => {
        if (!alive || skipRestore.current || !r.data?.text) return;
        let applied = false;
        setOut((cur) => {
          if (cur || skipRestore.current) return cur;
          applied = true;
          return r.data.text;
        });
        if (!applied) return;
        if (r.data.session_id) setLangTrace((cur) => cur || r.data.session_id);
        if (r.data.truncated) setLangNote((cur) => cur || "上次改过的文字较长，这里只保留前一部分。");
      })
      .catch((e) => {
        if (alive) message.warning(formatError(e, "上次改过的文字没打开"));
      });
    return () => {
      alive = false;
    };
  }, [id, params]);
  useEffect(() => {
    if (params.get("note") || params.get("ms")) return;
    const draft = takeLangDraft();
    if (draft?.text) {
      skipRestore.current = true;
      setOut("");
      setLangNote("");
      setText(draft.text);
      if (draft.mode === "polish" || draft.mode === "translate" || draft.mode === "expand") {
        setMode(draft.mode);
      }
      setDraftContext(draft.context || "");
    }
  }, [id]);
  useEffect(() => {
    const lit = params.get("lit");
    if (lit) setLitId(lit);
    const ms = params.get("ms");
    if (ms) setManuscriptId(ms);
  }, [id, params]);
  useEffect(() => {
    if (!litId) {
      setNotes([]);
      setNoteId();
      setNoteErr("");
      return;
    }
    const loadNotes = () => {
      setNoteErr("");
      api
        .notes(litId)
        .then((r) => {
          setNotes(asItems(r.data));
          setNoteErr("");
        })
        .catch((e) => {
          setNotes([]);
          setNoteErr(formatError(e, "笔记列表加载失败"));
        });
    };
    loadNotes();
  }, [litId]);
  useEffect(() => {
    const nid = params.get("note");
    if (!nid) return;
    const n = notes.find((x) => x.id === nid);
    if (!n) return;
    setNoteId(nid);
    if (loadedNoteRef.current !== nid) {
      setText(n.content_md || "");
      loadedNoteRef.current = nid;
      setWriteTarget("note");
    }
  }, [notes, params]);
  useEffect(() => {
    const ms = params.get("ms");
    if (!ms || params.get("note")) return;
    const m = manuscripts.find((x) => x.id === ms);
    if (!m) return;
    setManuscriptId(ms);
    if (loadedMsRef.current !== ms) {
      const heading = params.get("heading") || "";
      const piece = heading ? sectionBody(m.content_md || "", heading) : "";
      setText(heading ? piece : m.content_md || "");
      loadedSectionRef.current = heading ? piece : "";
      loadedMsRef.current = ms;
      setWriteTarget("manuscript");
    }
  }, [manuscripts, params]);
  const onWriteTarget = (v) => {
    setWriteTarget(v);
    if (v !== "manuscript") {
      setWriteMode("");
      setSelection("");
    }
  };
  const selectedNote = notes.find((n) => n.id === noteId);
  const pageHint = parsePage(params) || selectedNote?.page_no;
  const selectedLit = papers.find((p) => p.id === litId);
  const readerHref = litId
    ? literatureHref(litId, pageHint || selectedLit?.last_page_no, {
        note: noteId,
        ms: manuscriptId,
        panel: noteId ? "notes" : undefined,
      })
    : "";
  const sectionHeading = params.get("heading") || "";
  const fromSection = Boolean(sectionHeading && (params.get("ms") || manuscriptId));
  const msHref = manuscriptId ? manuscriptSectionHref(manuscriptId, sectionHeading) : "";
  const writeIntoThisSection = async () => {
    const chunk = (out || "").trim();
    if (!manuscriptId || !sectionHeading) {
      message.info("先从论文的某一节进来");
      return;
    }
    if (!chunk) {
      message.info("先改出一版");
      return;
    }
    if (running || landing) {
      message.info("正在改，请稍等");
      return;
    }
    setLanding(true);
    setRunErr("");
    try {
      const ms = await api.getManuscript(manuscriptId);
      const body = ms.data.content_md || "";
      const parsed = parseManuscript(body);
      const sec = parsed.sections.find((s) => s.key === sectionHeading || s.title === sectionHeading);
      let href = manuscriptSectionHref(manuscriptId, sectionHeading);
      href = href.includes("?") ? `${href}&wrote=1` : `${href}?wrote=1`;
      if (sec) {
        await api.patchManuscript(manuscriptId, {
          content_md: joinManuscript(parsed, sec.id, chunk),
          expected_updated_at: ms.data.updated_at,
        });
        loadedSectionRef.current = chunk;
        message.success("已经写进这一节");
        setWroteSummary("已经写进这一节");
        setWroteHref(href);
        setWroteKind("manuscript");
        return;
      }
      const r = await api.writeback(id, {
        manuscript_id: manuscriptId,
        text: chunk,
        write_mode: "append_heading",
        heading: sectionHeading,
        expected_updated_at: ms.data.updated_at,
        literature_item_ids: litId ? [litId] : [],
      });
      loadedSectionRef.current = chunk;
      const hint = "已经写进这一节";
      message.success(hint);
      setWroteSummary(r.data.writeback?.summary || hint);
      setWroteNext(r.data.next_step || null);
      setWroteHref(href);
      setWroteKind("manuscript");
      setReproWarn(r.data.ungrounded_reproduce || "");
    } catch (e) {
      if (e?.response?.status === 409) {
        const d = e?.response?.data?.detail;
        setConflictMsg(formatError(e, "内容已被更新，请刷新后再写回"));
        setConflictHref((d && d.href) || manuscriptSectionHref(manuscriptId, sectionHeading));
        return;
      }
      message.error(formatError(e, "没写进这一节"));
    } finally {
      setLanding(false);
    }
  };
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        title={fromSection ? `正在改这篇论文的${sectionHeading}` : "改文字"}
        hint={
          fromSection
            ? "改完写进这一节，会回到这一节。"
            : params.get("ms")
              ? "载入了论文，还没写回去。写回要再点一次。"
              : "先改文字，再写回笔记或论文。"
        }
        extra={
          <Space>
            {msHref ? <Link to={msHref}>回论文</Link> : null}
            {readerHref ? <Link to={readerHref}>回阅读器对应页</Link> : null}
          </Space>
        }
      >
        {fromSection ? `正在改这篇论文的${sectionHeading}` : "改完再写回去"}
      </PageHeader>
      {draftContext ? (
        <Collapse
          ghost
          defaultActiveKey={["ctx"]}
          items={[
            {
              key: "ctx",
              label: "已带上阅读器前后文，只用来消歧，不会当正文改。",
              children: (
                <>
                  <pre className="fs-compare-src">{draftContext}</pre>
                  <Button size="small" onClick={() => setDraftContext("")}>
                    不用前后文
                  </Button>
                </>
              ),
            },
          ]}
        />
      ) : null}
      {err ? <LoadFailed message={err} onRetry={reloadLists} /> : null}
      {termErr ? <LoadFailed message={termErr} onRetry={reloadLists} /> : null}
      <ModelKeyHint err={err} />
      {termErr ? (
        <p className="fs-muted">术语表加载失败，不代表没有锁定词。</p>
      ) : terms.filter((t) => t.do_not_translate).length ? (
        <p className="fs-muted">锁定不译：{terms.filter((t) => t.do_not_translate).map((t) => t.src).join("、")}</p>
      ) : (
        <p className="fs-muted">可在知识库锁定专有名词。</p>
      )}
      {conflictMsg ? (
        <Alert
          type="warning"
          showIcon
          message={
            <span>
              {conflictMsg}{" "}
              {conflictHref ? <ActionChip to={conflictHref}>打开当前对象</ActionChip> : null}
            </span>
          }
        />
      ) : null}
      <WritebackSuccess
        summary={wroteSummary}
        href={wroteHref}
        kind={wroteKind === "note" ? "note" : "manuscript"}
        next={wroteKind === "note" ? null : wroteNext}
      />
      {reproWarn ? <Alert type="warning" showIcon message={reproWarn} /> : null}
      <UnusedResearchAlert note={unusedNote} />
      {litId ? (
        <div className="fs-compare-item">
          <p>
            <Link to={readerHref}>{selectedLit?.title || "打开阅读器"}</Link>
            {selectedLit && readingPctLabel(selectedLit) ? (
              <span className="fs-muted"> · {readingPctLabel(selectedLit)}</span>
            ) : null}
          </p>
          <ActionChip to={readerHref}>回阅读器</ActionChip>
        </div>
      ) : null}
      <div className="fs-work-grid">
        <Card size="small" title="运行">
          <Space wrap>
            <Select
              value={mode}
              style={{ width: 140 }}
              onChange={setMode}
              options={[
                { value: "translate", label: "翻译" },
                { value: "polish", label: "改措辞" },
                { value: "expand", label: "拓展" },
              ]}
            />
            <Select value={target} style={{ width: 120 }} onChange={setTarget} options={[{ value: "zh", label: "中文" }, { value: "en", label: "英文" }]} />
            {mode === "polish" ? (
              <Select
                value={polishLevel}
                style={{ width: 160 }}
                onChange={setPolishLevel}
                options={[
                  { value: "grammar", label: "语法" },
                  { value: "academic", label: "学术" },
                  { value: "venue_style", label: "投稿体例" },
                  { value: "de_ai", label: "去 AI 痕迹" },
                ]}
              />
            ) : null}
            {mode === "expand" ? (
              <Select
                value={expandGoal}
                style={{ width: 180 }}
                onChange={setExpandGoal}
                options={[
                  { value: "explain_junior", label: "给新人讲解" },
                  { value: "related_work", label: "相关工作" },
                  { value: "discussion", label: "讨论" },
                  { value: "compress_abstract", label: "压缩摘要" },
                ]}
              />
            ) : null}
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="方向（拓展相关工作）"
              style={{ width: 220 }}
              value={directionId}
              onChange={(v) => syncQuery({ direction_id: v || null })}
              notFoundContent={<DirectionSelectEmpty labId={id} teacher={teacher} />}
              options={dirs.map((d) => ({
                value: d.id,
                label: d.parent_id ? `↳ ${d.name}` : d.includes_children || d.child_count ? `${d.name}（含小课题）` : d.name,
              }))}
            />
            <DirectionFilterHint labId={id} teacher={teacher} dirs={dirs} />
            <div className="fs-tutor-chips">
              {litId ? (
                <button
                  type="button"
                  className="fs-chip"
                  onClick={async () => {
                    try {
                      const r = await api.literatureDetail(litId, { page: pageHint || 1, include: "page_text" });
                      const body = r.data.page_text || "";
                      if (!body) {
                        message.info("这一页还没有可复制文字");
                        return;
                      }
                      setText(body);
                    } catch (e) {
                      message.error(formatError(e, "无法载入本页"));
                    }
                  }}
                >
                  本页 {pageHint || 1}
                </button>
              ) : null}
              {selectedLit?.abstract ? (
                <button type="button" className="fs-chip" onClick={() => setText(selectedLit.abstract)}>
                  摘要
                </button>
              ) : null}
              {selectedNote?.content_md ? (
                <button type="button" className="fs-chip" onClick={() => setText(selectedNote.content_md)}>
                  本页笔记
                </button>
              ) : null}
            </div>
            {running ? (
              <Button
                onClick={() => {
                  runAbort.current?.abort();
                }}
              >
                停止
              </Button>
            ) : (
            <Button
              id="fs-lang-run"
              type={fromSection && out ? "default" : "primary"}
              onClick={async () => {
                if (!text.trim()) {
                  message.info("先贴一段文字");
                  setRunErr("先贴一段文字。");
                  return;
                }
                if (writeTarget === "manuscript" && !manuscriptId) {
                  message.info("先选论文");
                  return;
                }
                if (writeTarget === "manuscript" && !writeMode && !fromSection) {
                  message.info("先选写回方式");
                  return;
                }
                if (writeTarget === "note" && !noteId) {
                  message.info("先选要写回的笔记");
                  return;
                }
                if (writeTarget === "new_note" && !litId) {
                  message.info("先选一篇文献");
                  return;
                }
                setRunErr("");
                setUnusedNote(null);
                setRunning(true);
                const ac = new AbortController();
                runAbort.current = ac;
                try {
                  const extra = {
                    polish_level: polishLevel,
                    expand_goal: expandGoal,
                    venue_style_notes: venueNotes,
                    stream: true,
                  };
                  if (litId) extra.literature_item_id = litId;
                  if (manuscriptId) extra.manuscript_id = extra.manuscript_id || manuscriptId;
                  if (params.get("direction_id")) extra.direction_id = params.get("direction_id");
                  const pg = parsePage(params) || notes.find((n) => n.id === noteId)?.page_no;
                  if (pg) extra.page_no = pg;
                  if (draftContext && (mode === "polish" || mode === "translate")) extra.context = draftContext;
                  if (writeTarget === "manuscript" && !fromSection) {
                    const ms = await api.getManuscript(manuscriptId);
                    extra.manuscript_id = manuscriptId;
                    extra.write_mode = writeMode;
                    extra.selection = selection;
                    extra.heading = mode === "expand" && expandGoal === "related_work" ? "相关工作" : sectionHeading || "语言工作台";
                    extra.expected_updated_at = ms.data.updated_at;
                    if (litId) extra.literature_item_ids = [litId];
                  } else if (writeTarget === "note") {
                    extra.note_id = noteId;
                    extra.expected_updated_at = notes.find((n) => n.id === noteId)?.updated_at;
                  } else if (writeTarget === "new_note") {
                    extra.write_note = true;
                  }
                  const fn = mode === "polish" ? api.polish : mode === "expand" ? api.expand : api.translate;
                  const cfg = {
                    signal: ac.signal,
                    onDelta: (_d, acc) => {
                      if (mode !== "translate") {
                        setOut(acc);
                        return;
                      }
                      const vis = visibleTranslation(acc);
                      if (vis) setOut(vis);
                    },
                  };
                  const r = mode === "translate" ? await fn(id, text, target, extra, cfg) : await fn(id, text, extra, cfg);
                  setOut(r.data.text);
                  setReproWarn(r.data.ungrounded_reproduce || "");
                  setUnusedNote(r.data.unused_research || null);
                  if (r.data.trace_id || r.data.session_id) setLangTrace(r.data.trace_id || r.data.session_id);
                  setLangSteps(r.data.steps || []);
                  setLangDegraded(r.data.degraded || r.data.degraded_to || "");
                  setLangTools(r.data.available_tools || []);
                  setLangFollowups(r.data.followups || []);
                  if (r.data.quota_warning) message.warning(r.data.quota_warning);
                  if (r.data.wrote_note) {
                    const hint = r.data.writeback?.summary || "已写回笔记";
                    message.success(hint);
                    setWroteSummary(hint);
                    setWroteNext(null);
                    if (r.data.note_id) {
                      setNoteId(r.data.note_id);
                      setWriteTarget("note");
                      loadedNoteRef.current = r.data.note_id;
                    }
                    if (r.data.href) setWroteHref(r.data.href);
                    setWroteKind("note");
                    const lid = litId || r.data.literature_item_id;
                    if (lid) {
                      api
                        .notes(lid)
                        .then((x) => {
                          setNotes(asItems(x.data));
                          setNoteErr("");
                        })
                        .catch((e) => {
                          setNotes([]);
                          setNoteErr(formatError(e, "笔记列表加载失败"));
                        });
                    }
                    syncQuery({
                      lit: lid || null,
                      note: r.data.note_id || noteId || null,
                      page: r.data.page_no || parsePage(params) || null,
                    });
                  } else if (r.data.wrote) {
                    const cov = r.data.core_coverage || {};
                    const named =
                      cov.core_total > 0 ? `已点名 ${cov.core_mentioned_count || 0} / ${cov.core_total} 篇核心文献` : "";
                    const hint = [r.data.writeback?.summary || "已写回论文", named].filter(Boolean).join(" · ");
                    message.success(hint);
                    setWroteSummary(hint);
                    setWroteNext(r.data.next_step || null);
                    const hid = r.data.manuscript_id || manuscriptId;
                    setWroteHref(r.data.href || manuscriptHref(hid));
                    setWroteKind("manuscript");
                    if (hid) syncQuery({ ms: hid });
                    reloadLists();
                  }
                } catch (e) {
                  if (e?.name === "AbortError" || e?.code === "ERR_CANCELED") return;
                  if (e?.response?.status === 409) {
                    const d = e?.response?.data?.detail;
                    setConflictMsg(formatError(e, "内容已被更新，请刷新后再写回"));
                    setConflictHref((d && d.href) || "");
                    return;
                  }
                  const keep = mode === "translate" ? visibleTranslation(e?.partial || "") : "";
                  if (keep) setOut(keep);
                  setRunErr(keep ? "模型夹带了说明。已尽量只留下译文。" : formatError(e, "运行失败"));
                } finally {
                  setRunning(false);
                  runAbort.current = null;
                }
              }}
            >
              {mode === "polish" ? "改措辞" : mode === "expand" ? "拓展" : "译"}
            </Button>
            )}
            {fromSection ? (
              <span className="fs-press-wrap" onClick={whenBlocked(!out || landing, !out ? "先改出一版" : "正在写进这一节")}>
                <Button type={out ? "primary" : "default"} loading={landing} disabled={!out || landing} onClick={writeIntoThisSection}>
                  写进这一节
                </Button>
              </span>
            ) : null}
          </Space>
          {runErr ? <Alert type="error" showIcon message={runErr} style={{ marginTop: 8 }} /> : null}
          {polishLevel === "venue_style" ? (
            <Input.TextArea
              rows={2}
              placeholder="自定义投稿须知"
              value={venueNotes}
              onChange={(e) => setVenueNotes(e.target.value)}
              style={{ marginTop: 8 }}
            />
          ) : null}
        </Card>
        <Card size="small" title="载入原文">
          <p className="fs-muted">只把内容放进左侧原文，不会写回。</p>
          <Space wrap>
            <Select
              allowClear
              placeholder="选择论文"
              style={{ width: 200 }}
              value={manuscriptId}
              onChange={(v) => {
                setManuscriptId(v);
                loadedMsRef.current = "";
                syncQuery({ ms: v || null });
              }}
              options={manuscripts.map((m) => ({ value: m.id, label: m.title }))}
            />
            <Button
              onClick={() => {
                const m = manuscripts.find((x) => x.id === manuscriptId);
                if (!m) {
                  message.info("请先选择论文");
                  return;
                }
                const heading = params.get("heading") || "";
                const piece = heading ? sectionBody(m.content_md || "", heading) : "";
                setText(heading ? piece : m.content_md || "");
                loadedSectionRef.current = heading ? piece : "";
                loadedMsRef.current = m.id;
                message.success(heading ? (piece ? "已载入这一节" : "这一节还是空的") : "已载入论文正文");
              }}
            >
              载入论文
            </Button>
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="选择文献"
              style={{ width: 200 }}
              value={litId}
              onChange={(v) => {
                setLitId(v);
                setNoteId();
                loadedNoteRef.current = "";
                syncQuery({ lit: v || null, note: null });
              }}
              options={papers.map((p) => ({ value: p.id, label: p.title }))}
            />
            {litId ? (
              <Link to={readerHref || literatureHref(litId, pageHint)}>打开阅读器</Link>
            ) : null}
            {noteErr ? (
              <LoadFailed
                message={noteErr}
                onRetry={() => {
                  setNoteErr("");
                  api
                    .notes(litId)
                    .then((r) => {
                      setNotes(asItems(r.data));
                      setNoteErr("");
                    })
                    .catch((e) => {
                      setNotes([]);
                      setNoteErr(formatError(e, "笔记列表加载失败"));
                    });
                }}
              />
            ) : null}
            <Select
              allowClear
              placeholder={noteErr ? "笔记加载失败" : "选择已有笔记"}
              style={{ width: 240 }}
              value={noteId}
              onChange={(v) => {
                setNoteId(v);
                const n = notes.find((x) => x.id === v);
                syncQuery({ note: v || null, page: n?.page_no || parsePage(params) || null });
              }}
              options={notes.map((n) => ({ value: n.id, label: noteLabel(n) }))}
            />
            <Button
              onClick={() => {
                const n = notes.find((x) => x.id === noteId);
                if (!n) {
                  message.info("请先选择笔记");
                  return;
                }
                setText(n.content_md || "");
                loadedNoteRef.current = n.id;
                message.success("已载入笔记");
              }}
            >
              载入笔记
            </Button>
          </Space>
        </Card>
        <Card size="small" title="写回目标">
          <Radio.Group value={writeTarget} onChange={(e) => onWriteTarget(e.target.value)} optionType="button" buttonStyle="solid">
            <Radio.Button value="none">不写回</Radio.Button>
            <Radio.Button value="manuscript">论文</Radio.Button>
            <Radio.Button value="note">已有笔记</Radio.Button>
            <Radio.Button value="new_note">作为新笔记</Radio.Button>
          </Radio.Group>
          {writeTarget === "none" ? <p className="fs-muted">只生成结果，不改论文或笔记。</p> : null}
          {fromSection ? <p className="fs-muted">改完点「写进这一节」，会换上这一节里的文字。</p> : null}
          {writeTarget === "manuscript" && !fromSection ? (
            <Space wrap style={{ marginTop: 8 }}>
              <Select
                placeholder="写回方式"
                style={{ width: 180 }}
                value={writeMode || undefined}
                onChange={(v) => setWriteMode(v || "")}
                options={[
                  { value: "replace_selection", label: "替换选区" },
                  { value: "append_heading", label: "追加到标题下" },
                  { value: "append", label: "文末追加" },
                ]}
              />
              <span className="fs-muted">
                使用载入区已选论文。拓展「相关工作」并写回时写入「相关工作」节（文末追加也会改成追加到该节），并点名已选文献。未选论文或未选方式不会写回。
              </span>
            </Space>
          ) : null}
          {writeTarget === "note" ? <p className="fs-muted">写入载入区已选笔记。仅作者可改，冲突请刷新后再运行。写回后可回阅读器对应页。</p> : null}
          {writeTarget === "new_note" ? (
            <p className="fs-muted">
              在载入区所选文献下新建一条笔记
              {pageHint ? `，绑定第 ${pageHint} 页` : ""}
              。仅选文献、目标仍为「不写回」时不会建笔记。
            </p>
          ) : null}
          {writeTarget === "manuscript" && writeMode === "replace_selection" ? (
            <Space wrap style={{ marginTop: 8 }}>
              <Input placeholder="要替换的选区原文" value={selection} onChange={(e) => setSelection(e.target.value)} style={{ width: 280 }} />
              <Button
                size="small"
                onClick={() => {
                  const el = textRef.current?.resizableTextArea?.textArea;
                  if (!el || el.selectionStart === el.selectionEnd) {
                    message.info("请先在原文中选一段");
                    return;
                  }
                  setSelection(text.slice(el.selectionStart, el.selectionEnd));
                }}
              >
                用原文选区
              </Button>
            </Space>
          ) : null}
        </Card>
      </div>
      <div className="fs-dual">
        <Card title="原文">
          <Input.TextArea
            ref={textRef}
            rows={16}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={fromSection ? "这一节还是空的。先贴一段，改完再写回去。" : "粘贴要译、要改或要拓展的文字"}
          />
        </Card>
        <Card
          title="结果"
          extra={
            out ? (
              <Button
                size="small"
                onClick={() => copyText(out)}
              >
                复制
              </Button>
            ) : null
          }
        >
          {langNote ? <p className="fs-muted">{langNote}</p> : null}
          {out ? (
            <MdPreview text={out} literatureId={litId} extra={{ page: pageHint, ms: manuscriptId, note: noteId }} />
          ) : fromSection ? (
            <p className="fs-muted">改完会出现在这里，再写进这一节。</p>
          ) : (
            <EmptyHint to={`/labs/${id}/manuscripts`}>先打开一篇论文，改完写回那一节。</EmptyHint>
          )}
          <AiResultBar
            text={out}
            busy={running}
            traceId={langTrace}
            steps={langSteps}
            degraded={langDegraded}
            availableTools={langTools}
            followups={langFollowups}
            retryLabel="再跑上一句"
            onRetry={out || langTrace ? () => document.getElementById("fs-lang-run")?.click() : undefined}
          />
        </Card>
      </div>
    </Space>
  );
}
