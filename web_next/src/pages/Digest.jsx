import { useEffect, useState } from "react";
import { Button, Card, Collapse, Select, Space, Switch, Table, message } from "antd";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, asItems } from "../api.js";
import { formatError, VERB_LABEL } from "../err.js";
import { ActionChip, AgentTraceBox, AiOverviewCard, AskWeekChip, ContinueAskChip, DirectionFilterHint, DirectionSelectEmpty, EmptyHint, FeedbackStrip, HintTip, LoadFailed, NotRunTitleChips, PageHeader, ResumeJob, SituationBar, StuckBriefBar, UnusedTitleChips, aiToolsLine, fmtWhen, whenBlocked } from "../ui.jsx";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";
import { DigestCompareBar, FunnelChart, FunnelEmptyHero, FunnelLeakChips } from "../charts.jsx";

export default function Digest() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const directionId = params.get("direction_id") || undefined;
  const me = useSession((s) => s.me);
  const teacher = roleOf(me, id) === "teacher" || !!me?.is_admin;
  const [dirs, setDirs] = useState([]);
  const [includesChildren, setIncludesChildren] = useState(false);
  const [rows, setRows] = useState([]);
  const [scope, setScope] = useState("");
  const [recommendHref, setRecommendHref] = useState("");
  const [err, setErr] = useState("");
  const [mail, setMail] = useState(false);
  const [mailKnown, setMailKnown] = useState(false);
  const [mailErr, setMailErr] = useState("");
  const [spine, setSpine] = useState(null);
  const [ungrounded, setUngrounded] = useState(0);
  const [ungroundedHref, setUngroundedHref] = useState("");
  const [situation, setSituation] = useState(null);
  const [aiBrief, setAiBrief] = useState("");
  const [stuckBrief, setStuckBrief] = useState("");
  const [stuckPack, setStuckPack] = useState(null);
  const [delta, setDelta] = useState(null);
  const [weekChange, setWeekChange] = useState(null);
  const [stuckNote, setStuckNote] = useState("");
  const [unusedN, setUnusedN] = useState(0);
  const [unusedHref, setUnusedHref] = useState("");
  const [unusedTitles, setUnusedTitles] = useState([]);
  const [unusedTitleMore, setUnusedTitleMore] = useState(0);
  const [funnel, setFunnel] = useState(null);
  const [itemsEmptyText, setItemsEmptyText] = useState("");
  const [itemsEmptyHref, setItemsEmptyHref] = useState("");
  const [lastAi, setLastAi] = useState([]);
  const [weekEmptyAi, setWeekEmptyAi] = useState([]);
  const [weekDegradedAi, setWeekDegradedAi] = useState([]);
  const [weekNeverAi, setWeekNeverAi] = useState([]);
  const load = () =>
    api
      .digest(id, 7, { direction_id: directionId })
      .then((r) => {
        setRows(Array.isArray(r.data.items) ? r.data.items : []);
        setScope(r.data.scope || "");
        setRecommendHref(r.data.recommend_href || "");
        setSpine(r.data.reproduce_spine || null);
        setUngrounded(r.data.manuscripts_ungrounded || 0);
        setUngroundedHref(r.data.manuscripts_ungrounded_href || "");
        setSituation(r.data.situation || null);
        setAiBrief(r.data.ai_brief_md || "");
        setStuckBrief(r.data.stuck_brief_md || r.data.stuck_brief?.text || "");
        setStuckPack(r.data.stuck_brief || null);
        setDelta(r.data.delta || null);
        setWeekChange(r.data.week_change || null);
        setStuckNote(r.data.stuck_count_note || "");
        setUnusedN(r.data.unused_research || 0);
        setUnusedHref(r.data.unused_research_href || "");
        setUnusedTitles(r.data.unused_titles || []);
        setUnusedTitleMore(r.data.unused_title_more || 0);
        setFunnel(r.data.funnel || null);
        setIncludesChildren(!!r.data.includes_children);
        setItemsEmptyText(r.data.items_empty_text || "");
        setItemsEmptyHref(r.data.items_empty_next?.href || "");
        setLastAi(Array.isArray(r.data.last_ai) ? r.data.last_ai : []);
        setWeekEmptyAi(Array.isArray(r.data.week_empty_ai) ? r.data.week_empty_ai : []);
        setWeekDegradedAi(Array.isArray(r.data.week_degraded_ai) ? r.data.week_degraded_ai : []);
        setWeekNeverAi(Array.isArray(r.data.week_never_ai) ? r.data.week_never_ai : []);
        setErr("");
      })
      .catch((e) => setErr(formatError(e, "周报加载失败")));
  const loadMail = () => {
    if (!teacher) return;
    setMailErr("");
    api
      .getLab(id)
      .then((r) => {
        setMail(!!r.data.settings?.weekly_digest_email);
        setMailKnown(true);
      })
      .catch((e) => {
        setMailKnown(false);
        setMailErr(formatError(e, "邮件周报设置加载失败"));
      });
  };
  useEffect(() => {
    load();
    loadMail();
    api.directions(id).then((r) => setDirs(asItems(r.data))).catch((e) => {
      setDirs([]);
      message.error(formatError(e, "方向列表没打开"));
    });
  }, [id, teacher, directionId]);
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        title="周报"
        hint="邮件是全实验室的，不跟这次筛选走。"
      >
        这一周发生了什么
      </PageHeader>
      {weekChange?.count && weekChange?.text ? (
        <p className="fs-ask-week">
          <span className="fs-ask-week-tag">这周</span>
          {String(weekChange.text).replace(/^这周/, "")}
        </p>
      ) : null}
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="按方向"
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
      <DirectionFilterHint labId={id} teacher={teacher} dirs={dirs} />
      {stuckPack?.text || stuckBrief ? (
        <Collapse
          ghost
          items={[
            {
              key: "watch",
              label: "需要关注",
              children: (
                <StuckBriefBar brief={stuckPack || (stuckBrief ? { text: stuckBrief, href: `/labs/${id}/directions` } : null)} />
              ),
            },
          ]}
        />
      ) : null}
      {delta?.text ? <p className="fs-hint">{delta.text}</p> : null}
      <FunnelEmptyHero funnel={funnel} href={`/labs/${id}/literature`} />
      {funnel?.empty ? null : <FunnelChart funnel={funnel} />}
      <FunnelLeakChips funnel={funnel} />
      <DigestCompareBar delta={delta} />
      {stuckNote ? <p className="fs-muted">{stuckNote}</p> : null}
      <Collapse
        ghost
        items={[
          {
            key: "situation",
            label: "本周概况（与进度页相同，邮件里也会带上）",
            children: (
              <>
                <SituationBar situation={situation} />
                <AiOverviewCard overview={situation?.ai} />
                {aiBrief ? <p className="fs-muted">{aiBrief}</p> : null}
              </>
            ),
          },
        ]}
      />
      {err ? <LoadFailed message={err} onRetry={load} /> : null}
      {recommendHref ? (
        <p>
          <Link to={recommendHref}>打开按方向推荐（含组内相关）</Link>
          {scope ? <span className="fs-muted"> · {scope === "lab" ? "全组" : "仅自己"}</span> : null}
        </p>
      ) : null}
      {(lastAi || []).length ? (
        <div>
          <p>
            <b>最近问过</b>
            <span className="fs-muted"> · 原话，不是模型改写</span>
          </p>
          {lastAi.map((row) => (
            <div key={row.trace_id || row.user_id} style={{ marginBottom: 8 }}>
              <p>
                {row.display_name ? `${row.display_name} · ` : ""}
                {row.label || "问"}：{row.question}
                {row.ground_why ? <span className="fs-muted"> · {row.ground_why}</span> : null}
              </p>
              <AgentTraceBox traceId={row.trace_id} />
              <ContinueAskChip last={row} meId={me?.id} />
            </div>
          ))}
        </div>
      ) : null}
      {(weekEmptyAi || []).length ? (
        <div>
          <p>
            <b>本周没查到材料</b>
            <span className="fs-muted"> · 原话，不是模型改写</span>
          </p>
          {weekEmptyAi.map((row) => (
            <div key={row.trace_id || `${row.user_id}-empty`} style={{ marginBottom: 8 }}>
              <p>
                {row.display_name ? `${row.display_name} · ` : ""}
                {row.label || "问"}：{row.question}
                {aiToolsLine(row.tools)}
              </p>
              <AgentTraceBox traceId={row.trace_id} />
            </div>
          ))}
        </div>
      ) : null}
      {(weekDegradedAi || []).length ? (
        <div>
          <p>
            <b>本周这次降级了</b>
            <span className="fs-muted"> · 原话，不是模型改写</span>
          </p>
          {weekDegradedAi.map((row) => (
            <div key={row.trace_id || `${row.user_id}-degraded`} style={{ marginBottom: 8 }}>
              <p>
                {row.display_name ? `${row.display_name} · ` : ""}
                {row.label || "问"}：{row.question}
                {aiToolsLine(row.tools)}
              </p>
              <AgentTraceBox traceId={row.trace_id} />
            </div>
          ))}
        </div>
      ) : null}
      {(weekNeverAi || []).length ? (
        <div>
          <p>
            <b>本周没问过</b>
            <span className="fs-muted"> · 按姓名，不是模型改写</span>
          </p>
          {weekNeverAi.map((row) => (
            <p key={row.user_id} className="fs-muted">
              {row.display_name || row.user_id} <AskWeekChip meId={me?.id} userId={row.user_id} labId={id} />
            </p>
          ))}
        </div>
      ) : null}
      {spine ? (
        <div className="fs-toolbar">
          <Link className="fs-metric" to={spine.measured_href || spine.href}>
            <b>{spine.measured ?? 0}</b>
            <span>组内已实测</span>
          </Link>
          <Link className="fs-metric" to={spine.not_run_href || spine.href}>
            <b>{spine.not_run ?? 0}</b>
            <span>还没做复现</span>
          </Link>
          <Link className="fs-metric" to={spine.stub_href || spine.href}>
            <b>{spine.stub ?? 0}</b>
            <span>试跑</span>
          </Link>
          <Link className="fs-metric" to={ungroundedHref || `/labs/${id}/manuscripts?ungrounded=1`}>
            <b>{ungrounded}</b>
            <span>写了结果，组里还没跑过</span>
          </Link>
          {unusedN ? (
            <Link className="fs-metric" to={unusedHref || `/labs/${id}/runs?unused=1`}>
              <b>{unusedN}</b>
              <span>综述没提到</span>
            </Link>
          ) : null}
        </div>
      ) : ungrounded || unusedN ? (
        <div className="fs-toolbar">
          {ungrounded ? (
            <Link className="fs-metric" to={ungroundedHref || `/labs/${id}/manuscripts?ungrounded=1`}>
              <b>{ungrounded}</b>
              <span>写了结果，组里还没跑过</span>
            </Link>
          ) : null}
          {unusedN ? (
            <Link className="fs-metric" to={unusedHref || `/labs/${id}/runs?unused=1`}>
              <b>{unusedN}</b>
              <span>综述没提到</span>
            </Link>
          ) : null}
        </div>
      ) : null}
      <UnusedTitleChips titles={unusedTitles} href={unusedHref || `/labs/${id}/runs?unused=1`} more={unusedTitleMore} />
      <NotRunTitleChips
        titles={spine?.not_run_titles}
        href={spine?.not_run_href || `/labs/${id}/reproduces?honest=not_run`}
        more={spine?.not_run_title_more || 0}
      />
      {teacher ? (
        <Space>
          <span>{mailKnown ? "邮件周报（默认关）" : mailErr ? "邮件周报（设置未知）" : "邮件周报（读取中）"}</span>
          <span className="fs-press-wrap" onClick={whenBlocked(!mailKnown, mailErr ? "邮件设置没读出来，请刷新" : "邮件设置还在读取")}>
          <Switch
            checked={mailKnown ? mail : false}
            disabled={!mailKnown}
            onChange={async (on) => {
              try {
                await api.patchLab(id, { settings: { weekly_digest_email: on } });
                setMail(on);
                setMailKnown(true);
                message.success(on ? "已打开邮件周报" : "已关闭邮件周报");
              } catch (e) {
                message.error(formatError(e, "无法保存"));
              }
            }}
          />
          </span>
          {mailErr ? <LoadFailed message={mailErr} onRetry={loadMail} /> : null}
          <HintTip title="邮件周报">
            {directionId ? "邮件仍是全实验室的，不跟上面的方向走。不是用模型写的。" : "邮件是全实验室周报，不是用模型写的。"}
          </HintTip>
          <Button
            onClick={async () => {
              try {
                await api.emailDigest(id);
                message.success("已尝试发送（开发环境可能跳过）");
              } catch (e) {
                message.error(formatError(e, "发送失败"));
              }
            }}
          >
            发送邮件预览
          </Button>
        </Space>
      ) : null}
      <Table
        rowKey="id"
        dataSource={rows}
        locale={{
          emptyText: (
            <EmptyHint to={itemsEmptyHref || undefined}>
              {itemsEmptyText || "本周暂无动态。"}
            </EmptyHint>
          ),
        }}
        columns={[
          { title: "时间", dataIndex: "created_at", render: fmtWhen, width: 160 },
          { title: "人", dataIndex: "actor_name" },
          { title: "动作", dataIndex: "verb", render: (v) => VERB_LABEL[v] || v },
          {
            title: "对象",
            render: (_, r) => (
              <span>
                <Link to={r.href}>{r.summary || r.object_type}</Link>
                {r.next_step || r.feedback ? <FeedbackStrip row={r} /> : null}
                <ResumeJob row={r} onDone={load} />
                {r.latest_reproduce?.measured ? (
                  <span className="fs-muted"> · 已经跑出结果</span>
                ) : r.latest_reproduce ? (
                  <span className="fs-muted"> · 还没跑出结果</span>
                ) : null}
                {r.ungrounded_reproduce ? <span className="fs-muted"> · 写了结果，组里还没跑过</span> : null}
                {r.unused_research?.text ? <span className="fs-muted"> · 综述没提到</span> : null}
                {r.reproduce_href ? (
                  <details className="fs-inline-more">
                    <summary>还有</summary>
                    <ActionChip to={r.latest_reproduce?.href || r.reproduce_href}>
                      {r.latest_reproduce ? "查看复现" : "去复现"}
                    </ActionChip>
                  </details>
                ) : null}
              </span>
            ),
          },
        ]}
      />
    </Space>
  );
}
