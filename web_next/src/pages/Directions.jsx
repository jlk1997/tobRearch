import { useEffect, useMemo, useState } from "react";
import { Button, Collapse, Drawer, Form, Input, Popconfirm, Select, Space, Table, message } from "antd";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, asItems } from "../api.js";
import { formatError, DEGREE_KIND, DEGREE_STATUS, MS_STATUS, RUN_STATUS, literatureHref } from "../err.js";
import { ActionChip, AgentTraceBox, AnalyzeChip, AskWeekChip, ContinueAskChip, CoreMissingTitleChips, CreateDialog, DirectionDraftList, DirectionGapAlert, DirectionHangChips, EmptyHint, FeedbackStrip, LabReaderBits, LoadFailed, NextStepList, NoCardTitleChips, NotRunTitleChips, PageHeader, PdfGapLink, RelatedWorkTitleChips, ResumeJob, StatusTag, ActivityLine, UnreadTitleChips, UnusedTitleChips, dueLabel, toolLabel, useComposeOpen } from "../ui.jsx";
import { CoverageRadar, FunnelChart, FunnelEmptyHero, FunnelLeakChips } from "../charts.jsx";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";
import { writingDeskHref } from "../manuscriptSections.js";
import { CitedAnswer, kbSpoken } from "../kbSpeak.jsx";

function RouteEditor({ labId, directionId, route, onSaved }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setText((route || []).map((row) => `${row.stage || "入门"}｜${row.title || ""}`).filter((line) => !line.endsWith("｜")).join("\n"));
  }, [route]);
  return (
    <div style={{ marginTop: 8 }}>
      <p className="fs-muted">阅读路线。一行一条，写成「入门｜题名」。刷新外面的论文时这条还在。</p>
      <Input.TextArea rows={4} value={text} onChange={(e) => setText(e.target.value)} />
      <Button
        size="small"
        style={{ marginTop: 8 }}
        loading={busy}
        onClick={async () => {
          const next = text
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean)
            .map((line) => {
              const parts = line.split("｜");
              const stage = parts.length > 1 ? parts[0] : "入门";
              const title = parts.length > 1 ? parts.slice(1).join("｜") : line;
              return { stage: stage.trim() || "入门", title: title.trim() };
            })
            .filter((row) => row.title);
          setBusy(true);
          try {
            await api.patchDirection(directionId, { route: next });
            message.success("阅读路线已记下");
            onSaved?.();
          } catch (e) {
            message.error(formatError(e, "没记下阅读路线"));
          } finally {
            setBusy(false);
          }
        }}
      >
        记下阅读路线
      </Button>
      <Button
        size="small"
        style={{ marginTop: 8, marginLeft: 8 }}
        loading={busy}
        onClick={async () => {
          setBusy(true);
          try {
            const r = await api.draftDirectionRoute(labId, directionId);
            const lines = (r.data?.route || []).map((row) => `${row.stage || "入门"}｜${row.title || ""}`);
            if (!lines.length) {
              message.info("还没有能草拟的篇。先更新外面的论文，或挂上还没读的文献。");
              return;
            }
            setText(lines.join("\n"));
            message.success(r.data?.outlet_text || "草拟好了，确认后点记下。");
          } catch (e) {
            message.error(formatError(e, "没草拟成"));
          } finally {
            setBusy(false);
          }
        }}
      >
        按缺口草拟
      </Button>
    </div>
  );
}

function asDirTree(rows) {
  const kids = {};
  (rows || []).forEach((r) => {
    if (r.parent_id) (kids[r.parent_id] ||= []).push({ ...r });
  });
  return (rows || [])
    .filter((r) => !r.parent_id)
    .map((r) => (kids[r.id] ? { ...r, children: kids[r.id] } : r));
}

function DirStuckChips({ row, labId }) {
  const stop = (e) => e.stopPropagation();
  const items = [
    [row.unread_count, "无人在读", row.unread_href || `/labs/${labId}/literature?direction_id=${row.id}&reading_status=unread`],
    [
      row.related_work_missing,
      "未写相关工作",
      row.related_work_missing_href || `/labs/${labId}/manuscripts?direction_id=${row.id}&related_work=0`,
    ],
    [
      row.core_missing,
      "核心还没写进正文",
      row.core_missing_href || `/labs/${labId}/manuscripts?direction_id=${row.id}&core_missing=1`,
    ],
    [row.reproduce_not_run, "还没做复现", row.reproduce_not_run_href || `/labs/${labId}/reproduces?direction_id=${row.id}`],
    [
      row.manuscripts_ungrounded,
      "写了结果，组里还没跑过",
      row.manuscripts_ungrounded_href || `/labs/${labId}/manuscripts?ungrounded=1&direction_id=${row.id}`,
    ],
    [row.unused_research, "综述没提到", row.unused_research_href || `/labs/${labId}/runs?unused=1&direction_id=${row.id}`],
  ].filter(([n]) => Number(n) > 0);
  if (!items.length) return null;
  return (
    <div className="fs-dir-stuck">
      {items.map(([n, label, href]) => (
        <Link key={label} className="fs-chip" to={href} onClick={stop}>
          {label} {n}
        </Link>
      ))}
    </div>
  );
}

