import { useEffect, useRef, useState } from "react";
import { Button, Card, Collapse, Form, Input, Select, Space, Steps, Table, Tag, message, Alert } from "antd";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, consumeSse, saveBlob, asItems } from "../api.js";
import { formatError, humanize, ERROR_CLASS_LABEL, SUBGRAPH_LABEL, RUN_STATUS, REPRO_STAGE_LABEL, runWaitHint, literatureHref } from "../err.js";
import { ActionChip, AiResultBar, AnalyzeChip, CreateDialog, DirectionFilterHint, DirectionSelectEmpty, EmptyHint, FeedbackStrip, MdPreview, ModelKeyHint, NotRunTitleChips, PageHeader, PageLoading, PressButton, ReproduceHonest, ResumeJob, StatusTag, EventFeed, MethodCard, LoadFailed, StreamDropped, feedbackAlertType, jobBlockedReason, tellFeedback, UnusedTitleChips, unusedScopeLead, useComposeOpen, whenBlocked } from "../ui.jsx";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";

const LIVE_STATUSES = new Set(["running", "pending", "queued", "paused", "awaiting_gpu", "blocked"]);

const PREFLIGHT_BRIEF = {
  gpu: "实验算力关着。可以先跑文献综述，不会标成做完。",
  worker: "后台还没开工。创建后会排队。",
  engine: "调研程序还没装好。创建后会停住，并说明缺什么。",
  method_card: "还没抽出这篇的方法。仍可以创建。",
  dataset: "还没选数据集。缺数据会停住。",
  docker: "这台机器没有 Docker。跑起来会停住。",
};

function PreflightBrief({ missing }) {
  if (!missing?.length) return null;
  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 12 }}
      message="还可以创建。缺的东西会让它中途停住，不会假装做完。"
      description={
        <>
          {missing.map((m) => (
            <p key={m.key || m.label} style={{ margin: "4px 0" }}>
              {PREFLIGHT_BRIEF[m.key] || m.label}
              {m.href ? (
                <>
                  {" "}
                  <ActionChip to={m.href}>去看</ActionChip>
                </>
              ) : null}
            </p>
          ))}
          <details>
            <summary>给管理员看</summary>
            {missing.map((m) => (
              <p key={`raw-${m.key || m.label}`} className="fs-muted">
                {m.label}：{m.text}
                {m.admin_href ? (
                  <>
                    {" "}
                    <ActionChip to={m.admin_href}>管理页</ActionChip>
                  </>
                ) : null}
              </p>
            ))}
          </details>
        </>
      }
    />
  );
}

function artifactTable(items, download) {
  return (
    <Table
      rowKey="path"
      size="small"
      dataSource={items || []}
      pagination={false}
      locale={{ emptyText: "还没有文件" }}
      columns={[
        {
          title: "文件",
          dataIndex: "path",
          render: (p) =>
            String(p || "").includes("reproduce_gaps.md") ? (
              <span>
                {p} <span className="fs-muted">差在哪</span>
              </span>
            ) : String(p || "").includes("search_hits.jsonl") ? (
              <span>
                {p} <span className="fs-muted">查到的论文</span>
              </span>
            ) : (
              p
            ),
        },
        { title: "大小", dataIndex: "size" },
        {
          title: "下载",
          render: (_, row) => (
            <Button
              size="small"
              onClick={() =>
                download(row.path, row.name || row.path)
                  .then(() => message.success("已下载"))
                  .catch((e) => message.error(formatError(e, "没下载下来")))
              }
            >
              下载
            </Button>
          ),
        },
      ]}
    />
  );
}

