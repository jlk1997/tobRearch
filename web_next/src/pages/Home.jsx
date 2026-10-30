import { useEffect, useState } from "react";
import { Button, Card, Col, Collapse, Form, Input, List, Row, Select, Space, Typography, Empty, message } from "antd";
import { Link, Navigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api.js";
import { formatError, noticeHref, literatureHref, MS_STATUS, RUN_STATUS } from "../err.js";
import { ActionChip, AgentTraceBox, AnalyzeChip, AttentionBar, DirectionGapAlert, DirectionHangChips, FeedbackStrip, GuideList, NextStepList, PageHeader, PdfGapLink, ReadTag, ReproduceChip, ReproduceHonest, ResumeJob, StatusTag, StuckBriefBar, UnusedTitleChips, EmptyHint, LoadFailed, PageLoading, spokenName } from "../ui.jsx";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";
import { writingDeskHref } from "../manuscriptSections.js";

const { Paragraph } = Typography;

function shownSteps(home, teacher) {
  if (!home) return [];
  const raw = teacher ? home.next_steps || [] : home.next_steps_shown || (home.next_steps || []).slice(0, 2);
  return raw.filter((s) => !/复现|reproduces/.test(`${s.key || ""} ${s.label || ""} ${s.text || ""} ${s.title || ""} ${s.href || ""}`));
}

function HomeDirectionForm({ home, labId, teacher, onSaved }) {
  const [saving, setSaving] = useState(false);
  const hasDirs = !!(home.directions || []).length;
  return (
    <>
      <p className="fs-hint">
        {home.my_direction_id ? (
          <Link to={`/labs/${labId}/literature?direction_id=${home.my_direction_id}`}>打开本方向文献</Link>
        ) : hasDirs ? (
          "先选定方向。"
        ) : teacher ? (
          <>
            还没有方向。
            <Link to={`/labs/${labId}/literature`}>从文献归纳</Link>
            或
            <Link to={`/labs/${labId}/directions?compose=1`}> 新建</Link>
            。
          </>
        ) : (
          "还没有方向。等导师建好后再选。"
        )}
      </p>
      <Form
        key={`home-dir-${home.my_direction_id || "none"}-${(home.my_direction_ids || []).join(",")}-${(home.directions || []).length}`}
        layout="inline"
        className="fs-home-dir"
        style={{ marginBottom: 16 }}
        initialValues={{
          direction_id: home.my_direction_id || undefined,
          direction_ids: (home.my_direction_ids || []).filter((x) => x && x !== home.my_direction_id),
        }}
        onFinish={async (v) => {
          setSaving(true);
          try {
            const extras = v.direction_ids || [];
            const primary = v.direction_id || extras[0] || null;
            const ids = primary ? [...new Set([primary, ...extras])] : extras;
            await api.pickDirection(labId, { direction_id: primary, direction_ids: ids });
            message.success("已保存方向");
            onSaved?.();
          } catch (e) {
            message.error(formatError(e, "无法保存方向"));
          } finally {
            setSaving(false);
          }
        }}
      >
        <Form.Item name="direction_id">
          <Select
            allowClear
            disabled={!hasDirs}
            placeholder={hasDirs ? "选一个方向" : teacher ? "还没有方向" : "等导师创建"}
            style={{ width: 240 }}
            options={(home.directions || []).map((d) => ({
              value: d.id,
              label: d.parent_id ? `↳ ${d.name}` : d.name,
            }))}
          />
        </Form.Item>
        {home.my_direction_id ? (
          <Form.Item name="direction_ids">
            <Select
              mode="multiple"
              allowClear
              placeholder="还可再挂几个"
              style={{ minWidth: 240 }}
              options={(home.directions || []).map((d) => ({
                value: d.id,
                label: d.parent_id ? `↳ ${d.name}` : d.name,
              }))}
            />
          </Form.Item>
        ) : null}
        {hasDirs ? (
          <Button htmlType="submit" type={home.my_direction_id ? "default" : "primary"} loading={saving}>
            {saving ? "正在保存" : "保存方向"}
          </Button>
        ) : (
          <span className="fs-press-wrap" onClick={() => message.info(teacher ? "先新建一个方向" : "等导师建好方向再选")}>
            <Button disabled style={{ pointerEvents: "none" }}>
              保存方向
            </Button>
          </span>
        )}
      </Form>
    </>
  );
}

export default function Home({ me, labId }) {
  const qc = useQueryClient();
  const setUnread = useSession((s) => s.setUnread);
  const [logForm] = Form.useForm();
  const teacher = roleOf(me, labId) === "teacher" || !!me?.is_admin;
  const [drafts, setDrafts] = useState([]);
  const [dirGap, setDirGap] = useState(null);
  const [hangBusy, setHangBusy] = useState("");
  const { data: home, isLoading, isError, error } = useQuery({
    queryKey: ["home", labId],
    queryFn: () => api.home(labId).then((r) => r.data),
    enabled: !!labId,
  });
  useEffect(() => {
    if (!labId) return undefined;
    let alive = true;
    api
      .literature(labId, { page: 1, page_size: 1 })
      .then((r) => {
        if (alive) {
          setDrafts(r.data?.direction_drafts || []);
          setDirGap(r.data?.direction_gap || null);
        }
      })
      .catch((e) => {
        if (alive) {
          setDrafts([]);
          setDirGap(null);
          message.warning(formatError(e, "方向建议没加载出来"));
        }
      });
    return () => {
      alive = false;
    };
  }, [labId, (home?.directions || []).length]);
  if (!labId) {
    if (me && !me.workbench_ready && !me.is_admin) {
      return (
        <PageHeader title="工作台">
          机构尚未开通。请打开 <Link to="/pending">开通进度</Link>
          ，或前往 <Link to="/apply">申请开通机构</Link>。
        </PageHeader>
      );
    }
    return (
      <PageHeader title="工作台">
        尚未加入实验室。请打开导师发给你的邀请链接；若你是本机构导师，可在「成员」页再建一组。
      </PageHeader>
    );
  }
  return (
    <div className="fs-workbench">
      <PageHeader title="我的" hint="接着读，或接着写。" />
      {isLoading ? <PageLoading label="正在打开工作台…" /> : null}
      {isError ? <LoadFailed message={formatError(error, "工作台加载失败")} onRetry={() => qc.invalidateQueries({ queryKey: ["home", labId] })} /> : null}
      {home ? (
        <>
        {shownSteps(home, teacher).length ? (
          <div className="fs-today">
            <NextStepList steps={shownSteps(home, teacher).slice(0, 1)} title="今天先做这一件" />
          </div>
        ) : null}
        {teacher && shownSteps(home, teacher).length > 1 ? (
          <Collapse
            ghost
            className="fs-home-more"
            items={[{
              key: "rest",
              label: "其他",
              children: (
                <>
                  {shownSteps(home, teacher).length > 1 ? (
                    <NextStepList steps={shownSteps(home, teacher).slice(1)} title="还有" />
                  ) : null}
                </>
              ),
            }]}
          />
        ) : null}
        {!teacher && (home.directions || []).length > 1 ? (
          <List
            dataSource={home.directions}
            renderItem={(d) => (
              <List.Item>
                <Link to={`/labs/${labId}/directions/${d.id}`}>{d.name}</Link>
                {d.keywords ? (
                  <span className="fs-muted">
                    在找：{Array.isArray(d.keywords) ? d.keywords.slice(0, 6).join("，") : d.keywords}
                  </span>
                ) : null}
              </List.Item>
            )}
          />
        ) : (home.directions || []).length ? (
          <Collapse
            ghost
            className="fs-home-more"
            items={[{ key: "dir", label: "改方向", children: <HomeDirectionForm home={home} labId={labId} teacher={teacher} onSaved={() => qc.invalidateQueries({ queryKey: ["home", labId] })} /> }]}
          />
        ) : !teacher ? (
          <p className="fs-muted">还没有方向。等导师建好后再选。</p>
        ) : null}
        {false && teacher && dirGap && dirGap.kind !== "no_direction" ? (
        <Collapse
          ghost
          className="fs-home-more"
          items={[{
            key: "gap",
            label: "还没放进方向的论文",
            children: (
        <DirectionGapAlert
          gap={dirGap}
          drafts={drafts}
          labId={labId}
          teacher={teacher}
          onAdopted={() => {
            qc.invalidateQueries({ queryKey: ["home", labId] });
            api
              .literature(labId, { page: 1, page_size: 1 })
              .then((r) => {
                setDrafts(r.data?.direction_drafts || []);
                setDirGap(r.data?.direction_gap || null);
              })
              .catch((e) => {
                setDrafts([]);
                setDirGap(null);
                message.warning(formatError(e, "方向建议没加载出来"));
              });
          }}
        />
            ),
          }]}
        />
        ) : null}
        {(home.my_directions || []).length ? (
          <p className="fs-muted fs-home-more" style={{ marginBottom: 16 }}>
            已选：
            {(home.my_directions || []).map((d, i) => (
              <span key={d.id}>
                {i ? " · " : ""}
                <Link to={`/labs/${labId}/literature?direction_id=${d.id}`}>{d.name}{d.is_primary ? "（主）" : ""}</Link>
              </span>
            ))}
          </p>
        ) : null}
        </>
      ) : null}
      {false && labId && ((home?.directions || []).length || (home?.readings || []).length) ? (
        <Collapse ghost className="fs-home-more" items={[{ key: "about", label: "了解这个组", children: <OnboardingCard labId={labId} /> }]} />
      ) : null}
      {false && home && teacher && home.stuck_brief?.text && !String(home.stuck_brief.text).includes("没有还没做完") ? (
        <Collapse
          ghost
          className="fs-home-more"
          items={[
            {
              key: "watch",
              label: "需要关注",
              children: (
                <>
                  <StuckBriefBar
                    brief={{
                      ...home.stuck_brief,
                      not_run: 0,
                      ungrounded: 0,
                      text: String(home.stuck_brief.text || "")
                        .replace(/还没做复现[^。]*。?/g, "")
                        .replace(/写了结果，组里还没跑过[^。]*。?/g, ""),
                    }}
                  />
                  <UnusedTitleChips
                    titles={home.stuck_brief?.unused_titles || []}
                    href={home.stuck_brief?.unused_research_href || `/labs/${labId}/runs?unused=1`}
                    more={home.stuck_brief?.unused_title_more || 0}
                  />
                </>
              ),
            },
          ]}
        />
      ) : null}
      {teacher && home && (home.last_ai?.question || (home.week_never_ai && ((home.readings || []).length || (home.manuscripts || []).length)) || (home.milestones_upcoming || []).length || home.unassigned?.count || home.queue?.awaiting_gpu || home.queue?.blocked || (home.queue?.items || []).length) ? (
        <Collapse
          ghost
          className="fs-home-more"
          items={[{
            key: "desk-rest",
            label: "其余",
            children: (
              <>
      {home.last_ai?.question || home.week_never_ai ? (
        <p className="fs-muted" style={{ marginBottom: 16 }}>
          {home.last_ai?.question ? (
            <>
              最近{home.last_ai.label || "问"}：{home.last_ai.question}
              {home.last_ai.ground_why ? ` · ${home.last_ai.ground_why}` : ""}
              {home.last_ai.href ? (
                <>
                  {" "}
                  <ActionChip to={home.last_ai.href}>接着上次问的</ActionChip>
                </>
              ) : null}
              <AgentTraceBox traceId={home.last_ai.trace_id} />
            </>
          ) : null}
          {home.week_never_ai ? (
            <>
              {home.last_ai?.question ? " " : ""}
              本周还没问过
              <ActionChip to={`/labs/${labId}/directions`}>问这些论文</ActionChip>
            </>
          ) : null}
        </p>
      ) : null}
        <AttentionBar
          labId={labId}
          milestones={home.milestones_upcoming}
          queue={{
            ...(home.queue || {}),
            items: (home.queue?.items || []).filter((it) => it.kind !== "reproduce"),
          }}
          unassigned={home.unassigned}
          onResume={() => qc.invalidateQueries({ queryKey: ["home", labId] })}
        />
              </>
            ),
          }]}
        />
      ) : null}
      {home && (teacher || (home.directions || []).length <= 1) ? (
        <>
        {(home.readings || []).length || (home.manuscripts || []).length ? (
        <Row gutter={[16, 16]}>
          <Col xs={24} md={12}>
            <Card className="fs-home-card" title={teacher ? "在读" : "接着读"} extra={<Link to={home.reading_href || `/labs/${labId}/literature`}>{home.reading_label || "全部"}</Link>}>
              <List
                dataSource={home.readings}
                locale={{ emptyText: <Empty description={<EmptyHint to={home.reading_href || `/labs/${labId}/literature`}>{teacher ? "从文献库入库并标为在读" : "去找一篇来读"}</EmptyHint>} /> }}
                renderItem={(x) => (
                  <List.Item>
                    <Link to={x.href || literatureHref(x.id, x.last_page_no)}>{x.title}</Link>
                    {teacher ? (
                      <>
                    <ReadTag status={x.status} />
                    {x.progress_pct ? <span className="fs-muted"> {Math.round(x.progress_pct)}%</span> : null}{" "}
                    <PdfGapLink row={x} /> <AnalyzeChip row={x} />
                    {x.direction_suggestions?.length ? (
                      <DirectionHangChips
                        suggestions={x.direction_suggestions}
                        busyId={hangBusy.startsWith(`${x.id}:`) ? hangBusy.slice(String(x.id).length + 1) : ""}
                        onHang={async (s) => {
                          if (!x.id || !s?.id || hangBusy) return;
                          setHangBusy(`${x.id}:${s.id}`);
                          try {
                            const next = [...new Set([...(x.direction_ids || []), s.id])];
                            await api.patchLiterature(x.id, { direction_ids: next });
                            message.success("已挂到方向");
                            qc.invalidateQueries({ queryKey: ["home", labId] });
                          } catch (e) {
                            message.error(formatError(e, "无法挂方向"));
                          } finally {
                            setHangBusy("");
                          }
                        }}
                      />
                    ) : null}
                      </>
                    ) : null}
                  </List.Item>
                )}
              />
            </Card>
          </Col>
          <Col xs={24} md={12}>
            <Card className="fs-home-card" title={teacher ? "论文" : "接着写"} extra={<Link to={home.manuscripts_href || `/labs/${labId}/manuscripts`}>{home.manuscripts_label || "全部"}</Link>}>
              <List
                dataSource={home.manuscripts}
                locale={{ emptyText: <Empty description={<EmptyHint to={home.manuscripts_href || `/labs/${labId}/manuscripts`}>先建论文</EmptyHint>} /> }}
                renderItem={(x) => (
                  <List.Item extra={teacher ? <ActionChip to={writingDeskHref(x.writing_href, labId, x.id, "相关工作")}>去写作</ActionChip> : null}>
                    <Link to={x.href || `/manuscripts/${x.id}`}>{x.title}</Link>
                    {teacher ? (
                      <>
                    <StatusTag status={x.status} map={MS_STATUS} />
                    {x.related_work_written === false ? <span className="fs-muted"> · 未写相关工作</span> : null}
                    {x.core_missing_count ? <span className="fs-muted"> · 还有 {x.core_missing_count} 篇没写进正文</span> : null}
                      </>
                    ) : null}
                  </List.Item>
                )}
              />
            </Card>
          </Col>
        </Row>
        ) : null}
        {false && teacher && (home.runs || []).length ? (
          <Collapse
            ghost
            className="fs-home-more"
            items={[{
              key: "runs",
              label: "文献调研",
              children: (
            <Card className="fs-home-card" title="进行中" extra={<Link to={home.runs_href || `/labs/${labId}/runs`}>{home.runs_label || "文献调研"}</Link>}>
              <List
                dataSource={home.runs || []}
                locale={{ emptyText: <Empty description={<EmptyHint to={`/labs/${labId}/runs`}>文献调研会出现在这里</EmptyHint>} /> }}
                renderItem={(x) => (
                  <List.Item>
                    <Link to={x.href || `/runs/${x.id}`}>
                      {x.question || "文献调研"}
                    </Link>
                    <StatusTag status={x.status} map={RUN_STATUS} />
                    {x.honest || x.measured ? <ReproduceHonest ev={{ measured: x.measured, honest: x.honest }} /> : null}
                    {x.next_step || x.feedback ? (
                      <FeedbackStrip row={x} />
                    ) : x.status === "awaiting_gpu" || x.error_class === "awaiting_gpu" ? (
                      <span className="fs-muted"> · 在等机器</span>
                    ) : null}
                    <ResumeJob row={{ ...x, kind: "research" }} onDone={() => qc.invalidateQueries({ queryKey: ["home", labId] })} />
                  </List.Item>
                )}
              />
            </Card>
              ),
            }]}
          />
        ) : null}
        {(home.tasks || []).length || home.unread || (home.unread_items || []).length ? (
        <Collapse
          ghost
          className="fs-home-more"
          items={[
            {
              key: "more",
              label: "指派、通知与日志",
              children: (
                <Row gutter={[16, 16]}>
          <Col xs={24} md={12}>
            <Card className="fs-home-card" title="指派给我" extra={<Link to={`/labs/${labId}/tasks`}>看板</Link>}>
              <List
                dataSource={home.tasks}
                locale={{ emptyText: <Empty description={<EmptyHint to={`/labs/${labId}/tasks`}>看板里被指派的任务会出现在这里</EmptyHint>} /> }}
                renderItem={(x) => (
                  <List.Item
                    extra={
                      x.meeting_href ? (
                        <ActionChip to={x.meeting_href}>
                          {x.source_line ? `回纪要第 ${x.source_line} 行` : "回纪要"}
                        </ActionChip>
                      ) : null
                    }
                  >
                    <Link to={x.href || `/labs/${labId}/tasks?open=${x.id}`}>{x.title}</Link>
                    <StatusTag status={x.status} />
                  </List.Item>
                )}
              />
            </Card>
          </Col>
          <Col xs={24} md={12}>
            <Card className="fs-home-card" title="未读通知" extra={<Link to="/notifications">全部</Link>}>
              <List
                dataSource={home.unread_items || []}
                locale={{ emptyText: home.unread ? `${home.unread} 条未读，打开通知页查看` : "没有未读通知" }}
                renderItem={(n) => (
                  <List.Item
                    extra={
                      n.reproduce_href || n.latest_reproduce?.href ? (
                        <ReproduceChip ev={n.latest_reproduce} to={n.latest_reproduce?.href || n.reproduce_href} />
                      ) : null
                    }
                  >
                    <Link
                      to={noticeHref(n)}
                      onClick={async () => {
                        try {
                          await api.readNotification(n.id);
                          const c = await api.unreadCount();
                          setUnread(c.data.unread || 0);
                          qc.invalidateQueries({ queryKey: ["home", labId] });
                        } catch (e) {
                          message.error(formatError(e, "没标上已读，仍可打开"));
                        }
                      }}
                    >
                      {n.title}
                    </Link>
                    {n.next_step || n.feedback ? <FeedbackStrip row={n} /> : null}
                    {(n.followups || []).map((f) => (
                      <ActionChip
                        key={f.href || f.label}
                        to={f.href}
                        onClick={async () => {
                          try {
                            await api.readNotification(n.id);
                            const c = await api.unreadCount();
                            setUnread(c.data.unread || 0);
                            qc.invalidateQueries({ queryKey: ["home", labId] });
                          } catch (e) {
                            message.error(formatError(e, "没标上已读，仍可打开"));
                          }
                        }}
                      >
                        {f.label}
                      </ActionChip>
                    ))}
                    <ReproduceHonest ev={n.latest_reproduce} />
                    <ResumeJob row={n} onDone={() => qc.invalidateQueries({ queryKey: ["home", labId] })} />
                  </List.Item>
                )}
              />
            </Card>
          </Col>
          <Col xs={24} md={12}>
            <Card className="fs-home-card" title="三句工作日志" extra={<Link to={`/labs/${labId}/work-logs`}>全部</Link>}>
              <Form
                form={logForm}
                onFinish={async (v) => {
                  try {
                    await api.addWorkLog(labId, v.content);
                    logForm.resetFields();
                    message.success("已记录");
                    qc.invalidateQueries({ queryKey: ["home", labId] });
                  } catch (e) {
                    message.error(formatError(e, "无法记录"));
                  }
                }}
              >
                <Form.Item name="content" rules={[{ required: true, message: "请填写进展" }]}>
                  <Input.TextArea rows={3} placeholder="想记就写一句" />
                </Form.Item>
                <Button htmlType="submit">记录</Button>
              </Form>
            </Card>
          </Col>
                </Row>
              ),
            },
          ]}
        />
        ) : null}
        </>
      ) : null}
      {(home?.guide || []).some((g) => !g.done) ? (
        <Card title="先动手" style={{ marginTop: 16 }}>
          <Paragraph className="fs-muted">
            {(home.readings || []).length || (home.manuscripts || []).length
              ? "还有几步没做完。"
              : "组还是空的。按下面这几步就能开始。"}
          </Paragraph>
          <GuideList items={home?.guide || []} />
        </Card>
      ) : null}
    </div>
  );
}

function OnboardingCard({ labId }) {
  const [q, setQ] = useState("");
  const { data, isError, refetch } = useQuery({
    queryKey: ["onboarding", labId],
    queryFn: () => api.labOnboarding(labId).then((r) => r.data),
    enabled: !!labId,
  });
  if (!data) {
    return isError ? <LoadFailed message="入门卡加载失败" onRetry={() => refetch()} /> : null;
  }
  const ask = (text) => {
    const next = (text || q).trim();
    if (!next) return;
    const base = data.ask_href || `/labs/${labId}/kb?tab=ask&scope=onboarding`;
    const url = new URL(base, window.location.origin);
    url.searchParams.delete("tab");
    url.searchParams.set("q", next);
    const path = url.pathname.includes("/assistant") ? url.pathname : `/labs/${labId}/assistant`;
    window.location.assign(`${path}?${url.searchParams.toString()}`);
  };
  return (
    <div className="fs-onboard">
      <Paragraph className="fs-muted">
        <Link to={`/labs/${labId}/assistant`}>去问助手</Link>
      </Paragraph>
      {data.lab_brief ? <p>{data.lab_brief}</p> : null}
      {(data.directions || []).length ? (
        <p>
          方向：
          {data.directions.map((d, i) => (
            <span key={d.id}>
              {i ? " · " : ""}
              <Link to={d.href}>{d.name}</Link>
              {(d.children || []).length ? `（${d.children.map((c) => c.name).join("、")}）` : ""}
            </span>
          ))}
        </p>
      ) : (
        <p className="fs-muted">还没有登记研究方向。</p>
      )}
      {spokenName(data.me?.advisor_name) ? (
        <p>
          我的导师：
          {data.me.advisor_user_id ? (
            <Link to={`/labs/${labId}/cockpit/members/${data.me.advisor_user_id}`}>{spokenName(data.me.advisor_name)}</Link>
          ) : (
            spokenName(data.me.advisor_name)
          )}
        </p>
      ) : null}
      {(data.teachers || []).some((t) => spokenName(t.display_name)) ? (
        <p>
          导师：
          {(data.teachers || []).filter((t) => spokenName(t.display_name)).map((t, i) => (
            <span key={t.user_id}>
              {i ? " · " : ""}
              <Link to={t.href}>{spokenName(t.display_name)}</Link>
              {t.title ? `（${t.title}）` : ""}
            </span>
          ))}
        </p>
      ) : null}
      {(data.starter_reading || []).length ? (
        <p>
          {data.reading_scope === "direction" ? "本方向可先读：" : "可先读："}
          {data.starter_reading.slice(0, 3).map((p, i) => (
            <span key={p.id}>
              {i ? " · " : ""}
              <Link to={p.href}>{p.title}</Link>
            </span>
          ))}
        </p>
      ) : null}
      {(data.manuscripts || []).length ? (
        <p>
          组里在写：
          {data.manuscripts.slice(0, 2).map((m, i) => (
            <span key={m.id}>
              {i ? " · " : ""}
              <Link to={m.href}>{m.title}</Link>
            </span>
          ))}
        </p>
      ) : null}
      {(data.milestones || []).length ? (
        <p>
          {data.milestone_scope === "direction" ? "本方向节点：" : "时间节点："}
          {data.milestones.slice(0, 2).map((m, i) => (
            <span key={m.id}>
              {i ? " · " : ""}
              {m.href ? <Link to={m.href}>{m.title}</Link> : m.title}
            </span>
          ))}
        </p>
      ) : null}
      {(data.meetings || []).length ? (
        <p>
          最近组会：
          {data.meetings.slice(0, 2).map((m, i) => (
            <span key={m.id}>
              {i ? " · " : ""}
              <Link to={m.href}>{m.title}</Link>
            </span>
          ))}
        </p>
      ) : null}
      {data.next_step?.title ? (
        <p>
          {data.next_step.title}
          {(data.next_step.steps || []).map((s) => (
            <ActionChip key={s.key || s.href} to={s.href}>
              {s.label}
            </ActionChip>
          ))}
        </p>
      ) : null}
      <Space wrap>
        {(data.suggested_questions || []).map((item) => (
          <Button key={item} size="small" onClick={() => ask(item)}>
            {item}
          </Button>
        ))}
      </Space>
      <Form
        layout="inline"
        style={{ marginTop: 12 }}
        onFinish={() => ask()}
      >
        <Form.Item>
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="问实验室"
            style={{ width: 280 }}
          />
        </Form.Item>
        <Button htmlType="submit">问</Button>
      </Form>
    </div>
  );
}
