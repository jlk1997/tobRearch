import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Collapse, Form, Input, Select, Space, Table, message, Badge, Drawer, Upload } from "antd";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, saveBlob, asItems } from "../api.js";
import { noticeHref, formatError } from "../err.js";
import { ActionChip, AgentTraceBox, AiOverviewCard, AiResultBar, AskWeekChip, ContinueAskChip, CreateDialog, DirectionFilterHint, DirectionSelectEmpty, FeedbackStrip, HintTip, hintTitle, PageHeader, ReproduceChip, ReproduceHonest, ResumeJob, SituationBar, StatusTag, StuckBriefBar, UnusedTitleChips, NotRunTitleChips, aiToolsLine, fmtWhen, LoadFailed, EmptyHint, PageLoading, press, useComposeOpen } from "../ui.jsx";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";
import { CompareBarChart, CoverageRadar, FunnelChart, FunnelEmptyHero, FunnelLeakChips, StackedDirectionChart, TrendLineChart } from "../charts.jsx";

export function Tasks() {
  const { id } = useParams();
  const me = useSession((s) => s.me);
  const [params, setParams] = useSearchParams();
  const openId = params.get("open");
  const assigneeNone = params.get("assignee") === "none";
  const [compose, setCompose] = useComposeOpen(params, setParams);
  const hitRef = useRef(null);
  const [taskForm] = Form.useForm();
  const [rows, setRows] = useState([]);
  const [members, setMembers] = useState([]);
  const [err, setErr] = useState("");
  const [emptyNext, setEmptyNext] = useState(null);
  const [taskBusy, setTaskBusy] = useState("");
  const [creating, setCreating] = useState(false);
  const reload = () => {
    setErr("");
    api
      .tasks(id, assigneeNone ? { assignee: "none" } : undefined)
      .then((r) => {
        setRows(asItems(r.data));
        setEmptyNext(r.data?.empty_next || null);
      })
      .catch((e) => {
        setRows([]);
        setEmptyNext(null);
        setErr(formatError(e, "看板加载失败"));
      });
    api.members(id).then((r) => setMembers(asItems(r.data))).catch((e) => setErr(formatError(e, "成员列表加载失败")));
  };
  useEffect(() => {
    reload();
  }, [id, assigneeNone]);
  useEffect(() => {
    if (openId && hitRef.current) hitRef.current.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [openId, rows]);
  useEffect(() => {
    if (!openId || !rows.length) return;
    if (!rows.some((t) => t.id === openId)) message.warning("找不到该任务（可能未指派给你）");
  }, [openId, rows]);
  const setStatus = async (tid, status) => {
    if (taskBusy) {
      message.info("上一次还没完成");
      return;
    }
    setTaskBusy(`${tid}:${status}`);
    try {
      await press(() => api.patchTask(tid, { status }), { ok: "已更新", fail: "无法改状态" });
      reload();
    } catch {
      /* press already told the user */
    } finally {
      setTaskBusy("");
    }
  };
  const openTask = (tid) => {
    const next = new URLSearchParams(params);
    next.set("open", tid);
    setParams(next, { replace: true });
  };
  const cols = [
    { key: "todo", title: "待办" },
    { key: "doing", title: "进行中" },
    { key: "done", title: "完成" },
  ];
  const bucket = (status) => (["todo", "doing", "done"].includes(status) ? status : "todo");
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        title="看板"
        hint="点按钮改状态。改完会告诉你结果。"
        extra={
          <Button type="primary" onClick={() => setCompose(true)}>
            添加任务
          </Button>
        }
      >
        我的待办
      </PageHeader>
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}
      {emptyNext && !rows.length && !err ? (
        <Alert
          type="info"
          showIcon
          message={emptyNext.title || "本筛选下没有任务"}
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
      <div className="fs-filterbar">
        <Button
          onClick={() => {
            const next = new URLSearchParams(params);
            if (assigneeNone) next.delete("assignee");
            else next.set("assignee", "none");
            setParams(next, { replace: true });
          }}
        >
          {assigneeNone ? "全部任务" : "待认领"}
        </Button>
      </div>
      <CreateDialog open={compose} onClose={() => setCompose(false)} title="添加任务" okText="添加" form={taskForm} loading={creating}>
        <Form
          form={taskForm}
          layout="vertical"
          onFinish={async (v) => {
            setCreating(true);
            try {
              await api.createTask(id, { title: v.title, assignee_id: v.assignee_id });
              taskForm.resetFields();
              setCompose(false);
              message.success("已添加");
              reload();
            } catch (e) {
              message.error(formatError(e, "没添加上"));
            } finally {
              setCreating(false);
            }
          }}
        >
          <Form.Item name="title" label="任务" rules={[{ required: true, message: "请填写任务" }]}>
            <Input placeholder="要做什么" />
          </Form.Item>
          <Form.Item name="assignee_id" label="指派">
            <Select
              allowClear
              placeholder="不选则待认领"
              options={members.map((m) => ({ value: m.user_id, label: m.display_name || m.email }))}
            />
          </Form.Item>
        </Form>
      </CreateDialog>
      <div className="fs-kanban">
        {cols.map((col) => {
          const items = (rows || []).filter((t) => bucket(t.status) === col.key);
          return (
            <Card key={col.key} title={`${col.title} · ${items.length}`}>
              {items.length ? (
                items.map((t) => (
                  <div
                    key={t.id}
                    ref={t.id === openId ? hitRef : undefined}
                    className={t.id === openId ? "fs-kanban-hit" : undefined}
                  >
                    <Card size="small" style={{ marginBottom: 8, cursor: "pointer" }} onClick={() => openTask(t.id)}>
                      <div>{t.title}</div>
                      {t.assignee_name ? <div className="fs-muted">{t.assignee_name}</div> : <div className="fs-muted">待认领</div>}
                      {t.description ? <div className="fs-muted">{t.description}</div> : null}
                      {t.meeting_href ? (
                        <div className="fs-muted">
                          <Link to={t.meeting_href} onClick={(e) => e.stopPropagation()}>
                            {t.source_line ? `来自组会第 ${t.source_line} 行` : "来自组会纪要"}
                          </Link>
                        </div>
                      ) : null}
                      <Space wrap style={{ marginTop: 8 }}>
                        {!t.assignee_id && me?.id ? (
                          <Button
                            size="small"
                            loading={taskBusy === `${t.id}:claim`}
                            onClick={async (e) => {
                              e.stopPropagation();
                              if (taskBusy) {
                                message.info("上一次还没完成");
                                return;
                              }
                              setTaskBusy(`${t.id}:claim`);
                              try {
                                await press(() => api.patchTask(t.id, { assignee_id: me.id }), { ok: "已认领", fail: "无法认领" });
                                reload();
                              } catch {
                                /* told */
                              } finally {
                                setTaskBusy("");
                              }
                            }}
                          >
                            {taskBusy === `${t.id}:claim` ? "正在认领" : "认领"}
                          </Button>
                        ) : null}
                        {col.key !== "todo" && (
                          <Button
                            size="small"
                            loading={taskBusy === `${t.id}:todo`}
                            onClick={(e) => {
                              e.stopPropagation();
                              setStatus(t.id, "todo");
                            }}
                          >
                            {taskBusy === `${t.id}:todo` ? "正在更新" : "待办"}
                          </Button>
                        )}
                        {col.key !== "doing" && (
                          <Button
                            size="small"
                            loading={taskBusy === `${t.id}:doing`}
                            onClick={(e) => {
                              e.stopPropagation();
                              setStatus(t.id, "doing");
                            }}
                          >
                            {taskBusy === `${t.id}:doing` ? "正在更新" : "进行中"}
                          </Button>
                        )}
                        {col.key !== "done" && (
                          <Button
                            size="small"
                            loading={taskBusy === `${t.id}:done`}
                            onClick={(e) => {
                              e.stopPropagation();
                              setStatus(t.id, "done");
                            }}
                          >
                            {taskBusy === `${t.id}:done` ? "正在更新" : "完成"}
                          </Button>
                        )}
                      </Space>
                    </Card>
                  </div>
                ))
              ) : (
                <p className="fs-kanban-empty">本列暂无任务</p>
              )}
            </Card>
          );
        })}
      </div>
    </Space>
  );
}

export function Meetings() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const [compose, setCompose] = useComposeOpen(params, setParams);
  const [meetForm] = Form.useForm();
  const [rows, setRows] = useState([]);
  const [members, setMembers] = useState([]);
  const [open, setOpen] = useState(null);
  const [err, setErr] = useState("");
  const [emptyNext, setEmptyNext] = useState(null);
  const [meetTrace, setMeetTrace] = useState("");
  const [meetSteps, setMeetSteps] = useState([]);
  const [meetDegraded, setMeetDegraded] = useState("");
  const [meetTools, setMeetTools] = useState([]);
  const [meetFollowups, setMeetFollowups] = useState([]);
  const [meetSaving, setMeetSaving] = useState(false);
  const [meetText, setMeetText] = useState("");
  const lastMeet = useRef({ id: "", notes: "", assign: [] });
  const openId = params.get("open");
  const lineNo = Number(params.get("line"));
  const highlightLine = Number.isFinite(lineNo) && lineNo >= 1 ? Math.trunc(lineNo) : 0;
  const reload = () => {
    setErr("");
    api
      .meetings(id)
      .then((r) => {
        setRows(asItems(r.data));
        setEmptyNext(r.data?.empty_next || null);
      })
      .catch((e) => {
        setRows([]);
        setEmptyNext(null);
        setErr(formatError(e, "组会列表加载失败"));
      });
    api.members(id).then((r) => setMembers(asItems(r.data))).catch((e) => setErr(formatError(e, "成员列表加载失败")));
  };
  const closeDrawer = () => {
    setOpen(null);
    if (params.has("open") || params.has("line")) {
      const next = new URLSearchParams(params);
      next.delete("open");
      next.delete("line");
      setParams(next, { replace: true });
    }
  };
  const showMeeting = async (meetingId, keepLine) => {
    try {
      const r = await api.getMeeting(meetingId);
      setOpen(r.data);
      const next = new URLSearchParams(params);
      next.set("open", meetingId);
      if (!keepLine) next.delete("line");
      setParams(next, { replace: true });
    } catch (e) {
      message.error(formatError(e, "无法打开该纪要"));
    }
  };
  useEffect(() => {
    reload();
  }, [id]);
  useEffect(() => {
    if (!openId) return;
    api
      .getMeeting(openId)
      .then((r) => setOpen(r.data))
      .catch((e) => message.error(formatError(e, "无法打开该纪要")));
  }, [openId]);
  useEffect(() => {
    if (!open || !highlightLine) return;
    const el = document.getElementById(`meeting-line-${highlightLine}`);
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [open, highlightLine]);
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        title="组会"
        hint="以「行动:」开头的行会变成待办。"
        extra={
          <Button type="primary" onClick={() => setCompose(true)}>
            写纪要
          </Button>
        }
      >
        这次会定了什么
      </PageHeader>
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}
      {emptyNext && !rows.length && !err ? (
        <Alert
          type="info"
          showIcon
          message={emptyNext.title || "还没有组会"}
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
      <CreateDialog open={compose} onClose={() => setCompose(false)} title="写纪要" okText={meetSaving ? "正在整理" : "生成纪要并派发"} form={meetForm} width={640} loading={meetSaving}>
        <Form
          form={meetForm}
          layout="vertical"
          onFinish={async (v) => {
            setMeetSaving(true);
            try {
              const m = await api.createMeeting(id, { title: v.title, notes_raw: v.notes_raw });
              const r = await api.minutes(m.data.id, v.notes_raw, v.assign_to);
              lastMeet.current = { id: m.data.id, notes: v.notes_raw, assign: v.assign_to || [] };
              if (r.data.trace_id || r.data.session_id) setMeetTrace(r.data.trace_id || r.data.session_id);
              setMeetSteps(r.data.steps || []);
              setMeetDegraded(r.data.degraded || r.data.degraded_to || "");
              setMeetTools(r.data.available_tools || []);
              setMeetFollowups(r.data.followups || []);
              setMeetText(r.data.minutes || v.notes_raw || "");
              meetForm.resetFields();
              setCompose(false);
              if ((r.data.ungrounded || []).length) {
                message.warning(`有 ${r.data.ungrounded.length} 条不是纪要原文，未建任务`);
              } else if (r.data.llm_error) {
                message.warning(r.data.llm_error);
              } else {
                message.success("已生成行动项并派发");
              }
              reload();
            } catch (e) {
              message.error(formatError(e, "没保存上"));
            } finally {
              setMeetSaving(false);
            }
          }}
        >
          <Form.Item name="title" label="标题" rules={[{ required: true, message: "请填写标题" }]}>
            <Input placeholder="这次组会叫什么" />
          </Form.Item>
          <Form.Item name="notes_raw" label="纪要" rules={[{ required: true, message: "请粘贴纪要" }]}>
            <Input.TextArea rows={8} placeholder={"粘贴纪要，用「行动:」开头的行会变成看板任务"} />
          </Form.Item>
          <Form.Item name="assign_to" label="同时指派">
            <Select
              mode="multiple"
              allowClear
              placeholder="可选"
              options={members.map((m) => ({ value: m.user_id, label: m.display_name || m.email }))}
            />
          </Form.Item>
        </Form>
      </CreateDialog>
      <AiResultBar
        text={meetText}
        copyLabel="复制纪要"
        traceId={meetTrace}
        steps={meetSteps}
        degraded={meetDegraded}
        availableTools={meetTools}
        followups={meetFollowups}
        retryLabel="再生成纪要"
        onRetry={
          lastMeet.current.id
            ? async () => {
                try {
                  const r = await api.minutes(lastMeet.current.id, lastMeet.current.notes, lastMeet.current.assign);
                  if (r.data.trace_id || r.data.session_id) setMeetTrace(r.data.trace_id || r.data.session_id);
                  setMeetSteps(r.data.steps || []);
                  setMeetDegraded(r.data.degraded || r.data.degraded_to || "");
                  setMeetTools(r.data.available_tools || []);
                  setMeetFollowups(r.data.followups || []);
                  setMeetText(r.data.minutes || lastMeet.current.notes || "");
                  message.success("已再生成纪要");
                  reload();
                } catch (e) {
                  message.error(formatError(e, "无法再生成纪要"));
                }
              }
            : undefined
        }
      />
      <Table
        className="fs-click-row"
        rowKey="id"
        dataSource={rows}
        locale={{
          emptyText: (
            <EmptyHint>{err ? "组会列表加载失败，可重试。" : emptyNext?.text || "还没有组会。粘贴纪要后会出现在此。"}</EmptyHint>
          ),
        }}
        onRow={(row) => ({
          onClick: () => {
            showMeeting(row.id);
          },
        })}
        columns={[
          {
            title: "标题",
            dataIndex: "title",
            render: (t, row) => (
              <Link
                to={row.href || `/labs/${id}/meetings?open=${row.id}`}
                onClick={(e) => e.stopPropagation()}
              >
                {t}
              </Link>
            ),
          },
          {
            title: "行动",
            dataIndex: "action_count",
            render: (v) => (v ? `${v} 条` : "—"),
          },
          { title: "时间", dataIndex: "held_at", render: fmtWhen },
        ]}
      />
      <Drawer title={open?.title || "纪要"} open={!!open} onClose={closeDrawer} width={480}>
        {open?.llm_error ? <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={open.llm_error} /> : null}
        {(open?.ungrounded || []).length ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="有行动句不是纪要原文，未建任务"
            description={(open.ungrounded || []).map((x) => x.title || x).join("；")}
          />
        ) : null}
        {open?.lines?.length ? (
          <div className="fs-minutes-lines">
            {open.lines.map((ln) => (
              <div
                key={ln.n}
                id={`meeting-line-${ln.n}`}
                className={ln.n === highlightLine ? "fs-minutes-line-hit" : undefined}
              >
                <span className="fs-muted">{ln.n}</span>{" "}
                {ln.task_href ? <Link to={ln.task_href}>{ln.text || "（空行）"}</Link> : ln.text || " "}
              </div>
            ))}
          </div>
        ) : (
          <pre className="fs-pre">{open?.minutes?.[0]?.content_md || open?.notes_raw || "暂无纪要"}</pre>
        )}
        {open?.my_trace_id ? (
          <div style={{ marginTop: 12 }}>
            <AgentTraceBox traceId={open.my_trace_id} />
          </div>
        ) : null}
        {(open?.actions || []).length ? (
          <ul>
            {open.actions.map((a) => (
              <li key={a.id}>
                {a.task_href ? <Link to={a.task_href}>{a.title}</Link> : a.title} <StatusTag status={a.status} />
              </li>
            ))}
          </ul>
        ) : (
          <p className="fs-muted">没有解析到行动项</p>
        )}
      </Drawer>
    </Space>
  );
}