export function Runs() {
  const { id } = useParams();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const datasetId = params.get("dataset_id") || undefined;
  const status = params.get("status") || undefined;
  const unused = params.get("unused") || undefined;
  const directionId = params.get("direction_id") || undefined;
  const requesterId = params.get("requester_id") || undefined;
  const [rows, setRows] = useState([]);
  const [papers, setPapers] = useState([]);
  const [datasets, setDatasets] = useState([]);
  const [err, setErr] = useState("");
  const [creating, setCreating] = useState(false);
  const [emptyNext, setEmptyNext] = useState(null);
  const [preflight, setPreflight] = useState(null);
  const [preflightErr, setPreflightErr] = useState("");
  const [preflightTick, setPreflightTick] = useState(0);
  const [form] = Form.useForm();
  const [compose, setCompose] = useComposeOpen(params, setParams);
  const unusedOn = unused === "1";
  const statusHero =
    unusedOn
      ? null
      : status === "awaiting_gpu"
        ? { title: "在等机器的文献调研", lead: "在等算力，不是已经失败。" }
        : status === "pending" || status === "queued"
          ? { title: "已入队的文献调研", lead: "还在排队。" }
          : status === "failed"
            ? { title: "失败的文献调研", lead: "这些任务没跑完。" }
            : status === "paused"
              ? { title: "已暂停的文献调研", lead: "点进去可以恢复。" }
              : status === "blocked"
                ? { title: "缺数据的文献调研", lead: "缺数据集，卡住了。" }
                : datasetId
                  ? { title: "该数据集的文献调研", lead: "只用了这个数据集。" }
                  : null;
  const [unusedScope, setUnusedScope] = useState(null);
  const [unusedTitles, setUnusedTitles] = useState([]);
  const [unusedTitleMore, setUnusedTitleMore] = useState(0);
  const formDs = Form.useWatch("dataset_id", form);
  const formPath = Form.useWatch("dataset_path", form);
  const reload = () => {
    setErr("");
    api
      .researchRuns(id, {
        dataset_id: datasetId,
        status,
        unused: unusedOn ? "1" : undefined,
        direction_id: unusedOn ? directionId : undefined,
        requester_id: unusedOn ? requesterId : undefined,
      })
      .then((r) => {
        setRows(asItems(r.data));
        setEmptyNext(r.data?.empty_next || null);
        if (r.data?.preflight) setPreflight(r.data.preflight);
        setUnusedScope(r.data?.unused_scope || null);
        setUnusedTitles(r.data?.unused_titles || []);
        setUnusedTitleMore(r.data?.unused_title_more || 0);
      })
      .catch((e) => setErr(formatError(e, "任务列表加载失败")));
  };
  useEffect(() => {
    reload();
    api.literature(id).then((r) => setPapers(asItems(r.data))).catch((e) => setErr(formatError(e, "文献列表加载失败")));
    api.datasets(id).then((r) => setDatasets(asItems(r.data))).catch((e) => setErr(formatError(e, "数据集列表加载失败")));
  }, [id, datasetId, status, unused, directionId, requesterId]);
  useEffect(() => {
    if (!id) return undefined;
    let alive = true;
    api
      .researchPreflight(id, {
        dataset_id: formDs || datasetId || undefined,
        dataset_path: formPath || undefined,
      })
      .then((r) => {
        if (alive) {
          setPreflight(r.data);
          setPreflightErr("");
          if (r.data?.suggest_interrupt_after && !form.getFieldValue("interrupt_after")) {
            form.setFieldValue("interrupt_after", r.data.suggest_interrupt_after);
          }
        }
      })
      .catch((e) => {
        if (alive) {
          setPreflight(null);
          setPreflightErr(formatError(e, "创建前诊断加载失败"));
        }
      });
    return () => {
      alive = false;
    };
  }, [id, formDs, formPath, datasetId, preflightTick]);
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        tone={unusedOn || statusHero ? "unused" : undefined}
        title={unusedOn ? emptyNext?.title || "正在看：综述没提到的论文" : statusHero?.title || "文献调研"}
        hint={
          unusedOn && status
            ? "综述没提到和状态不能一起筛。打开一个会丢掉另一个。"
            : unusedOn
              ? "综述写完了，但这几篇没写进去。"
              : "提一个问题，自动查文献、写综述。可以暂停、恢复、取消。"
        }
        extra={
          <Button type="primary" onClick={() => setCompose(true)}>
            创建文献调研
          </Button>
        }
      >
        {unusedOn && status
          ? "这两个筛不能一起开。"
          : unusedOn
            ? unusedScopeLead(unusedScope)
            : statusHero?.lead || "提一个问题，自动写综述。"}
        {unusedOn || statusHero ? (
          <>
            {" "}
            <ActionChip to={`/labs/${id}/runs`}>全部文献调研</ActionChip>
            {unusedOn && (directionId || requesterId) ? <ActionChip to={`/labs/${id}/runs?unused=1`}>全部没提到的</ActionChip> : null}
          </>
        ) : null}
      </PageHeader>
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}
      <ModelKeyHint err={err} />
      {unusedOn ? (
        <UnusedTitleChips titles={unusedTitles} href={`/labs/${id}/runs?unused=1`} more={unusedTitleMore} rows={rows} />
      ) : null}
      {emptyNext && !rows.length ? (
        <Alert
          type="info"
          showIcon
          message={emptyNext.title || "本筛选下没有文献调研"}
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
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 160 }}
          value={status}
          onChange={(v) => {
            const next = new URLSearchParams(params);
            if (v) {
              next.set("status", v);
              next.delete("unused");
              next.delete("direction_id");
              next.delete("requester_id");
            } else next.delete("status");
            setParams(next, { replace: true });
          }}
          options={Object.entries(RUN_STATUS).map(([value, label]) => ({ value, label }))}
        />
        <Select
          allowClear
          placeholder="综述没提到"
          style={{ width: 160 }}
          value={unused}
          onChange={(v) => {
            const next = new URLSearchParams(params);
            if (v) {
              next.set("unused", v);
              next.delete("status");
            } else {
              next.delete("unused");
              next.delete("direction_id");
              next.delete("requester_id");
            }
            setParams(next, { replace: true });
          }}
          options={[{ value: "1", label: "综述没提到" }]}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="按数据集筛选"
          style={{ width: 240 }}
          value={datasetId}
          onChange={(v) => {
            const next = new URLSearchParams(params);
            if (v) next.set("dataset_id", v);
            else next.delete("dataset_id");
            setParams(next, { replace: true });
          }}
          options={datasets.map((d) => ({ value: d.id, label: d.name }))}
        />
        {unusedOn && unusedScope?.requester_name ? <Tag>{unusedScope.requester_name}</Tag> : null}
        {unusedOn && unusedScope?.direction_name ? <Tag>方向 {unusedScope.direction_name}</Tag> : null}
      </div>
      <CreateDialog
        open={compose}
        onClose={() => setCompose(false)}
        title="创建文献调研"
        okText="创建"
        form={form}
        width={640}
        loading={creating}
        extra={
          <>
            {preflightErr ? <LoadFailed message={preflightErr} onRetry={() => setPreflightTick((n) => n + 1)} /> : null}
            <PreflightBrief missing={preflight?.missing} />
          </>
        }
      >
        <Form
          form={form}
          layout="vertical"
          key={datasetId || "all"}
          initialValues={{ dataset_id: datasetId, domain: "general" }}
          onFinish={async (v) => {
            if (creating) return;
            setCreating(true);
            try {
              const r = await api.createRun(id, {
                question: v.question,
                dataset_id: v.dataset_id || undefined,
                dataset_path: v.dataset_path || "",
                literature_item_ids: v.literature_item_ids || [],
                domain: v.domain || "general",
                interrupt_after: v.interrupt_after || "",
              });
              tellFeedback(r.data, "已创建");
              setCompose(false);
              if (r.data.id) nav(`/runs/${r.data.id}`);
              else reload();
            } catch (e) {
              message.error(formatError(e, "无法创建任务"));
            } finally {
              setCreating(false);
            }
          }}
        >
          <Form.Item name="question" label="研究问题" rules={[{ required: true, message: "请填写研究问题" }]}>
            <Input placeholder="想查什么" />
          </Form.Item>
          <Form.Item name="literature_item_ids" label="关联文献">
            <Select mode="multiple" allowClear placeholder="可选" options={papers.map((p) => ({ value: p.id, label: p.title }))} />
          </Form.Item>
          <p className="fs-muted">没选文献时，会带上你这个方向里的论文。</p>
          <Form.Item name="dataset_id" label="数据集">
            <Select allowClear placeholder="可选" options={datasets.map((d) => ({ value: d.id, label: d.name }))} />
          </Form.Item>
          <Form.Item name="domain" label="领域" initialValue="general">
            <Select
              options={[
                { value: "general", label: "通用" },
                { value: "medical", label: "医学" },
                { value: "management", label: "经管" },
                { value: "materials", label: "材料" },
              ]}
            />
          </Form.Item>
          <Collapse
            ghost
            items={[
              {
                key: "adv",
                label: "进阶（路径与暂停点）",
                children: (
                  <>
                    <Form.Item name="dataset_path" label="只读数据路径">
                      <Input placeholder="可选" />
                    </Form.Item>
                    <Form.Item name="interrupt_after" label="暂停点">
                      <Select
                        allowClear
                        placeholder="跑完某阶段后暂停（可选）"
                        options={Object.entries(SUBGRAPH_LABEL).map(([value, label]) => ({ value, label: `暂停于${label}` }))}
                      />
                    </Form.Item>
                  </>
                ),
              },
            ]}
          />
        </Form>
      </CreateDialog>
      <Table
        className="fs-click-row"
        rowKey="id"
        dataSource={rows}
        locale={{ emptyText: <EmptyHint>{emptyNext?.text || "还没有文献调研。提交一个研究问题开始。"}</EmptyHint> }}
        onRow={(row) => ({ onClick: () => nav(`/runs/${row.id}`) })}
        columns={[
          { title: "问题", dataIndex: "question" },
          { title: "状态", dataIndex: "status", render: (s) => <StatusTag status={s} /> },
          {
            title: "文献",
            render: (_, row) =>
              (row.literature || []).length
                ? row.literature.map((p) => (
                    <span key={p.id} style={{ marginRight: 8 }} onClick={(e) => e.stopPropagation()}>
                      <Link to={p.href || literatureHref(p.id, p.last_page_no)}>{p.title || "文献"}</Link>{" "}
                      <ActionChip
                        to={p.reproduce_href || `/labs/${row.lab_id}/reproduces?literature_item_id=${encodeURIComponent(p.id)}`}
                        tone="gold"
                      >
                        去复现
                      </ActionChip>
                    </span>
                  ))
                : "—",
          },
          {
            title: "数据集",
            render: (_, row) =>
              row.dataset?.id || row.dataset_id ? (
                <Link to={`/datasets/${row.dataset?.id || row.dataset_id}`} onClick={(e) => e.stopPropagation()}>
                  {row.dataset?.name || "数据集"}
                </Link>
              ) : (
                "—"
              ),
          },
          { title: "错误", dataIndex: "error_class", render: (c) => ERROR_CLASS_LABEL[c] || c },
          {
            title: "下一步",
            dataIndex: "next_step",
            render: (v, row) => <FeedbackStrip row={{ ...row, next_step: v || row.diagnosis?.next_step || row.next_step }} />,
          },
          {
            title: "恢复",
            render: (_, row) => (
              <span onClick={(e) => e.stopPropagation()}>
                <ResumeJob row={{ ...row, kind: "research" }} onDone={reload} />
              </span>
            ),
          },
          { title: "可恢复", dataIndex: "resumable", render: (v) => (v ? "是" : "") },
        ]}
      />
    </Space>
  );
}