export default function Directions() {
  const routeParams = useParams();
  const id = routeParams.id;
  const dirId = routeParams.dirId || routeParams.directionId;
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const me = useSession((s) => s.me);
  const teacher = roleOf(me, id) === "teacher" || !!me?.is_admin;
  const [compose, setCompose] = useComposeOpen(params, setParams);
  const [dirForm] = Form.useForm();
  const [kwForm] = Form.useForm();
  const [mileForm] = Form.useForm();
  const [rows, setRows] = useState([]);
  const [members, setMembers] = useState([]);
  const [err, setErr] = useState("");
  const [overview, setOverview] = useState(null);
  const [ovErr, setOvErr] = useState("");
  const [kwHints, setKwHints] = useState([]);
  const [drafts, setDrafts] = useState([]);
  const [dirGap, setDirGap] = useState(null);
  const [hangBusy, setHangBusy] = useState("");
  const [dirAsk, setDirAsk] = useState("");
  const [dirAsking, setDirAsking] = useState(false);
  const [pane, setPane] = useState("papers");
  const reload = () => {
    api
      .directions(id)
      .then((r) => {
        setRows(asItems(r.data));
        setErr("");
      })
      .catch((e) => setErr(formatError(e, "方向加载失败")));
    api
      .members(id)
      .then((r) => setMembers(asItems(r.data)))
      .catch(() => setMembers([]));
  };
  const loadOverview = (did) => {
    if (!did) {
      setOverview(null);
      return;
    }
    setOvErr("");
    api
      .directionOverview(id, did)
      .then((r) => setOverview(r.data))
      .catch((e) => setOvErr(formatError(e, "方向详情加载失败")));
  };
  useEffect(() => {
    reload();
  }, [id]);
  useEffect(() => {
    loadOverview(dirId);
  }, [id, dirId]);
  useEffect(() => {
    if (!overview) return;
    kwForm.setFieldsValue({ keywords: (overview.keywords || []).join("，") });
  }, [overview?.id, (overview?.keywords || []).join(",")]);
  useEffect(() => {
    if (!id) return undefined;
    let alive = true;
    api
      .literature(id, { page: 1, page_size: 1 })
      .then((r) => {
        if (!alive) return;
        setKwHints(r.data?.direction_gap?.keyword_hints || []);
        setDrafts(r.data?.direction_drafts || []);
        setDirGap(r.data?.direction_gap || null);
      })
      .catch((e) => {
        if (!alive) return;
        setKwHints([]);
        setDrafts([]);
        setDirGap(null);
        message.warning(formatError(e, "方向建议没打开"));
      });
    return () => {
      alive = false;
    };
  }, [id, compose, rows.length]);
  const tree = useMemo(() => asDirTree(rows), [rows]);
  const createForm = (
    <Form
      form={dirForm}
      layout="vertical"
      className="fs-dir-create-form"
      onFinish={async (v) => {
        try {
          await api.createDirection(id, {
            name: v.name,
            description: v.description || "",
            parent_id: v.parent_id || null,
            owner_user_id: v.owner_user_id || null,
            keywords: (v.keywords || "")
              .split(/[,，]/)
              .map((x) => x.trim())
              .filter(Boolean),
          });
          dirForm.resetFields();
          setCompose(false);
          message.success("已创建方向");
          reload();
        } catch (e) {
          message.error(formatError(e, "无法创建方向"));
        }
      }}
    >
      <div className="fs-dir-create-grid">
        <Form.Item name="name" label="名称" rules={[{ required: true, message: "请填写名称" }]}>
          <Input placeholder="例如：视觉大组" />
        </Form.Item>
        <Form.Item name="description" label="说明">
          <Input.TextArea rows={3} placeholder="可选" />
        </Form.Item>
        <Form.Item name="keywords" label="关键词">
          <Input placeholder="逗号分隔，检索会叠这些词" />
        </Form.Item>
        {kwHints.length ? (
          <p className="fs-dir-hints">
            <span className="fs-muted">从未挂靠题名抽出，点一下写入（不会自动建方向）：</span>
            {kwHints.map((w) => (
              <Button
                key={w}
                size="small"
                onClick={() => {
                  const cur = dirForm.getFieldValue("keywords") || "";
                  const parts = cur
                    .split(/[,，]/)
                    .map((x) => x.trim())
                    .filter(Boolean);
                  if (!parts.includes(w)) dirForm.setFieldsValue({ keywords: [...parts, w].join("，") });
                }}
              >
                {w}
              </Button>
            ))}
          </p>
        ) : null}
        <Form.Item name="parent_id" label="作为子课题">
          <Select
            allowClear
            placeholder="不选就是大方向"
            options={rows.filter((d) => !d.parent_id).map((d) => ({ value: d.id, label: d.name }))}
          />
        </Form.Item>
        <Form.Item name="owner_user_id" label="负责人">
          <Select
            allowClear
            placeholder="可选"
            options={members.map((m) => ({ value: m.user_id, label: m.display_name || m.email }))}
          />
        </Form.Item>
      </div>
    </Form>
  );
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        title={dirId && overview?.name ? overview.name : "方向"}
        hint={dirId ? "在找什么，接着读，接着写。" : "一个方向就是一组要一起找的论文。"}
        extra={
          dirId ? (
            <Button onClick={() => nav(`/labs/${id}/directions`)}>全部方向</Button>
          ) : teacher ? (
            <Button type="primary" onClick={() => setCompose(true)}>
              新建方向
            </Button>
          ) : null
        }
      >
        {dirId
          ? (overview?.keywords || []).length
            ? `在找：${(overview.keywords || []).join("、")}`
            : "先写这个方向在找什么。"
          : "点开一个方向，先看它在找什么，再去找论文。"}
      </PageHeader>
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}
      {teacher ? (
        <CreateDialog
          open={compose}
          onClose={() => setCompose(false)}
          title="新建方向"
          okText="新建"
          form={dirForm}
          width={640}
          extra={
            drafts.length ? (
              <DirectionDraftList
                drafts={drafts}
                labId={id}
                teacher={teacher}
                onAdopted={() => {
                  reload();
                  setCompose(false);
                }}
              />
            ) : null
          }
        >
          {createForm}
        </CreateDialog>
      ) : dirId ? null : (
        <p className="fs-muted">方向由导师维护。加入后可在工作台选定自己的方向。</p>
      )}
      {dirId ? null : (
      <DirectionGapAlert
        gap={dirGap}
        drafts={drafts}
        labId={id}
        teacher={teacher}
        onAdopted={() => {
          reload();
          setCompose(false);
          api
            .literature(id, { page: 1, page_size: 1 })
            .then((r) => {
              setKwHints(r.data?.direction_gap?.keyword_hints || []);
              setDrafts(r.data?.direction_drafts || []);
              setDirGap(r.data?.direction_gap || null);
            })
            .catch((e) => message.warning(formatError(e, "方向建议没打开")));
        }}
      />
      )}
      {dirId ? null : !rows.length ? (
        err ? null : (
          teacher ? (
            dirGap ? null : <EmptyHint>还没有方向。可从文献归纳后采用，或点新建方向。</EmptyHint>
          ) : (
            <EmptyHint to={`/labs/${id}`}>还没有方向。导师确认后才会出现，再在工作台选定。</EmptyHint>
          )
        )
      ) : (
        <Table
          className="fs-dir-table fs-read-table fs-wall"
          showHeader={false}
          rowKey="id"
          pagination={false}
          expandable={{ defaultExpandAllRows: true }}
          dataSource={tree}
          onRow={(row) => ({
            onClick: () => nav(`/labs/${id}/directions/${row.id}`),
            className: "fs-click-row",
          })}
          columns={[
            {
              title: "方向",
              dataIndex: "name",
              render: (v, row) => {
                const words = (row.keywords || []).join("、");
                const meta = [
                  words || "还没写在找什么",
                  `文献 ${row.literature_count ?? 0}`,
                  `论文 ${row.manuscript_count ?? 0}`,
                  row.owner_name || "",
                ].filter(Boolean).join(" · ");
                return (
                  <div>
                    <div>
                      <strong>{v}</strong>
                      {row.child_count ? <span className="fs-muted"> · 含小课题 {row.child_count}</span> : null}
                    </div>
                    <p className="fs-muted" style={{ margin: "2px 0 0" }}>{meta}</p>
                  </div>
                );
              },
            },
            {
              title: "负责人",
              dataIndex: "owner_name",
              width: 180,
              className: "fs-col-fold",
              render: (v, row) =>
                teacher ? (
                  <Select
                    size="small"
                    allowClear
                    style={{ minWidth: 140 }}
                    placeholder="负责人"
                    value={row.owner_user_id || undefined}
                    options={members.map((m) => ({ value: m.user_id, label: m.display_name || m.email }))}
                    onClick={(e) => e.stopPropagation()}
                    onChange={async (uid) => {
                      try {
                        await api.patchDirection(row.id, { owner_user_id: uid || "" });
                        message.success("已更新负责人");
                        reload();
                      } catch (e) {
                        message.error(formatError(e, "无法改负责人"));
                      }
                    }}
                  />
                ) : (
                  v || "—"
                ),
            },
            {
              title: "文献 · 论文",
              width: 140,
              className: "fs-col-fold",
              render: (_, row) => (
                <span>
                  <Link
                    to={`/labs/${id}/literature?direction_id=${row.id}`}
                    onClick={(e) => e.stopPropagation()}
                  >
                    文献 {row.literature_count ?? 0}
                  </Link>
                  <span className="fs-muted"> · </span>
                  <Link
                    to={`/labs/${id}/manuscripts?direction_id=${row.id}`}
                    onClick={(e) => e.stopPropagation()}
                  >
                    论文 {row.manuscript_count ?? 0}
                  </Link>
                </span>
              ),
            },
            {
              title: "下一步",
              width: 140,
              render: (_, row) =>
                (row.keywords || []).length ? (
                  <Link to={`/labs/${id}/literature?direction_id=${row.id}`} onClick={(e) => e.stopPropagation()}>
                    去找论文
                  </Link>
                ) : (
                  <Link to={`/labs/${id}/directions/${row.id}`} onClick={(e) => e.stopPropagation()}>
                    先写在找什么
                  </Link>
                ),
            },
            ...(teacher
              ? [
                  {
                    title: "",
                    width: 88,
                    render: (_, row) => (
                      <Popconfirm
                        title="归档该方向？"
                        onConfirm={async () => {
                          try {
                            await api.deleteDirection(row.id);
                            message.success("已归档");
                            reload();
                          } catch (e) {
                            message.error(formatError(e, "无法归档"));
                          }
                        }}
                      >
                        <Button danger size="small" onClick={(e) => e.stopPropagation()}>
                          归档
                        </Button>
                      </Popconfirm>
                    ),
                  },
                ]
              : []),
          ]}
        />
      )}
      {dirId ? (
      <div className="fs-desk">
        {ovErr ? <LoadFailed message={ovErr} onRetry={() => loadOverview(dirId)} /> : null}
        {overview ? (
          <Space direction="vertical" style={{ width: "100%" }}>
            {overview.description ? <p>{overview.description}</p> : null}
            {overview.child_count ? (
              <p className="fs-muted">
                含 {overview.child_count} 个子课题，下面还有
                {(overview.children || []).map((c) => (
                  <span key={c.id}>
                    {" · "}
                    <Link to={c.href}>{c.name}</Link>
                  </span>
                ))}
              </p>
            ) : null}
            {overview.owner_name ? <p className="fs-muted">负责人：{overview.owner_name}</p> : null}
            {(overview.members || []).length ? (
              <p>
                {(overview.members || [])
                  .slice(0, 8)
                  .map((m) => `${m.display_name || "成员"} · ${m.role === "teacher" ? "导师" : "学生"}`)
                  .join("、")}
                {(overview.members || []).length > 8 ? ` 等 ${overview.members.length} 人` : ""}
              </p>
            ) : (
              <p className="fs-muted">还没有人挂在这个方向。</p>
            )}
            {overview.active_reading_count ? (
              <p>
                <Link to={overview.reading_href || `/labs/${id}/literature?direction_id=${overview.id}&reading_status=reading&reading_scope=lab`}>
                  组里在读 {overview.active_reading_count} 篇
                </Link>
              </p>
            ) : null}
            {!(overview.keywords || []).length && !teacher ? (
              <p>先写这个方向在找什么。请导师写上。</p>
            ) : null}
            {teacher ? (
              <Form
                form={kwForm}
                layout="inline"
                initialValues={{ keywords: (overview.keywords || []).join("，") }}
                onFinish={async (v) => {
                  try {
                    await api.patchDirection(overview.id, {
                      keywords: (v.keywords || "")
                        .split(/[,，]/)
                        .map((x) => x.trim())
                        .filter(Boolean),
                    });
                    message.success("已保存关键词");
                    reload();
                    loadOverview(dirId);
                  } catch (e) {
                    message.error(formatError(e, "没保存上"));
                  }
                }}
              >
                <Form.Item name="keywords">
                  <Input placeholder="这个方向在找什么，逗号分隔" style={{ width: 280 }} />
                </Form.Item>
                <Button htmlType="submit" type={(overview.keywords || []).length ? "default" : "primary"}>
                  {(overview.keywords || []).length ? "保存" : "写上"}
                </Button>
              </Form>
            ) : null}
            <nav className="fs-jumpbar" aria-label="这一页的分段">
              <button type="button" className={pane === "papers" ? "is-on" : ""} onClick={() => setPane("papers")}>
                文献{(overview.literature || []).length ? ` ${(overview.literature || []).length}` : ""}
              </button>
              <button type="button" className={pane === "read" ? "is-on" : ""} onClick={() => setPane("read")}>接着读</button>
              {teacher ? (
                <button type="button" className={pane === "edit" ? "is-on" : ""} onClick={() => setPane("edit")}>改这个方向</button>
              ) : null}
            </nav>
            <div className={pane === "read" ? "fs-dir-pane" : "fs-dir-pane fs-is-off"}>
            <div className="fs-desk-piles">
              <div id="dir-read">
                <h3>接着读</h3>
                {(overview.literature || []).filter((p) => !p.my_reading || p.my_reading === "reading" || p.my_reading === "want_read").slice(0, 5).length ? (
                  (overview.literature || [])
                    .filter((p) => !p.my_reading || p.my_reading === "reading" || p.my_reading === "want_read")
                    .slice(0, 5)
                    .map((p) => (
                      <p key={p.id}>
                        <Link to={p.href || literatureHref(p.id, p.last_page_no, { direction: overview.id })}>{p.title}</Link>
                      </p>
                    ))
                ) : (
                  <p className="fs-muted">去找一篇来读</p>
                )}
              </div>
              <div>
                <h3>接着写</h3>
                {(overview.manuscripts || []).length ? (
                  (overview.manuscripts || []).slice(0, 5).map((m) => (
                    <p key={m.id}>
                      <Link to={m.href || `/manuscripts/${m.id}`}>{m.title}</Link>
                    </p>
                  ))
                ) : (
                  <p>
                    <Link to={`/labs/${id}/manuscripts?direction_id=${overview.id}&compose=1`}>先建论文</Link>
                  </p>
                )}
              </div>
            </div>
            </div>
            <div className={pane === "papers" ? "fs-dir-pane" : "fs-dir-pane fs-is-off"}>
            <h3 id="dir-papers" className="fs-subhead">这些论文</h3>
            {(overview.literature || []).length ? (
              <div className="fs-read-list">
                {overview.literature.map((p) => (
                  <div key={p.id} className="fs-read-row">
                    <div>
                      <Link className="fs-lit-title" to={p.href || literatureHref(p.id, p.last_page_no, { direction: overview.id })}>{p.title}</Link>
                      <p className="fs-muted fs-lit-why">
                        {[
                          p.last_page_no ? `上次读到第 ${p.last_page_no} 页` : "",
                          p.my_reading === "finished" ? "已读" : p.my_reading === "want_read" ? "想读" : p.my_reading === "reading" ? "在读" : "",
                          p.is_core ? "核心" : "",
                        ].filter(Boolean).join(" · ")}
                        {p.lab_reader_total ? (
                          <>
                            {" · "}组里在读：
                            <LabReaderBits itemId={p.id} names={p.lab_readers} marks={p.lab_reader_marks} />
                          </>
                        ) : null}
                      </p>
                    </div>
                    {p.has_method_card ? (
                      <Link to={literatureHref(p.id, p.last_page_no, { panel: "tutor", direction: overview.id })}>问这篇</Link>
                    ) : (
                      <span>
                        <PdfGapLink row={p} /> <AnalyzeChip row={p} />
                      </span>
                    )}
                    {p.direction_suggestions?.length ? (
                      <details className="fs-inline-more">
                        <summary>更多</summary>
                        <DirectionHangChips
                          suggestions={p.direction_suggestions}
                          busyId={hangBusy.startsWith(`${p.id}:`) ? hangBusy.slice(p.id.length + 1) : ""}
                          onHang={async (s) => {
                            if (!p.id || !s?.id || hangBusy) return;
                            setHangBusy(`${p.id}:${s.id}`);
                            try {
                              const next = [...new Set([...(p.direction_ids || []), s.id])];
                              await api.patchLiterature(p.id, { direction_ids: next });
                              message.success("已挂到方向");
                              loadOverview(dirId);
                            } catch (e) {
                              message.error(formatError(e, "无法挂方向"));
                            } finally {
                              setHangBusy("");
                            }
                          }}
                        />
                      </details>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : (
              <p className="fs-muted">这个方向还没有论文。先去文献里找，再放进来。</p>
            )}
            {overview.brief?.one_line || overview.brief?.problem ? (
              <section className="fs-dir-brief">
                {overview.brief?.one_line ? <p>{overview.brief.one_line}</p> : null}
                {overview.brief?.problem || overview.brief?.how || (overview.brief?.papers || []).length ? (
                  <details>
                    <summary>再看一点</summary>
                    {overview.brief?.problem ? <p>要解决的是：{overview.brief.problem}</p> : null}
                    {overview.brief?.how ? <p>做法是：{overview.brief.how}</p> : null}
                    {(overview.brief?.papers || []).length ? (
                      <p>
                        {overview.brief.papers.slice(0, 3).map((p) => (
                          <Link key={p.id || p.title} to={`/literature/${p.id}`}>《{p.title}》</Link>
                        ))}
                      </p>
                    ) : null}
                    {(overview.brief?.people || []).length ? (
                      <p className="fs-muted">{overview.brief.people.map((p) => p.name).filter(Boolean).join("、")}</p>
                    ) : null}
                  </details>
                ) : null}
              </section>
            ) : null}
            {(overview.brief?.themes || []).length ? (
              <section className="fs-dir-themes">
                <h4>这个方向里分成 {overview.brief.themes.length} 块</h4>
                <div className="fs-dir-theme-grid">
                  {overview.brief.themes.map((t) => (
                    <div key={t.key || t.name} className="fs-dir-theme">
                      <p className="fs-dir-theme-head">
                        <b>{t.name}</b>
                        {t.size ? <span className="fs-muted">{t.size} 篇</span> : null}
                      </p>
                      {t.line ? <p className="fs-dir-theme-line">{t.line}</p> : null}
                      {(t.reps || []).slice(0, 3).map((p) => (
                        <Link key={p.id} className="fs-dir-theme-rep" to={`/literature/${p.id}`}>
                          {p.title}
                        </Link>
                      ))}
                    </div>
                  ))}
                </div>
              </section>
            ) : null}
            {(overview.frontier?.papers || []).length || teacher ? (
            <details>
              <summary>这个方向外面最近在读什么</summary>
              {overview.frontier?.note ? <p className="fs-muted">{overview.frontier.note}</p> : null}
              {(overview.frontier?.themes || []).map((t) => (
                <p key={t.name}>{t.name}：{(t.papers || []).slice(0, 2).join("；")}</p>
              ))}
              {(overview.frontier?.papers || []).slice(0, 5).map((p) => (
                <p key={p.title}>
                  还没放进组里 · {p.title}
                  <Button
                    type="link"
                    size="small"
                    onClick={() =>
                      api.addLiterature(id, {
                        title: p.title,
                        authors: p.authors || [],
                        doi: p.doi || null,
                        url: p.url || null,
                        arxiv_id: p.arxiv_id || null,
                        publish_year: p.year || null,
                        direction_ids: [overview.id],
                      }).then(() => message.success("已放进组里"))
                    }
                  >
                    放进组里
                  </Button>
                </p>
              ))}
              {(overview.frontier?.reading || []).slice(0, 5).map((p) => (
                <p key={p.title} className="fs-muted">可以先看：{p.title}。{p.why}</p>
              ))}
              {(overview.frontier?.authors || []).slice(0, 4).map((a) => (
                <p key={a.name} className="fs-muted">{a.name}{a.institution ? ` · ${a.institution}` : ""} · {a.said}</p>
              ))}
              {!overview.frontier?.papers?.length ? <p className="fs-muted">还没看过外面。点下面更新一次。</p> : null}
              {teacher ? (
                <RouteEditor
                  labId={id}
                  directionId={overview.id}
                  route={overview.frontier?.route || []}
                  onSaved={() => loadOverview(dirId)}
                />
              ) : null}
              {overview.frontier?.outlet_text ? <p className="fs-muted">{overview.frontier.outlet_text}</p> : null}
              {(overview.my_route?.steps || []).length ? (
                <p className="fs-muted">
                  {overview.my_route.note || "你在这条路线上："}
                  {(overview.my_route.note ? " " : "")}
                  {overview.my_route.steps.map((row) => `${row.state || "还没读"}《${row.title}》`).join("；")}
                </p>
              ) : null}
              {!teacher && (overview.frontier?.route || []).length ? (
                <p className="fs-muted">
                  阅读路线：
                  {(overview.frontier.route || []).map((row) => `${row.stage || "入门"}《${row.title}》`).join("；")}
                </p>
              ) : null}
              {teacher ? (
                <Button
                  onClick={() =>
                    api.refreshFrontier(id, overview.id).then(() => {
                      message.success("外面的论文更新了");
                      loadOverview(dirId);
                    }).catch((e) => message.error(formatError(e, "这次没连上外面")))
                  }
                >
                  更新外面的论文
                </Button>
              ) : null}
            </details>
            ) : null}
            {(overview.keywords || []).length ? (
              <Link to={`/labs/${id}/literature?direction_id=${overview.id}`}>
                <Button type={(overview.literature || []).length ? "default" : "primary"}>去找论文</Button>
              </Link>
            ) : null}
            </div>
            <Link to={`/labs/${id}/assistant?direction_id=${overview.id}&q=${encodeURIComponent(`讲讲${overview.name || "这个方向"}在做什么`)}`}>
              问这个方向
            </Link>
            {dirAsk ? <CitedAnswer text={dirAsk} /> : null}
            <div className={pane === "edit" ? "fs-dir-pane" : "fs-dir-pane fs-is-off"}>
            {teacher ? (
              <Collapse
                ghost
                id="dir-edit"
                defaultActiveKey="edit"
                items={[{
                  key: "edit",
                  label: "改这个方向",
                  children: (
                    <>
                      <Form
                        layout="inline"
                        initialValues={{ owner_user_id: overview.owner_user_id || undefined, parent_id: overview.parent_id || undefined }}
                        onFinish={async (v) => {
                          try {
                            await api.patchDirection(overview.id, {
                              owner_user_id: v.owner_user_id || "",
                              parent_id: v.parent_id || "",
                            });
                            message.success("已保存");
                            reload();
                            loadOverview(dirId);
                          } catch (e) {
                            message.error(formatError(e, "没保存上"));
                          }
                        }}
                      >
                        <Form.Item name="owner_user_id">
                          <Select
                            allowClear
                            placeholder="负责人"
                            style={{ width: 180 }}
                            options={members.map((m) => ({ value: m.user_id, label: m.display_name || m.email }))}
                          />
                        </Form.Item>
                        <Form.Item name="parent_id">
                          <Select
                            allowClear
                            placeholder="挂到上一级"
                            style={{ width: 180 }}
                            options={rows.filter((r) => !r.parent_id && r.id !== overview.id).map((r) => ({ value: r.id, label: r.name }))}
                          />
                        </Form.Item>
                        <Button htmlType="submit">保存</Button>
                      </Form>
                      <p className="fs-muted">改关键词后，下次按方向推荐会重新检索外面的论文。打开本页不会立刻去检索。</p>
                    </>
                  ),
                }]}
              />
            ) : null}
            {teacher && (overview.keyword_hints || []).length ? (
              <p className="fs-dir-hints">
                <span className="fs-muted">对不上的篇里反复出现，点一下写进本方向：</span>
                {overview.keyword_hints.map((w) => (
                  <Button
                    key={w}
                    size="small"
                    onClick={async () => {
                      const cur = [...(overview.keywords || [])];
                      if (cur.includes(w)) return;
                      const next = [...cur, w];
                      try {
                        await api.patchDirection(overview.id, { keywords: next });
                        kwForm.setFieldsValue({ keywords: next.join("，") });
                        message.success("已写进本方向");
                        reload();
                        loadOverview(dirId);
                      } catch (e) {
                        message.error(formatError(e, "无法更新关键词"));
                      }
                    }}
                  >
                    {w}
                  </Button>
                ))}
              </p>
            ) : null}
            </div>
            {teacher ? (
            <details className="fs-inline-more">
              <summary>进度和统计</summary>
            <p>
              <Link to={overview.literature_href || `/labs/${id}/literature?direction_id=${overview.id}`}>
                {overview.literature_label || "本方向文献库"}
              </Link>
              {" · "}
              <Link to={overview.recommend_href || `/labs/${id}/literature?direction_id=${overview.id}&tab=recommend`}>组内相关推荐</Link>
              {" · "}
              <Link to={overview.manuscripts_href || `/labs/${id}/manuscripts?direction_id=${overview.id}`}>
                {overview.manuscripts_label || "本方向论文"}
              </Link>
              {" · "}
              <Link to={overview.graph_href || `/labs/${id}/graph?direction_id=${overview.id}`}>引用图谱</Link>
            </p>
            {overview.active_reading_count || overview.want_read_count || overview.method_card_count ? (
              <p className="fs-muted">
                <Link to={overview.reading_href || `/labs/${id}/literature?direction_id=${overview.id}&reading_status=reading&reading_scope=lab`}>
                  本方向有人在读的篇目 {overview.active_reading_count || 0}
                </Link>
                {overview.want_read_count ? (
                  <>
                    {" · "}
                    <Link to={overview.want_read_href || `/labs/${id}/literature?direction_id=${overview.id}&reading_status=want_read&reading_scope=lab`}>
                      想读 {overview.want_read_count}
                    </Link>
                  </>
                ) : null}
                {overview.method_card_count ? ` · 已写方法 ${overview.method_card_count} 篇` : ""}
                {overview.core_pinned_count ? ` · 已钉核心 ${overview.core_pinned_count}` : ""}
              </p>
            ) : null}
            {overview.stuck?.active?.length ? (
              <NextStepList steps={overview.stuck.active} title="谁还没做完" labId={id} canSearch={teacher} />
            ) : (
              <p className="fs-muted">{overview.stuck?.empty_text || "该方向还没有还没做完的事。"}</p>
            )}
            <UnusedTitleChips
              titles={overview.unused_titles || overview.stuck?.unused_titles || []}
              href={overview.unused_research_href || overview.stuck?.unused_research_href || `/labs/${id}/runs?unused=1&direction_id=${overview.id}`}
              more={overview.unused_title_more || overview.stuck?.unused_title_more || 0}
            />
            <UnreadTitleChips
              titles={overview.stuck?.unread_titles}
              href={overview.stuck?.lines?.find?.((x) => x.key === "unread")?.href || `/labs/${id}/literature?direction_id=${overview.id}&reading_status=unread`}
              more={overview.stuck?.unread_title_more || 0}
            />
            <NoCardTitleChips
              titles={overview.stuck?.no_card_titles}
              href={overview.stuck?.lines?.find?.((x) => x.key === "no_card")?.href || `/labs/${id}/literature?direction_id=${overview.id}&has_card=0`}
              more={overview.stuck?.no_card_title_more || 0}
            />
            <RelatedWorkTitleChips
              titles={overview.stuck?.related_work_titles}
              href={overview.stuck?.lines?.find?.((x) => x.key === "related_work")?.href || `/labs/${id}/manuscripts?direction_id=${overview.id}&related_work=0`}
              more={overview.stuck?.related_work_title_more || 0}
            />
            <CoreMissingTitleChips
              titles={overview.stuck?.core_missing_titles}
              href={overview.stuck?.lines?.find?.((x) => x.key === "core")?.href || `/labs/${id}/manuscripts?direction_id=${overview.id}&core_missing=1`}
              more={overview.stuck?.core_missing_title_more || 0}
            />
            <FunnelEmptyHero
              funnel={overview.funnel}
              href={overview.literature_href || `/labs/${id}/literature?direction_id=${overview.id}`}
            />
            {overview.funnel?.empty ? null : <FunnelChart funnel={overview.funnel} />}
            <FunnelLeakChips funnel={overview.funnel} />
            <CoverageRadar coverage={overview.coverage} />
            {teacher && (overview.stuck?.idle_members || []).length ? (
              <div>
                <p className="fs-muted">尚未开读：</p>
                {overview.stuck.idle_members.map((m) => (
                  <div key={m.user_id}>
                    <p>
                      <Link to={m.href}>{m.display_name}{m.title ? ` · ${m.title}` : ""}</Link>
                      {m.literature_href ? (
                        <>
                          {" "}
                          <ActionChip to={m.literature_href}>{m.literature_label || "该成员尚未开读"}</ActionChip>
                        </>
                      ) : null}
                    </p>
                    <UnreadTitleChips
                      mine
                      titles={m.idle_unread_titles}
                      href={m.literature_href}
                      more={m.idle_unread_title_more || 0}
                    />
                  </div>
                ))}
              </div>
            ) : null}
            <p>
              <b>成员</b>
            </p>
            {(overview.members || []).length
              ? overview.members.map((m) => (
                  <p key={m.user_id}>
                    {m.display_name}
                    {m.title ? ` · ${m.title}` : ""} · {m.role === "teacher" ? "导师" : "学生"}
                    {m.membership_status && m.membership_status !== "active"
                      ? ` · ${m.membership_status === "graduated" ? "已毕业" : "休学"}`
                      : ""}
                    {" · "}
                    <Link to={`/labs/${id}/cockpit/members/${m.user_id}`}>进度时间线</Link>
                    {m.last_ai?.question ? (
                      <span className="fs-muted">
                        {" · "}
                        最近{m.last_ai.label || "问"}：{m.last_ai.question}
                        {m.last_ai.ground_why ? ` · ${m.last_ai.ground_why}` : ""}
                        <AgentTraceBox traceId={m.last_ai.trace_id} />
                      </span>
                    ) : null}
                    {m.week_empty_ai?.length
                      ? m.week_empty_ai.map((row) => (
                          <span key={row.trace_id} className="fs-muted">
                            {" · "}
                            本周没查到材料：{row.question}
                            {(row.tools || []).length ? (
                              <span>
                                {" "}
                                · 用过：{(row.tools || []).map(toolLabel).filter(Boolean).join("、")}
                              </span>
                            ) : null}
                            <AgentTraceBox traceId={row.trace_id} />
                          </span>
                        ))
                      : null}
                    {m.week_degraded_ai?.length
                      ? m.week_degraded_ai.map((row) => (
                          <span key={row.trace_id} className="fs-muted">
                            {" · "}
                            本周这次降级了：{row.question}
                            {(row.tools || []).length ? (
                              <span>
                                {" "}
                                · 用过：{(row.tools || []).map(toolLabel).filter(Boolean).join("、")}
                              </span>
                            ) : null}
                            <AgentTraceBox traceId={row.trace_id} />
                          </span>
                        ))
                      : null}
                    {m.week_never_ai ? (
                      <span className="fs-muted">
                        {" · "}
                        本周没问过
                      </span>
                    ) : null}
                  </p>
                ))
              : <p className="fs-muted">还没有人选定该方向。</p>}
            {(overview.recent_ai || []).length ? (
              <div>
                <p>
                  <b>这个方向最近的问 / 辅导</b>
                </p>
                {overview.recent_ai.map((row) => (
                  <div key={row.trace_id} style={{ marginBottom: 8 }}>
                    <p>
                      {row.display_name ? `${row.display_name} · ` : ""}
                      {row.label || (row.feature === "tutor" ? "辅导" : "实验室问")}
                      {row.degraded ? <span className="fs-muted"> · 这次降级了</span> : null}
                      {row.ground_why && !row.degraded ? <span className="fs-muted"> · {row.ground_why}</span> : null}
                      {row.question ? <span className="fs-muted"> · {row.question}</span> : null}
                      {(row.tools || []).length ? (
                        <span className="fs-muted">
                          {" "}
                          · 用过：{(row.tools || []).map(toolLabel).filter(Boolean).join("、")}
                        </span>
                      ) : null}
                    </p>
                    <AgentTraceBox traceId={row.trace_id} />
                  </div>
                ))}
              </div>
            ) : null}
            <p>
              <b>进行中的任务</b>
            </p>
            {(overview.agents || []).length
              ? overview.agents.map((a) => (
                  <p key={a.id}>
                    <Link to={a.href || (a.kind === "reproduce" ? `/reproduces/${a.id}` : `/runs/${a.id}`)}>
                      {a.kind === "reproduce" ? "复现" : "文献调研"} · {a.title || a.id}
                    </Link>{" "}
                    <StatusTag status={a.status} map={RUN_STATUS} />
                    {a.next_step || a.feedback ? <FeedbackStrip row={a} /> : null}
                    <ResumeJob row={a} onDone={() => loadOverview(dirId)} />
                  </p>
                ))
              : <p className="fs-muted">该方向成员还没有进行中的任务。</p>}
            <p>
              <b>文献</b>
            </p>
            {overview.reproduce_spine ? (
              <>
                <p className="fs-muted">
                  本方向复现：已实测 {overview.reproduce_spine.measured ?? 0} · 未跑 {overview.reproduce_spine.not_run ?? 0}
                  {overview.reproduce_spine.not_run_href ? (
                    <>
                      {" "}
                      <Link to={overview.reproduce_spine.not_run_href}>看未跑</Link>
                    </>
                  ) : null}
                </p>
                <NotRunTitleChips
                  titles={overview.reproduce_spine.not_run_titles}
                  href={overview.reproduce_spine.not_run_href}
                  more={overview.reproduce_spine.not_run_title_more || 0}
                />
              </>
            ) : null}
            </details>
            ) : null}
            {(overview.unmatched || []).length ? (
              <div className="fs-compare-items">
                <p className="fs-muted">
                  题名对不上现有方向
                  {overview.unmatched_more ? ` · 另有 ${overview.unmatched_more} 篇` : ""}
                  {" · "}
                  <Link to={overview.unmatched_href || `/labs/${id}/literature`}>
                    {teacher ? "去文献库归纳方向" : "去文献库"}
                  </Link>
                </p>
                {overview.unmatched.map((p) => (
                  <div key={p.id} className="fs-compare-item">
                    <p>
                      <Link to={p.href || literatureHref(p.id, undefined, { direction: overview.id })}>{p.title}</Link>
                    </p>
                  </div>
                ))}
                {!teacher ? <p className="fs-muted">新方向要导师确认后才会出现。</p> : null}
              </div>
            ) : null}
            {(overview.hangable || []).length ? (
              <div className="fs-compare-items">
                <p className="fs-muted">可能属于这里</p>
                {overview.hangable.map((p) => (
                  <div key={p.id} className="fs-compare-item">
                    <p>
                      <Link to={p.href || literatureHref(p.id, undefined, { direction: overview.id })}>{p.title}</Link>
                      {(p.matched || []).length ? (
                        <span className="fs-muted"> （{(p.matched || []).slice(0, 3).join("、")}）</span>
                      ) : null}{" "}
                      <span className="fs-muted">
                        {(p.matched || []).length ? `对上了${(p.matched || []).slice(0, 3).join("、")}。` : ""}
                      </span>{" "}
                      <DirectionHangChips
                        verb="放进来"
                        suggestions={p.direction_suggestions}
                        busyId={hangBusy.startsWith(`${p.id}:`) ? hangBusy.slice(p.id.length + 1) : ""}
                        onHang={async (s) => {
                          if (!p.id || !s?.id || hangBusy) return;
                          setHangBusy(`${p.id}:${s.id}`);
                          try {
                            await api.patchLiterature(p.id, { direction_ids: [s.id] });
                            message.success(`已放进${s.name || "这个方向"}`);
                            loadOverview(dirId);
                          } catch (e) {
                            message.error(formatError(e, "没放进这个方向"));
                          } finally {
                            setHangBusy("");
                          }
                        }}
                      />
                    </p>
                  </div>
                ))}
              </div>
            ) : null}
            <details className="fs-inline-more">
              <summary>学位节点</summary>
            <p>
              <b>学位节点</b>
            </p>
            <p className="fs-muted">开题 / 中期 / 答辩挂在本方向与论文上，不是独立学位页。</p>
            {(overview.milestones || []).length
              ? overview.milestones.map((m) => (
                  <p key={m.id}>
                    {m.kind_label || DEGREE_KIND[m.kind] || m.kind} · {m.title}
                    {dueLabel(m) ? ` · ${dueLabel(m)}` : ""}
                    {m.overdue ? " · 已过期" : ""}
                    {" · "}
                    {teacher || m.owner_id === me?.id ? (
                      <Select
                        size="small"
                        value={m.status}
                        style={{ width: 110 }}
                        options={Object.entries(DEGREE_STATUS).map(([value, label]) => ({ value, label }))}
                        onChange={async (status) => {
                          try {
                            await api.patchMilestone(id, m.id, { status });
                            message.success("已更新节点");
                            loadOverview(dirId);
                          } catch (e) {
                            message.error(formatError(e, "无法更新节点"));
                          }
                        }}
                      />
                    ) : (
                      DEGREE_STATUS[m.status] || m.status
                    )}
                    {m.manuscript_href ? (
                      <>
                        {" · "}
                        <Link to={m.manuscript_href}>论文</Link>
                      </>
                    ) : null}
                  </p>
                ))
              : <p className="fs-muted">还没有学位节点。</p>}
            <Form
              form={mileForm}
              layout="vertical"
              initialValues={{ kind: "proposal" }}
              onFinish={async (v) => {
                try {
                  await api.addMilestone(id, overview.id, {
                    kind: v.kind || "proposal",
                    title: v.title || "",
                    notes: v.notes || "",
                    manuscript_id: v.manuscript_id || undefined,
                    due_at: v.due_at || undefined,
                  });
                  mileForm.resetFields();
                  message.success("已记下学位节点");
                  loadOverview(dirId);
                } catch (e) {
                  message.error(formatError(e, "无法记下节点"));
                }
              }}
            >
              <Form.Item name="kind" label="节点" initialValue="proposal">
                <Select options={Object.entries(DEGREE_KIND).map(([value, label]) => ({ value, label }))} />
              </Form.Item>
              <Form.Item name="title">
                <Input placeholder="标题，可空则用开题/中期/答辩" />
              </Form.Item>
              <Form.Item name="manuscript_id">
                <Select
                  allowClear
                  placeholder="挂到本方向论文（可选）"
                  options={(overview.manuscripts || []).map((m) => ({ value: m.id, label: m.title }))}
                />
              </Form.Item>
              <Form.Item name="due_at" label="截止日期">
                <Input type="date" />
              </Form.Item>
              <Button htmlType="submit" size="small">
                记下节点
              </Button>
            </Form>
            </details>
            <details className="fs-inline-more">
              <summary>还有</summary>
            <p>
              <b>论文</b>
            </p>
            {(overview.manuscripts || []).length ? (
              overview.manuscripts.map((m) => (
              <p key={m.id}>
                <Link to={m.href || `/manuscripts/${m.id}`}>{m.title}</Link> <StatusTag status={m.status} map={MS_STATUS} />
                {m.related_work_written === false ? (
                  <>
                    <span className="fs-muted"> · 未写相关工作</span>{" "}
                    <ActionChip to={writingDeskHref(m.writing_href, id, m.id, "相关工作")}>去写作</ActionChip>
                  </>
                ) : null}
                {m.ungrounded_reproduce ? <span className="fs-muted"> · 写了结果，组里还没跑过</span> : null}
                {m.unused_research?.text ? (
                  <>
                    <span className="fs-muted"> · 综述没提到</span>{" "}
                    <ActionChip to={m.unused_research.href || `/labs/${id}/runs?unused=1`}>打开文献调研</ActionChip>
                  </>
                ) : null}
                {m.core_missing_count ? (
                  <>
                    <span className="fs-muted"> · 这几篇还没写进正文 {m.core_missing_count}</span>{" "}
                    <ActionChip to={`/manuscripts/${m.id}?tab=claims`}>还没写到</ActionChip>
                  </>
                ) : null}
              </p>
              ))
            ) : (
              <p className="fs-muted">该方向还没有论文。</p>
            )}
            <p>
              <b>近期动态</b>
            </p>
            {(overview.recent_events || []).length ? (
              overview.recent_events.map((e) => (
                <p key={e.id} className="fs-muted">
                  <ActivityLine event={e} labId={id} />
                </p>
              ))
            ) : (
              <p className="fs-muted">该方向还没有可判定对象的动态，不是全实验室没有动态。</p>
            )}
            </details>
          </Space>
        ) : null}
      </div>
      ) : null}
    </Space>
  );
}