export function Files() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const openId = params.get("open");
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  const [emptyNext, setEmptyNext] = useState(null);
  const [uploading, setUploading] = useState(false);
  const reload = () => {
    setErr("");
    api
      .files(id)
      .then((r) => {
        setRows(asItems(r.data));
        setEmptyNext(r.data?.empty_next || null);
      })
      .catch((e) => {
        setRows([]);
        setEmptyNext(null);
        setErr(formatError(e, "资料库加载失败"));
      });
  };
  useEffect(() => {
    reload();
  }, [id]);
  useEffect(() => {
    if (!openId || !rows.length) return;
    if (!rows.some((r) => r.id === openId)) message.warning("找不到该资料（可能已删除）");
  }, [openId, rows]);
  const openFile = (fid) => {
    const next = new URLSearchParams(params);
    next.set("open", fid);
    setParams(next, { replace: true });
  };
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader title="资料库" hint="同名文件会变成新版本。">
        找到这份文件
      </PageHeader>
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}
      {emptyNext && !rows.length && !err ? (
        <Alert
          type="info"
          showIcon
          message={emptyNext.title || "还没有资料"}
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
      <Upload.Dragger
        showUploadList={false}
        disabled={uploading}
        beforeUpload={async (file) => {
          if (uploading) {
            message.info("正在上传，请稍等");
            return false;
          }
          setUploading(true);
          try {
            await api.uploadFile(id, file);
            message.success("已上传");
            reload();
          } catch (e) {
            message.error(formatError(e, "没上传上"));
          } finally {
            setUploading(false);
          }
          return false;
        }}
      >
        <p>{uploading ? "正在上传" : "点击或拖入文件"}</p>
        <p className="fs-muted">同名会变成新版本，旧的还在。</p>
      </Upload.Dragger>
      <Table
        className="fs-click-row"
        rowKey="id"
        dataSource={rows}
        rowClassName={(row) => (row.id === openId ? "fs-row-hit" : "")}
        onRow={(row) => ({ onClick: () => openFile(row.id) })}
        locale={{
          emptyText: (
            <EmptyHint>{err ? "资料库加载失败，可重试。" : emptyNext?.text || "还没有资料。拖入文件即可。"}</EmptyHint>
          ),
        }}
        columns={[
          {
            title: "文件",
            dataIndex: "filename",
            render: (name, row) => (
              <Link
                to={row.href || `/labs/${id}/files?open=${row.id}`}
                onClick={(e) => e.stopPropagation()}
              >
                {name}
              </Link>
            ),
          },
          { title: "版本", dataIndex: "version" },
          { title: "大小", dataIndex: "size" },
          { title: "上传者", dataIndex: "uploaded_by_name" },
          {
            title: "下载",
            render: (_, row) => (
              <Button
                size="small"
                onClick={async (e) => {
                  e.stopPropagation();
                  try {
                    const r = await api.downloadFile(row.id);
                    saveBlob(r.data, row.filename);
                  } catch (e2) {
                    message.error(formatError(e2, "下载失败"));
                  }
                }}
              >
                下载
              </Button>
            ),
          },
        ]}
      />
    </Space>
  );
}