export function RunDetail() {
  const { id } = useParams();
  const me = useSession((s) => s.me);
  const [run, setRun] = useState(null);
  const [err, setErr] = useState("");
  const [streamOff, setStreamOff] = useState(false);
  const [streamGen, setStreamGen] = useState(0);
  const [resumeBusy, setResumeBusy] = useState(false);
  const [pauseBusy, setPauseBusy] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [rerunBusy, setRerunBusy] = useState(false);
  const [params, setParams] = useSearchParams();
  const urlAsk = params.get("ask") || "";
  const [reviewQ, setReviewQ] = useState("");
  const [reviewTurns, setReviewTurns] = useState([]);
  const [reviewSession, setReviewSession] = useState(urlAsk);
  const [reviewBusy, setReviewBusy] = useState(false);
  const restoredAsk = useRef("");
  const runRef = useRef(null);
  runRef.current = run;
  const load = () =>
    api
      .getRun(id)
      .then((r) => {
        setRun(r.data);
        setErr("");
      })
      .catch((e) => setErr(formatError(e, "任务加载失败")));
  useEffect(() => {
    setReviewQ("");
    setReviewTurns([]);
    setReviewSession(params.get("ask") || "");
    restoredAsk.current = "";
    load();
    setStreamOff(false);
    const ac = new AbortController();
    consumeSse(
      `/research-runs/${id}/stream`,
      (event, data) => {
        if (data && typeof data === "object") {
          setRun((prev) => ({ ...(prev || {}), ...data, messages: data.messages || prev?.messages, artifacts: prev?.artifacts }));
        }
        if (event === "done") load();
      },
      { signal: ac.signal }
    )
      .then(() => {
        if (LIVE_STATUSES.has(runRef.current?.status)) setStreamOff(true);
      })
      .catch((e) => {
        if (e?.name === "AbortError") return;
        setStreamOff(true);
      });
    const t = setInterval(load, 8000);
    return () => {
      ac.abort();
      clearInterval(t);
    };
  }, [id, streamGen]);
  useEffect(() => {
    if (!urlAsk || !run?.lab_id) return undefined;
    if (reviewTurns.length || restoredAsk.current === urlAsk) {
      restoredAsk.current = urlAsk;
      return undefined;
    }
    restoredAsk.current = urlAsk;
    let alive = true;
    api
      .kbMeta(run.lab_id)
      .then((r) => {
        if (!alive) return;
        const row = (r.data?.ask_sessions || []).find((s) => s.session_id === urlAsk);
        if (!row?.question) return;
        setReviewSession(urlAsk);
        setReviewTurns([{ q: row.question, a: row.answer || "", steps: [], trace_id: urlAsk }]);
      })
      .catch((e) => {
        if (alive) message.warning(formatError(e, "上次的提问没打开"));
      });
    return () => {
      alive = false;
    };
  }, [urlAsk, run?.lab_id, reviewTurns.length]);
  if (err && !run) return <LoadFailed message={err} onRetry={load} />;
  if (!run) return <PageLoading label="正在连接任务流…" />;
  const current = (run.subgraphs || []).findIndex((s) => s.current);
  const hint = runWaitHint(run);
  const askReview = async (textIn) => {
    const text = String(textIn || reviewQ || "").trim();
    if (!text || !run.lab_id || reviewBusy) return;
    setReviewBusy(true);
    try {
      const prior = [{ q: "已写好的综述", a: String(run.review_md || "").slice(0, 4000) }];
      const last = reviewTurns[reviewTurns.length - 1];
      if (last?.q) prior.push({ q: last.q, a: String(last.a || "").slice(0, 4000) });
      const r = await api.kbQuery(run.lab_id, text, {}, {
        history: prior.slice(-2),
        session_id: reviewSession || undefined,
      });
      const answer = r.data?.answer || "";
      setReviewTurns((prev) => [
        ...prev,
        { q: text, a: answer, steps: r.data?.steps || [], trace_id: r.data?.session_id || reviewSession || "" },
      ]);
      if (r.data?.session_id) {
        setReviewSession(r.data.session_id);
        const next = new URLSearchParams(params);
        next.set("ask", r.data.session_id);
        setParams(next, { replace: true });
      }
      setReviewQ("");
    } catch (e) {
      message.error(formatError(e, "问答失败"));
    } finally {
      setReviewBusy(false);
    }
  };
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader title={run.question} hint="暂停、恢复、取消都在上面。点了会告诉你结果。">
        任务进度和产出。
      </PageHeader>
      {hint ? (
        <Alert
          type={feedbackAlertType(run, run.status === "failed" ? "error" : run.status === "awaiting_gpu" ? "warning" : "info")}
          showIcon
          message={
            <span>
              {hint}
              {me?.is_admin && run.status === "awaiting_gpu" ? (
                <>
                  {" "}
                  <Link to="/admin">打开管理页</Link>
                </>
              ) : null}
            </span>
          }
        />
      ) : run.diagnosis?.next_step ? (
        <Alert type="info" showIcon message={run.diagnosis.next_step} />
      ) : null}
      <Card size="small" title="带上的文献">
        <p className="fs-muted">{humanize(run.injected?.note || "还没带上组里的文献或数据。这次只按你写的问题查。")}</p>
        {(run.injected?.literature || run.literature || []).map((p) => (
          <p key={p.id}>
            <Link to={p.href || literatureHref(p.id, p.last_page_no)}>{p.title || "文献"}</Link>
            {p.reproduce?.measured || p.latest_reproduce?.measured ? (
              <span className="fs-muted"> · 已经跑出结果</span>
            ) : p.reproduce || p.latest_reproduce || p.reproduce_brief ? (
              <span className="fs-muted"> · 还没跑出结果</span>
            ) : null}
            {" "}
            <ActionChip
              to={
                p.reproduce?.href
                || p.latest_reproduce?.href
                || p.reproduce_href
                || (run.lab_id ? `/labs/${run.lab_id}/reproduces?literature_item_id=${encodeURIComponent(p.id)}` : "")
              }
              tone={p.reproduce?.measured || p.latest_reproduce?.measured ? "" : "gold"}
            >
              {p.reproduce || p.latest_reproduce ? "查看复现" : "去复现"}
            </ActionChip>
          </p>
        ))}
        {run.injected?.dataset || run.dataset ? (
          <p>
            <Link to={`/datasets/${(run.injected?.dataset || run.dataset).id}`}>
              {(run.injected?.dataset || run.dataset).name || "数据集"}
            </Link>
          </p>
        ) : null}
      </Card>
      {streamOff ? (
        <StreamDropped
          message="实时流已断开，已改为每 8 秒刷新。暂停 / 取消仍可用。"
          onReconnect={() => setStreamGen((n) => n + 1)}
        />
      ) : null}
      <div className="fs-toolbar">
        <StatusTag status={run.status} />
        {run.error_class && <Tag color="red">{ERROR_CLASS_LABEL[run.error_class] || run.error_class}</Tag>}
        {run.resumable && <Tag color="blue">可 Resume</Tag>}
        {(run.literature || []).map((p) => (
          <span key={p.id} style={{ marginRight: 8 }}>
            <Link to={p.href || literatureHref(p.id, p.last_page_no)}>{p.title || "文献"}</Link>{" "}
            <ActionChip
              to={p.reproduce_href || (run.lab_id ? `/labs/${run.lab_id}/reproduces?literature_item_id=${encodeURIComponent(p.id)}` : "")}
              tone="gold"
            >
              去复现
            </ActionChip>
          </span>
        ))}
        {!(run.literature || []).length
          ? (run.literature_item_ids || []).map((lid) => (
              <Link key={lid} to={literatureHref(lid)}>
                文献
              </Link>
            ))
          : null}
        {run.dataset?.id || run.dataset_id ? (
          <Link to={`/datasets/${run.dataset?.id || run.dataset_id}`}>{run.dataset?.name || "数据集"}</Link>
        ) : null}
        <PressButton
          blocked={!(run.can_pause ?? run.feedback?.can_pause)}
          blockedReason={jobBlockedReason(run, "pause")}
          pendingLabel="正在暂停"
          ok="已暂停"
          fail="无法暂停"
          onPress={async () => {
            const r = await api.pauseRun(id);
            load();
            return r;
          }}
        >
          暂停
        </PressButton>
        <PressButton
          blocked={!(run.can_resume ?? run.feedback?.can_resume)}
          blockedReason={jobBlockedReason(run, "resume")}
          pendingLabel="正在恢复"
          ok="已恢复"
          fail="无法恢复"
          onPress={async () => {
            const r = await api.resumeRun(id);
            load();
            return r;
          }}
        >
          恢复
        </PressButton>
        <PressButton
          danger
          blocked={!(run.can_cancel ?? run.feedback?.can_cancel)}
          blockedReason={jobBlockedReason(run, "cancel")}
          pendingLabel="正在取消"
          ok="已取消"
          fail="无法取消"
          onPress={async () => {
            const r = await api.cancelRun(id);
            load();
            return r;
          }}
        >
          取消
        </PressButton>
      </div>
      <Card title="进度">
        {run.steps_total ? (
          <p className="fs-muted">已走到 {run.steps_done ?? 0} / {run.steps_total}</p>
        ) : null}
        <Steps
          size="small"
          current={current >= 0 ? current : (run.subgraphs || []).filter((s) => s.done).length}
          items={(run.subgraphs || []).map((s) => ({
            title: SUBGRAPH_LABEL[s.name] || s.name,
            status: s.done ? "finish" : s.current ? "process" : "wait",
          }))}
        />
      </Card>
      {run.error && <Card title="为什么停了">{humanize(run.error)}</Card>}
      {(run.diagnosis?.unused_bib_titles || []).length ||
      (run.diagnosis?.unused_injected_titles || []).length ||
      (run.diagnosis?.search_hit_titles || []).length ? (
        <Alert
          type={(run.diagnosis?.unused_bib_titles || []).length || (run.diagnosis?.unused_injected_titles || []).length ? "warning" : "info"}
          showIcon
          message={
            <span>
              {(run.diagnosis?.unused_bib_titles || []).length ? (
                <>
                  综述没提到 {run.diagnosis.unused_bib_titles.length} 篇
                  {(run.diagnosis.unused_bib_titles || []).map((t) => (
                    <Tag key={`bib-${t}`} style={{ marginLeft: 6 }}>
                      {t}
                    </Tag>
                  ))}
                </>
              ) : null}
              {(run.diagnosis?.unused_injected_titles || []).length ? (
                <>
                  {(run.diagnosis?.unused_bib_titles || []).length ? " · " : ""}
                  组内未交代 {run.diagnosis.unused_injected_titles.length} 篇
                  {(run.diagnosis.unused_injected_titles || []).map((t) => (
                    <Tag key={`inj-${t}`} color="gold" style={{ marginLeft: 6 }}>
                      {t}
                    </Tag>
                  ))}
                </>
              ) : null}
              {(run.diagnosis?.search_hit_titles || []).length ? (
                <span className="fs-muted">
                  {(run.diagnosis?.unused_bib_titles || []).length || (run.diagnosis?.unused_injected_titles || []).length ? " · " : ""}
                  检索命中 {run.diagnosis.search_hit_count || run.diagnosis.search_hit_titles.length} 条
                  {(run.diagnosis.search_hit_titles || []).slice(0, 8).map((t) => (
                    <Tag key={`hit-${t}`} style={{ marginLeft: 6 }}>
                      {t}
                    </Tag>
                  ))}
                </span>
              ) : null}
              {run.diagnosis?.search_hits_path ? (
                <>
                  {" "}
                  <Button
                    size="small"
                    type="link"
                    onClick={() =>
                      api
                        .runArtifactFile(id, run.diagnosis.search_hits_path)
                        .then((r) => saveBlob(r.data, "search_hits.jsonl"))
                        .catch((e) => message.error(formatError(e, "无法打开工件")))
                    }
                  >
                    下载命中
                  </Button>
                </>
              ) : null}
              <ResumeJob row={{ ...run, kind: "research" }} onDone={load} />
            </span>
          }
        />
      ) : run.diagnosis?.search_hit_count ? (
        <p className="fs-muted">检索命中 {run.diagnosis.search_hit_count} 条，见工件 search_hits.jsonl</p>
      ) : null}
      {run.review_md ? (
        <Card title="综述">
          <MdPreview text={run.review_md} />
          <AiResultBar
            text={run.review_md}
            copyLabel="复制综述"
            retryLabel="再跑一次"
            busy={rerunBusy}
            steps={(run.subgraphs || [])
              .filter((s) => s.done || s.current)
              .map((s) => ({
                tool: s.name,
                label: SUBGRAPH_LABEL[s.name] || s.name,
                summary: s.done ? "已完成" : "进行中",
              }))}
            followups={run.lab_id ? [{ label: "问这篇综述" }] : []}
            onPick={() => askReview("这篇综述还缺什么？")}
            onRetry={
              run.lab_id && run.question
                ? async () => {
                    if (rerunBusy) return;
                    setRerunBusy(true);
                    try {
                      const r = await api.createRun(run.lab_id, {
                        question: run.question,
                        dataset_id: run.dataset_id || undefined,
                        literature_item_ids: run.literature_item_ids || [],
                        domain: run.domain || "general",
                      });
                      message.success("已再开一轮文献调研");
                      if (r.data?.id) nav(`/runs/${r.data.id}`);
                    } catch (e) {
                      message.error(formatError(e, "无法再跑一次"));
                    } finally {
                      setRerunBusy(false);
                    }
                  }
                : undefined
            }
          />
          <Form
            layout="inline"
            style={{ marginTop: 8 }}
            onFinish={() => askReview(reviewQ)}
          >
            <Form.Item style={{ flex: 1, minWidth: 220 }}>
              <Input
                value={reviewQ}
                onChange={(e) => setReviewQ(e.target.value)}
                placeholder="问这篇综述"
                disabled={reviewBusy}
              />
            </Form.Item>
            <span className="fs-press-wrap" onClick={whenBlocked(!reviewQ.trim() || reviewBusy, reviewBusy ? "正在问，请稍等" : "先写一句再问")}>
            <Button htmlType="submit" loading={reviewBusy} disabled={!reviewQ.trim()}>
              问
            </Button>
            </span>
          </Form>
          {reviewTurns.map((t, i) => (
            <div key={`${t.q}-${i}`} style={{ marginTop: 8 }}>
              <p>
                <b>{t.q}</b>
              </p>
              {t.a ? <MdPreview text={t.a} /> : <p className="fs-muted">这次没有写出回答。</p>}
              <AiResultBar text={t.a} copyLabel="复制回答" steps={t.steps} traceId={t.trace_id} />
            </div>
          ))}
        </Card>
      ) : null}
      <Card title="工件">{artifactTable(run.artifacts, (path, name) => api.runArtifactFile(id, path).then((r) => saveBlob(r.data, name)))}</Card>
      <Card title="进度">
        <EventFeed items={run.messages || run.progress} />
      </Card>
    </Space>
  );
}

