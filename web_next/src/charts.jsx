import { lazy, Suspense, useMemo, useState } from "react";
import { Alert, Button, Table } from "antd";
import { useNavigate } from "react-router-dom";
import { ActionChip, HintTip } from "./ui.jsx";

const JADE = "#3F6F54";
const TEAL = "#0F6E6B";
const GOLD = "#C47B17";
const GRAY = "#78716C";
const INK = "#1C1917";

const LazyCanvas = lazy(() => import("./charts-canvas.jsx"));

export function ChartBlock({ title, option, onPick, table, empty, note }) {
  const [mode, setMode] = useState("chart");
  if (empty) {
    return (
      <section className="fs-chart-block">
        {title ? (
          <h3>
            {title}
            {note ? <HintTip title={title}>{note}</HintTip> : null}
          </h3>
        ) : null}
        <p className="fs-muted">{empty}</p>
      </section>
    );
  }
  return (
    <section className="fs-chart-block">
      <div className="fs-chart-head">
        {title ? (
          <h3>
            {title}
            {note ? <HintTip title={title}>{note}</HintTip> : null}
          </h3>
        ) : (
          <span />
        )}
        {table ? (
          <Button size="small" type="link" onClick={() => setMode(mode === "chart" ? "table" : "chart")}>
            {mode === "chart" ? "表格" : "看图"}
          </Button>
        ) : null}
      </div>
      {mode === "table" && table ? (
        table
      ) : (
        <Suspense fallback={<p className="fs-muted">正在打开图…</p>}>
          <LazyCanvas option={option} onPick={onPick} />
        </Suspense>
      )}
    </section>
  );
}

export function FunnelChart({ funnel, title = "从文献到发表" }) {
  const nav = useNavigate();
  const stages = funnel?.stages || [];
  const option = useMemo(
    () => ({
      tooltip: { trigger: "item" },
      color: [TEAL, JADE, GOLD, GRAY],
      series: [
        {
          type: "funnel",
          left: "8%",
          width: "70%",
          minSize: "18%",
          label: { color: INK },
          data: stages.map((s) => ({
            name: `${s.label} ${s.count}${(s.conversion && s.conversion.label && s.conversion.label !== "起点") ? ` · ${s.conversion.label}` : ""}`,
            value: Math.max(s.count || 0, 0),
            href: s.href,
          })),
        },
      ],
    }),
    [stages]
  );
  return (
    <ChartBlock
      title={title}
      empty={funnel?.empty ? funnel.empty_text : stages.length ? "" : funnel?.empty_text}
      option={option}
      onPick={(href) => href && nav(href)}
      table={
        <Table
          size="small"
          rowKey="key"
          pagination={false}
          dataSource={stages}
          columns={[
            { title: "阶段", dataIndex: "label" },
            { title: "计数", dataIndex: "count" },
            { title: "转化", render: (_, r) => r.conversion?.label || "暂无" },
            {
              title: "流失",
              render: (_, r) =>
                r.leak_href && r.leak_count ? <ActionChip to={r.leak_href}>流失 {r.leak_count}</ActionChip> : "—",
            },
          ]}
        />
      }
    />
  );
}

export function FunnelLeakChips({ funnel }) {
  if (!funnel || funnel.empty) return null;
  const leaks = (funnel.stages || []).filter((s) => s.leak_href && (s.leak_count || 0) > 0);
  if (!leaks.length) return null;
  return (
    <p className="fs-unused-titles">
      {leaks.map((s) => (
        <ActionChip key={s.key} to={s.leak_href}>
          {s.label}流失 {s.leak_count}
        </ActionChip>
      ))}
      <span className="fs-muted"> </span>
    </p>
  );
}

export function FunnelEmptyHero({ funnel, href }) {
  if (!funnel?.empty) return null;
  return (
    <Alert
      type="info"
      showIcon
      style={{ marginBottom: 12 }}
      message={funnel.empty_text || "还没有文献。入库后会出现从文献到发表，不是空图。"}
      description={href ? <ActionChip to={href}>去文献库</ActionChip> : null}
    />
  );
}