export function Stats() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const me = useSession((s) => s.me);
  const teacher = roleOf(me, id) === "teacher" || !!me?.is_admin;
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [exporting, setExporting] = useState(false);
  const days = Number(params.get("days") || 30) || 30;
  const dirFilter = params.get("direction_id") || undefined;
  const setDays = (v) => {
    const next = new URLSearchParams(params);
    next.set("days", String(v || 30));
    setParams(next, { replace: true });
  };
  const setDirFilter = (v) => {
    const next = new URLSearchParams(params);
    if (v) next.set("direction_id", v);
    else next.delete("direction_id");
    setParams(next, { replace: true });
  };
  const load = () => {
    setErr("");
    api
      .stats(id, { days, direction_id: dirFilter || undefined })
      .then((r) => setData(r.data))
      .catch((e) => setErr(formatError(e, "统计加载失败")));
  };
  useEffect(() => {
    load();
  }, [id, days, dirFilter]);
  if (err) return <LoadFailed message={err} onRetry={load} />;
  if (!data) return <PageLoading label="正在打开统计…" />;
  const tm = data.this_month || {};
  const statusEntries = Object.entries(data.manuscript_by_status || {});
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        title="统计"
        hint={
          dirFilter
            ? "用量、论文总数和等机器的数字仍是全实验室的。点数字打开对应列表。"
            : "点数字打开对应列表。"
        }
        extra={
          <Button
            loading={exporting}
            onClick={async () => {
              setExporting(true);
              try {
                const r = await api.statsCsv(id, { days, direction_id: dirFilter || undefined });
                saveBlob(r.data, "lab_stats.csv");
                message.success("已导出");
              } catch (e) {
                message.error(formatError(e, "没导出"));
              } finally {
                setExporting(false);
              }
            }}
          >
            {exporting ? "正在导出" : "导出表格"}
          </Button>
        }
      >
        从读到发表有多少
      </PageHeader>
      <Space wrap>
        <Select
          size="small"
          style={{ width: 140 }}
          value={days}
          onChange={setDays}
          options={[
            { value: 7, label: "近 7 天" },
            { value: 30, label: "近 30 天" },
            { value: 90, label: "近 90 天" },
          ]}
        />
        <Select
          size="small"
          allowClear
          style={{ width: 200 }}
          placeholder="按方向"
          value={dirFilter}
          onChange={setDirFilter}
          notFoundContent={<DirectionSelectEmpty labId={id} teacher={teacher} />}
          options={(data.by_direction || []).map((d) => ({
            value: d.direction_id || d.id,
            label: d.parent_id ? `↳ ${d.name}` : d.includes_children ? `${d.name}（含小课题）` : d.name,
          }))}
        />
        <DirectionFilterHint labId={id} teacher={teacher} dirs={data.by_direction || []} />
      </Space>
      <Collapse
        ghost
        items={[
          {
            key: "note",
            label: "这些数字从哪来",
            children: (
              <>
                <SituationBar situation={data.situation} />
                <AiOverviewCard overview={data.ai || data.situation?.ai} />
                <StuckBriefBar brief={data.stuck_brief} />
              </>
            ),
          },
        ]}
      />
      <FunnelEmptyHero funnel={data.funnel} href={data.literature_href || `/labs/${id}/literature`} />
      {data.funnel?.empty ? null : <FunnelChart funnel={data.funnel} />}
      <FunnelLeakChips funnel={data.funnel} />
      <StackedDirectionChart
        rows={data.by_direction}
        note={dirFilter ? data.directions_chart_note || "这张图仍是全实验室各方向。" : ""}
      />
      <TrendLineChart trend={data.trend} />
      <CoverageRadar coverage={data.coverage} />
      <CompareBarChart compare={data.compare_month} />
      <div className="fs-metrics">
        <Link className="fs-metric" to={data.literature_href || `/labs/${id}/literature`}>
          <b>{data.literature_count ?? 0}</b>
          <span>文献</span>
        </Link>
        <Link className="fs-metric" to={data.manuscripts_href || `/labs/${id}/manuscripts`}>
          <b>{data.manuscript_count ?? 0}</b>
          <span>论文</span>
        </Link>
        <Link className="fs-metric" to={data.kb_href || `/labs/${id}/kb`}>
          <b>{data.kb_query_count ?? 0}</b>
          <span>知识库问答</span>
        </Link>
        <Link className="fs-metric is-month" to={tm.reading_started_href || `/labs/${id}/literature?reading_status=reading`}>
          <b>{tm.reading_started ?? 0}</b>
          <span>本月开始阅读</span>
        </Link>
        <Link className="fs-metric is-month" to={tm.submitted_href || `/labs/${id}/manuscripts?status=submitted`}>
          <b>{tm.submitted ?? 0}</b>
          <span>本月在投</span>
        </Link>
        <Link className="fs-metric is-month" to={tm.kb_query_href || `/labs/${id}/kb`}>
          <b>{tm.kb_query ?? 0}</b>
          <span>本月知识库问答</span>
        </Link>
        <Link className="fs-metric" to={data.awaiting_research_href || `/labs/${id}/runs?status=awaiting_gpu`}>
          <b>{data.awaiting_research ?? 0}</b>
          <span>
            等算力
            <HintTip title="等算力">文献调研任务在等实验算力，不是已经失败。</HintTip>
          </span>
        </Link>
        <Link className="fs-metric" to={data.awaiting_reproduce_href || `/labs/${id}/reproduces?status=awaiting_gpu`}>
          <b>{data.awaiting_reproduce ?? 0}</b>
          <span>
            复现等 GPU
            <HintTip title="复现等 GPU">复现任务在等 GPU，不是已经失败。</HintTip>
          </span>
        </Link>
        <Link className="fs-metric" to={data.pending_research_href || `/labs/${id}/runs?status=pending`}>
          <b>{data.pending_research ?? 0}</b>
          <span>已入队</span>
        </Link>
        <Link className="fs-metric" to={data.pending_reproduce_href || `/labs/${id}/reproduces?status=pending`}>
          <b>{data.pending_reproduce ?? 0}</b>
          <span>复现已入队</span>
        </Link>
        <Link className="fs-metric" to={(data.reproduce_spine && data.reproduce_spine.measured_href) || `/labs/${id}/reproduces?status=succeeded`}>
          <b>{data.reproduce_spine?.measured ?? 0}</b>
          <span>已实测</span>
        </Link>
        <Link className="fs-metric" to={(data.reproduce_spine && data.reproduce_spine.not_run_href) || `/labs/${id}/reproduces?status=failed`}>
          <b>{data.reproduce_spine?.not_run ?? 0}</b>
          <span>还没做复现</span>
        </Link>
        <Link className="fs-metric" to={data.manuscripts_ungrounded_href || `/labs/${id}/manuscripts?ungrounded=1`}>
          <b>{data.manuscripts_ungrounded ?? 0}</b>
          <span>
            写成成功
            <HintTip title="写成成功">论文把没做的复现写成已经成功。</HintTip>
          </span>
        </Link>
        <Link className="fs-metric" to={data.unused_research_href || `/labs/${id}/runs?unused=1`}>
          <b>{data.unused_research ?? data.stuck_brief?.unused_research ?? 0}</b>
          <span>综述没提到</span>
        </Link>
        {statusEntries.map(([k, n]) => (
          <Link className="fs-metric" key={k} to={`/labs/${id}/manuscripts?status=${encodeURIComponent(k)}`}>
            <b>{n}</b>
            <span>
              论文 · <StatusTag status={k} />
            </span>
          </Link>
        ))}
      </div>
      <UnusedTitleChips
        titles={data.unused_titles || data.stuck_brief?.unused_titles || []}
        href={data.unused_research_href || `/labs/${id}/runs?unused=1`}
        more={data.unused_title_more || data.stuck_brief?.unused_title_more || 0}
      />
      <NotRunTitleChips
        titles={data.reproduce_spine?.not_run_titles}
        href={data.reproduce_spine?.not_run_href || `/labs/${id}/reproduces?honest=not_run`}
        more={data.reproduce_spine?.not_run_title_more || 0}
      />
      {(data.by_year || []).length ? (
        <Table
          rowKey="year"
          pagination={false}
          dataSource={data.by_year}
          locale={{ emptyText: <EmptyHint>还没有带年份的文献。</EmptyHint> }}
          columns={[
            {
              title: "年份",
              dataIndex: "year",
              render: (y, row) => <Link to={row.href || `/labs/${id}/literature?year=${y}`}>{y}</Link>,
            },
            {
              title: "文献",
              dataIndex: "count",
              render: (n, row) => <Link to={row.href || `/labs/${id}/literature?year=${row.year}`}>{n}</Link>,
            },
          ]}
        />
      ) : null}
      <Table
        rowKey="direction_id"
        dataSource={data.by_direction || []}
        locale={{
          emptyText: (
            <EmptyHint to={`/labs/${id}/directions`}>
              {teacher ? "还没有方向数据。先建研究方向。" : "还没有方向数据。由导师创建后会出现在这里。"}
            </EmptyHint>
          ),
        }}
        columns={[
          {
            title: "方向",
            dataIndex: "name",
            render: (n, row) => <Link to={row.href || `/labs/${id}/directions/${row.direction_id}`}>{n}</Link>,
          },
          {
            title: "文献",
            dataIndex: "literature_count",
            render: (n, row) => <Link to={row.literature_href || `/labs/${id}/literature?direction_id=${row.direction_id}`}>{n}</Link>,
          },
          {
            title: "在读",
            dataIndex: "reading_count",
            render: (n, row) => (
              <Link to={row.reading_href || `/labs/${id}/literature?direction_id=${row.direction_id}&reading_status=reading&reading_scope=lab`}>
                {n ?? 0}
              </Link>
            ),
          },
          {
            title: "想读",
            dataIndex: "want_read_count",
            render: (n, row) => (
              <Link to={row.want_read_href || `/labs/${id}/literature?direction_id=${row.direction_id}&reading_status=want_read&reading_scope=lab`}>
                {n ?? 0}
              </Link>
            ),
          },
          { title: "在投", dataIndex: "submitted_count" },
          {
            title: "发表",
            dataIndex: "published_count",
            render: (n, row) => <Link to={row.manuscripts_href || `/labs/${id}/manuscripts?direction_id=${row.direction_id}`}>{n}</Link>,
          },
          {
            title: hintTitle("已写方法", "打开文献，点「提炼方法」。会抽出这篇的方法。"),
            dataIndex: "method_card_count",
            render: (n, row) => <Link to={row.literature_href || `/labs/${id}/literature?direction_id=${row.direction_id}`}>{n ?? 0}</Link>,
          },
          {
            title: hintTitle("还没写方法", "打开一篇，点「提炼方法」。"),
            dataIndex: "no_card_count",
            render: (n, row) => (
              <Link to={row.no_card_href || `/labs/${id}/literature?direction_id=${row.direction_id}&has_card=0`}>{n ?? 0}</Link>
            ),
          },
          {
            title: hintTitle("写了方法", "已经提炼过「这篇的方法」的文献占比。"),
            dataIndex: "method_card_coverage",
            render: (n) => (n == null ? "—" : `${Math.round(Number(n) * 100)}%`),
          },
          { title: "相关工作已写", dataIndex: "related_work_written" },
          {
            title: "未写相关工作",
            dataIndex: "related_work_missing",
            render: (n, row) => (
              <Link to={row.related_work_missing_href || `/labs/${id}/manuscripts?direction_id=${row.direction_id}&related_work=0`}>
                {n ?? 0}
              </Link>
            ),
          },
          {
            title: hintTitle("核心文献", "这几篇还没写进正文。"),
            dataIndex: "core_missing",
            render: (n, row) => (
              <Link to={row.core_missing_href || `/labs/${id}/manuscripts?direction_id=${row.direction_id}&core_missing=1`}>
                {n ?? 0}
              </Link>
            ),
          },
          {
            title: "无人在读",
            dataIndex: "unread_count",
            render: (n, row) => (
              <Link to={row.unread_href || `/labs/${id}/literature?direction_id=${row.direction_id}&reading_status=unread`}>
                {n ?? 0}
              </Link>
            ),
          },
          {
            title: hintTitle("还没做完", "还没读完、还没写相关工作或还没复现。。"),
            dataIndex: "stuck_count",
            render: (n, row) => <Link to={row.href || `/labs/${id}/directions/${row.direction_id}`}>{n ?? 0}</Link>,
          },
          {
            title: "已实测",
            dataIndex: "reproduce_measured",
            render: (n, row) => <Link to={row.reproduce_href || `/labs/${id}/reproduces`}>{n ?? 0}</Link>,
          },
          {
            title: "还没做复现",
            dataIndex: "reproduce_not_run",
            render: (n, row) => (
              <Link to={row.reproduce_not_run_href || `/labs/${id}/reproduces?status=failed`}>{n ?? 0}</Link>
            ),
          },
          {
            title: hintTitle("写成成功", "论文把没做的复现写成已经成功。"),
            dataIndex: "manuscripts_ungrounded",
            render: (n, row) => (
              <Link to={row.manuscripts_ungrounded_href || `/labs/${id}/manuscripts?ungrounded=1`}>{n ?? 0}</Link>
            ),
          },
        ]}
      />
      {teacher ? <Table
        rowKey="user_id"
        dataSource={data.by_member || []}
        locale={{
          emptyText: (
            <EmptyHint to={data.members_empty_next?.href || `/labs/${id}/members`}>
              {data.members_empty_text || "还没有成员数据。"}
            </EmptyHint>
          ),
        }}
        columns={[
          {
            title: "成员",
            dataIndex: "display_name",
            render: (v, row) => <Link to={row.href || `/labs/${id}/cockpit/members/${row.user_id}`}>{v || row.user_id}</Link>,
          },
          { title: "职称/年级", dataIndex: "title", render: (t) => t || "—" },
          {
            title: "在读",
            dataIndex: "reading_count",
            render: (n, row) => (
              <Link to={row.literature_href || `/labs/${id}/literature?reader_id=${row.user_id}&reading_status=${n ? "reading" : "unread"}`}>
                {n ?? 0}
              </Link>
            ),
          },
          {
            title: "想读",
            dataIndex: "want_read_count",
            render: (n, row) => (
              <Link to={row.want_read_href || `/labs/${id}/literature?reader_id=${row.user_id}&reading_status=want_read`}>
                {n ?? 0}
              </Link>
            ),
          },
          {
            title: "论文",
            dataIndex: "manuscript_count",
            render: (n, row) => (
              <Link to={row.manuscripts_href || `/labs/${id}/manuscripts?owner_id=${row.user_id}`}>{n ?? 0}</Link>
            ),
          },
          {
            title: "未写相关工作",
            dataIndex: "related_work_missing",
            render: (n, row) => (
              <Link to={row.related_work_href || `/labs/${id}/manuscripts?related_work=0&owner_id=${row.user_id}`}>{n ?? 0}</Link>
            ),
          },
          {
            title: hintTitle("核心文献", "这几篇还没写进正文。"),
            dataIndex: "core_missing",
            render: (n, row) => (
              <Link to={row.core_missing_href || `/labs/${id}/manuscripts?core_missing=1&owner_id=${row.user_id}`}>{n ?? 0}</Link>
            ),
          },
          {
            title: hintTitle("写成成功", "论文把没做的复现写成已经成功。"),
            dataIndex: "ungrounded",
            render: (n, row) => (
              <Link to={row.ungrounded_href || `/labs/${id}/manuscripts?ungrounded=1&owner_id=${row.user_id}`}>{n ?? 0}</Link>
            ),
          },
          {
            title: hintTitle("综述没提到", "文献调研正文没点到这些篇。"),
            dataIndex: "unused_research",
            render: (n, row) => (
              <Link to={row.unused_research_href || `/labs/${id}/runs?unused=1&requester_id=${row.user_id}`}>{n ?? 0}</Link>
            ),
          },
          {
            title: hintTitle("还没做完", data.stuck_count_note || "还没读完、还没写相关工作或还没复现。。"),
            dataIndex: "stuck_count",
            render: (n, row) => <Link to={row.href || `/labs/${id}/cockpit/members/${row.user_id}`}>{n ?? 0}</Link>,
          },
          { title: "发表", dataIndex: "published_count" },
          {
            title: hintTitle("本周开读", "该成员本周开始阅读的次数。计数，不是评语。"),
            dataIndex: "week_reading_started",
            render: (n, row) => (
              <>
                <Link to={row.literature_href || `/labs/${id}/literature?reader_id=${row.user_id}`}>{n ?? 0}</Link>
                {row.week_reading_titles?.[0]?.title ? (
                  <span className="fs-muted"> · {(row.week_reading_titles[0].title || "").slice(0, 16)}</span>
                ) : null}
              </>
            ),
          },
          {
            title: hintTitle("本周出卡", "该成员本周写出全文这篇的方法的篇数。摘要卡不算。"),
            dataIndex: "week_cards_made",
            render: (n, row) => (
              <>
                {n ?? 0}
                {row.week_card_titles?.[0]?.title ? (
                  <span className="fs-muted"> · {(row.week_card_titles[0].title || "").slice(0, 16)}</span>
                ) : null}
                {row.weak_card_titles?.[0]?.title ? (
                  <span className="fs-muted">
                    {" · 未写出 "}
                    <Link to={row.weak_card_titles[0].href || `/literature/${row.weak_card_titles[0].id}?panel=card`}>
                      {(row.weak_card_titles[0].title || "").slice(0, 16)}
                    </Link>
                  </span>
                ) : null}
              </>
            ),
          },
          {
            title: hintTitle("本周笔记", "该成员本周写下的笔记条数。"),
            dataIndex: "week_notes",
            render: (n, row) => (
              <>
                {n ?? 0}
                {row.week_note_titles?.[0]?.title ? (
                  <span className="fs-muted"> · {(row.week_note_titles[0].title || "").slice(0, 16)}</span>
                ) : null}
              </>
            ),
          },
          {
            title: hintTitle("本周写稿", "该成员本周改过的论文篇数。旁注题名点进现有论文。"),
            dataIndex: "week_manuscripts",
            render: (n, row) => (
              <>
                <Link to={row.manuscripts_href || `/labs/${id}/manuscripts?owner_id=${row.user_id}`}>{n ?? 0}</Link>
                {row.week_ms_titles?.[0]?.title ? (
                  <span className="fs-muted">
                    {" · "}
                    <Link to={row.week_ms_titles[0].href || `/manuscripts/${row.week_ms_titles[0].id}`}>
                      {(row.week_ms_titles[0].title || "").slice(0, 16)}
                    </Link>
                  </span>
                ) : null}
              </>
            ),
          },
          {
            title: hintTitle("本周辅导", "该成员本周提问辅导的次数。计数，不是评语。"),
            dataIndex: "week_tutor",
            render: (n, row) => (
              <>
                {n ?? 0}
                {row.week_tutor_titles?.[0]?.title ? (
                  <span className="fs-muted"> · {(row.week_tutor_titles[0].title || "").slice(0, 16)}</span>
                ) : null}
              </>
            ),
          },
          { title: "模型调用", dataIndex: "ai_calls", render: (n) => (n == null ? "—" : n) },
          { title: "用量", dataIndex: "tokens", render: (n) => (n == null ? "—" : n) },
          {
            title: hintTitle("最近问", "最近一次问或辅导的原话。本周没查到材料、这次降级了也写在这里。"),
            key: "last_ai",
            render: (_, row) => {
              const last = row.last_ai;
              const empty = row.week_empty_ai || [];
              const degraded = row.week_degraded_ai || [];
              if (!last?.question && !empty.length && !degraded.length && !row.week_never_ai) return "—";
              return (
                <span onClick={(e) => e.stopPropagation()}>
                  {row.week_never_ai ? (
                    <div className="fs-muted">
                      本周没问过 <AskWeekChip meId={me?.id} userId={row.user_id} labId={id} />
                    </div>
                  ) : null}
                  {last?.question ? (
                    <span className="fs-muted">
                      {last.question}
                      {last.ground_why ? ` · ${last.ground_why}` : ""}
                      {aiToolsLine(last.tools)}
                      <AgentTraceBox traceId={last.trace_id} />
                      <ContinueAskChip last={last} meId={me?.id} />
                    </span>
                  ) : null}
                  {empty.map((x) => (
                    <div key={x.trace_id} className="fs-muted">
                      本周没查到材料：{x.question}
                      {aiToolsLine(x.tools)}
                      <AgentTraceBox traceId={x.trace_id} />
                    </div>
                  ))}
                  {degraded.map((x) => (
                    <div key={x.trace_id} className="fs-muted">
                      本周这次降级了：{x.question}
                      {aiToolsLine(x.tools)}
                      <AgentTraceBox traceId={x.trace_id} />
                    </div>
                  ))}
                </span>
              );
            },
          },
        ]}
      /> : <p className="fs-muted">成员对照只给导师看。</p>}
    </Space>
  );
}