export function Reproduces() {
  const { id } = useParams();
  const nav = useNavigate();
  const me = useSession((s) => s.me);
  const teacher = roleOf(me, id) === "teacher" || !!me?.is_admin;
  const loc = useLocation();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") || undefined;
  const honest = params.get("honest") || undefined;
  const litId = params.get("literature_item_id") || undefined;
  const datasetId = params.get("dataset_id") || undefined;
  const directionId = params.get("direction_id") || undefined;
  const [rows, setRows] = useState([]);
  const [papers, setPapers] = useState([]);
  const [datasets, setDatasets] = useState([]);
  const [dirs, setDirs] = useState([]);
  const [err, setErr] = useState("");
  const [creating, setCreating] = useState(false);
  const [preflight, setPreflight] = useState(null);
  const [formPreflight, setFormPreflight] = useState(null);
  const [preflightErr, setPreflightErr] = useState("");
  const [preflightTick, setPreflightTick] = useState(0);
  const [emptyNext, setEmptyNext] = useState(null);
  const [form] = Form.useForm();
  const [compose, setCompose] = useComposeOpen(params, setParams);
  const formLit = Form.useWatch("literature_item_id", form);
  const formDs = Form.useWatch("dataset_id", form);
  const formPath = Form.useWatch("dataset_path", form);
  const reload = () => {
    setErr("");
    api
      .reproduceRuns(id, {
        status,
        literature_item_id: litId,
        dataset_id: datasetId,
        honest,
        direction_id: directionId,
      })
      .then((r) => {
        setRows(asItems(r.data));
        setPreflight(r.data?.preflight || null);
        setEmptyNext(r.data?.empty_next || null);
      })
      .catch((e) => setErr(formatError(e, "复现列表加载失败")));
  };
  useEffect(() => {
    reload();
    api.literature(id).then((r) => setPapers(asItems(r.data))).catch((e) => setErr(formatError(e, "文献列表加载失败")));
    api.datasets(id).then((r) => setDatasets(asItems(r.data))).catch((e) => setErr(formatError(e, "数据集列表加载失败")));
    api.directions(id).then((r) => setDirs(asItems(r.data))).catch((e) => {
      setDirs([]);
      message.error(formatError(e, "方向列表没打开"));
    });
  }, [id, status, litId, datasetId, honest, directionId]);
  useEffect(() => {
    if (!id) return undefined;
    let alive = true;
    api
      .reproducePreflight(id, {
        literature_item_id: formLit || litId || undefined,
        dataset_id: formDs || datasetId || undefined,
        dataset_path: formPath || undefined,
      })
      .then((r) => {
        if (alive) {
          setFormPreflight(r.data);
          setPreflightErr("");
        }
      })
      .catch((e) => {
        if (alive) {
          setFormPreflight(null);
          setPreflightErr(formatError(e, "创建前诊断加载失败"));
        }
      });
    return () => {
      alive = false;
    };
  }, [id, formLit, formDs, formPath, litId, datasetId, preflightTick]);
  const shownPreflight = formPreflight || preflight;
  const dirMeta = dirs.find((d) => d.id === directionId);
  const scopedKids = Boolean(
    emptyNext?.includes_children || dirMeta?.includes_children || dirMeta?.child_count
  );
  const hero =
    litId
      ? { title: emptyNext?.title || "该文献的复现", lead: "只看这篇。" }
      : honest === "measured"
        ? { title: emptyNext?.title || "已经跑出结果的复现", lead: "已经跑出数字的。" }
        : honest === "not_run" || honest === "unmeasured"
          ? { title: emptyNext?.title || "还没跑出结果的复现", lead: "还没跑出数字。" }
          : honest === "awaiting" || status === "awaiting_gpu"
            ? { title: emptyNext?.title || "在等机器的复现", lead: "在等 GPU。" }
            : honest === "blocked" || status === "blocked"
              ? { title: emptyNext?.title || "缺数据的复现", lead: "缺数据集，卡住了。" }
              : honest === "stub"
                ? { title: emptyNext?.title || "试跑", lead: "试跑，不是正式结果。" }
                : status === "failed"
                  ? { title: emptyNext?.title || "失败的复现", lead: "这些任务没跑完。" }
                  : status === "pending" || status === "queued"
                    ? { title: emptyNext?.title || "已入队的复现", lead: "还在排队。" }
                    : datasetId
                      ? { title: emptyNext?.title || "该数据集的复现", lead: "只用了这个数据集。" }
                      : directionId
                        ? {
                            title: emptyNext?.title || (scopedKids ? "这个大方向（含小课题）的复现" : "本方向的复现"),
                            lead: scopedKids ? "这个大方向，含小课题。" : "只看这个方向。",
                          }
                      : null;
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        tone={hero ? "unused" : undefined}
        title={hero?.title || "复现"}
        hint="缺数据会停住。在等机器时不会假装跑完。"
        extra={
          <Button type="primary" onClick={() => setCompose(true)}>
            开始复现
          </Button>
        }
      >
        {hero?.lead || "这篇跑出来没有。"}
        {hero ? (
          <>
            {" "}
            <ActionChip to={`/labs/${id}/reproduces`}>全部复现</ActionChip>
          </>
        ) : null}
      </PageHeader>
      <NotRunTitleChips
        titles={rows
          .filter((r) => !r.measured && r.honest !== "awaiting" && r.literature_title && r.literature_item_id)
          .reduce((acc, r) => {
            if (acc.some((x) => x.href.includes(r.literature_item_id))) return acc;
            const href = `/labs/${id}/reproduces?literature_item_id=${encodeURIComponent(r.literature_item_id)}${
              directionId ? `&direction_id=${directionId}` : ""
            }`;
            acc.push({ title: r.literature_title, href });
            return acc;
          }, [])
          .slice(0, 8)}
        more={Math.max(
          0,
          new Set(
            rows
              .filter((r) => !r.measured && r.honest !== "awaiting" && r.literature_item_id)
              .map((r) => r.literature_item_id)
          ).size - 8
        )}
      />
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}
      {emptyNext && !rows.length ? (
        <Alert
          type="info"
          showIcon
          message={emptyNext.title || "本筛选下没有复现任务"}
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
          options={Object.entries(RUN_STATUS).map(([value, label]) => ({ value, label }))}
        />
        <Select
          allowClear
          placeholder="有没有跑出结果"
          style={{ width: 160 }}
          value={honest}
          onChange={(v) => {
            const next = new URLSearchParams(params);
            if (v) next.set("honest", v);
            else next.delete("honest");
            setParams(next, { replace: true });
          }}
          options={[
            { value: "measured", label: "已经跑出结果" },
            { value: "not_run", label: "还没跑出结果" },
            { value: "awaiting", label: "在等机器" },
            { value: "blocked", label: "缺数据" },
            { value: "stub", label: "试跑" },
          ]}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="关联文献"
          style={{ width: 260 }}
          value={litId}
          onChange={(v) => {
            const next = new URLSearchParams(params);
            if (v) next.set("literature_item_id", v);
            else next.delete("literature_item_id");
            setParams(next, { replace: true });
          }}
          options={papers.map((p) => ({ value: p.id, label: p.title }))}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="数据集"
          style={{ width: 220 }}
          value={datasetId}
          onChange={(v) => {
            const next = new URLSearchParams(params);
            if (v) next.set("dataset_id", v);
            else next.delete("dataset_id");
            setParams(next, { replace: true });
          }}
          options={datasets.map((d) => ({ value: d.id, label: d.name }))}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="方向"
          style={{ width: 220 }}
          value={directionId}
          onChange={(v) => {
            const next = new URLSearchParams(params);
            if (v) next.set("direction_id", v);
            else next.delete("direction_id");
            setParams(next, { replace: true });
          }}
          notFoundContent={<DirectionSelectEmpty labId={id} teacher={teacher} />}
          options={dirs.map((d) => ({
            value: d.id,
            label: d.parent_id ? `↳ ${d.name}` : d.includes_children || d.child_count ? `${d.name}（含小课题）` : d.name,
          }))}
        />
        <DirectionFilterHint labId={id} teacher={teacher} dirs={dirs} />
      </div>
      <CreateDialog
        open={compose}
        onClose={() => setCompose(false)}
        title="开始复现"
        okText="开始复现"
        form={form}
        width={560}
        loading={creating}
        extra={
          <>
            {preflightErr ? <LoadFailed message={preflightErr} onRetry={() => setPreflightTick((n) => n + 1)} /> : null}
            <PreflightBrief missing={shownPreflight?.missing} />
          </>
        }
      >
        <Form
          form={form}
          layout="vertical"
          key={`${litId || ""}-${datasetId || ""}-${loc.state?.literature_item_id || ""}`}
          initialValues={{
            literature_item_id: loc.state?.literature_item_id || litId || undefined,
            dataset_id: datasetId || undefined,
          }}
          onFinish={async (v) => {
            if (creating) return;
            setCreating(true);
            try {
              const r = await api.createReproduce(id, {
                literature_item_id: v.literature_item_id || null,
                dataset_id: v.dataset_id || undefined,
                dataset_path: v.dataset_path || "",
              });
              tellFeedback(r.data, "已创建");
              setCompose(false);
              reload();
              if (r.data.id) nav(`/reproduces/${r.data.id}`);
            } catch (e) {
              message.error(formatError(e, "无法创建复现"));
            } finally {
              setCreating(false);
            }
          }}
        >
          <Form.Item name="literature_item_id" label="文献">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="可选"
              options={papers.map((p) => ({ value: p.id, label: p.title }))}
            />
          </Form.Item>
          <Form.Item name="dataset_id" label="数据集">
            <Select allowClear placeholder="可选" options={datasets.map((d) => ({ value: d.id, label: d.name }))} />
          </Form.Item>
          <Form.Item name="dataset_path" label="路径">
            <Input placeholder="没有登记数据集时填写路径" />
          </Form.Item>
          <p className="fs-muted">没填论文里的数字时，会用这篇的方法里的数字，并标明还不是你们跑出来的。</p>
        </Form>
      </CreateDialog>
      <Table
        className="fs-click-row"
        rowKey="id"
        dataSource={rows}
        locale={{ emptyText: <EmptyHint to={`/labs/${id}/datasets`}>还没有复现任务。可先登记数据集，或从文献阅读器发起。</EmptyHint> }}
        onRow={(row) => ({ onClick: () => nav(`/reproduces/${row.id}`) })}
        columns={[
          { title: "状态", dataIndex: "status", render: (s) => <StatusTag status={s} /> },
          {
            title: "结果",
            render: (_, row) => <ReproduceHonest ev={{ measured: row.measured, honest: row.honest }} none="—" />,
          },
          { title: "阶段", dataIndex: "current_stage", render: (s) => REPRO_STAGE_LABEL[s] || s || "—" },
          {
            title: "文献",
            render: (_, row) =>
              row.literature_item_id ? (
                <Link to={row.href || literatureHref(row.literature_item_id, row.last_page_no)} onClick={(e) => e.stopPropagation()}>
                  {row.literature_title || "文献"}
                </Link>
              ) : (
                "—"
              ),
          },
          {
            title: "数据集",
            render: (_, row) =>
              row.dataset?.id || row.dataset_id ? (
                <Link to={`/datasets/${row.dataset?.id || row.dataset_id}`} onClick={(e) => e.stopPropagation()}>
                  {row.dataset?.name || "数据集"}
                </Link>
              ) : (
                "—"
              ),
          },
          { title: "分类", dataIndex: "error_class", render: (c) => ERROR_CLASS_LABEL[c] || c },
          {
            title: "引导",
            dataIndex: "guide",
            render: (g, row) => (
              <span onClick={(e) => e.stopPropagation()}>
                {g?.kind && g.href ? (
                  <Link to={g.href}>{g.label || "下一步"}</Link>
                ) : (
                  <FeedbackStrip row={{ ...row, next_step: row.next_step || g?.next_step || row.diagnosis?.next_step }} />
                )}{" "}
                <AnalyzeChip row={row} />
              </span>
            ),
          },
          {
            title: "恢复",
            render: (_, row) => (
              <span onClick={(e) => e.stopPropagation()}>
                <ResumeJob row={{ ...row, kind: "reproduce" }} onDone={reload} />
              </span>
            ),
          },
        ]}
      />
    </Space>
  );
}