export function StackedDirectionChart({ rows, note }) {
  const nav = useNavigate();
  const data = rows || [];
  const option = useMemo(
    () => ({
      tooltip: { trigger: "axis" },
      legend: { data: ["文献", "在读", "已写方法", "实测"] },
      color: [TEAL, JADE, GOLD, GRAY],
      xAxis: { type: "category", data: data.map((r) => r.name) },
      yAxis: { type: "value" },
      series: [
        { type: "bar", name: "文献", stack: "s", data: data.map((r) => ({ value: r.literature_count || 0, href: r.literature_href })) },
        { type: "bar", name: "在读", stack: "s", data: data.map((r) => ({ value: r.reading_count || 0, href: r.reading_href })) },
        { type: "bar", name: "已写方法", stack: "s", data: data.map((r) => ({ value: r.method_card_count || 0, href: r.no_card_href })) },
        { type: "bar", name: "实测", stack: "s", data: data.map((r) => ({ value: r.reproduce_measured || 0, href: r.reproduce_href })) },
      ],
    }),
    [data]
  );
  return (
    <ChartBlock
      title="方向 × 阶段"
      empty={data.length ? "" : "还没有方向数据。"}
      note={note}
      option={option}
      onPick={(href) => href && nav(href)}
      table={
        <Table
          size="small"
          rowKey={(r) => r.direction_id || r.id || r.name}
          pagination={false}
          dataSource={data}
          columns={[
            { title: "方向", dataIndex: "name" },
            { title: "文献", dataIndex: "literature_count" },
            { title: "在读", dataIndex: "reading_count" },
            { title: "已写方法", dataIndex: "method_card_count" },
            { title: "实测", dataIndex: "reproduce_measured" },
          ]}
        />
      }
    />
  );
}

export function TrendLineChart({ trend }) {
  const points = trend?.points || [];
  const option = useMemo(
    () => ({
      tooltip: { trigger: "axis" },
      color: [TEAL, GOLD, JADE],
      xAxis: { type: "category", data: points.map((p) => p.day) },
      yAxis: { type: "value" },
      series: [
        { type: "line", name: "入库", data: points.map((p) => p.literature_count || 0), smooth: true },
        { type: "line", name: "在读", data: points.map((p) => p.reading_count || p.reading_item_count || 0), smooth: true },
        { type: "line", name: "实测", data: points.map((p) => p.reproduce_measured || 0), smooth: true },
      ],
    }),
    [points]
  );
  return (
    <ChartBlock
      title="近窗趋势"
      empty={!trend?.has_rollup ? trend?.empty_text || "尚无每日汇总。" : ""}
      note={trend?.legacy_note || (trend?.legacy_rollup ? "更早的日行可能只计本节点，没有回填。" : "")}
      option={option}
      table={
        <Table
          size="small"
          rowKey="day"
          pagination={false}
          dataSource={points}
          columns={[
            { title: "日", dataIndex: "day" },
            { title: "入库", dataIndex: "literature_count" },
            { title: "在读", dataIndex: "reading_count" },
            { title: "实测", dataIndex: "reproduce_measured" },
          ]}
        />
      }
    />
  );
}

export function HeatstripChart({ members, labId }) {
  const nav = useNavigate();
  const rows = (members || []).filter((m) => (m.membership_status || "active") === "active");
  const option = useMemo(
    () => ({
      tooltip: { trigger: "item" },
      grid: { left: 80, right: 16, top: 24, bottom: 24 },
      xAxis: { type: "category", data: ["在读", "论文", "相关工作缺", "核心缺", "未测"] },
      yAxis: { type: "category", data: rows.map((m) => m.display_name || m.user_id) },
      visualMap: { min: 0, max: 6, inRange: { color: ["#E8F3EC", JADE] }, orient: "horizontal", left: "center", show: false },
      series: [
        {
          type: "heatmap",
          data: rows.flatMap((m, y) => [
            [0, y, m.reading_count || 0, m.literature_href],
            [1, y, m.manuscript_count || 0, m.manuscripts_href],
            [2, y, m.related_work_missing || 0, m.related_work_href],
            [3, y, m.core_missing || 0, m.core_missing_href],
            [4, y, m.ungrounded || 0, m.ungrounded_href],
          ]),
        },
      ],
    }),
    [rows]
  );
  return (
    <ChartBlock
      title="成员阶段覆盖"
      empty={rows.length ? "" : "还没有在组内的成员。"}
      option={option}
      onPick={(href) => href && nav(href)}
      table={
        <Table
          size="small"
          rowKey="user_id"
          pagination={false}
          dataSource={rows}
          columns={[
            { title: "成员", dataIndex: "display_name" },
            { title: "在读", dataIndex: "reading_count" },
            { title: "论文", dataIndex: "manuscript_count" },
            { title: "相关工作缺", dataIndex: "related_work_missing" },
            { title: "核心缺", dataIndex: "core_missing" },
            { title: "未测", dataIndex: "ungrounded" },
          ]}
        />
      }
    />
  );
}

