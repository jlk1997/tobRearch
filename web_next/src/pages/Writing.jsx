import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Input, Select, Space, Steps, Tree, message } from "antd";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, asItems } from "../api.js";
import { formatError, MS_KIND, literatureHref, languageHref, manuscriptHref, readingPctLabel } from "../err.js";
import { ActionChip, AgentTraceBox, AiResultBar, AnalyzeChip, AskSourceChips, DirectionFilterHint, DirectionSelectEmpty, EmptyHint, LoadFailed, MdPreview, ModelKeyHint, PageHeader, UnusedResearchAlert, UnusedTitleChips, WritebackSuccess, whenBlocked } from "../ui.jsx";
import { joinManuscript, parseManuscript, sectionBody } from "../manuscriptSections.js";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";

function treeData(outline) {
  return (outline?.sections || []).map((s) => ({
    key: s.id || s.heading,
    title: s.heading,
    heading: s.heading,
    children: (s.children || []).map((c) => ({
      key: c.id || c.heading,
      title: c.heading,
      heading: c.heading,
    })),
  }));
}

export default function Writing() {
  const { id } = useParams();
  const nav = useNavigate();
  const me = useSession((s) => s.me);
  const teacher = roleOf(me, id) === "teacher" || !!me?.is_admin;
  const [params, setParams] = useSearchParams();
  const [dirs, setDirs] = useState([]);
  const [papers, setPapers] = useState([]);
  const [manuscripts, setManuscripts] = useState([]);
  const [directionId, setDirectionId] = useState();
  const [kind, setKind] = useState("survey");
  const [litIds, setLitIds] = useState(() =>
    (params.get("lit") || "")
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean)
  );
  const [title, setTitle] = useState("");
  const [stepOpen, setStepOpen] = useState({});
  const [showLegacy, setShowLegacy] = useState(false);
  const [outline, setOutline] = useState(null);
  const [outlineMd, setOutlineMd] = useState("");
  const [skeleton, setSkeleton] = useState(false);
  const [outlineNote, setOutlineNote] = useState("");
  const [sectionNote, setSectionNote] = useState("");
  const [honestyNotes, setHonestyNotes] = useState([]);
  const [sectionUngrounded, setSectionUngrounded] = useState(false);
  const [sectionSpans, setSectionSpans] = useState([]);
  const [heading, setHeading] = useState(() => params.get("heading") || "");
  const [notes, setNotes] = useState("");
  const [draft, setDraft] = useState("");
  const [cites, setCites] = useState([]);
  const [comments, setComments] = useState([]);
  const [commentErr, setCommentErr] = useState("");
  const [manuscriptId, setManuscriptId] = useState(() => params.get("ms") || undefined);
  const [writeMode, setWriteMode] = useState("append_heading");
  const [selection, setSelection] = useState("");
  const [running, setRunning] = useState("");
  const [err, setErr] = useState("");
  const [wbHint, setWbHint] = useState("");
  const [wbHref, setWbHref] = useState("");
  const [wbNext, setWbNext] = useState(null);
  const [wbConflict, setWbConflict] = useState("");
  const [wbConflictHref, setWbConflictHref] = useState("");
  const [reproWarn, setReproWarn] = useState("");
  const [coverage, setCoverage] = useState(null);
  const [coverageErr, setCoverageErr] = useState("");
  const [draftNext, setDraftNext] = useState(null);
  const [outlineNext, setOutlineNext] = useState(null);
  const [writeTrace, setWriteTrace] = useState("");
  const [writeSteps, setWriteSteps] = useState([]);
  const [writeDegraded, setWriteDegraded] = useState("");
  const [writeTools, setWriteTools] = useState([]);
  const [writeFollowups, setWriteFollowups] = useState([]);
  const lastWrite = useRef("");
  const writeAbort = useRef(null);
  const intoLock = useRef(false);
  const skipHeadingWrite = useRef(false);
  const applyWritePack = (data) => {
    if (data?.trace_id || data?.session_id) setWriteTrace(data.trace_id || data.session_id);
    setWriteSteps(data?.steps || []);
    setWriteDegraded(data?.degraded || data?.degraded_to || "");
    setWriteTools(data?.available_tools || []);
    setWriteFollowups(data?.followups || []);
  };
  const writebackError = (e) => {
    if (e?.response?.status === 409) {
      const d = e?.response?.data?.detail;
      setWbConflict(formatError(e, "这篇刚被改过，请刷新后再写回"));
      setWbConflictHref((d && d.href) || (manuscriptId ? `/manuscripts/${manuscriptId}` : ""));
    }
    return formatError(e, e?.response?.status === 409 ? "这篇刚被改过，请刷新后再写回" : "写回失败");
  };
  const reloadLists = () => {
    setErr("");
    api.directions(id).then((r) => setDirs(asItems(r.data))).catch((e) => setErr(formatError(e, "方向列表加载失败")));
    api
      .literature(id, { page: 1, page_size: 100, direction_id: directionId || undefined })
      .then((r) => setPapers(asItems(r.data)))
      .catch((e) => setErr(formatError(e, "文献列表加载失败")));
    api
      .manuscripts(id, directionId ? { direction_id: directionId } : {})
      .then((r) => setManuscripts(asItems(r.data)))
      .catch((e) => setErr(formatError(e, "论文列表加载失败")));
  };
  const doWriteback = async ({ text, write_mode, heading: h, selection: sel }) => {
    if (running === "wb") return null;
    setRunning("wb");
    try {
    const ms = await api.getManuscript(manuscriptId);
    const r = await api.writeback(id, {
      manuscript_id: manuscriptId,
      text,
      write_mode,
      heading: h,
      selection: sel,
      expected_updated_at: ms.data.updated_at,
      literature_item_ids: litIds,
    });
    const cov = r.data.core_coverage || {};
    const named =
      cov.core_total > 0 ? `已点名 ${cov.core_mentioned_count || 0} / ${cov.core_total} 篇核心文献` : "";
    setWbHint([r.data.writeback?.summary || "已写入论文", named].filter(Boolean).join(" · "));
    setWbHref(r.data.href || (manuscriptId ? `/manuscripts/${manuscriptId}` : ""));
    setWbNext(r.data.next_step || null);
    setReproWarn(r.data.ungrounded_reproduce || "");
    if (r.data.core_coverage) setCoverage(r.data.core_coverage);
    reloadLists();
    return r.data;
    } finally {
      setRunning("");
    }
  };
  const writeIntoThisSection = async () => {
    if (!manuscriptId) {
      message.info("先建论文");
      return;
    }
    if (!litIds.length) {
      message.warning("先选要写进去的文献");
      return;
    }
    if (intoLock.current || running) {
      message.info("正在写，请稍等");
      return;
    }
    const h = heading || "相关工作";
    intoLock.current = true;
    let text = "";
    let stage = "sec";
    try {
      try {
        const existing = await api.getManuscript(manuscriptId);
        if (sectionBody(existing.data.content_md || "", h).trim()) {
          message.info("这一节已经有文字。新的内容会加在后面。");
        }
      } catch {
        /* 打不开旧稿时仍继续写这一节 */
      }
      setRunning("sec");
      const ac = new AbortController();
      writeAbort.current = ac;
      const r = await api.writeSection(
        id,
        {
          heading: h,
          notes,
          literature_item_ids: litIds,
          manuscript_id: manuscriptId,
          direction_id: directionId || undefined,
        },
        { signal: ac.signal }
      );
      text = r.data.text || "";
      setDraft(text);
      setCites(r.data.citations || []);
      if (r.data.ungrounded_reproduce) setReproWarn(r.data.ungrounded_reproduce);
      if (r.data.core_coverage) setCoverage(r.data.core_coverage);
      if (!String(text).trim()) {
        message.warning("这一节还是空的");
        return;
      }
      stage = "wb";
      const fresh = await api.getManuscript(manuscriptId);
      const parsed = parseManuscript(fresh.data.content_md || "");
      const sec = parsed.sections.find((s) => s.key === h || s.title === h);
      if (sec && sec.headingLine.trim() !== `## ${h}`) {
        const nextBody = sec.body.trim() ? `${sec.body}\n\n${text}` : text;
        await api.patchManuscript(manuscriptId, {
          content_md: joinManuscript(parsed, sec.id, nextBody),
          expected_updated_at: fresh.data.updated_at,
        });
        nav(`/manuscripts/${manuscriptId}?section=${encodeURIComponent(h)}&wrote=1`);
        return;
      }
      const data = await doWriteback({ text, write_mode: "append_heading", heading: h });
      if (!data) return;
      nav(`/manuscripts/${manuscriptId}?section=${encodeURIComponent(h)}&wrote=1`);
    } catch (e) {
      if (e?.name === "AbortError" || e?.code === "ERR_CANCELED") return;
      message.error(stage === "wb" ? writebackError(e) : formatError(e, "这一节没写成"));
    } finally {
      intoLock.current = false;
      writeAbort.current = null;
      setRunning("");
    }
  };
  useEffect(() => {
    reloadLists();
  }, [id, directionId]);
  useEffect(() => {
    if (!id) return undefined;
    let alive = true;
    api
      .writingLatest(id)
      .then((r) => {
        if (!alive) return;
        const md = r.data?.markdown || r.data?.text || "";
        if (!md) return;
        setOutlineMd((cur) => cur || md);
        if (r.data?.outline) setOutline((cur) => cur || r.data.outline);
        if (r.data?.session_id) setWriteTrace((cur) => cur || r.data.session_id);
        if (r.data?.truncated) setOutlineNote((cur) => cur || "上次大纲较长，这里只保留前一部分。");
      })
      .catch((e) => {
        if (alive) message.warning(formatError(e, "上次的大纲没打开"));
      });
    return () => {
      alive = false;
    };
  }, [id]);
  useEffect(() => {
    if (!id) return undefined;
    let alive = true;
    api
      .writingSectionLatest(id)
      .then((r) => {
        if (!alive) return;
        const md = r.data?.markdown || r.data?.text || "";
        if (!md) return;
        let applied = false;
        setDraft((cur) => {
          if (cur) return cur;
          applied = true;
          return md;
        });
        if (!applied) return;
        if (r.data?.heading) setHeading((cur) => cur || r.data.heading);
        if (r.data?.session_id) setWriteTrace((cur) => cur || r.data.session_id);
        if (r.data?.truncated) setSectionNote((cur) => cur || "上次章节较长，这里只保留前一部分。");
      })
      .catch((e) => {
        if (alive) message.warning(formatError(e, "上次的章节没打开"));
      });
    return () => {
      alive = false;
    };
  }, [id]);
  useEffect(() => {
    const lit = (params.get("lit") || "").split(",").map((x) => x.trim()).filter(Boolean);
    setLitIds((cur) => (cur.join(",") === lit.join(",") ? cur : lit));
    const ms = params.get("ms") || undefined;
    setManuscriptId((cur) => (cur === ms ? cur : ms));
    const h = params.get("heading") || "";
    setHeading((cur) => {
      if (cur === h) return cur;
      skipHeadingWrite.current = true;
      return h;
    });
  }, [id, params]);
  useEffect(() => {
    if (skipHeadingWrite.current) {
      skipHeadingWrite.current = false;
      return;
    }
    const next = new URLSearchParams(params);
    if (litIds.length) next.set("lit", litIds.join(","));
    else next.delete("lit");
    if (manuscriptId) next.set("ms", manuscriptId);
    else next.delete("ms");
    if (heading) next.set("heading", heading);
    else next.delete("heading");
    if (next.toString() !== params.toString()) setParams(next, { replace: true });
  }, [litIds, manuscriptId, heading]);
  useEffect(() => {
    if (!manuscriptId) {
      setComments([]);
      setCommentErr("");
      setCoverage(null);
      setCoverageErr("");
      return undefined;
    }
    const loadComments = () => {
      setCommentErr("");
      api
        .comments(manuscriptId)
        .then((r) => {
          setComments(asItems(r.data));
          setCommentErr("");
        })
        .catch((e) => {
          setComments([]);
          setCommentErr(formatError(e, "批注加载失败"));
        });
    };
    loadComments();
    api
      .manuscriptClaims(manuscriptId)
      .then((r) => {
        setCoverage(r.data);
        setCoverageErr("");
      })
      .catch((e) => {
        setCoverage(null);
        setCoverageErr(formatError(e, "核心文献覆盖加载失败"));
      });
    return undefined;
  }, [manuscriptId]);
  const liveStep = manuscriptId ? "" : !outline ? "ol" : !draft ? "sec" : "wb";
  const stepShown = (key) => (Object.prototype.hasOwnProperty.call(stepOpen, key) ? !!stepOpen[key] : key === liveStep);
  const toggleStep = (key) => setStepOpen((s) => ({ ...s, [key]: !stepShown(key) }));
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        title={manuscriptId ? `正在帮这篇论文写${heading || "这一节"}` : "帮论文写一节"}
        hint={manuscriptId ? "写完会回到这一节。" : "写完的文字会回到那一篇里。"}
      >
        {manuscriptId ? "选好文献，写进这一节。" : "先打开一篇论文，或在这里新建。"}
      </PageHeader>
      {showLegacy ? (
        <Steps
          size="small"
          current={!outline ? 0 : heading === "相关工作" && draft ? 1 : !draft ? 1 : manuscriptId ? 3 : 2}
          style={{ marginBottom: 8 }}
          items={[{ title: "选题与大纲" }, { title: "相关工作" }, { title: "章节" }, { title: "写回" }]}
        />
      ) : null}
      <p className="fs-muted" style={{ marginBottom: 16 }}>
        {manuscriptId
          ? `正在写${heading || "这一节"}。`
          : "先打开一篇论文，或在这里新建。写完的文字会回到那一篇里。"}
      </p>
      {manuscriptId ? (
        <Card>
          <Select
            mode="multiple"
            allowClear
            placeholder="先选要写进去的文献"
            style={{ minWidth: 280, marginBottom: 8 }}
            value={litIds}
            onChange={setLitIds}
            options={papers.map((p) => ({ value: p.id, label: p.title }))}
          />
          <div>
            <Button type="primary" loading={running === "sec" || running === "wb"} onClick={writeIntoThisSection}>
              写进这一节
            </Button>
            {running === "sec" ? (
              <Button
                style={{ marginLeft: 8 }}
                onClick={() => {
                  writeAbort.current?.abort();
                }}
              >
                停止
              </Button>
            ) : null}
          </div>
          {!litIds.length ? <p className="fs-muted">还没选文献。先选好，再写进这一节。</p> : null}
        </Card>
      ) : (
        <Space>
          <Button type="primary" onClick={() => nav(`/labs/${id}/manuscripts`)}>
            先建论文
          </Button>
          <Link to={`/labs/${id}/manuscripts`}>去论文</Link>
        </Space>
      )}
      {coverageErr ? <LoadFailed message={coverageErr} onRetry={() => {
        if (!manuscriptId) return;
        api
          .manuscriptClaims(manuscriptId)
          .then((r) => {
            setCoverage(r.data);
            setCoverageErr("");
          })
          .catch((e) => setCoverageErr(formatError(e, "核心文献覆盖加载失败")));
      }} /> : null}
      {coverage?.core_pinned_count ? (
        <p className="fs-muted">
          {coverage.includes_children ? "这个大方向（含小课题）" : "本方向"}已钉 {coverage.core_pinned_count} 篇核心。
        </p>
      ) : null}
      {coverage?.core_missing_count ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`还有 ${coverage.core_missing_count} 篇没写进正文。${coverage.includes_children ? "算上这个大方向里的小课题。" : ""}`}
          description={
            manuscriptId ? (
              <ActionChip to={`/manuscripts/${manuscriptId}?tab=claims`}>去看还没写到</ActionChip>
            ) : null
          }
        />
      ) : null}
      <UnusedResearchAlert note={coverage?.unused_research} />
      {err ? <LoadFailed message={err} onRetry={reloadLists} /> : null}
      <ModelKeyHint err={err} />
      <Button onClick={() => setShowLegacy((v) => !v)}>{showLegacy ? "收起其他写法" : "其他写法"}</Button>
      <div className={!showLegacy ? "fs-write-legacy is-folded" : "fs-write-legacy"}>
      <Card
        size="small"
        className={stepShown("ol") ? "fs-step-live" : "fs-step-done"}
        title={
          <button type="button" className="fs-step-toggle" onClick={() => toggleStep("ol")}>
            选题与大纲
          </button>
        }
      >
        <Space wrap>
          <Select
            allowClear
            placeholder="方向（可选）"
            style={{ width: 180 }}
            value={directionId}
            onChange={setDirectionId}
            notFoundContent={<DirectionSelectEmpty labId={id} teacher={teacher} />}
            options={dirs.map((d) => ({
              value: d.id,
              label: `${d.parent_id ? "↳ " : ""}${d.name}${d.includes_children || d.child_count ? " · 含小课题" : ""}`,
            }))}
          />
          <DirectionFilterHint labId={id} teacher={teacher} dirs={dirs} />
          <Select value={kind} style={{ width: 140 }} onChange={setKind} options={Object.entries(MS_KIND).map(([value, label]) => ({ value, label }))} />
          <Select
            mode="multiple"
            allowClear
            placeholder="文献（可选，建议至少两篇）"
            style={{ minWidth: 280 }}
            value={litIds}
            onChange={setLitIds}
            options={papers.map((p) => ({ value: p.id, label: p.title }))}
          />
          <Input placeholder="题目" style={{ width: 220 }} value={title} onChange={(e) => setTitle(e.target.value)} />
          {running === "ol" ? (
            <Button
              onClick={() => {
                writeAbort.current?.abort();
              }}
            >
              停止
            </Button>
          ) : (
          <Button
            id="fs-write-ol"
            type="default"
            onClick={async () => {
              setRunning("ol");
              const ac = new AbortController();
              writeAbort.current = ac;
              try {
                const r = await api.outline(
                  id,
                  {
                    title: title || "未命名",
                    kind,
                    direction_id: directionId,
                    literature_item_ids: litIds,
                    manuscript_id: manuscriptId || undefined,
                  },
                  { signal: ac.signal }
                );
                setOutline(r.data.outline);
                setOutlineMd(r.data.markdown || "");
                setOutlineNote(r.data.notes || "");
                setHonestyNotes(r.data.honesty_notes || []);
                setOutlineNext(r.data.next_step || null);
                setSkeleton(
                  r.data.notes === "未结合文献" ||
                    String(r.data.notes || "").includes("还没写方法") ||
                    String(r.data.notes || "").includes("方法卡") ||
                    r.data.outline?.fallback
                );
                if (r.data.quota_warning) message.warning(r.data.quota_warning);
                lastWrite.current = "ol";
                applyWritePack(r.data);
              } catch (e) {
                if (e?.name === "AbortError" || e?.code === "ERR_CANCELED") return;
                message.error(formatError(e, "大纲失败"));
              } finally {
                setRunning("");
                writeAbort.current = null;
              }
            }}
          >
            生成大纲
          </Button>
          )}
          <span className="fs-press-wrap" onClick={whenBlocked(!outlineMd, "先生成大纲")}>
          <Button
            disabled={!outlineMd}
            onClick={async () => {
              try {
                const r = await api.addManuscript(id, {
                  title: title || outline?.title || "未命名",
                  kind,
                  content_md: outlineMd,
                  direction_ids: directionId ? [directionId] : [],
                });
                setManuscriptId(r.data.id);
                setHeading("相关工作");
                setOutlineNext(r.data.next_step || null);
                reloadLists();
                message.success(r.data.next_step?.text || "已从大纲创建论文");
              } catch (e) {
                message.error(formatError(e, "无法创建论文"));
              }
            }}
          >
            从大纲创建论文
          </Button>
          </span>
        </Space>
        {litIds.length ? (
          <div className="fs-compare-items" style={{ marginTop: 12 }}>
            {litIds.map((lid) => {
              const p = papers.find((x) => x.id === lid) || {};
              return (
                <div key={lid} className="fs-compare-item">
                  <p>
                    <Link to={manuscriptId ? literatureHref(lid, p.last_page_no, { ms: manuscriptId, section: heading || "相关工作" }) : p.href || literatureHref(lid, p.last_page_no)}>
                      {p.title || "打开文献"}
                    </Link>
                    {readingPctLabel(p) ? <span className="fs-muted"> · {readingPctLabel(p)}</span> : null}
                    {" "}
                    <AnalyzeChip row={p.id ? p : { id: lid, has_method_card: p.has_method_card }} />
                  </p>
                  <details className="fs-inline-more">
                    <summary>还有</summary>
                    <ActionChip to={p.reproduce_href || `/labs/${id}/reproduces?literature_item_id=${encodeURIComponent(lid)}`}>
                      去复现
                    </ActionChip>
                  </details>
                </div>
              );
            })}
          </div>
        ) : null}
        {(honestyNotes.length
          ? honestyNotes
          : outlineNote
            ? [{ kind: "note", level: skeleton ? "info" : "success", text: outlineNote }]
            : []
        ).map((n) => (
          <Alert
            key={n.kind || n.text}
            type={n.level === "success" ? "success" : n.level === "warning" ? "warning" : "info"}
            showIcon
            style={{ marginTop: 12 }}
            message={n.text}
            description={
              n.kind === "unused_research" ? (
                <>
                  {n.href ? <ActionChip to={n.href}>{n.label || "打开文献调研"}</ActionChip> : null}
                  <UnusedTitleChips titles={n.titles || []} href={n.href} more={n.title_more || 0} />
                </>
              ) : n.kind === "literature"
                ? "已用骨架大纲。选文献后再生成，章节标题会吃进论文主题。生成不等于写回，也不会自动建稿。"
                : n.kind === "method_card" && n.level !== "success"
                  ? (
                    <>
                      先打开文献，点「提炼方法」，再生成大纲。{" "}
                      <AnalyzeChip row={{ id: n.item_id, card_href: n.href, has_method_card: false }} />
                    </>
                  )
                  : n.kind === "reproduce" && n.level !== "success"
                    ? "组里还没跑出结果时，只写差距。"
                    : null
            }
          />
        ))}
        {outlineNext?.href ? (
          <div className="fs-writeback-next" style={{ marginTop: 10 }}>
            下一步：<ActionChip to={outlineNext.href}>{outlineNext.label || "写相关工作"}</ActionChip>
            {outlineNext.text ? <span className="fs-muted"> {outlineNext.text}</span> : null}
          </div>
        ) : null}
        <div style={{ marginTop: 12 }}>
          {outline ? (
            <Tree
              defaultExpandAll
              treeData={treeData(outline)}
              onSelect={(_, info) => {
                const h = info.node?.heading;
                if (h) setHeading(h);
              }}
            />
          ) : outlineMd ? (
            <MdPreview text={outlineMd} />
          ) : (
            <EmptyHint>可不选文献：会生成含「相关工作」的骨架。选文献后标题应能看出论文主题。</EmptyHint>
          )}
        </div>
      </Card>
      <div className="fs-object">
        <Card
          className={stepShown("sec") ? "fs-step-live" : "fs-step-done"}
          title={<button type="button" className="fs-step-toggle" onClick={() => toggleStep("sec")}>章节草稿</button>}
        >
          <div className="fs-tutor-chips">
            <span className="fs-chip">文献 {litIds.length} 篇</span>
            {manuscriptId ? <span className="fs-chip">已选论文</span> : null}
            <span className="fs-chip">{heading || "相关工作"}</span>
          </div>
          <Input placeholder="章节标题" value={heading} onChange={(e) => setHeading(e.target.value)} style={{ marginBottom: 8 }} />
          <Input.TextArea rows={4} placeholder="备注" value={notes} onChange={(e) => setNotes(e.target.value)} />
          <Button
            style={{ margin: "8px 8px 8px 0" }}
            onClick={() => setHeading("相关工作")}
          >
            写相关工作
          </Button>
          {running === "sec" ? (
            <Button
              style={{ margin: "8px 8px 8px 0" }}
              onClick={() => {
                writeAbort.current?.abort();
              }}
            >
              停止
            </Button>
          ) : (
          <Button
            id="fs-write-sec"
            style={{ margin: "8px 8px 8px 0" }}
            type="default"
            onClick={async () => {
              setRunning("sec");
              setSectionNote("");
              const ac = new AbortController();
              writeAbort.current = ac;
              try {
                const r = await api.writeSection(
                  id,
                  {
                    heading: heading || "相关工作",
                    notes,
                    literature_item_ids: litIds,
                    manuscript_id: manuscriptId || undefined,
                    direction_id: directionId || undefined,
                  },
                  { signal: ac.signal }
                );
                setDraft(r.data.text || "");
                setCites(r.data.citations || []);
                setSectionUngrounded(Boolean(r.data.ungrounded));
                setSectionSpans(r.data.ungrounded_spans || []);
                if (r.data.ungrounded_reproduce) setReproWarn(r.data.ungrounded_reproduce);
                if ((r.data.no_fulltext_titles || []).length) {
                  message.warning(`无全文仅摘要：${(r.data.no_fulltext_titles || []).join("；")}`);
                }
                if (r.data.related_work && !r.data.used_method_card && (litIds || []).length) {
                  message.info("相关工作还没对照这篇的方法，只按题名写了。");
                }
                if (r.data.citations_added) message.success(`已为论文补 ${r.data.citations_added} 条引用`);
                if (r.data.quota_warning) message.warning(r.data.quota_warning);
                if (r.data.core_coverage) setCoverage(r.data.core_coverage);
                if (r.data.honesty_notes?.length) setHonestyNotes(r.data.honesty_notes);
                if (r.data.unused_research) {
                  setCoverage((cur) => ({ ...(cur || {}), unused_research: r.data.unused_research }));
                }
                setDraftNext(r.data.next_step || null);
                lastWrite.current = "sec";
                applyWritePack(r.data);
              } catch (e) {
                if (e?.name === "AbortError" || e?.code === "ERR_CANCELED") return;
                message.error(formatError(e, "撰写失败"));
              } finally {
                setRunning("");
                writeAbort.current = null;
              }
            }}
          >
            撰写本章
          </Button>
          )}
          <span className="fs-press-wrap" onClick={whenBlocked(!draft || running === "sec", !draft ? "先写这一节" : "正在写，请稍等")}>
          <Button
            id="fs-write-rewrite"
            style={{ margin: "8px 0" }}
            disabled={!draft || running === "sec"}
            onClick={async () => {
              setRunning("sec");
              setSectionNote("");
              const ac = new AbortController();
              writeAbort.current = ac;
              try {
                const r = await api.writeSection(
                  id,
                  {
                    heading: heading || "相关工作",
                    notes,
                    literature_item_ids: litIds,
                    manuscript_id: manuscriptId || undefined,
                    rewrite: true,
                    prior_draft: draft,
                    direction_id: directionId || undefined,
                  },
                  { signal: ac.signal }
                );
                setDraft(r.data.text || "");
                setCites(r.data.citations || []);
                setSectionUngrounded(Boolean(r.data.ungrounded));
                setSectionSpans(r.data.ungrounded_spans || []);
                if (r.data.ungrounded_reproduce) setReproWarn(r.data.ungrounded_reproduce);
                if (r.data.quota_warning) message.warning(r.data.quota_warning);
                if (r.data.core_coverage) setCoverage(r.data.core_coverage);
                setDraftNext(r.data.next_step || null);
                lastWrite.current = "rewrite";
                applyWritePack(r.data);
              } catch (e) {
                if (e?.name === "AbortError" || e?.code === "ERR_CANCELED") return;
                message.error(formatError(e, "改写失败"));
              } finally {
                setRunning("");
                writeAbort.current = null;
              }
            }}
          >
            改写本章
          </Button>
          </span>
          {sectionUngrounded ? (
            <Alert
              type="warning"
              showIcon
              style={{ margin: "8px 0" }}
              message="部分主张缺少知识库或已选文献依据，未当作已核验综述。"
              description={sectionSpans.length ? `对不上原文：${sectionSpans.join("、")}` : null}
            />
          ) : null}
          {reproWarn ? <Alert type="warning" showIcon style={{ margin: "8px 0" }} message={reproWarn} /> : null}
          {sectionNote ? <p className="fs-muted">{sectionNote}</p> : null}
          {draft ? (
            <MdPreview text={draft} literatureId={litIds[0]} extra={manuscriptId ? { ms: manuscriptId } : undefined} />
          ) : (
            <EmptyHint>点大纲节点填入标题，或留空则写「相关工作」。未选论文时只出草稿。</EmptyHint>
          )}
          {draft && draftNext?.href ? (
            <p className="fs-writeback-next">
              下一步：<ActionChip to={draftNext.href}>{draftNext.label || "写回论文"}</ActionChip>
              {draftNext.text ? <span className="fs-muted"> {draftNext.text}</span> : null}
            </p>
          ) : null}
          <AiResultBar
            text={draft || outlineMd}
            copyLabel={draft ? "复制本章" : "复制大纲"}
            busy={!!running}
            traceId={writeTrace}
            steps={writeSteps}
            degraded={writeDegraded}
            availableTools={writeTools}
            followups={writeFollowups}
            retryLabel={
              lastWrite.current === "ol"
                ? "再跑大纲"
                : lastWrite.current === "rewrite"
                  ? "再改写本章"
                  : lastWrite.current === "review"
                    ? "再跑评审"
                    : "再写本章"
            }
            onRetry={
              lastWrite.current
                ? () => {
                    const kind = lastWrite.current;
                    const btn = document.getElementById(
                      kind === "ol" ? "fs-write-ol" : kind === "rewrite" ? "fs-write-rewrite" : kind === "review" ? "fs-write-review" : "fs-write-sec"
                    );
                    btn?.click();
                  }
                : undefined
            }
          />
        </Card>
        <Card
          className={stepShown("wb") ? "fs-step-live" : "fs-step-done"}
          title={<button type="button" className="fs-step-toggle" onClick={() => toggleStep("wb")}>写回论文</button>}
        >
          <p className="fs-muted">
            {manuscriptId
              ? "写进这一节会加在这个标题下面。"
              : "还没选论文。先建论文，再写进去。"}
          </p>
          {wbConflict ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 8 }}
              message={
                <span>
                  {wbConflict}{" "}
                  {wbConflictHref ? <ActionChip to={wbConflictHref}>打开当前稿</ActionChip> : null}
                </span>
              }
            />
          ) : null}
          {wbHint ? (
            <WritebackSuccess summary={wbHint} href={wbHref || (manuscriptId ? `/manuscripts/${manuscriptId}` : "")} next={wbNext} />
          ) : null}
          <Space wrap>
            <Select
              allowClear
              placeholder="选择论文（可选）"
              style={{ width: 220 }}
              value={manuscriptId}
              onChange={setManuscriptId}
              options={manuscripts.map((m) => ({ value: m.id, label: m.title }))}
            />
            {manuscriptId ? <Link to={`/manuscripts/${manuscriptId}`}>打开论文</Link> : null}
            <details>
              <summary>换一种写回方式</summary>
              <Space wrap style={{ marginTop: 8 }}>
                <Select
                  value={writeMode}
                  style={{ width: 180 }}
                  onChange={setWriteMode}
                  options={[
                    { value: "append_heading", label: "追加到标题下" },
                    { value: "replace_selection", label: "替换选区" },
                    { value: "append", label: "文末追加" },
                  ]}
                />
                {writeMode === "replace_selection" ? (
                  <Input placeholder="论文中的选区原文" style={{ width: 240 }} value={selection} onChange={(e) => setSelection(e.target.value)} />
                ) : null}
              </Space>
            </details>
            <Button
              loading={running === "wb"}
              onClick={async () => {
                if (!manuscriptId) {
                  message.info("先选论文");
                  return;
                }
                if (!outlineMd) {
                  message.info("先生成大纲");
                  return;
                }
                try {
                  const data = await doWriteback({
                    text: outlineMd,
                    write_mode: "append",
                    heading: title || outline?.title || "大纲",
                  });
                  if (!data) return;
                  message.success(data.writeback?.summary || "大纲已写入论文");
                } catch (e) {
                  message.error(writebackError(e));
                }
              }}
            >
              写回大纲
            </Button>
            <Button
              loading={running === "wb"}
              onClick={async () => {
                if (!manuscriptId) {
                  message.info("先选论文");
                  return;
                }
                if (!draft) {
                  message.info("先写这一节");
                  return;
                }
                try {
                  const data = await doWriteback({
                    text: draft,
                    write_mode: writeMode,
                    heading: heading || "相关工作",
                    selection,
                  });
                  if (!data) return;
                  message.success(data.writeback?.summary || "已写入论文");
                } catch (e) {
                  message.error(writebackError(e));
                }
              }}
            >
              写入章节
            </Button>
            <Button
              type="default"
              loading={running === "wb"}
              onClick={async () => {
                if (!manuscriptId) {
                  message.info("先选论文");
                  return;
                }
                if (!draft) {
                  message.info("先写这一节");
                  return;
                }
                try {
                  const data = await doWriteback({
                    text: draft,
                    write_mode: "append_heading",
                    heading: heading || "相关工作",
                    selection,
                  });
                  if (!data) return;
                  message.success(data.writeback?.summary || "相关工作已写入论文");
                } catch (e) {
                  message.error(writebackError(e));
                }
              }}
            >
              写回相关工作
            </Button>
            <span className="fs-press-wrap" onClick={whenBlocked(!manuscriptId, "先选论文")}>
            <Button
              id="fs-write-review"
              disabled={!manuscriptId}
              onClick={async () => {
                try {
                  const r = await api.reviewSimulate(manuscriptId);
                  lastWrite.current = "review";
                  applyWritePack(r.data);
                  if (r.data.ungrounded_reproduce) setReproWarn(r.data.ungrounded_reproduce);
                  if (r.data.unused_research) {
                    setCoverage((cur) => ({ ...(cur || {}), unused_research: r.data.unused_research }));
                  }
                  message.success(r.data.disclaimer || `已写入 ${r.data.findings?.length || 0} 条批注（非官方查重）`);
                  const c = await api.comments(manuscriptId);
                  setComments(asItems(c.data));
                  setCommentErr("");
                } catch (e) {
                  message.error(formatError(e, "模拟评审失败"));
                }
              }}
            >
              导师式批注
            </Button>
            </span>
          </Space>
        </Card>
      </div>
      <Card
        className={stepShown("cites") ? "fs-step-live" : "fs-step-done"}
        title={<button type="button" className="fs-step-toggle" onClick={() => toggleStep("cites")}>引用片段</button>}
      >
        <AskSourceChips
          citations={(cites || []).map((h, i) => ({
            label: `S${i + 1}`,
            title: h.filename || h.heading_path || (h.citation || "").slice(0, 40),
            page: h.page_no || h.last_page_no,
            page_no: h.page_no || h.last_page_no,
            href: h.literature_item_id
              ? literatureHref(h.literature_item_id, h.page_no || h.last_page_no || 1, manuscriptId ? { ms: manuscriptId, section: heading || "相关工作" } : undefined)
              : h.href || "",
            item_id: h.literature_item_id,
            literature_item_id: h.literature_item_id,
            kind: "page",
            kind_label: "正文",
            excerpt: h.citation || h.content || "",
          }))}
        />
        {(cites || []).length ? (
          cites.map((h) => (
            <div key={h.chunk_id} className="fs-cite">
              <div className="fs-cite-row">
                {h.literature_item_id ? (
                  <Link
                    to={
                      manuscriptId
                        ? literatureHref(h.literature_item_id, h.page_no || h.last_page_no || 1, { ms: manuscriptId, section: heading || "相关工作" })
                        : h.href ||
                          literatureHref(h.literature_item_id, h.page_no || h.last_page_no || 1)
                    }
                  >
                    [p.{h.page_no}] {h.citation}
                    {readingPctLabel(h) ? ` · ${readingPctLabel(h)}` : ""}
                  </Link>
                ) : (
                  <span>{h.citation || h.content}（无 PDF）</span>
                )}
                {h.literature_item_id ? (
                  <details className="fs-inline-more">
                    <summary>还有</summary>
                    <ActionChip to={h.reproduce_href || `/labs/${id}/reproduces?literature_item_id=${encodeURIComponent(h.literature_item_id)}`}>
                      去复现
                    </ActionChip>
                  </details>
                ) : null}
              </div>
            </div>
          ))
        ) : (
          <p className="fs-muted">撰写章节后显示可点引用。引用必须来自原文片段。</p>
        )}
      </Card>
      {manuscriptId ? (
        <Card
          className={stepShown("review") ? "fs-step-live" : "fs-step-done"}
          title={<button type="button" className="fs-step-toggle" onClick={() => toggleStep("review")}>导师式批注</button>}
        >
          {commentErr ? (
            <LoadFailed
              message={commentErr}
              onRetry={() => {
                setCommentErr("");
                api
                  .comments(manuscriptId)
                  .then((r) => {
                    setComments(asItems(r.data));
                    setCommentErr("");
                  })
                  .catch((e) => {
                    setComments([]);
                    setCommentErr(formatError(e, "批注加载失败"));
                  });
              }}
            />
          ) : (comments || []).length ? (
            comments.map((c) => (
              <p key={c.id}>
                <Link to={c.href || manuscriptHref(manuscriptId, { comment: c.id })}>{c.body}</Link>
              </p>
            ))
          ) : (
            <p className="fs-muted">点「导师式批注」后，意见会出现在这里，并写入论文评论。</p>
          )}
        </Card>
      ) : null}
      </div>
    </Space>
  );
}