export function WorkLogs() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const openId = params.get("open");
  const [form] = Form.useForm();
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  const [emptyNext, setEmptyNext] = useState(null);
  const [saving, setSaving] = useState(false);
  const reload = () => {
    setErr("");
    api
      .workLogs(id)
      .then((r) => {
        setRows(asItems(r.data));
        setEmptyNext(r.data?.empty_next || null);
      })
      .catch((e) => setErr(formatError(e, "日志加载失败")));
  };
  useEffect(() => {
    reload();
  }, [id]);
  useEffect(() => {
    if (!openId || !rows.length) return;
    if (!rows.some((r) => r.id === openId)) message.warning("找不到该日志");
  }, [openId, rows]);
  const openLog = (lid) => {
    const next = new URLSearchParams(params);
    next.set("open", lid);
    setParams(next, { replace: true });
  };
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader title="工作日志" hint="自愿写。不是考勤。">
        想记就写一句
      </PageHeader>
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}
      {emptyNext && !rows.length ? (
        <Alert
          type="info"
          showIcon
          message={emptyNext.title || "还没有日志"}
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
      <Form
        form={form}
        onFinish={async (v) => {
          setSaving(true);
          try {
            await api.addWorkLog(id, v.content);
            form.resetFields();
            message.success("已记下");
            reload();
          } catch (e) {
            message.error(formatError(e, "没记下"));
          } finally {
            setSaving(false);
          }
        }}
      >
        <Form.Item name="content" rules={[{ required: true, message: "请填写进展" }]}>
          <Input.TextArea rows={4} placeholder="想记就写一句" />
        </Form.Item>
        <Button htmlType="submit" type="primary" loading={saving}>
          {saving ? "正在记下" : "记下"}
        </Button>
      </Form>
      <Table
        className="fs-click-row"
        rowKey="id"
        dataSource={rows}
        rowClassName={(row) => (row.id === openId ? "fs-row-hit" : "")}
        onRow={(row) => ({ onClick: () => openLog(row.id) })}
        locale={{ emptyText: <EmptyHint>{emptyNext?.text || "还没有日志。写三句话即可。"}</EmptyHint> }}
        columns={[
          {
            title: "内容",
            dataIndex: "content",
            render: (text, row) => (
              <Link
                to={row.href || `/labs/${id}/work-logs?open=${row.id}`}
                onClick={(e) => e.stopPropagation()}
              >
                {text}
              </Link>
            ),
          },
          { title: "作者", dataIndex: "display_name" },
          { title: "时间", dataIndex: "created_at", render: fmtWhen },
        ]}
      />
    </Space>
  );
}

