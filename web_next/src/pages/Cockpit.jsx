import { useEffect, useState } from "react";
import { Button, Card, Collapse, Form, Input, Segmented, Select, Space, Tag, Timeline, message } from "antd";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, asItems } from "../api.js";
import { ActionChip, ActivityLine, AgentTraceBox, AiOverviewCard, AnalyzeChip, AskWeekChip, AttentionBar, ContinueAskChip, DirectionHangChips, DirectionSelectEmpty, EmptyHint, FeedbackStrip, GuideList, LibraryProgress, LoadFailed, NextStepList, NotRunTitleChips, PageHeader, PageLoading, PdfGapLink, ReadTag, ResumeJob, SituationBar, StatusTag, StuckBriefBar, UnusedTitleChips, WeekPaperChips, WorkLine, aiToolsLine, fmtWhen, spokenName, toolLabel, whenBlocked } from "../ui.jsx";
import { formatError, MS_STATUS, RUN_STATUS, literatureHref } from "../err.js";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";
import { writingDeskHref } from "../manuscriptSections.js";
import { DirectionSunburst, FunnelChart, FunnelEmptyHero, FunnelLeakChips, HeatstripChart } from "../charts.jsx";
import LabGlance, { DigestMail, LabTabs } from "./LabGlance.jsx";

