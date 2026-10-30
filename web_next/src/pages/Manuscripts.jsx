import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Dropdown, Form, Input, Select, Space, Table, Tag, message, Popconfirm } from "antd";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, saveBlob, asItems } from "../api.js";
import { formatError, humanize, DEGREE_KIND, DEGREE_STATUS, MS_KIND, MS_STATUS, RUN_STATUS, languageHref, literatureHref, readingPctLabel } from "../err.js";
import { ActionChip, AgentTraceBox, AiResultBar, AnalyzeChip, CoreMissingTitleChips, CreateDialog, EmptyHint, LoadFailed, MdPreview, PageHeader, PageLoading, PdfGapLink, RelatedWorkTitleChips, StatusTag, UnusedResearchAlert, dueLabel, fmtWhen, useComposeOpen, whenBlocked } from "../ui.jsx";
import { ensureHeading, joinManuscript, manuscriptSectionHref, nextEmptySection, parseManuscript, suggestCiteKey, writingDeskHref } from "../manuscriptSections.js";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";

export function Manuscripts() {
  const { id } = useParams();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const directionId = params.get("direction_id") || undefined;
  const status = params.get("status") || undefined;
  const ungrounded = params.get("ungrounded") === "1";
  const relatedWork = params.get("related_work");
  const coreMissing = params.get("core_missing") === "1";
  const ownerId = params.get("owner_id") || undefined;
  const [compose, setCompose] = useComposeOpen(params, setParams);
  const [msForm] = Form.useForm();
  const [rows, setRows] = useState([]);
  const [owners, setOwners] = useState([]);
  const [ownerErr, setOwnerErr] = useState("");
  const [emptyNext, setEmptyNext] = useState(null);
  const [includesChildren, setIncludesChildren] = useState(false);
  const [dirs, setDirs] = useState([]);
  const [err, setErr] = useState("");
  const reload = () =>
    api
      .manuscripts(id, {
        direction_id: directionId,
        status,
        owner_id: ownerId,
        ungrounded: ungrounded || undefined,
        related_work: relatedWork === "0" ? false : relatedWork === "1" ? true : undefined,
        core_missing: coreMissing || undefined,
      })
      .then((r) => {
        setRows(asItems(r.data));
        setEmptyNext(r.data?.empty_next || null);
        setIncludesChildren(!!r.data?.includes_children);
        setErr("");
      })
      .catch((e) => setErr(formatError(e, "论文列表加载失败")));
  useEffect(() => {
    reload();
  }, [id, directionId, status, ownerId, ungrounded, relatedWork, coreMissing]);
  useEffect(() => {
    api
      .members(id)
      .then((r) => {
        setOwners(r.data || []);
        setOwnerErr("");
      })
      .catch((e) => {
        setOwners([]);
        setOwnerErr(formatError(e, "成员列表加载失败"));
      });
    api
      .directions(id)
      .then((r) => setDirs(asItems(r.data)))
      .catch((e) => {
        setDirs([]);
        message.error(formatError(e, "方向列表没打开"));
      });
  }, [id]);
  const ownerName = (owners.find((m) => m.user_id === ownerId) || {}).display_name || "";
  const stuckOn = coreMissing || relatedWork === "0" || ungrounded;
  const heroTitle = coreMissing
    ? emptyNext?.title || "正在看：还没写进正文的论文"
    : relatedWork === "0"
      ? emptyNext?.title || "正在看：还没写相关工作"
      : ungrounded
        ? emptyNext?.title || "正在看：写了结果、还没跑过的论文"
        : ownerId
          ? emptyNext?.title || "正在看：这人的论文"
          : "我的论文";
  const heroLead = coreMissing
    ? ownerName
      ? `${ownerName} 还没写到核心文献。`
      : "还没写到核心文献。"
    : relatedWork === "0"
      ? ownerName
        ? `${ownerName} 还没写相关工作。`
        : "还没写相关工作。"
      : ungrounded
        ? ownerName
          ? `${ownerName} 把没做的复现写成成功。`
          : "把没做的复现写成成功。"
        : ownerId
          ? ownerName
            ? `${ownerName} 的论文。`
            : "这人的论文。"
          : "组里互看，作者才能改。";
  const heroHint = "只有作者能改。点标题打开这一篇。";
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        tone={stuckOn || ownerId ? "unused" : undefined}
        title={heroTitle}
        hint={
          <>
            {heroHint}
            {directionId && !stuckOn
              ? includesChildren || dirs.find((d) => d.id === directionId)?.includes_children || dirs.find((d) => d.id === directionId)?.child_count
                ? " 大方向含下面的小课题。"
                : " 只看这个方向。"
              : ""}
            {status && !stuckOn ? " 按状态筛选。" : ""}
          </>
        }
        extra={
          <Button type="primary" onClick={() => setCompose(true)}>
            新建我的论文
          </Button>
        }
      >
        {heroLead}
        {stuckOn || ownerId ? (
          <>
            {" "}
            <ActionChip to={`/labs/${id}/manuscripts`}>全部论文</ActionChip>
          </>
        ) : null}
      </PageHeader>
      {relatedWork === "0" ? (
        <RelatedWorkTitleChips
          titles={rows.slice(0, 8).map((r) => ({
            title: r.title,
            href: writingDeskHref(r.writing_href, id, r.id, "相关工作"),
          }))}
          more={Math.max(0, rows.length - 8)}
        />
      ) : null}
      {coreMissing ? (
        <CoreMissingTitleChips
          titles={rows.slice(0, 8).map((r) => ({
            title: r.title,
            href: `/manuscripts/${r.id}?tab=claims`,
          }))}
          more={Math.max(0, rows.length - 8)}
        />
      ) : null}
      {rows.length || status || ungrounded || relatedWork || coreMissing || ownerId || directionId ? (
      <div className="fs-filterbar">
      <Select
        allowClear
        placeholder="状态"
        style={{ width: 160 }}
        value={status}
        onChange={(v) => {
          const next = new URLSearchParams(params);
          if (v) next.set("status", v);
          else next.delete("status");
          setParams(next, { replace: true });
        }}
        options={Object.entries(MS_STATUS).map(([value, label]) => ({ value, label }))}
      />
      <Select
        allowClear
        placeholder="正文和结果"
        style={{ width: 180 }}
        value={ungrounded ? "1" : undefined}
        onChange={(v) => {
          const next = new URLSearchParams(params);
          if (v) next.set("ungrounded", v);
          else next.delete("ungrounded");
          setParams(next, { replace: true });
        }}
        options={[{ value: "1", label: "写了结果，组里还没跑过" }]}
      />
      <Select
        allowClear
        placeholder="相关工作"
        style={{ width: 180 }}
        value={relatedWork || undefined}
        onChange={(v) => {
          const next = new URLSearchParams(params);
          if (v) next.set("related_work", v);
          else next.delete("related_work");
          setParams(next, { replace: true });
        }}
        options={[
          { value: "0", label: "未写相关工作" },
          { value: "1", label: "已写相关工作" },
        ]}
      />
      <Select
        allowClear
        placeholder="核心文献"
        style={{ width: 180 }}
        value={coreMissing ? "1" : undefined}
        onChange={(v) => {
          const next = new URLSearchParams(params);
          if (v) next.set("core_missing", v);
          else next.delete("core_missing");
          setParams(next, { replace: true });
        }}
        options={[{ value: "1", label: "这几篇还没写进正文" }]}
      />
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="作者"
        style={{ width: 180 }}
        value={ownerId}
        onChange={(v) => {
          const next = new URLSearchParams(params);
          if (v) next.set("owner_id", v);
          else next.delete("owner_id");
          setParams(next, { replace: true });
        }}
        options={owners.map((m) => ({ value: m.user_id, label: m.display_name || m.email || m.user_id }))}
      />
      </div>
      ) : null}
      {ownerErr ? (
        <LoadFailed
          message={ownerErr}
          onRetry={() =>
            api
              .members(id)
              .then((r) => {
                setOwners(r.data || []);
                setOwnerErr("");
              })
              .catch((e) => setOwnerErr(formatError(e, "成员列表加载失败")))
          }
        />
      ) : null}
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}
      {emptyNext && !rows.length ? (
        <Alert
          className="fs-invite"
          type="info"
          showIcon
          message={emptyNext.title || "本筛选下没有论文"}
          description={
            <span>
              {emptyNext.text}{" "}
              {(emptyNext.steps || []).map((s) => (
                <ActionChip key={s.key || s.href} to={s.href}>
                  {s.label}
                </ActionChip>
              ))}
            </span>
          }
        />
      ) : null}
      <CreateDialog open={compose} onClose={() => setCompose(false)} title="新建我的论文" okText="创建" form={msForm}>
        <Form
          form={msForm}
          layout="vertical"
          initialValues={{ kind: "conference" }}
          onFinish={async (v) => {
            try {
              const r = await api.addManuscript(id, {
                title: v.title,
                kind: v.kind || "conference",
                content_md: "## 相关工作\n\n",
              });
              msForm.resetFields();
              setCompose(false);
              message.success("论文已建。先写相关工作这一节。");
              nav(manuscriptSectionHref(r.data.id, "相关工作"));
            } catch (e) {
              message.error(formatError(e, "无法创建论文"));
            }
          }}
        >
          <Form.Item name="title" label="标题" rules={[{ required: true, message: "请填写标题" }]}>
            <Input placeholder="论文题目" />
          </Form.Item>
          <Form.Item name="kind" label="类型" initialValue="conference">
            <Select options={Object.entries(MS_KIND).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
        </Form>
      </CreateDialog>
      {emptyNext && !rows.length ? null : (
      <Table
        className="fs-click-row fs-read-table"
        showHeader={false}
        rowKey="id"
        dataSource={rows}
        locale={{ emptyText: <EmptyHint>{directionId ? "该方向还没有论文。" : "还没有论文。创建一篇后组内可见正文。"}</EmptyHint> }}
        onRow={(row) => ({ onClick: () => nav(row.href || `/manuscripts/${row.id}`) })}
        columns={[
          {
            title: "标题",
            dataIndex: "title",
            render: (t, row) => {
              const why = [
                MS_STATUS[row.status] || "",
                row.owner_name || "",
                row.related_work_written === false ? "相关工作还没写" : "",
                row.core_missing_count > 0 ? `还有 ${row.core_missing_count} 篇没写进正文` : "",
              ].filter(Boolean).join(" · ");
              return (
                <span className="fs-paper-main">
                  <Link className="fs-lit-title" to={row.href || `/manuscripts/${row.id}`}>{t}</Link>
                  <span className="fs-muted fs-lit-why">{why || "接着写这一节"}</span>
                </span>
              );
            },
          },
          { title: "类型", dataIndex: "kind", className: "fs-col-fold", render: (k) => MS_KIND[k] || k },
          { title: "状态", dataIndex: "status", className: "fs-col-fold", render: (s) => <StatusTag status={s} map={MS_STATUS} /> },
          { title: "作者", className: "fs-col-fold", render: (_, r) => r.owner_name || r.owner_id },
          {
            title: "相关工作",
            className: "fs-col-fold",
            render: (_, row) =>
              row.related_work_written === false ? <span className="fs-muted">未写相关工作</span> : <span className="fs-muted">—</span>,
          },
          {
            title: "核心文献",
            className: "fs-col-fold",
            render: (_, row) =>
              row.core_missing_count > 0 ? (
                <span className="fs-muted">还没写到 {row.core_missing_count}</span>
              ) : (
                <span className="fs-muted">—</span>
              ),
          },
          {
            title: "正文",
            className: "fs-col-fold",
            render: (_, row) => {
              const bits = [
                row.ungrounded_reproduce ? "写了结果，组里还没跑过" : "",
                row.unused_research?.text ? "综述没提到" : "",
              ].filter(Boolean);
              return <span className="fs-muted">{bits.join(" · ") || "—"}</span>;
            },
          },
          {
            title: "",
            width: 120,
            className: "fs-row-actions",
            render: (_, row) => (
              <span onClick={(e) => e.stopPropagation()}>
                <ActionChip to={row.related_work_written === false ? writingDeskHref(row.writing_href, id, row.id, "相关工作") : (row.href || `/manuscripts/${row.id}`)}>
                  {row.related_work_written === false ? "写相关工作" : "接着写"}
                </ActionChip>
                <details className="fs-inline-more">
                  <summary>还有</summary>
                  <Popconfirm
                    title="删除该论文？"
                    onConfirm={async (e) => {
                      e?.stopPropagation?.();
                      try {
                        await api.deleteManuscript(row.id);
                        message.success("已删除");
                        reload();
                      } catch (err) {
                        message.error(formatError(err, "无法删除"));
                      }
                    }}
                  >
                    <Button size="small" danger onClick={(e) => e.stopPropagation()}>
                      删
                    </Button>
                  </Popconfirm>
                </details>
              </span>
            ),
          },
        ]}
      />
      )}
    </Space>
  );
}