export function Notifications() {
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  const [readingAll, setReadingAll] = useState(false);
  const setUnread = useSession((s) => s.setUnread);
  const setUnreadErr = useSession((s) => s.setUnreadErr);
  const refreshUnread = () =>
    api
      .unreadCount()
      .then((r) => setUnread(r.data.unread || 0))
      .catch((e) => setUnreadErr(formatError(e, "未读数加载失败")));
  const reload = () =>
    api
      .notifications()
      .then((r) => {
        setRows(asItems(r.data));
        setErr("");
      })
      .catch((e) => setErr(formatError(e, "通知加载失败")));
  useEffect(() => {
    reload();
  }, []);
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        title="通知"
        hint="未读会同步到顶栏铃铛。"
        extra={
          <Button
            loading={readingAll}
            onClick={async () => {
              if (readingAll) return;
              setReadingAll(true);
              try {
                await api.readAllNotifications();
                setUnread(0);
                message.success("已全部标为已读");
                reload();
              } catch (e) {
                message.error(formatError(e, "没标上"));
              } finally {
                setReadingAll(false);
              }
            }}
          >
            {readingAll ? "正在标已读" : "全部已读"}
          </Button>
        }
      >
        点标题打开对应内容。
      </PageHeader>
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}
      <Table
        className="fs-listish"
        rowKey="id"
        dataSource={rows}
        locale={{ emptyText: <EmptyHint>没有通知。组内事件会出现在这里。</EmptyHint> }}
        columns={[
          {
            title: "标题",
            dataIndex: "title",
            render: (t, row) => (
              <span>
                <Link
                  to={noticeHref(row)}
                  onClick={async () => {
                    if (!row.is_read) {
                      try {
                        await api.readNotification(row.id);
                        refreshUnread();
                        reload();
                      } catch (e) {
                        message.error(formatError(e, "无法标已读"));
                      }
                    }
                  }}
                >
                  {!row.is_read && <Badge status="processing" />} {t}
                </Link>
                {row.next_step || row.feedback ? <FeedbackStrip row={row} /> : null}
                {(row.followups || []).map((f) => (
                  <ActionChip
                    key={f.href || f.label}
                    to={f.href}
                    onClick={async () => {
                      if (!row.is_read) {
                        try {
                          await api.readNotification(row.id);
                          refreshUnread();
                          reload();
                        } catch (e) {
                          message.error(formatError(e, "无法标已读"));
                        }
                      }
                    }}
                  >
                    {f.label}
                  </ActionChip>
                ))}
                {row.unused_titles?.length ? (
                  <UnusedTitleChips titles={row.unused_titles} href={row.unused_research_href} more={row.unused_title_more || 0} />
                ) : null}
                <ReproduceHonest ev={row.latest_reproduce} />
                {row.ungrounded_reproduce ? <span className="fs-muted"> · 写了结果，组里还没跑过</span> : null}
                <ResumeJob row={row} onDone={reload} />
                {row.reproduce_href || row.latest_reproduce?.href ? (
                  <>
                    {" "}
                    <ReproduceChip ev={row.latest_reproduce} to={row.latest_reproduce?.href || row.reproduce_href} />
                  </>
                ) : null}
              </span>
            ),
          },
          { title: "内容", dataIndex: "content" },
          { title: "时间", dataIndex: "created_at", render: fmtWhen },
          {
            title: "操作",
            render: (_, row) =>
              row.is_read ? null : (
                <Button
                  size="small"
                  onClick={async () => {
                    await api.readNotification(row.id);
                    refreshUnread();
                    reload();
                  }}
                >
                  已读
                </Button>
              ),
          },
        ]}
      />
    </Space>
  );
}