export function Cockpit() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const me = useSession((s) => s.me);
  const student = roleOf(me, id) === "student";
  const teacher = roleOf(me, id) === "teacher" || !!me?.is_admin;
  const [params, setParams] = useSearchParams();
  const directionId = params.get("direction_id") || undefined;
  const [dirs, setDirs] = useState([]);
  const [hangBusy, setHangBusy] = useState("");
  const [face, setFace] = useState("people");
  const hangReading = async (row, s) => {
    if (!row?.id || !s?.id || hangBusy) return;
    setHangBusy(`${row.id}:${s.id}`);
    try {
      const next = [...new Set([...(row.direction_ids || []), s.id])];
      await api.patchLiterature(row.id, { direction_ids: next });
      message.success("已挂到方向");
      qc.invalidateQueries({ queryKey: ["cockpit", id] });
    } catch (e) {
      message.error(formatError(e, "无法挂方向"));
    } finally {
      setHangBusy("");
    }
  };
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["cockpit", id, directionId || ""],
    queryFn: () => api.cockpit(id, { direction_id: directionId }).then((r) => r.data),
    enabled: !!id,
    refetchInterval: 15000,
  });
  const library = useQuery({
    queryKey: ["digest-status", id],
    queryFn: () => api.digestStatus(id).then((r) => r.data),
    enabled: !!id && teacher,
    refetchInterval: 60000,
  });
  useEffect(() => {
    if (!id) return;
    api.directions(id).then((r) => setDirs(asItems(r.data))).catch((e) => {
      setDirs([]);
      message.error(formatError(e, "方向列表没打开"));
    });
  }, [id]);
  if (isError) return <LoadFailed message={formatError(error, "组里加载失败")} onRetry={() => qc.invalidateQueries({ queryKey: ["cockpit", id] })} />;
  if (isLoading || !data) return <PageLoading label="正在打开这个组…" />;
  const rawTab = params.get("tab") || "directions";
  const tab = rawTab === "progress" && !teacher ? "directions" : rawTab;
  const setTab = (next) => {
    const q = new URLSearchParams(params);
    q.set("tab", next);
    setParams(q, { replace: true });
  };
  if (tab !== "progress") {
    return (
      <LabGlance
        labId={id}
        teacher={teacher}
        overview={data.overview || {}}
        tab={tab}
        setTab={setTab}
        onRefresh={() => qc.invalidateQueries({ queryKey: ["cockpit", id] })}
      />
    );
  }
  const guideOpen = (data.guide || []).some((g) => !g.done);
  return (
    <Space direction="vertical" style={{ width: "100%" }} size="large">
      <PageHeader title="组里" hint="需要关注的数字在这一页。">
        <LabTabs tab="progress" teacher={teacher} onChange={setTab} />
      </PageHeader>
      <p className="fs-muted">
        方向 {data.overview?.counts?.directions || 0} · 成员 {data.overview?.counts?.members || 0} · 文献 {data.overview?.counts?.literature || 0}
      </p>
      {teacher ? <LibraryProgress status={library.data} labId={id} /> : null}
      {teacher ? <DigestMail labId={id} /> : null}
      <Collapse
        ghost
        defaultActiveKey={["nums"]}
        items={[
          {
            key: "nums",
            label: "数字",
            children: (
              <>
      <AttentionBar
        labId={id}
        milestones={data.milestones_upcoming}
        queue={data.queue}
        unassigned={data.unassigned}
        spine={data.reproduce_spine}
        onResume={() => qc.invalidateQueries({ queryKey: ["cockpit", id] })}
      />
      {data.members_counts_note ? <p className="fs-muted">{data.members_counts_note}</p> : null}
      <SituationBar situation={data.situation} />
      {data.stuck_brief?.text || (data.unused_titles || data.stuck_brief?.unused_titles || []).length ? (
        <Collapse
          ghost
          items={[
            {
              key: "watch",
              label: "需要关注",
              children: (
                <>
                  <StuckBriefBar brief={data.stuck_brief} />
                  <UnusedTitleChips
                    titles={data.unused_titles || data.stuck_brief?.unused_titles || []}
                    href={data.unused_research_href || data.stuck_brief?.unused_research_href || `/labs/${id}/runs?unused=1`}
                    more={data.unused_title_more || data.stuck_brief?.unused_title_more || 0}
                  />
                </>
              ),
            },
          ]}
        />
      ) : null}
      <FunnelEmptyHero funnel={data.funnel} href={data.literature_href || `/labs/${id}/literature`} />
      {data.funnel?.empty ? null : <FunnelChart funnel={data.funnel} />}
      <FunnelLeakChips funnel={data.funnel} />
      <AiOverviewCard overview={data.situation?.ai} />
      {data.reproduce_spine ? (
        <div className="fs-toolbar">
          <Link className="fs-metric" to={data.reproduce_spine.measured_href || data.reproduce_spine.href}>
            <b>{data.reproduce_spine.measured ?? 0}</b>
            <span>已经跑出结果</span>
          </Link>
          <Link className="fs-metric" to={data.reproduce_spine.not_run_href || data.reproduce_spine.href}>
            <b>{data.reproduce_spine.not_run ?? 0}</b>
            <span>还没做复现</span>
          </Link>
          <Link className="fs-metric" to={data.reproduce_spine.stub_href || data.reproduce_spine.href}>
            <b>{data.reproduce_spine.stub ?? 0}</b>
            <span>试跑</span>
          </Link>
        </div>
      ) : null}
      <NotRunTitleChips
        titles={data.reproduce_spine?.not_run_titles}
        href={data.reproduce_spine?.not_run_href || `/labs/${id}/reproduces?honest=not_run`}
        more={data.reproduce_spine?.not_run_title_more || 0}
      />
      <HeatstripChart members={data.by_member} labId={id} />
      <DirectionSunburst rows={data.by_direction} />
              </>
            ),
          },
        ]}
      />
      <div className="fs-card-grid fs-is-off">
        {(data.members || []).filter((m) => (m.membership_status || "active") === "active").map((m) => {
          const name = spokenName(m.display_name) || (m.role === "teacher" ? "导师" : "学生");
          const step = (m.stuck?.steps || []).find((s) => s?.href && s.key !== "reading" && s.key !== "direction");
          const hasMore = Boolean(
            (m.readings || []).length ||
            (m.manuscripts || []).length ||
            (m.agents || []).length ||
            m.last_ai?.question ||
            step ||
            m.week_event_count ||
            m.ai_calls ||
            m.tokens
          );
          return (
          <Card
            key={m.user_id}
            hoverable
            className="fs-member-card"
            title={
              <span className="fs-card-title">
                <span className="fs-avatar fs-avatar-ink">{name.slice(0, 1)}</span>
                {name}
                {m.title ? <span className="fs-muted"> · {m.title}</span> : null}
              </span>
            }
            extra={<Tag>{m.role === "teacher" ? "导师" : "学生"}</Tag>}
            onClick={() => nav(`/labs/${id}/cockpit/members/${m.user_id}`)}
          >
            <p className="fs-muted" onClick={(e) => e.stopPropagation()}>
              {m.direction?.name || "还没选方向"} · {fmtWhen(m.last_seen_at)}
            </p>
            {m.work_line || (m.work_sources || []).length ? (
              <p className="fs-muted"><WorkLine text={m.work_line} sources={m.work_sources} /></p>
            ) : null}
            {m.readings?.[0] ? (
              <p onClick={(e) => e.stopPropagation()}>
                <Link to={m.readings[0].href || literatureHref(m.readings[0].id, m.readings[0].last_page_no)}>这周在读《{m.readings[0].title}》</Link>
              </p>
            ) : null}
            {m.manuscripts?.[0] ? (
              <p onClick={(e) => e.stopPropagation()}>
                <Link to={m.manuscripts[0].href || `/manuscripts/${m.manuscripts[0].id}`}>论文《{m.manuscripts[0].title}》</Link>
              </p>
            ) : null}
            {!m.readings?.[0] && !m.manuscripts?.[0] ? <p className="fs-muted">这周还没开始。</p> : null}
            {step ? (
              <p onClick={(e) => e.stopPropagation()}>
                <Link to={step.href}>{step.text || step.label || "卡在这一件"}</Link>
              </p>
            ) : null}
            {hasMore ? (
            <div onClick={(e) => e.stopPropagation()}>
              <Collapse
                ghost
                items={[
                  {
                    key: "more",
                    label: "更多",
                    children: (
                      <>
            {m.stuck?.steps?.length ? (
              <div onClick={(e) => e.stopPropagation()}>
                <Collapse
                  ghost
                  items={[
                    {
                      key: "stuck",
                      label: "需要关注",
                      children: <NextStepList steps={m.stuck.steps} title="谁还没做完" labId={id} canSearch={teacher} />,
                    },
                  ]}
                />
              </div>
            ) : null}
            <p>方向：{m.direction?.name || "未选择"}</p>
            {m.ai_calls != null || m.tokens != null ? (
              <p className="fs-muted">
                本月问了 {m.ai_calls || 0} 次 · 用量 {m.tokens || 0}
              </p>
            ) : null}
            {m.last_ai?.question ? (
              <div onClick={(e) => e.stopPropagation()}>
                <p className="fs-muted">
                  最近{m.last_ai.label || "问"}：{m.last_ai.question}
                  {m.last_ai.ground_why ? ` · ${m.last_ai.ground_why}` : ""}
                  {(m.last_ai.tools || []).length ? ` · 用过：${(m.last_ai.tools || []).map(toolLabel).filter(Boolean).join("、")}` : ""}
                </p>
                <AgentTraceBox traceId={m.last_ai.trace_id} />
              </div>
            ) : null}
            {m.week_never_ai ? <p className="fs-muted">本周没问过</p> : null}
            <p>最后活跃：{fmtWhen(m.last_seen_at)}</p>
            <p>本周事件：{m.week_event_count}</p>
            <p className="fs-muted">
              在读{" "}
              {m.literature_href ? (
                <Link to={m.literature_href} onClick={(e) => e.stopPropagation()}>
                  {m.reading_count ?? 0} 篇
                </Link>
              ) : (
                <>{m.reading_count ?? 0} 篇</>
              )}
              {m.want_read_count ? (
                <>
                  {" · "}
                  {m.want_read_href ? (
                    <Link to={m.want_read_href} onClick={(e) => e.stopPropagation()}>
                      想读 {m.want_read_count}
                    </Link>
                  ) : (
                    `想读 ${m.want_read_count}`
                  )}
                </>
              ) : (
                ""
              )}
              {m.avg_progress_pct ? ` · 均 ${Math.round(m.avg_progress_pct)}%` : ""}
              {m.touched_count
                ? ` · 已写方法 ${m.method_card_count || 0}/${m.touched_count}`
                : ""}
              {m.note_count ? ` · 笔记 ${m.note_count}` : ""}
              {m.tutor_count ? ` · 辅导 ${m.tutor_count}` : ""}
              {m.week_reading_started || m.week_cards_made || m.week_notes || m.week_manuscripts || m.week_tutor
                ? ` · 本周开读 ${m.week_reading_started || 0} / 出卡 ${m.week_cards_made || 0} / 笔记 ${m.week_notes || 0} / 写稿 ${m.week_manuscripts || 0} / 辅导 ${m.week_tutor || 0}`
                : ""}
              {m.week_reading_titles?.length ? (
                <>
                  {" · "}
                  <WeekPaperChips items={m.week_reading_titles} prefix="本周开读" />
                </>
              ) : null}
              {m.week_card_titles?.length ? (
                <>
                  {" · "}
                  <WeekPaperChips items={m.week_card_titles} prefix="本周出卡" />
                </>
              ) : null}
              {m.week_note_titles?.length ? (
                <>
                  {" · "}
                  <WeekPaperChips items={m.week_note_titles} prefix="本周笔记" />
                </>
              ) : null}
              {m.week_ms_titles?.length ? (
                <>
                  {" · "}
                  <WeekPaperChips items={m.week_ms_titles} prefix="本周写稿" />
                </>
              ) : null}
              {m.weak_card_titles?.length ? (
                <>
                  {" · "}
                  <WeekPaperChips items={m.weak_card_titles} prefix="未写出" />
                </>
              ) : null}
              {m.week_fail_titles?.length ? (
                <>
                  {" · "}
                  <WeekPaperChips items={m.week_fail_titles} prefix="炼失败" />
                </>
              ) : null}
              {m.week_empty_ai?.length ? (
                <div onClick={(e) => e.stopPropagation()}>
                  <p className="fs-muted">本周没查到材料</p>
                  {m.week_empty_ai.map((row) => (
                    <div key={row.trace_id}>
                      <span className="fs-muted">{row.question}</span>
                      {aiToolsLine(row.tools)}
                      <AgentTraceBox traceId={row.trace_id} />
                    </div>
                  ))}
                </div>
              ) : null}
              {m.week_degraded_ai?.length ? (
                <div onClick={(e) => e.stopPropagation()}>
                  <p className="fs-muted">本周这次降级了</p>
                  {m.week_degraded_ai.map((row) => (
                    <div key={row.trace_id}>
                      <span className="fs-muted">{row.question}</span>
                      {aiToolsLine(row.tools)}
                      <AgentTraceBox traceId={row.trace_id} />
                    </div>
                  ))}
                </div>
              ) : null}
              {m.unread_reproduce_titles?.length ? (
                <>
                  {" · "}
                  <WeekPaperChips items={m.unread_reproduce_titles} prefix="在读未复现" />
                </>
              ) : null}
              {m.week_tutor_titles?.length ? (
                <>
                  {" · "}
                  <WeekPaperChips items={m.week_tutor_titles} prefix="本周辅导" />
                </>
              ) : null}
              {m.manuscript_dwell_days ? ` · 论文停留 ${m.manuscript_dwell_days} 天` : ""}
              {m.awaiting_gpu_count ? ` · 在等机器 ${m.awaiting_gpu_count}` : ""}
              {m.blocked_count ? ` · 缺数据 ${m.blocked_count}` : ""}
            </p>
            <p>
              在读：
              {m.readings?.length
                ? m.readings.map((r) => (
                    <span key={r.id} style={{ marginRight: 8 }}>
                      <Link to={r.href || literatureHref(r.id, r.last_page_no)} onClick={(e) => e.stopPropagation()}>
                        {r.title}
                      </Link>
                      {r.status && r.status !== "reading" ? <ReadTag status={r.status} /> : null}
                      {r.last_page_no ? <span className="fs-muted"> p.{r.last_page_no}</span> : null}
                      {r.progress_pct ? <span className="fs-muted"> {Math.round(r.progress_pct)}%</span> : null}
                      {r.has_method_card ? <span className="fs-muted"> · 已有这篇的方法</span> : null}
                      {r.has_note ? <span className="fs-muted"> · 有笔记</span> : null}
                      {r.excerpt ? <span className="fs-muted"> {r.excerpt}</span> : null}{" "}
                      <PdfGapLink row={r} /> <AnalyzeChip row={r} />{" "}
                      {r.direction_suggestions?.length ? (
                        <DirectionHangChips
                          suggestions={r.direction_suggestions}
                          busyId={hangBusy.startsWith(`${r.id}:`) ? hangBusy.slice(String(r.id).length + 1) : ""}
                          onHang={(s) => hangReading(r, s)}
                        />
                      ) : null}{" "}
                      {r.latest_reproduce?.measured ? (
                        <span className="fs-muted"> · 已经跑出结果</span>
                      ) : r.latest_reproduce ? (
                        <span className="fs-muted"> · 还没跑出结果</span>
                      ) : null}{" "}
                      <ActionChip
                        to={r.latest_reproduce?.href || r.reproduce_href || `/labs/${id}/reproduces?literature_item_id=${encodeURIComponent(r.id)}`}
                        tone={r.latest_reproduce?.measured ? "" : "gold"}
                        onClick={(e) => e.stopPropagation()}
                      >
                        {r.latest_reproduce ? "查看复现" : "去复现"}
                      </ActionChip>
                    </span>
                  ))
                : "组员读了文献会显示在这里"}
            </p>
            <div>
              论文：
              {m.manuscripts?.length
                ? m.manuscripts.map((x) => (
                    <p key={x.id} style={{ margin: "4px 0" }}>
                      <Link to={x.href || `/manuscripts/${x.id}`} onClick={(e) => e.stopPropagation()}>
                        {x.title}
                      </Link>{" "}
                      <StatusTag status={x.status} map={MS_STATUS} />
                      {x.related_work_written === false ? <span className="fs-muted"> · 未写相关工作</span> : null}
                      {x.core_missing_count ? <span className="fs-muted"> · 这几篇还没写进正文 {x.core_missing_count}</span> : null}
                      {x.ungrounded_reproduce ? <span className="fs-muted"> · 写了结果，组里还没跑过</span> : null}
                      {x.excerpt ? <span className="fs-muted"> {x.excerpt}</span> : null}{" "}
                      <ActionChip
                        to={writingDeskHref(x.writing_href, id, x.id, "相关工作")}
                        onClick={(e) => e.stopPropagation()}
                      >
                        去写作
                      </ActionChip>
                    </p>
                  ))
                : "组员写了论文会显示在这里"}
            </div>
            <p>
              实验任务：
              {m.agents?.length
                ? m.agents.map((a) => (
                    <Tag
                      key={a.id}
                      color={a.status === "awaiting_gpu" ? "gold" : undefined}
                      onClick={(e) => {
                        e.stopPropagation();
                        nav(a.href || (a.kind === "reproduce" ? `/reproduces/${a.id}` : `/runs/${a.id}`));
                      }}
                    >
                      {a.kind === "reproduce" ? "复现" : "文献调研"} · {RUN_STATUS[a.status] || a.status}
                      {a.status === "awaiting_gpu" ? " · 在等机器" : ""}
                    </Tag>
                  ))
                : "组员开跑后会显示在这里"}
            </p>
                      </>
                    ),
                  },
                ]}
              />
            </div>
            ) : null}
          </Card>
          );
        })}
      </div>
      {guideOpen ? (
        <Collapse ghost items={[{ key: "guide", label: "开始使用", children: <GuideList items={data.guide || []} /> }]} />
      ) : null}
      {(data.members || []).some((m) => (m.membership_status || "active") !== "active") ? (
        <Card title="历史成员">
          <p className="fs-muted">休学或已毕业，不再计入尚未开读。点姓名仍可看历史进展。</p>
          {(data.members || [])
            .filter((m) => (m.membership_status || "active") !== "active")
            .map((m) => (
              <p key={m.user_id}>
                <Link to={`/labs/${id}/cockpit/members/${m.user_id}`}>{spokenName(m.display_name) || "成员"}</Link>
                <span className="fs-muted"> · {m.membership_status === "graduated" ? "已毕业" : "休学"}</span>
              </p>
            ))}
        </Card>
      ) : null}
      {(data.recent_events || []).length ? (
      <Collapse
        ghost
        items={[{
          key: "events",
          label: "最近动态",
          children: (
            <>
        {(data.recent_events || []).slice(0, 12).map((e) => (
          <p key={e.id}>
            <ActivityLine event={e} labId={id} />
          </p>
        ))}
        {!(data.recent_events || []).length ? (
          <p className="fs-muted">实验室事件会在此出现。入库、在读、论文与组会都会留下记录。</p>
        ) : null}
            </>
          ),
        }]}
      />
      ) : null}
    </Space>
  );
}