export function ManuscriptEditor() {
  const { id } = useParams();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const commentId = params.get("comment");
  const me = useSession((s) => s.me);
  const bodyRef = useRef(null);
  const [doc, setDoc] = useState(null);
  const [saved, setSaved] = useState("");
  const [comments, setComments] = useState([]);
  const [versions, setVersions] = useState([]);
  const [citations, setCitations] = useState([]);
  const [claims, setClaims] = useState(null);
  const [reviewTrace, setReviewTrace] = useState("");
  const [reviewSteps, setReviewSteps] = useState([]);
  const [reviewDegraded, setReviewDegraded] = useState("");
  const [reviewTools, setReviewTools] = useState([]);
  const [reviewFollowups, setReviewFollowups] = useState([]);
  const [reviewText, setReviewText] = useState("");
  const [reviewFindings, setReviewFindings] = useState([]);
  const [reviewNote, setReviewNote] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const [activeId, setActiveId] = useState("");
  const [picked, setPicked] = useState(null);
  const [altText, setAltText] = useState("");
  const [altBusy, setAltBusy] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteDraft, setNoteDraft] = useState("");
  const [noteSaving, setNoteSaving] = useState(false);
  const altAbort = useRef(null);
  const latestMd = useRef("");
  const savedRef = useRef("");
  const updatedAtRef = useRef("");
  const saveTail = useRef(Promise.resolve());
  const conflictRef = useRef(false);
  const papersLoaded = useRef(false);
  const wroteOnce = useRef("");
  const jumpedComment = useRef("");
  latestMd.current = doc?.content_md || "";
  savedRef.current = saved;
  if (doc?.updated_at) updatedAtRef.current = doc.updated_at;
  const [papers, setPapers] = useState([]);
  const [gitUrl, setGitUrl] = useState("");
  const [gitToken, setGitToken] = useState("");
  const [loadError, setLoadError] = useState("");
  const [conflict, setConflict] = useState("");
  const [saving, setSaving] = useState(false);
  const [sideErr, setSideErr] = useState("");
  const failSide = (fallback) => (e) => setSideErr((prev) => prev || formatError(e, fallback));
  const load = (opts) => {
    const force = !!(opts && opts.force);
    api
      .getManuscript(id)
      .then((r) => {
        const server = r.data.content_md || "";
        const local = latestMd.current || "";
        const keepLocal = !force && local !== (savedRef.current || "") && local !== server;
        setDoc((cur) => {
          if (!cur || !keepLocal) return r.data;
          return { ...r.data, content_md: cur.content_md, updated_at: cur.updated_at };
        });
        if (!keepLocal) {
          savedRef.current = server;
          if (r.data.updated_at) updatedAtRef.current = r.data.updated_at;
          if (force) conflictRef.current = false;
          setSaved(server);
        }
        setGitUrl(r.data.overleaf_git_url || "");
        setLoadError("");
        if (r.data.lab_id && !papersLoaded.current) {
          papersLoaded.current = true;
          api
            .literature(r.data.lab_id)
            .then((x) => setPapers(asItems(x.data)))
            .catch((e) => {
              papersLoaded.current = false;
              failSide("文献列表加载失败")(e);
            });
        }
      })
      .catch((e) => setLoadError(formatError(e, "论文加载失败")));
    setSideErr("");
    api.comments(id).then((r) => setComments(r.data || [])).catch(failSide("评论加载失败"));
    api.manuscriptVersions(id).then((r) => setVersions(r.data || [])).catch(failSide("版本加载失败"));
    api.citations(id).then((r) => setCitations(r.data || [])).catch(failSide("引用加载失败"));
    api.manuscriptClaims(id).then((r) => setClaims(r.data)).catch(failSide("主张追踪加载失败"));
  };
  useEffect(() => {
    load();
  }, [id]);
  useEffect(() => {
    if (!id) return undefined;
    let alive = true;
    api
      .reviewLatest(id)
      .then((r) => {
        if (!alive) return;
        const md = r.data?.markdown || r.data?.text || "";
        if (!md) return;
        let applied = false;
        setReviewText((cur) => {
          if (cur) return cur;
          applied = true;
          return md;
        });
        if (!applied) return;
        if (r.data?.session_id) setReviewTrace((cur) => cur || r.data.session_id);
        if (r.data?.truncated) setReviewNote((cur) => cur || "上次评审提示较长，这里只保留前一部分。");
      })
      .catch((e) => {
        if (alive) message.warning(formatError(e, "上次的评审没打开"));
      });
    return () => {
      alive = false;
    };
  }, [id]);
  useEffect(() => {
    if (!commentId || !comments.length || !doc) return undefined;
    const hit = comments.find((c) => c.id === commentId);
    if (!hit) {
      if (jumpedComment.current !== `missing:${commentId}`) {
        jumpedComment.current = `missing:${commentId}`;
        message.warning("找不到该评论");
      }
      return undefined;
    }
    if (jumpedComment.current === commentId) return undefined;
    document.getElementById(`ms-comment-${commentId}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    let anchor = {};
    try {
      anchor = typeof hit.anchor_json === "string" ? JSON.parse(hit.anchor_json || "{}") : hit.anchor_json || {};
    } catch {
      anchor = {};
    }
    const excerpt = anchor.excerpt || "";
    if (!excerpt) {
      jumpedComment.current = commentId;
      return undefined;
    }
    const tree = parseManuscript(doc.content_md || "");
    const sectionHit = tree.sections.find((s) => s.body.includes(excerpt) || s.headingLine.includes(excerpt));
    if (sectionHit) setActiveId(sectionHit.id);
    const timer = window.setTimeout(() => {
      const el = bodyRef.current?.resizableTextArea?.textArea || bodyRef.current;
      const idx = excerpt ? (el?.value || "").indexOf(excerpt) : -1;
      if (idx < 0 || !el) return;
      jumpedComment.current = commentId;
      el.focus();
      el.setSelectionRange(idx, idx + excerpt.length);
      const line = (el.value || "").slice(0, idx).split("\n").length;
      el.scrollTop = Math.max(0, (line - 3) * 22);
    }, 80);
    return () => window.clearTimeout(timer);
  }, [commentId, comments, doc]);
  const dirty = !!doc && (doc.content_md || "") !== saved;
  const rawTab = (params.get("tab") || "").trim();
  const sideTab =
    rawTab === "claims" || rawTab === "cites" || rawTab === "comments" || rawTab === "review" || rawTab === "versions"
      ? rawTab
      : commentId
        ? "comments"
        : "claims";
  const setSideTab = (t) => {
    const next = new URLSearchParams(params);
    next.set("tab", t);
    setParams(next, { replace: true });
  };
  useEffect(() => {
    if (params.get("wrote") !== "1" || !doc) return undefined;
    const mark = `${id}:${params.get("section") || ""}`;
    if (wroteOnce.current === mark) return undefined;
    wroteOnce.current = mark;
    message.success("已经写进这一节");
    const next = new URLSearchParams(params);
    next.delete("wrote");
    setParams(next, { replace: true });
    window.setTimeout(() => {
      const el = bodyRef.current?.resizableTextArea?.textArea || bodyRef.current;
      if (!el) return;
      el.classList.add("fs-settle");
      el.focus();
      const len = (el.value || "").length;
      if (el.setSelectionRange) el.setSelectionRange(len, len);
      el.scrollIntoView?.({ block: "center" });
      window.setTimeout(() => el.classList.remove("fs-settle"), 2000);
    }, 120);
    return undefined;
  }, [params, doc, id]);
  useEffect(() => {
    if (!doc) return;
    const tree = parseManuscript(doc.content_md || "");
    if (!tree.focused) return;
    const want = params.get("section") || "";
    setActiveId((cur) => {
      if (cur && tree.sections.some((s) => s.id === cur)) return cur;
      const hit =
        tree.sections.find((s) => want && (s.key === want || s.title === want)) ||
        tree.sections.find((s) => s.focus);
      return hit ? hit.id : "";
    });
  }, [doc, params]);
  const saveBody = (content, okText, quiet) => {
    if (!doc || me?.id !== doc.owner_id) return Promise.resolve(false);
    const explicit = typeof content === "string" ? content : null;
    const job = saveTail.current.then(async () => {
      let quietMode = !!quiet;
      let note = okText;
      let pending = explicit;
      for (let guard = 0; guard < 3; guard += 1) {
        const text = pending != null ? pending : latestMd.current ?? "";
        pending = null;
        if (conflictRef.current && quietMode) return false;
        if (quietMode && text === (savedRef.current || "")) return true;
        setSaving(true);
        try {
          const payload = {
            content_md: text,
            expected_updated_at: updatedAtRef.current || doc.updated_at || undefined,
          };
          if (!quietMode && gitUrl) payload.overleaf_git_url = gitUrl;
          const r = await api.patchManuscript(id, payload);
          if (!quietMode) message.success(typeof note === "string" ? note : "已保存");
          const serverAt = r.data?.updated_at || updatedAtRef.current;
          if (serverAt) updatedAtRef.current = serverAt;
          const stillSame = latestMd.current === text;
          if (stillSame) savedRef.current = text;
          conflictRef.current = false;
          setDoc((cur) => {
            if (!cur) return cur;
            return { ...cur, content_md: stillSame ? text : cur.content_md, updated_at: serverAt || cur.updated_at };
          });
          if (stillSame) {
            setSaved(text);
            setConflict("");
            if (!quietMode) load();
            return true;
          }
          quietMode = true;
          note = "";
        } catch (e) {
          if (e?.response?.status === 409) {
            conflictRef.current = true;
            setConflict(formatError(e, "这篇刚被改过，请先刷新再保存"));
            return false;
          }
          message.error(formatError(e, "没保存上"));
          return false;
        } finally {
          setSaving(false);
        }
      }
      return true;
    });
    const settled = job.then(
      (ok) => ok,
      () => false
    );
    saveTail.current = settled.then(
      () => undefined,
      () => undefined
    );
    return settled;
  };
  useEffect(() => {
    if (!doc || me?.id !== doc.owner_id) return undefined;
    if ((doc.content_md || "") === saved || conflict || saving || altBusy) return undefined;
    const text = doc.content_md || "";
    const timer = window.setTimeout(() => {
      saveBody(text, "", true);
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [doc, saved, conflict, saving, altBusy, me?.id]);
  useEffect(() => {
    if (!dirty) return undefined;
    const onLeave = (e) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onLeave);
    return () => window.removeEventListener("beforeunload", onLeave);
  }, [dirty]);
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
        e.preventDefault();
        saveBody();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  if (loadError && !doc) return <LoadFailed message={loadError} onRetry={load} />;
  if (!doc) return <PageLoading label="正在打开论文…" />;
  const teacher = roleOf(me, doc.lab_id) === "teacher" || !!me?.is_admin;
  const owner = me?.id === doc.owner_id;
  const parsed = parseManuscript(doc.content_md || "");
  const active = parsed.focused ? parsed.sections.find((s) => s.id === activeId) || parsed.sections.find((s) => s.focus) || null : null;
  const editorValue = active ? active.body : doc.content_md || "";
  const citeIds = (citations || []).map((c) => c.literature_item_id).filter(Boolean);
  const sectionName = active?.key || params.get("section") || "";
  const readBack = { ms: id, section: sectionName || undefined };
  const writeSectionHref = `/labs/${doc.lab_id}/writing?ms=${id}${active ? `&heading=${encodeURIComponent(active.key)}` : ""}${citeIds.length ? `&lit=${citeIds.join(",")}` : ""}`;
  const gapHref = doc.next_step?.href || `/labs/${doc.lab_id}/reproduces`;
  const missingCount = claims?.core_missing_count || 0;
  const nextEmpty = nextEmptySection(parsed, active?.key || "");
  const focusExcerpt = (excerpt) => {
    if (!excerpt) {
      message.info("这一段在正文里没找到");
      return;
    }
    const tree = parseManuscript(doc.content_md || "");
    const sectionHit = tree.sections.find((s) => s.body.includes(excerpt) || s.headingLine.includes(excerpt));
    if (sectionHit) setActiveId(sectionHit.id);
    window.setTimeout(() => {
      const el = bodyRef.current?.resizableTextArea?.textArea || bodyRef.current;
      const idx = (el?.value || "").indexOf(excerpt);
      if (idx < 0 || !el) {
        message.info("这一段在正文里没找到");
        return;
      }
      el.focus();
      el.setSelectionRange(idx, idx + excerpt.length);
      const line = (el.value || "").slice(0, idx).split("\n").length;
      el.scrollTop = Math.max(0, (line - 3) * 22);
      setPicked({ text: excerpt, start: idx, end: idx + excerpt.length });
    }, 80);
  };
  const openFinding = (f) => {
    let anchor = {};
    try {
      anchor = typeof f.anchor_json === "string" ? JSON.parse(f.anchor_json || "{}") : f.anchor_json || {};
    } catch {
      anchor = {};
    }
    if (anchor.excerpt) {
      focusExcerpt(anchor.excerpt);
      return;
    }
    if (anchor.heading) {
      const tree = parseManuscript(doc.content_md || "");
      const sectionHit = tree.sections.find((s) => s.title.includes(anchor.heading) || s.key === anchor.heading);
      if (sectionHit) {
        setActiveId(sectionHit.id);
        const next = new URLSearchParams(params);
        next.set("section", sectionHit.key);
        setParams(next, { replace: true });
        message.success(`已打开${sectionHit.key}`);
        return;
      }
      message.info(`还没有「${anchor.heading}」这一节`);
      return;
    }
    if (anchor.citation_key) {
      setSideTab("cites");
      message.info("这条在参考文献里");
      return;
    }
    message.info("这条没有对应的段落");
  };
  const setEditorValue = (value) => {
    if (!active) {
      setDoc({ ...doc, content_md: value });
      return;
    }
    setDoc({ ...doc, content_md: joinManuscript(parsed, active.id, value) });
  };
  const addSection = (name) => {
    const nextMd = ensureHeading(doc.content_md || "", name);
    if (nextMd === (doc.content_md || "")) {
      message.info(`已经有${name}这一节`);
      return;
    }
    const tree = parseManuscript(nextMd);
    const sectionHit = tree.sections.find((s) => s.key === name || s.title === name);
    setDoc({ ...doc, content_md: nextMd });
    if (sectionHit) setActiveId(sectionHit.id);
    const next = new URLSearchParams(params);
    next.set("section", name);
    setParams(next, { replace: true });
    message.success(`已加上${name}这一节`);
  };
  const goNextSection = () => {
    if (!nextEmpty) return;
    if (nextEmpty.exists && nextEmpty.id) {
      setActiveId(nextEmpty.id);
      setPicked(null);
      setAltText("");
      const next = new URLSearchParams(params);
      next.set("section", nextEmpty.name);
      setParams(next, { replace: true });
      message.success(`已打开${nextEmpty.name}`);
      return;
    }
    addSection(nextEmpty.name);
  };
  const runAlt = async (kind) => {
    if (!picked?.text) {
      message.info("先选中一段");
      return;
    }
    altAbort.current?.abort();
    const ac = new AbortController();
    altAbort.current = ac;
    setAltBusy(kind);
    setAltText("");
    try {
      const r =
        kind === "zh"
          ? await api.translate(doc.lab_id, picked.text, "zh", {}, { signal: ac.signal })
          : await api.polish(doc.lab_id, picked.text, {}, { signal: ac.signal });
      const text = (r.data?.text || "").trim();
      if (!text) {
        message.warning(kind === "zh" ? "没有译出新的一段" : "没有改出新的一段");
        return;
      }
      setAltText(text);
    } catch (e) {
      if (e?.name === "AbortError" || e?.code === "ERR_CANCELED") return;
      message.error(formatError(e, kind === "zh" ? "没译成" : "没改成"));
    } finally {
      setAltBusy("");
      altAbort.current = null;
    }
  };
  const applyAlt = async () => {
    if (!picked || !altText) return;
    if (picked.start < 0 || picked.end > editorValue.length || editorValue.slice(picked.start, picked.end) !== picked.text) {
      message.warning("这段已经变了，请重新选中");
      return;
    }
    const nextBody = editorValue.slice(0, picked.start) + altText + editorValue.slice(picked.end);
    const nextMd = active ? joinManuscript(parsed, active.id, nextBody) : nextBody;
    latestMd.current = nextMd;
    setEditorValue(nextBody);
    setPicked(null);
    setAltText("");
    await saveBody(nextMd, "已换成这版");
  };
  const sectionHint = {
    相关工作: "写别人做了什么，并点到文献题名。",
    方法: "写你准备怎么做。",
    实验: "写你跑了什么。没跑过的结果不要写成已经成功。",
  };
  const goWrite = async () => {
    if (dirty || latestMd.current !== (savedRef.current || "")) {
      const ok = await saveBody(latestMd.current, "", true);
      if (!ok) return;
    }
    nav(writeSectionHref);
  };
  const insertCite = (c) => {
    const token = c.citation_key ? `[@${c.citation_key}]` : c.title || "";
    if (!token) return;
    const el = bodyRef.current?.resizableTextArea?.textArea;
    const start = Number.isInteger(el?.selectionStart) ? el.selectionStart : editorValue.length;
    const end = Number.isInteger(el?.selectionEnd) ? el.selectionEnd : editorValue.length;
    const next = `${editorValue.slice(0, start)}${token}${editorValue.slice(end)}`;
    setEditorValue(next);
    message.success("已放入这一节");
  };
  const pinNote = async () => {
    const text = noteDraft.trim();
    if (!picked?.text) {
      message.info("先选中一段");
      return;
    }
    if (!text) {
      message.info("先写一条意见");
      return;
    }
    if (noteSaving) return;
    setNoteSaving(true);
    try {
      await api.addComment(id, text, { excerpt: picked.text });
      message.success("已钉在这一段");
      setNoteDraft("");
      setNoteOpen(false);
      setSideTab("comments");
      load();
    } catch (e) {
      message.error(formatError(e, "没钉上"));
    } finally {
      setNoteSaving(false);
    }
  };
  const findingLabel = {
    missing_section: "缺这一节",
    duplicate: "这段重复",
    missing_citation: "这条没引用",
    claim_no_page: "这条没标页码",
    unused_citation: "这条引用还没写进正文",
    ungrounded_reproduce: "写成了还没跑过的结果",
    unused_research: "还有没用上的结果",
    ok: "结构看过了",
  };
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        title={doc.title}
        hint="只有作者能保存正文（Ctrl / ⌘ + S）。导师可改状态和批注。"
        extra={
          <>
            <StatusTag status={doc.status} map={MS_STATUS} />
            {dirty ? <span className="fs-dirty">未保存</span> : null}
          </>
        }
      >
        {active ? `正在写${active.key}` : "改这一篇"}
      </PageHeader>
      {conflict ? null : nextEmpty ? (
        <Alert
          showIcon
          message={`${nextEmpty.name}这一节还没写。`}
          action={
            <Button size="small" type="primary" onClick={goNextSection}>
              接着写{nextEmpty.name}
            </Button>
          }
        />
      ) : missingCount ? (
        <Alert
          showIcon
          message={`还有 ${missingCount} 篇没写进正文。`}
          action={
            <Button size="small" type="primary" onClick={() => setSideTab("claims")}>
              去看还没写到
            </Button>
          }
        />
      ) : doc.next_step?.label ? (
        <Alert
          showIcon
          message={humanize(doc.next_step.text || "") || doc.next_step.label}
          action={
            doc.next_step.href ? (
              <Button
                size="small"
                type="primary"
                onClick={() =>
                  nav(
                    String(doc.next_step.href).includes("/writing")
                      ? writingDeskHref(doc.next_step.href, doc.lab_id, id, active?.key || "相关工作")
                      : doc.next_step.href
                  )
                }
              >
                {doc.next_step.key === "core" || String(doc.next_step.label || "").includes("相关工作")
                  ? "去写这一节"
                  : doc.next_step.label}
              </Button>
            ) : null
          }
        />
      ) : null}
      {doc.ungrounded_reproduce ? (
        <aside className="fs-margin-note">
          <p>{humanize(doc.ungrounded_reproduce) || "这一句写成了还没跑过的结果。"}</p>
          <Button
            size="small"
            type={nextEmpty || missingCount || doc.next_step?.label ? "default" : "primary"}
            onClick={() => {
              const el = bodyRef.current?.resizableTextArea?.textArea || bodyRef.current;
              el?.focus();
              const blob = el?.value || "";
              const hit = blob.search(/结果|实验|准确率|提升/);
              if (hit >= 0 && el?.setSelectionRange) el.setSelectionRange(hit, Math.min(blob.length, hit + 24));
            }}
          >
            改这句
          </Button>
          <details>
            <summary>还有</summary>
            <Button size="small" onClick={() => nav(gapHref)}>
              去补实验
            </Button>
          </details>
        </aside>
      ) : null}
      {conflict ? (
        <Alert
          type="warning"
          showIcon
          message={conflict}
          action={
            <Button
              size="small"
              onClick={() => {
                setConflict("");
                load({ force: true });
              }}
            >
              刷新当前稿
            </Button>
          }
        />
      ) : null}
      <div className="fs-toolbar">
        {doc.overleaf_last_sync_at ? <Tag>Overleaf {fmtWhen(doc.overleaf_last_sync_at)}</Tag> : null}
        {teacher ? (
          <Select
            value={doc.status}
            style={{ width: 160 }}
            onChange={async (status) => {
              try {
                await api.patchManuscript(id, { status });
                message.success("已更新状态");
                load();
              } catch (e) {
                message.error(formatError(e, "无权改状态"));
              }
            }}
            options={Object.entries(MS_STATUS).map(([value, label]) => ({ value, label }))}
          />
        ) : null}
        {owner && (dirty || saving) ? (
          <Button
            type="primary"
            loading={saving}
            onClick={saveBody}
          >
            {saving ? "正在保存" : "保存正文"}
          </Button>
        ) : owner ? null : (
          <span className="fs-muted">只有作者能改</span>
        )}
        <Dropdown
          menu={{
            items: [
              { key: "write", label: "去写作", onClick: () => goWrite() },
              {
                key: "lang",
                label: "去改文字",
                onClick: () => nav(languageHref(doc.lab_id, { ms: id, heading: active?.key || undefined })),
              },
              doc.status === "published"
                ? {
                    key: "lib",
                    label: "加入文献库",
                    onClick: async () => {
                      try {
                        const r = await api.addManuscriptToLibrary(id, true);
                        message.success("已加入文献库");
                        nav(literatureHref(r.data.literature_item_id));
                      } catch (e) {
                        message.error(formatError(e, "没加入文献库"));
                      }
                    },
                  }
                : null,
              {
                key: "zip",
                label: "导出压缩包",
                onClick: async () => {
                  try {
                    const r = await api.exportZip(id);
                    saveBlob(r.data, "overleaf_project.zip");
                    message.success("已导出");
                  } catch (e) {
                    message.error(formatError(e, "没导出"));
                  }
                },
              },
              {
                key: "bib",
                label: "导出参考文献",
                onClick: async () => {
                  try {
                    const r = await api.manuscriptBibtex(id);
                    const blob = new Blob([r.data.bibtex || ""], { type: "application/x-bibtex" });
                    saveBlob(blob, "refs.bib");
                    message.success("已导出");
                  } catch (e) {
                    message.error(formatError(e, "没导出参考文献"));
                  }
                },
              },
            ].filter(Boolean),
          }}
        >
          <Button>更多</Button>
        </Dropdown>
      </div>
      <div className="fs-object">
        <div className="fs-section-stack">
          <Card title={active ? `正在写：${active.key}` : "编辑"}>
            <div className="fs-section-bar">
              {parsed.focused
                ? parsed.sections.map((s) => (
                      <Button
                        key={s.id}
                        type="default"
                        style={s.id === active?.id ? { borderColor: "#0f6e6b", color: "#0f6e6b" } : undefined}
                        onClick={() => {
                          setActiveId(s.id);
                          setPicked(null);
                          setAltText("");
                          const next = new URLSearchParams(params);
                          next.set("section", s.key);
                          setParams(next, { replace: true });
                        }}
                      >
                        {s.key}
                      </Button>
                    ))
                : null}
              {(parsed.focused ? ["相关工作", "方法", "实验"] : ["相关工作"])
                .filter((name) => !parsed.sections.some((s) => s.key === name))
                .map((name) => (
                  <Button key={name} onClick={() => addSection(name)}>
                    {name === "相关工作" ? "加上相关工作这一节" : `加上${name}这一节`}
                  </Button>
                ))}
            </div>
            {active ? (
              <div style={{ marginBottom: 8 }}>
                <Button type={altText || nextEmpty || noteOpen ? "default" : "primary"} onClick={goWrite}>
                  帮我写这一节
                </Button>
                {sectionHint[active.key] ? <p className="fs-muted">{sectionHint[active.key]}</p> : null}
                {params.get("from_lit") ? (
                  <p className="fs-source-back">
                    <Link to={literatureHref(params.get("from_lit"), Number(params.get("from_page") || 1), { ms: id, section: active?.key || "" })}>
                      来自这篇第 {params.get("from_page") || 1} 页
                    </Link>
                  </p>
                ) : null}
                {owner ? (
                  citations.some((c) => c.literature_item_id) ? (
                    <div className="fs-section-bar">
                      <span className="fs-muted">这一节可以引用</span>
                      {citations
                        .filter((c) => c.literature_item_id)
                        .map((c) => (
                          <Button key={c.id} size="small" onClick={() => insertCite(c)}>
                            {c.citation_key ? `@${c.citation_key}` : c.title || "这篇"}
                          </Button>
                        ))}
                    </div>
                  ) : (
                    <p className="fs-muted">还没有可引用的文献。去读的时候点「加进这篇」。</p>
                  )
                ) : null}
                {parsed.preamble.trim() ? <p className="fs-muted">这一节前面的文字还在右边整篇里。</p> : null}
              </div>
            ) : (
              <p className="fs-muted">还没有相关工作、方法或实验这些标题。可以先整篇改，或加上相关工作这一节。</p>
            )}
            <Input.TextArea
              ref={bodyRef}
              rows={18}
              value={editorValue}
              readOnly={!owner}
              placeholder={active && !editorValue.trim() ? "这一节还是空的。可以自己写，或点上面帮我写。" : "这一节还是空的。"}
              onSelect={(e) => {
                const start = e.target.selectionStart;
                const end = e.target.selectionEnd;
                if (!Number.isInteger(start) || !Number.isInteger(end) || end <= start) return;
                setPicked({ text: editorValue.slice(start, end), start, end });
                setAltText("");
              }}
              onChange={(e) => {
                setEditorValue(e.target.value);
                setPicked(null);
                setAltText("");
                setNoteOpen(false);
                setNoteDraft("");
              }}
            />
            {picked && (owner || teacher) ? (
              <div className="fs-alt-box">
                {owner ? (
                  <Space wrap>
                    <span className="fs-press-wrap" onClick={whenBlocked(!!altBusy, "正在改，请稍等")}>
                      <Button size="small" disabled={!!altBusy} loading={altBusy === "polish"} onClick={() => runAlt("polish")}>
                        改这段
                      </Button>
                    </span>
                    <span className="fs-press-wrap" onClick={whenBlocked(!!altBusy, "正在改，请稍等")}>
                      <Button size="small" disabled={!!altBusy} loading={altBusy === "zh"} onClick={() => runAlt("zh")}>
                        译这段
                      </Button>
                    </span>
                    {altBusy ? (
                      <Button size="small" onClick={() => altAbort.current?.abort()}>
                        停止
                      </Button>
                    ) : null}
                  </Space>
                ) : null}
                {owner && altText ? (
                  <div style={{ marginTop: 8 }}>
                    <p className="fs-muted">改好的这一段。满意再换上。</p>
                    <pre className="fs-alt-preview">{altText}</pre>
                    <Space>
                      <Button type="primary" size="small" onClick={applyAlt}>
                        换成这版
                      </Button>
                      <Button
                        size="small"
                        onClick={() => {
                          setAltText("");
                          setPicked(null);
                        }}
                      >
                        关掉
                      </Button>
                    </Space>
                  </div>
                ) : null}
                <div style={{ marginTop: owner ? 8 : 0 }}>
                  {noteOpen ? (
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Input.TextArea
                        rows={2}
                        value={noteDraft}
                        placeholder="写一条意见"
                        onChange={(e) => setNoteDraft(e.target.value)}
                      />
                      <Space>
                        <Button type={altText ? "default" : "primary"} size="small" loading={noteSaving} onClick={pinNote}>
                          钉在这一段
                        </Button>
                        <Button
                          size="small"
                          onClick={() => {
                            setNoteOpen(false);
                            setNoteDraft("");
                          }}
                        >
                          关掉
                        </Button>
                      </Space>
                    </Space>
                  ) : (
                    <Button size="small" onClick={() => setNoteOpen(true)}>
                      记一条意见
                    </Button>
                  )}
                </div>
              </div>
            ) : null}
          </Card>
          <details className="fs-whole-preview">
            <summary>看整篇</summary>
            <MdPreview text={doc.content_md} />
          </details>
        </div>
        <div className="fs-object-side">
      {sideErr ? <LoadFailed message={sideErr} onRetry={load} /> : null}
      {(doc.milestones || []).length ? (
        <div className="fs-side-read">
          {(doc.milestones || []).map((m) => (
            <span key={m.id}>
              {m.kind_label || DEGREE_KIND[m.kind] || m.kind}
              {dueLabel(m) ? ` · ${dueLabel(m)}` : ""}
              {teacher || m.owner_id === me?.id ? (
                <Select
                  size="small"
                  value={m.status}
                  style={{ width: 96, marginLeft: 6 }}
                  options={Object.entries(DEGREE_STATUS).map(([value, label]) => ({ value, label }))}
                  onChange={async (status) => {
                    try {
                      await api.patchMilestone(doc.lab_id, m.id, { status });
                      message.success("已更新节点");
                      load();
                    } catch (e) {
                      message.error(formatError(e, "无法更新节点"));
                    }
                  }}
                />
              ) : (
                <span className="fs-muted"> · {DEGREE_STATUS[m.status] || m.status}</span>
              )}
              {m.direction_href ? (
                <>
                  {" · "}
                  <Link to={m.direction_href}>方向</Link>
                </>
              ) : null}
            </span>
          ))}
        </div>
      ) : null}
      <div className="fs-side-tabs is-wrap" role="tablist" aria-label="论文侧栏">
        {[
          ["claims", "还没写到"],
          ["cites", "参考文献"],
          ["comments", "意见"],
          ["review", "帮看结构"],
          ["versions", "以前的版本"],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={sideTab === key}
            className={`fs-side-tab${sideTab === key ? " is-on" : ""}`}
            onClick={() => setSideTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {sideTab === "versions" ? (
      <Card title="改过的版本" style={{ marginBottom: 16 }}>
        <Table
          rowKey="id"
          size="small"
          dataSource={versions}
          pagination={false}
          columns={[
            { title: "版本", dataIndex: "version" },
            { title: "时间", dataIndex: "created_at", render: fmtWhen },
            ...(owner
              ? [
                  {
                    title: "操作",
                    render: (_, row) => (
                      <Button
                        size="small"
                        onClick={async () => {
                          try {
                            await api.restoreVersion(id, row.id, doc.updated_at);
                            message.success(`已恢复 v${row.version}`);
                            load({ force: true });
                          } catch (e) {
                            if (e?.response?.status === 409) {
                              setConflict(formatError(e, "这篇刚被改过，请刷新后再恢复"));
                              return;
                            }
                            message.error(formatError(e, "无法恢复版本"));
                          }
                        }}
                      >
                        恢复
                      </Button>
                    ),
                  },
                ]
              : []),
          ]}
        />
      </Card>
      ) : null}
      {sideTab === "cites" ? (
      <Card title="参考文献">
        <p className="fs-muted">选一篇加进来。写的时候可以点它，把引用放进这一节。</p>
        <Form
          layout="inline"
          onFinish={async (v) => {
            if (!v.literature_item_id) {
              message.info("先选一篇文献");
              return;
            }
            if ((citations || []).some((c) => c.literature_item_id === v.literature_item_id)) {
              message.info("这篇已经在参考文献里");
              return;
            }
            const paper = papers.find((p) => p.id === v.literature_item_id) || {};
            const key = String(v.citation_key || "").trim() || suggestCiteKey(paper, citations);
            try {
              await api.addCitation(id, v.literature_item_id, key);
              message.success(`已加入这篇的参考文献，引用写成 [@${key}]`);
              load();
            } catch (e) {
              message.error(formatError(e, "无法添加引用"));
            }
          }}
        >
          <Form.Item name="literature_item_id" rules={[{ required: true, message: "先选一篇文献" }]}>
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="选择文献"
              style={{ width: 280 }}
              options={papers.map((p) => ({ value: p.id, label: p.title }))}
            />
          </Form.Item>
          <Button htmlType="submit">加进这篇</Button>
          <details>
            <summary>改引用写法</summary>
            <Form.Item name="citation_key">
              <Input placeholder="不填会自动写成作者加年份" />
            </Form.Item>
          </details>
        </Form>
        <Table
          rowKey="id"
          size="small"
          dataSource={citations}
          pagination={false}
          locale={{ emptyText: "还没有参考文献。选一篇加进来。" }}
          columns={[
            {
              title: "引用",
              dataIndex: "citation_key",
              render: (key) => (key ? `@${key}` : "—"),
            },
            {
              title: "文献",
              render: (_, row) =>
                row.literature_item_id ? (
                  <>
                    <Link to={row.literature_item_id ? literatureHref(row.literature_item_id, row.last_page_no, readBack) : row.href || "#"}>
                      {row.title || "打开文献"}
                    </Link>
                    {readingPctLabel(row) ? <span className="fs-muted"> · {readingPctLabel(row)}</span> : null}
                  </>
                ) : (
                  row.title || "—"
                ),
            },
            {
              title: "",
              render: (_, row) => (
                <Space>
                  {row.literature_item_id ? (
                    <details className="fs-inline-more">
                      <summary>还有</summary>
                      <ActionChip
                        to={row.reproduce_href || `/labs/${doc.lab_id}/reproduces?literature_item_id=${encodeURIComponent(row.literature_item_id)}`}
                      >
                        去复现
                      </ActionChip>
                    </details>
                  ) : null}
                  <Button
                    size="small"
                    danger
                    onClick={async () => {
                      try {
                        await api.deleteCitation(id, row.id);
                        load();
                      } catch (e) {
                        message.error(formatError(e, "无法删除引用"));
                      }
                    }}
                  >
                    移除
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      ) : null}
      {sideTab === "claims" ? (
      <Card title="正文点到了谁" style={{ marginBottom: 16 }}>
        <p className="fs-muted">对照引用和这篇的方法。点一条回到那篇文献。</p>
        <UnusedResearchAlert note={claims?.unused_research} />
        {(claims?.core_missing || []).length ? (
          <div className="fs-missing-list">
            <p>这几篇还没写进正文</p>
            {(claims.core_missing || []).map((p) => (
              <div key={p.id} className="fs-missing-row">
                <span>{p.title}</span>
                <Button
                  size="small"
                  onClick={() =>
                    nav(
                      `/labs/${doc.lab_id}/writing?ms=${id}&heading=${encodeURIComponent("相关工作")}&lit=${encodeURIComponent(p.id)}`
                    )
                  }
                >
                  写进相关工作
                </Button>
                <Link to={literatureHref(p.id, undefined, { ms: id, section: "相关工作" })}>去读</Link>
              </div>
            ))}
          </div>
        ) : claims?.core_total ? (
          <p className="fs-muted">这些核心文献都写进正文了。</p>
        ) : null}
        {claims?.citation_count || claims?.mentioned_only_count ? (
          <p className="fs-muted">
            正文已点名 {claims.cited_in_body_count}/{claims.citation_count}
            {(claims.missing_method_cards || []).length ? ` · ${claims.missing_method_cards.length} 篇还没写方法` : ""}
            {(claims.unmentioned_claims || []).length ? ` · ${claims.unmentioned_claims.length} 条主张未写入正文` : ""}
            {claims.mentioned_only_count ? ` · ${claims.mentioned_only_count} 篇正文点名、未进参考文献` : ""}
            {claims.core_total ? ` · 本方向核心文献已点名 ${claims.core_mentioned_count}/${claims.core_total}` : ""}
            {claims.core_pinned_count ? ` · 已钉 ${claims.core_pinned_count} 篇` : ""}
          </p>
        ) : null}
        {!(claims?.items || []).length ? (
          <p className="fs-muted">先加入参考文献。写了方法后会列出该篇主张。</p>
        ) : (
          <details className="fs-claim-more">
            <summary>每篇的依据</summary>
          {(claims.items || []).map((c) => {
            const openPage = c.source_page || c.last_page_no;
            const litTo = c.literature_item_id ? literatureHref(c.literature_item_id, openPage, readBack) : "";
            return (
            <article key={c.citation_id || c.literature_item_id} className="fs-cite fs-claim">
              <div className="fs-claim-top">
                {c.literature_item_id ? <Link to={litTo}>{c.title || c.citation_key}</Link> : <span>{c.title}</span>}
                {c.citation_key ? <span className="fs-muted">@{c.citation_key}</span> : null}
                {c.mentioned_only ? <Tag color="gold">正文点名、未进参考文献</Tag> : c.cited_in_body ? <Tag color="green">正文已点名</Tag> : <Tag color="gold">正文还没写到</Tag>}
                {c.card_source === "abstract_only" || c.card_quality?.weak ? <Tag color="gold">{c.card_quality?.abstract_only ? "仅摘要" : "弱卡"}</Tag> : null}
                <PdfGapLink row={{ ...c, id: c.literature_item_id }} />
                {readingPctLabel(c) ? <span className="fs-muted">{readingPctLabel(c)}</span> : null}
                {c.has_method_card ? null : <Tag>还没写方法</Tag>}
                {c.reproduce?.measured ? <Tag color="green">已经跑出结果</Tag> : c.reproduce ? <Tag>还没跑出结果</Tag> : null}
              </div>
              <div className="fs-claim-actions">
                {!c.has_method_card ? <AnalyzeChip row={{ ...c, id: c.literature_item_id }} /> : null}
                {c.reproduce_href ? (
                  <details className="fs-inline-more">
                    <summary>还有</summary>
                    <ActionChip to={c.reproduce_href}>
                      {c.reproduce_run_id ? "查看复现" : "去复现"}
                    </ActionChip>
                  </details>
                ) : null}
                {c.reproduce_status ? <StatusTag status={c.reproduce_status} map={RUN_STATUS} /> : null}
                {c.citation_key ? (
                  <Button
                    size="small"
                    type="link"
                    onClick={() => {
                      const key = c.citation_key;
                      if (!navigator.clipboard?.writeText) {
                        message.info(key);
                        return;
                      }
                      navigator.clipboard.writeText(key).then(
                        () => message.success("已复制"),
                        () => message.error("没复制上，请再试一次"),
                      );
                    }}
                  >
                    复制 key
                  </Button>
                ) : null}
              </div>
              <div className="fs-claim-rows">
                {(c.claim_rows || []).length
                  ? (c.claim_rows || []).slice(0, 8).map((row, i) => {
                      const rowTo =
                        c.literature_item_id &&
                        literatureHref(c.literature_item_id, row.page_no || openPage, readBack);
                      const body = (
                        <>
                          {row.text}
                          {row.page_no ? ` p.${row.page_no}` : ""}
                          {row.mentioned_in_body ? "（已写入）" : "（未写入）"}
                          {row.ungrounded ? "（对不上原文）" : ""}
                        </>
                      );
                      return (
                        <p key={i}>
                          {rowTo ? <Link to={rowTo}>{body}</Link> : body}
                        </p>
                      );
                    })
                  : (c.claims || []).length
                    ? <p>{(c.claims || []).slice(0, 2).join("；")}</p>
                    : null}
              </div>
            </article>
            );
          })}
          </details>
        )}
        {(claims?.unused_citations || []).length ? (
          <div className="fs-missing-list">
            <p className="fs-muted">参考文献里这几篇还没出现在正文。</p>
            {claims.unused_citations.map((u) => (
              <div key={u.citation_id || u.literature_item_id} className="fs-missing-row">
                <span>{u.title || u.citation_key}</span>
                {u.literature_item_id ? (
                  <Button
                    size="small"
                    onClick={() =>
                      nav(
                        `/labs/${doc.lab_id}/writing?ms=${id}&heading=${encodeURIComponent("相关工作")}&lit=${encodeURIComponent(u.literature_item_id)}`
                      )
                    }
                  >
                    写进相关工作
                  </Button>
                ) : null}
                {u.literature_item_id ? <Link to={literatureHref(u.literature_item_id, undefined, { ms: id, section: "相关工作" })}>去读</Link> : null}
              </div>
            ))}
          </div>
        ) : null}
      </Card>
      ) : null}
      {sideTab === "versions" ? (
      <Card title="推到 Overleaf">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Input placeholder="https://git.overleaf.com/..." value={gitUrl} onChange={(e) => setGitUrl(e.target.value)} />
          <Input.Password placeholder="访问令牌，只用于这一次，不会保存" value={gitToken} onChange={(e) => setGitToken(e.target.value)} />
          <Button
            onClick={async () => {
              try {
                const r = await api.overleafSync(id, { git_url: gitUrl || null, token: gitToken || null });
                if (r.data.ok) message.success("已推送");
                else {
                  message.warning(r.data.detail || "请改用 zip");
                  if (r.data.fallback === "zip") {
                    const z = await api.exportZip(id);
                    saveBlob(z.data, "overleaf_project.zip");
                  }
                }
              } catch (e) {
                message.error(formatError(e, "无法同步，请导出 zip"));
              }
            }}
          >
            推上去
          </Button>
        </Space>
      </Card>
      ) : null}
      {sideTab === "review" ? (
      <Card title="帮看结构" style={{ marginBottom: 16 }}>
        <p className="fs-muted">看缺了哪一节、哪段重复、哪条主张没引用。不会改正文，也不是查重。</p>
        <UnusedResearchAlert note={claims?.unused_research} />
        <Button
          id="fs-review-run"
          loading={reviewing}
          onClick={async () => {
            setReviewing(true);
            setReviewNote("");
            try {
              const r = await api.reviewSimulate(id);
              if (r.data.trace_id || r.data.session_id) setReviewTrace(r.data.trace_id || r.data.session_id);
              setReviewSteps(r.data.steps || []);
              setReviewDegraded(r.data.degraded || r.data.degraded_to || "");
              setReviewTools(r.data.available_tools || []);
              setReviewFollowups(r.data.followups || []);
              setReviewFindings(r.data.findings || []);
              setReviewText((r.data.findings || []).map((f) => f.body).filter(Boolean).join("\n"));
              if (r.data.ungrounded_reproduce) {
                message.warning(humanize(r.data.ungrounded_reproduce));
              } else {
                message.success("看完了。缺的节、重复的段、没引用的句子都列在下面。");
              }
              if (r.data.unused_research) {
                setClaims((cur) => ({ ...(cur || {}), unused_research: r.data.unused_research }));
              }
              load();
            } catch (e) {
              message.error(formatError(e, "评审失败"));
            } finally {
              setReviewing(false);
            }
          }}
        >
          帮我看这一篇
        </Button>
        {reviewNote ? <p className="fs-muted">{reviewNote}</p> : null}
        {reviewFindings.length ? (
          <ul className="fs-finding-list">
            {reviewFindings.map((f, i) => (
              <li key={f.id || i}>
                <Button type="link" size="small" onClick={() => openFinding(f)}>
                  {findingLabel[f.kind] || "去看这一处"}
                </Button>
                <span>{humanize(f.body || "")}</span>
              </li>
            ))}
          </ul>
        ) : null}
        <AiResultBar
          text={reviewText}
          copyLabel="复制提示"
          busy={reviewing}
          traceId={reviewTrace}
          steps={reviewSteps}
          degraded={reviewDegraded}
          availableTools={reviewTools}
          followups={reviewFollowups}
          retryLabel="再跑一轮"
          onRetry={() => document.getElementById("fs-review-run")?.click()}
        />
      </Card>
      ) : null}
      {sideTab === "comments" ? (
      <Card title="意见">
        <p className="fs-muted">意见钉在那一段。点开就跳过去。</p>
        {comments.map((c) => {
          let anchor = {};
          try {
            anchor = typeof c.anchor_json === "string" ? JSON.parse(c.anchor_json || "{}") : c.anchor_json || {};
          } catch {
            anchor = {};
          }
          return (
            <p key={c.id} id={`ms-comment-${c.id}`} className={c.id === commentId ? "fs-comment-hit" : undefined}>
              <b>{c.display_name || c.user_id}</b> {c.body}
              {anchor.excerpt ? (
                <Button size="small" type="link" onClick={() => focusExcerpt(anchor.excerpt)}>
                  跳到这一段
                </Button>
              ) : anchor.heading ? (
                <Button size="small" type="link" onClick={() => openFinding({ ...c, anchor_json: c.anchor_json })}>
                  打开这一节
                </Button>
              ) : null}
            </p>
          );
        })}
        <Form
          onFinish={async (v) => {
            try {
              const el = bodyRef.current?.resizableTextArea?.textArea;
              const start = el?.selectionStart;
              const end = el?.selectionEnd;
              const excerpt =
                Number.isInteger(start) && Number.isInteger(end) && end > start
                  ? (el.value || "").slice(start, end)
                  : undefined;
              await api.addComment(id, v.body, excerpt ? { excerpt } : {});
              message.success(excerpt ? "已钉在这一段" : "已记下");
              load();
            } catch (e) {
              message.error(formatError(e, "无法发表批注"));
            }
          }}
        >
          <Form.Item name="body" rules={[{ required: true }]}>
            <Input placeholder="写一条意见" />
          </Form.Item>
          <Button htmlType="submit">记下</Button>
        </Form>
      </Card>
      ) : null}
        </div>
      </div>
    </Space>
  );
}