export function ReproduceDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const me = useSession((s) => s.me);
  const [run, setRun] = useState(null);
  const [err, setErr] = useState("");
  const [streamOff, setStreamOff] = useState(false);
  const [streamGen, setStreamGen] = useState(0);
  const [resumeBusy, setResumeBusy] = useState(false);
  const [improveBusy, setImproveBusy] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [analyzeBusy, setAnalyzeBusy] = useState(false);
  const runRef = useRef(null);
  runRef.current = run;
  const load = () =>
    api
      .getReproduce(id)
      .then((r) => {
        setRun(r.data);
        setErr("");
      })
      .catch((e) => setErr(formatError(e, "复现任务加载失败")));
  useEffect(() => {
    load();
    setStreamOff(false);
    const ac = new AbortController();
    consumeSse(
      `/reproduce-runs/${id}/stream`,
      (event, data) => {
        if (data && typeof data === "object") setRun((prev) => ({ ...(prev || {}), ...data, artifacts: prev?.artifacts, attempts: prev?.attempts }));
        if (event === "done") load();
      },
      { signal: ac.signal }
    )
      .then(() => {
        if (LIVE_STATUSES.has(runRef.current?.status)) setStreamOff(true);
      })
      .catch((e) => {
        if (e?.name === "AbortError") return;
        setStreamOff(true);
      });
    const t = setInterval(load, 8000);
    return () => {
      ac.abort();
      clearInterval(t);
    };
  }, [id, streamGen]);
  if (err && !run) return <LoadFailed message={err} onRetry={load} />;
  if (!run) return <PageLoading label="正在连接复现流…" />;
  const hint = runWaitHint(run);
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader title="复现" hint="在等机器时不会假装跑完。">
        这篇跑出来没有
      </PageHeader>
      <div className="fs-toolbar">
        <StatusTag status={run.status} />
        {run.current_stage ? <Tag>{REPRO_STAGE_LABEL[run.current_stage] || run.current_stage}</Tag> : null}
        {run.error_class && <Tag color="gold">{ERROR_CLASS_LABEL[run.error_class] || run.error_class}</Tag>}
        {run.literature_item_id && (
          <Link to={run.href || literatureHref(run.literature_item_id, run.last_page_no || run.source_page)}>
            {run.literature_title || "打开文献"}
          </Link>
        )}
        {run.dataset?.id || run.dataset_id ? (
          <Link to={`/datasets/${run.dataset?.id || run.dataset_id}`}>{run.dataset?.name || "数据集"}</Link>
        ) : null}
        <PressButton
          blocked={!(run.can_resume ?? run.feedback?.can_resume)}
          blockedReason={jobBlockedReason(run, "resume")}
          pendingLabel="正在恢复"
          ok="已恢复"
          fail="无法恢复"
          onPress={async () => {
            const r = await api.resumeReproduce(id);
            setRun((prev) => ({ ...(prev || {}), ...r.data }));
            return r;
          }}
        >
          恢复
        </PressButton>
        <PressButton
          blocked={!(run.can_improve ?? run.feedback?.can_improve)}
          blockedReason={jobBlockedReason(run, "improve")}
          pendingLabel="正在改进"
          ok="已开始改进"
          fail="无法改进"
          onPress={async () => {
            const r = await api.improveReproduce(id);
            setRun((prev) => ({ ...(prev || {}), ...r.data }));
            return r;
          }}
        >
          改进一轮
        </PressButton>
        <PressButton
          danger
          blocked={!(run.can_cancel ?? run.feedback?.can_cancel)}
          blockedReason={jobBlockedReason(run, "cancel")}
          pendingLabel="正在取消"
          ok="已取消"
          fail="无法取消"
          onPress={async () => {
            const r = await api.cancelReproduce(id);
            load();
            return r;
          }}
        >
          取消
        </PressButton>
      </div>
      {hint || run.status === "awaiting_gpu" || run.status === "blocked" ? (
        <Alert
          type={feedbackAlertType(run, run.status === "failed" ? "error" : run.status === "awaiting_gpu" || run.status === "blocked" ? "warning" : "info")}
          showIcon
          message={
            <span>
              {hint ||
                (run.status === "awaiting_gpu"
                  ? "管理员未开启实验算力。已等待，未填写任何伪造指标。请联系管理员开启。"
                  : "缺少数据或环境，状态为 blocked。")}
              {run.status === "awaiting_gpu" && me?.is_admin ? (
                <>
                  {" "}
                  <Link to="/admin">打开管理页</Link>
                </>
              ) : null}
              {run.status === "blocked" ? (
                <>
                  {" "}
                  {run.lab_id ? <Link to={`/labs/${run.lab_id}/datasets`}>去登记数据集</Link> : "请补齐数据集后再跑。"}
                </>
              ) : null}
            </span>
          }
        />
      ) : null}
      {["abstract_only", "ungrounded", "unusable"].includes(run.guide?.kind) ? (
        <Alert
          type="warning"
          showIcon
          message={
            <span>
              {run.guide.next_step || run.guide.label || "请先上传可复制文字版 PDF。"}
              {run.guide.href ? (
                <>
                  {" "}
                  <Link to={run.guide.href}>打开文献上传 PDF</Link>
                  {" "}
                  <AnalyzeChip row={{ id: run.literature_item_id, card_href: run.guide.href, has_method_card: false }} />
                </>
              ) : null}
            </span>
          }
        />
      ) : null}
      {run.diagnosis?.next_step && run.status !== "awaiting_gpu" && run.status !== "blocked" ? (
        <Alert
          type="info"
          showIcon
          message={
            <span>
              {run.diagnosis.next_step}
              {run.diagnosis.suggested_action === "improve" && (run.can_improve ?? run.feedback?.can_improve) ? (
                <>
                  {" "}
                  <Button
                    type="link"
                    onClick={() =>
                      api
                        .improveReproduce(id)
                        .then((r) => {
                          tellFeedback(r.data, "已改进");
                          setRun((prev) => ({ ...(prev || {}), ...r.data }));
                        })
                        .catch((e) => message.error(formatError(e, "无法改进")))
                    }
                  >
                    改进一轮
                  </Button>
                </>
              ) : null}
              {run.diagnosis.suggested_action === "upload_pdf" && (run.guide?.href || run.literature_item_id) ? (
                <>
                  {" "}
                  <Link to={run.guide?.href || literatureHref(run.literature_item_id, undefined, { panel: "card" })}>
                    打开文献
                  </Link>
                </>
              ) : null}
              {run.diagnosis.suggested_action === "need_data" && run.lab_id ? (
                <>
                  {" "}
                  <Link to={`/labs/${run.lab_id}/datasets`}>去登记数据集</Link>
                </>
              ) : null}
              {run.diagnosis.suggested_action === "ask_admin_gpu" ? (
                me?.is_admin ? (
                  <>
                    {" "}
                    <Link to="/admin">打开管理页</Link>
                  </>
                ) : (
                  " 请联系管理员开启 GPU。"
                )
              ) : null}
            </span>
          }
        />
      ) : null}
      {streamOff ? (
        <StreamDropped
          message="实时流已断开，已改为每 8 秒刷新。暂停 / 取消仍可用。"
          onReconnect={() => setStreamGen((n) => n + 1)}
        />
      ) : null}
      {run.card_quality?.weak ? (
        <Alert type="warning" showIcon message="这版只根据摘要，或有句子对不上原文。更好的一版还留着。" />
      ) : null}
      <Card title="这篇的方法">
        <p className="fs-muted">作者要解决什么、怎么做、结论是什么。</p>
        <MethodCard
          analysis={run.method_card}
          onPage={
            run.literature_item_id
              ? (p) => {
                  nav(literatureHref(run.literature_item_id, p));
                }
              : undefined
          }
          onRetry={
            run.literature_item_id
              ? async () => {
                  setAnalyzeBusy(true);
                  try {
                    await api.analyze(run.literature_item_id);
                    message.success("已再提炼，打开文献侧栏可看这篇的方法。");
                    load();
                  } catch (e) {
                    message.error(formatError(e, "提炼方法未完成，请再点一次。"));
                  } finally {
                    setAnalyzeBusy(false);
                  }
                }
              : undefined
          }
          retryBusy={analyzeBusy}
        />
      </Card>
      {run.claimed_metrics && Object.keys(run.claimed_metrics).length ? (
        <Card
          title={
            run.claimed_metrics_source === "method_card"
              ? "论文里的数字（还不是你们跑出来的）"
              : "论文里的数字"
          }
        >
          <p>{Object.entries(run.claimed_metrics).map(([k, v]) => <span key={k}>{k}：{String(v)} </span>)}</p>
        </Card>
      ) : null}
      {run.actual_metrics && Object.keys(run.actual_metrics).length && run.diagnosis?.kind !== "not_run" ? (
        <Card title="跑出来的数字">
          <p>{Object.entries(run.actual_metrics).map(([k, v]) => <span key={k}>{k}：{String(v)} </span>)}</p>
        </Card>
      ) : (
        <Alert type="info" showIcon message="还没跑出结果。论文里的数字不能写成你们的结果。" />
      )}
      {run.status === "succeeded" && !(run.actual_metrics && Object.keys(run.actual_metrics).length) ? (
        <Alert type="warning" showIcon message="没有跑出结果，不能算跑完。" />
      ) : null}
      {run.claimed_metrics && Object.keys(run.claimed_metrics).length ? (
        <Card title="论文数字和跑出来的">
          <Table
            pagination={false}
            rowKey="metric"
            dataSource={Object.keys(run.claimed_metrics).map((k) => ({
              metric: k,
              claimed: run.claimed_metrics[k],
              actual: run.actual_metrics ? run.actual_metrics[k] : "还没有",
            }))}
            columns={[
              { title: "指标", dataIndex: "metric" },
              { title: "论文里", dataIndex: "claimed", render: (v) => String(v) },
              { title: "跑出来的", dataIndex: "actual", render: (v) => String(v ?? "还没有") },
            ]}
          />
        </Card>
      ) : null}
      <Card title="差在哪">
        <pre className="fs-pre">{run.gap_report_md || "还没跑出结果。"}</pre>
      </Card>
      <Collapse
        ghost
        items={[{
          key: "files",
          label: "文件和每一轮",
          children: (
            <>
      <Card title="文件">{artifactTable(run.artifacts, (path, name) => api.reproArtifactFile(id, path).then((r) => saveBlob(r.data, name)))}</Card>
      <Card title="每一轮">
        <Table
          rowKey="id"
          dataSource={run.attempts || []}
          pagination={false}
          locale={{ emptyText: "还没有轮次" }}
          columns={[
            { title: "类型", dataIndex: "kind", render: (k) => ({ implement: "实现", improve: "改进", evaluate: "评测" }[k] || k) },
            { title: "状态", dataIndex: "status", render: (s) => RUN_STATUS[s] || s },
            {
              title: "这一轮做了什么",
              dataIndex: "log_summary",
              render: (v) => (v ? <pre className="fs-pre" style={{ margin: 0, maxHeight: 160, overflow: "auto" }}>{v}</pre> : "—"),
            },
          ]}
        />
      </Card>
            </>
          ),
        }]}
      />
    </Space>
  );
}