export function CockpitMember() {
  const { id, userId } = useParams();
  const me = useSession((s) => s.me);
  const teacher = roleOf(me, id) === "teacher" || !!me?.is_admin;
  const [params, setParams] = useSearchParams();
  const directionId = params.get("direction_id") || undefined;
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [progress, setProgress] = useState([]);
  const [progPage, setProgPage] = useState(1);
  const [progTotal, setProgTotal] = useState(0);
  const [progErr, setProgErr] = useState("");
  const [progKnown, setProgKnown] = useState(false);
  const [itemsEmptyText, setItemsEmptyText] = useState("");
  const [itemsEmptyHref, setItemsEmptyHref] = useState("");
  const [includesChildren, setIncludesChildren] = useState(false);
  const [scanCapped, setScanCapped] = useState(false);
  const [dirs, setDirs] = useState([]);
  const [hangBusy, setHangBusy] = useState("");
  const hangReading = async (row, s) => {
    if (!row?.id || !s?.id || hangBusy) return;
    setHangBusy(`${row.id}:${s.id}`);
    try {
      const next = [...new Set([...(row.direction_ids || []), s.id])];
      await api.patchLiterature(row.id, { direction_ids: next });
      message.success("已挂到方向");
      load();
    } catch (e) {
      message.error(formatError(e, "无法挂方向"));
    } finally {
      setHangBusy("");
    }
  };
  const PROG_SIZE = 50;
  const loadProgress = (page) =>
    api
      .progress(id, userId, page, { direction_id: directionId })
      .then((r) => {
        setProgress(Array.isArray(r.data.items) ? r.data.items : []);
        setProgTotal(r.data.total || 0);
        setProgPage(page);
        setProgKnown(true);
        setIncludesChildren(!!r.data.includes_children);
        setScanCapped(!!r.data.scan_capped);
        setItemsEmptyText(r.data.items_empty_text || "");
        setItemsEmptyHref(r.data.items_empty_next?.href || "");
        setProgErr("");
      })
      .catch((e) => {
        setProgress([]);
        setProgTotal(0);
        setProgKnown(false);
        setProgErr(formatError(e, "成员动态加载失败"));
      });
  const load = () => {
    setErr("");
    api
      .cockpitMember(id, userId, { direction_id: directionId })
      .then((r) => setData(r.data))
      .catch((e) => setErr(formatError(e, "成员进展加载失败")));
    loadProgress(1);
  };
  useEffect(() => {
    load();
    api.directions(id).then((r) => setDirs(asItems(r.data))).catch((e) => {
      setDirs([]);
      message.error(formatError(e, "方向列表没打开"));
    });
  }, [id, userId, directionId]);
  if (err) return <LoadFailed message={err} onRetry={load} />;
  if (!data) return <PageLoading label="正在打开成员进展…" />;
  const feed = progKnown ? progress : data.timeline || [];
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        title={spokenName(data.user.display_name) || "这位成员"}
        hint={
          <>
            {directionId ? "只看这个方向。" : "这周在读什么、论文写到哪。"}
            {scanCapped ? " 只看最近 800 条，不是全部历史。" : ""}
          </>
        }
      >
        {[spokenName(String(data.user.email || "").split("@")[0]) ? data.user.email : "", data.user.title, `最后活跃 ${fmtWhen(data.user.last_seen_at)}`].filter(Boolean).join(" · ")}
        {data.profile?.bio || (data.profile?.interests || []).length || (data.profile?.skills || []).length || (data.profile?.directions || []).length || data.profile?.expected_graduate_at ? (
          <p className="fs-muted" style={{ marginTop: 8 }}>
            {data.profile.bio ? <span>{data.profile.bio} </span> : null}
            {(data.profile.interests || []).length ? <span>兴趣：{(data.profile.interests || []).join("、")} </span> : null}
            {(data.profile.directions || []).length ? (
              <span>方向：{(data.profile.directions || []).map((d) => d.name).filter(Boolean).join("、")} </span>
            ) : null}
            {data.profile.expected_graduate_at ? <span>预计毕业 {fmtWhen(data.profile.expected_graduate_at)} </span> : null}
            {(data.profile.skills || []).length ? <span>技能：{(data.profile.skills || []).join("、")} </span> : null}
            {(data.profile.identity || {}).confirmed ? <span>公开学术身份已确认。 </span> : <span>公开学术身份还没确认。 </span>}
            <Link to={`/labs/${id}/members`}>到成员页改档案</Link>
          </p>
        ) : null}
        {(data.week_reading_titles || [])[0] ? (
          <p>
            <Link to={(data.week_reading_titles[0].href || `/literature/${data.week_reading_titles[0].id}`) + ((data.week_reading_titles[0].last_page_no && !String(data.week_reading_titles[0].href || "").includes("page=")) ? `?page=${data.week_reading_titles[0].last_page_no}` : "")}>
              这周在读《{data.week_reading_titles[0].title}》
            </Link>
          </p>
        ) : null}
        {(data.week_ms_titles || [])[0] ? (
          <p>
            <Link to={data.week_ms_titles[0].href || `/manuscripts/${data.week_ms_titles[0].id}`}>
              论文写到{data.week_ms_titles[0].section || "这一节"}
            </Link>
          </p>
        ) : null}
        {(data.unread_reproduce_titles || data.weak_card_titles || [])[0] ? (
          <p>
            <Link to={(data.unread_reproduce_titles || data.weak_card_titles)[0].href || "#"}>
              卡在一句还没写完
            </Link>
          </p>
        ) : null}
      </PageHeader>
      {(data.week_reading_started || data.week_cards_made || data.week_notes || data.week_manuscripts || data.week_tutor || (data.week_reading_titles || []).length || (data.week_card_titles || []).length || (data.week_note_titles || []).length || (data.week_ms_titles || []).length || (data.week_tutor_titles || []).length || (data.weak_card_titles || []).length || (data.week_fail_titles || []).length || (data.unread_reproduce_titles || []).length) ? (
        <Collapse
          ghost
          className="fs-home-more"
          items={[{
            key: "counts",
            label: "这周的数字",
            children: (
              <>
        {data.week_reading_started || data.week_cards_made || data.week_notes || data.week_manuscripts || data.week_tutor ? (
          <p className="fs-muted" style={{ marginTop: 4 }}>
            本周开读 {data.week_reading_started || 0} / 出卡 {data.week_cards_made || 0} / 笔记 {data.week_notes || 0}
            / 写稿 {data.week_manuscripts || 0} / 辅导 {data.week_tutor || 0}
          </p>
        ) : null}
        {data.week_reading_titles?.length || data.week_card_titles?.length || data.week_note_titles?.length || data.week_ms_titles?.length || data.week_tutor_titles?.length || data.weak_card_titles?.length || data.week_fail_titles?.length || data.unread_reproduce_titles?.length ? (
          <p className="fs-muted" style={{ marginTop: 4 }}>
            <WeekPaperChips items={data.week_reading_titles} prefix="本周开读" />
            {data.week_reading_titles?.length && (data.week_card_titles?.length || data.week_note_titles?.length || data.week_ms_titles?.length || data.week_tutor_titles?.length || data.weak_card_titles?.length || data.week_fail_titles?.length || data.unread_reproduce_titles?.length) ? " · " : null}
            <WeekPaperChips items={data.week_card_titles} prefix="本周出卡" />
            {data.week_card_titles?.length && (data.week_note_titles?.length || data.week_ms_titles?.length || data.week_tutor_titles?.length || data.weak_card_titles?.length || data.week_fail_titles?.length || data.unread_reproduce_titles?.length) ? " · " : null}
            <WeekPaperChips items={data.week_note_titles} prefix="本周笔记" />
            {data.week_note_titles?.length && (data.week_ms_titles?.length || data.week_tutor_titles?.length || data.weak_card_titles?.length || data.week_fail_titles?.length || data.unread_reproduce_titles?.length) ? " · " : null}
            <WeekPaperChips items={data.week_ms_titles} prefix="本周写稿" />
            {data.week_ms_titles?.length && (data.week_tutor_titles?.length || data.weak_card_titles?.length || data.week_fail_titles?.length || data.unread_reproduce_titles?.length) ? " · " : null}
            <WeekPaperChips items={data.week_tutor_titles} prefix="本周辅导" />
            {data.week_tutor_titles?.length && (data.weak_card_titles?.length || data.week_fail_titles?.length || data.unread_reproduce_titles?.length) ? " · " : null}
            <WeekPaperChips items={data.weak_card_titles} prefix="未写出" />
            {data.weak_card_titles?.length && (data.week_fail_titles?.length || data.unread_reproduce_titles?.length) ? " · " : null}
            <WeekPaperChips items={data.week_fail_titles} prefix="炼失败" />
            {data.week_fail_titles?.length && data.unread_reproduce_titles?.length ? " · " : null}
            <WeekPaperChips items={data.unread_reproduce_titles} prefix="在读未复现" />
          </p>
        ) : null}
              </>
            ),
          }]}
        />
      ) : null}
      {dirs.length ? (
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="按方向看该成员动态"
        style={{ width: 240 }}
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
      ) : null}
      {(data.recent_ai || []).length ? (
        <Card title="最近问 / 辅导">
          {(data.recent_ai || []).map((row) => (
            <div key={row.trace_id} style={{ marginBottom: 8 }}>
              <p>
                {row.label || (row.feature === "tutor" ? "辅导" : "实验室问")}
                {row.degraded ? <span className="fs-muted"> · 这次降级了</span> : null}
                {row.question ? <span className="fs-muted"> · {row.question}</span> : null}
                {row.ground_why ? <span className="fs-muted"> · {row.ground_why}</span> : null}
                {(row.tools || []).length ? (
                  <span className="fs-muted">
                    {" "}
                    · 用过：{(row.tools || []).map(toolLabel).filter(Boolean).join("、")}
                  </span>
                ) : null}
              </p>
              <AgentTraceBox traceId={row.trace_id} />
              <ContinueAskChip last={row} meId={me?.id} />
            </div>
          ))}
        </Card>
      ) : null}
      {(data.week_empty_ai || []).length ? (
        <Card title="本周没查到材料">
          {(data.week_empty_ai || []).map((row) => (
            <div key={row.trace_id} style={{ marginBottom: 8 }}>
              <p>
                {row.label || "问"}
                {row.question ? <span className="fs-muted"> · {row.question}</span> : null}
                {row.ground_why ? <span className="fs-muted"> · {row.ground_why}</span> : null}
                {aiToolsLine(row.tools)}
              </p>
              <AgentTraceBox traceId={row.trace_id} />
            </div>
          ))}
        </Card>
      ) : null}
      {(data.week_degraded_ai || []).length ? (
        <Card title="本周这次降级了">
          {(data.week_degraded_ai || []).map((row) => (
            <div key={row.trace_id} style={{ marginBottom: 8 }}>
              <p>
                {row.label || "问"}
                {row.question ? <span className="fs-muted"> · {row.question}</span> : null}
                {row.ground_why ? <span className="fs-muted"> · {row.ground_why}</span> : null}
                {aiToolsLine(row.tools)}
              </p>
              <AgentTraceBox traceId={row.trace_id} />
            </div>
          ))}
        </Card>
      ) : null}
      {data.week_never_ai ? (
        <p className="fs-muted">
          本周没问过 <AskWeekChip meId={me?.id} userId={userId} labId={id} />
        </p>
      ) : null}
      {data.stuck?.empty ? (
        <p className="fs-muted">{data.stuck.empty_text || "这名成员当前没有还没做完的事。"}</p>
      ) : (
        <Collapse
          ghost
          items={[
            {
              key: "stuck",
              label: "需要关注",
              children: <NextStepList steps={data.stuck?.steps} title="谁还没做完" labId={id} canSearch={teacher} />,
            },
          ]}
        />
      )}
      <UnusedTitleChips
        titles={data.unused_titles || []}
        href={data.unused_research_href || `/labs/${id}/runs?unused=1&requester_id=${userId}`}
        more={data.unused_title_more || 0}
      />
      <Card
        title="正在读"
        extra={
          data.literature_href ? (
            <Link to={data.literature_href}>{data.literature_label || "该成员文献"}</Link>
          ) : null
        }
      >
        {(data.reading_now || []).map((r) => (
          <p key={r.id}>
            <Link to={r.href || literatureHref(r.id, r.last_page_no)}>{r.title}</Link>
            {r.last_page_no ? <span className="fs-muted"> p.{r.last_page_no}</span> : null}
            {r.progress_pct ? <span className="fs-muted"> {Math.round(r.progress_pct)}%</span> : null}
            {r.has_method_card ? <span className="fs-muted"> · 已有这篇的方法</span> : null}
            {r.has_note ? <span className="fs-muted"> · 有笔记</span> : null}{" "}
            <PdfGapLink row={r} /> <AnalyzeChip row={r} />{" "}
            {r.direction_suggestions?.length ? (
              <DirectionHangChips
                suggestions={r.direction_suggestions}
                busyId={hangBusy.startsWith(`${r.id}:`) ? hangBusy.slice(String(r.id).length + 1) : ""}
                onHang={(s) => hangReading(r, s)}
              />
            ) : null}{" "}
            {r.latest_reproduce?.measured ? (
              <span className="fs-muted"> · 已经跑出结果</span>
            ) : r.latest_reproduce ? (
              <span className="fs-muted"> · 还没跑出结果</span>
            ) : null}{" "}
            <ActionChip
              to={r.latest_reproduce?.href || r.reproduce_href || `/labs/${id}/reproduces?literature_item_id=${encodeURIComponent(r.id)}`}
              tone={r.latest_reproduce?.measured ? "" : "gold"}
            >
              {r.latest_reproduce ? "查看复现" : "去复现"}
            </ActionChip>
          </p>
        ))}
        {!data.reading_now?.length ? <p className="fs-muted">暂无在读</p> : null}
        {data.reading_now_more ? (
          <p>
            <Link to={data.literature_href || `/labs/${id}/literature?reader_id=${userId}&reading_status=reading`}>
              还有 {data.reading_now_more} 篇在文献库
            </Link>
          </p>
        ) : null}
      </Card>
      {(data.want_reads || []).length ? (
        <Card title="想读">
          {data.want_reads.map((r) => (
            <p key={r.id}>
              <Link to={r.href || literatureHref(r.id, r.last_page_no)}>{r.title}</Link> <ReadTag status={r.status} />
              {r.last_page_no ? <span className="fs-muted"> p.{r.last_page_no}</span> : null}
              {r.has_method_card ? <span className="fs-muted"> · 已有这篇的方法</span> : null}
              {r.has_note ? <span className="fs-muted"> · 有笔记</span> : null}{" "}
              <PdfGapLink row={r} /> <AnalyzeChip row={r} />{" "}
              {r.latest_reproduce?.measured ? (
                <span className="fs-muted"> · 已经跑出结果</span>
              ) : r.latest_reproduce ? (
                <span className="fs-muted"> · 还没跑出结果</span>
              ) : null}{" "}
              <ActionChip
                to={r.latest_reproduce?.href || r.reproduce_href || `/labs/${id}/reproduces?literature_item_id=${encodeURIComponent(r.id)}`}
                tone={r.latest_reproduce?.measured ? "" : "gold"}
              >
                {r.latest_reproduce ? "查看复现" : "去复现"}
              </ActionChip>
            </p>
          ))}
          {data.want_read_more ? (
            <p>
              <Link to={data.want_read_href || `/labs/${id}/literature?reader_id=${userId}&reading_status=want_read`}>
                还有 {data.want_read_more} 篇在文献库
              </Link>
            </p>
          ) : null}
        </Card>
      ) : null}
      {(data.finished_readings || []).length ? (
        <Card title="已读">
          {data.finished_readings.map((r) => (
            <p key={r.id}>
              <Link to={r.href || literatureHref(r.id, r.last_page_no)}>{r.title}</Link>
              {r.last_page_no ? <span className="fs-muted"> p.{r.last_page_no}</span> : null}
              {r.progress_pct ? <span className="fs-muted"> {Math.round(r.progress_pct)}%</span> : null}
              {r.has_method_card ? <span className="fs-muted"> · 已有这篇的方法</span> : null}
              {r.has_note ? <span className="fs-muted"> · 有笔记</span> : null}{" "}
              <PdfGapLink row={r} /> <AnalyzeChip row={r} />{" "}
              {r.latest_reproduce?.measured ? (
                <span className="fs-muted"> · 已经跑出结果</span>
              ) : r.latest_reproduce ? (
                <span className="fs-muted"> · 还没跑出结果</span>
              ) : null}{" "}
              <ActionChip
                to={r.latest_reproduce?.href || r.reproduce_href || `/labs/${id}/reproduces?literature_item_id=${encodeURIComponent(r.id)}`}
                tone={r.latest_reproduce?.measured ? "" : "gold"}
              >
                {r.latest_reproduce ? "查看复现" : "去复现"}
              </ActionChip>
            </p>
          ))}
          {data.finished_more ? (
            <p>
              <Link to={data.finished_href || `/labs/${id}/literature?reader_id=${userId}&reading_status=finished`}>
                还有 {data.finished_more} 篇在文献库
              </Link>
            </p>
          ) : null}
        </Card>
      ) : null}
      {(data.agents || []).length ? (
        <Card title="进行中的任务">
          {data.agents.map((a) => (
            <p key={a.id}>
              <Link to={a.href || (a.kind === "reproduce" ? `/reproduces/${a.id}` : `/runs/${a.id}`)}>
                {a.kind === "reproduce" ? "复现" : "文献调研"} · {a.title || a.id}
              </Link>
              <StatusTag status={a.status} map={RUN_STATUS} />
              {a.next_step || a.feedback ? <FeedbackStrip row={a} /> : null}
              <ResumeJob row={a} onDone={load} />
            </p>
          ))}
        </Card>
      ) : null}
      <Card
        title="论文正文摘要"
        extra={<Link to={data.manuscripts_href || `/labs/${id}/manuscripts?owner_id=${userId}`}>{data.manuscripts_label || "该成员论文"}</Link>}
      >
        {(data.manuscripts || []).map((x) => (
          <p key={x.id}>
            <Link to={x.href || `/manuscripts/${x.id}`}>{x.title}</Link> <StatusTag status={x.status} map={MS_STATUS} />{" "}
            {x.related_work_written === false ? <span className="fs-muted">未写相关工作</span> : null}{" "}
            {x.core_missing_count ? <span className="fs-muted">这几篇还没写进正文 {x.core_missing_count}</span> : null}{" "}
            {x.ungrounded_reproduce ? <span className="fs-muted">写了结果，组里还没跑过</span> : null}{" "}
            <ActionChip to={writingDeskHref(x.writing_href, id, x.id, "相关工作")}>去写作</ActionChip>
            <br />
            <span className="fs-muted">{x.excerpt || "（空）"}</span>
          </p>
        ))}
        {!data.manuscripts?.length ? <p className="fs-muted">暂无论文</p> : null}
      </Card>
      {progErr ? <LoadFailed message={progErr} onRetry={() => loadProgress(progPage)} /> : null}
      <Timeline
        items={feed.map((e) => ({
          children: <ActivityLine event={e} labId={id} />,
        }))}
      />
      {!progErr && !feed.length ? (
        <EmptyHint to={itemsEmptyHref || undefined}>
          {itemsEmptyText || "该成员还没有可读事件。在读、笔记、论文更新会出现在这里。"}
        </EmptyHint>
      ) : null}
      {progTotal > PROG_SIZE ? (
        <Space>
          <span className="fs-press-wrap" onClick={whenBlocked(progPage <= 1, "已经是第一页")}>
          <Button size="small" disabled={progPage <= 1} onClick={() => loadProgress(progPage - 1)}>
            上一页
          </Button>
          </span>
          <span className="fs-muted">
            {progPage} / {Math.max(1, Math.ceil(progTotal / PROG_SIZE))}
          </span>
          <span className="fs-press-wrap" onClick={whenBlocked(progPage * PROG_SIZE >= progTotal, "已经是最后一页")}>
          <Button size="small" disabled={progPage * PROG_SIZE >= progTotal} onClick={() => loadProgress(progPage + 1)}>
            下一页
          </Button>
          </span>
        </Space>
      ) : null}
    </Space>
  );
}