export function DirectionSunburst({ rows }) {
  const nav = useNavigate();
  const roots = (rows || []).filter((r) => !r.parent_id);
  const kids = (rows || []).filter((r) => r.parent_id);
  const data = roots.map((r) => ({
    name: r.name,
    value: r.literature_count || 0,
    href: r.href,
    children: kids
      .filter((c) => c.parent_id === r.direction_id || c.parent_id === r.id)
      .map((c) => ({ name: c.name, value: c.literature_count || 0, href: c.href })),
  }));
  const option = useMemo(
    () => ({
      color: [TEAL, JADE, GOLD],
      series: [{ type: "sunburst", data, radius: ["18%", "90%"], label: { color: INK } }],
    }),
    [data]
  );
  return (
    <ChartBlock
      title="方向层级"
      empty={data.length ? "" : "还没有方向。"}
      option={option}
      onPick={(href) => href && nav(href)}
      table={
        <Table
          size="small"
          rowKey="name"
          pagination={false}
          dataSource={data}
          columns={[
            { title: "方向", dataIndex: "name" },
            { title: "文献", dataIndex: "value" },
            { title: "子课题", render: (_, r) => (r.children || []).map((c) => c.name).join("、") || "—" },
          ]}
        />
      }
    />
  );
}

export function CompareBarChart({ compare }) {
  const deltas = compare?.deltas || [];
  const option = useMemo(
    () => ({
      tooltip: { trigger: "axis" },
      color: [TEAL, GRAY],
      xAxis: { type: "category", data: deltas.map((d) => d.key) },
      yAxis: { type: "value" },
      series: [
        { type: "bar", name: "本月", data: deltas.map((d) => d.this) },
        { type: "bar", name: "上月", data: deltas.map((d) => d.last) },
      ],
    }),
    [deltas]
  );
  return (
    <ChartBlock
      title="和自己上月比"
      empty={compare?.empty ? compare.empty_text : ""}
      note={compare?.legacy_note || (compare?.legacy_rollup ? "更早的日行可能只计本节点，没有回填。" : "")}
      option={option}
      table={
        <Table
          size="small"
          rowKey="key"
          pagination={false}
          dataSource={deltas}
          columns={[
            { title: "阶段", dataIndex: "key" },
            { title: "本月", dataIndex: "this" },
            { title: "上月", dataIndex: "last" },
            { title: "差值", dataIndex: "delta" },
          ]}
        />
      }
    />
  );
}

export function CoverageRadar({ coverage }) {
  const axes = coverage?.axes || [];
  const option = useMemo(
    () => ({
      color: [TEAL],
      radar: {
        indicator: axes.map((a) => ({ name: a.label, max: 1 })),
      },
      series: [{ type: "radar", data: [{ value: axes.map((a) => a.value || 0), name: "覆盖" }] }],
    }),
    [axes]
  );
  return (
    <ChartBlock
      title="本方向阶段覆盖"
      empty={axes.length && axes.some((a) => a.value != null) ? "" : coverage?.note || "覆盖率分母为零，写暂无。"}
      option={option}
      table={
        <Table
          size="small"
          rowKey="key"
          pagination={false}
          dataSource={axes}
          columns={[
            { title: "阶段", dataIndex: "label" },
            { title: "覆盖", render: (_, r) => r.label_text || "暂无" },
          ]}
        />
      }
    />
  );
}

export function DigestCompareBar({ delta }) {
  const reading = Number(delta?.reading_started_delta || 0);
  const submitted = Number(delta?.submitted_delta || 0);
  const option = useMemo(
    () => ({
      tooltip: { trigger: "axis" },
      color: [TEAL, GOLD],
      xAxis: { type: "category", data: ["开始阅读", "在投"] },
      yAxis: { type: "value" },
      series: [{ type: "bar", data: [reading, submitted] }],
    }),
    [reading, submitted]
  );
  return (
    <ChartBlock
      title="相对上周"
      empty={delta?.text ? "" : "本周还没有可对比的事件。"}
      option={option}
      table={
        <Table
          size="small"
          rowKey="key"
          pagination={false}
          dataSource={[
            { key: "reading", label: "开始阅读", value: reading },
            { key: "submitted", label: "在投", value: submitted },
          ]}
          columns={[
            { title: "事件", dataIndex: "label" },
            { title: "相对上周", dataIndex: "value" },
          ]}
        />
      }
    />
  );
}
