import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Select, Space, Table, message } from "antd";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, asItems } from "../api.js";
import { formatError } from "../err.js";
import { ActionChip, AnalyzeChip, DirectionFilterHint, DirectionHangChips, DirectionSelectEmpty, EmptyHint, LoadFailed, PageHeader, PageLoading, PdfGapLink, ReproduceHonest, UnusedResearchAlert } from "../ui.jsx";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";

const KIND = { cites: "引用", related: "组内相关", direction: "同方向", wrote: "写了" };
const VIA = { manuscript: "论文写了引用", method_card: "这篇的方法里的书目" };

function nodeFill(n) {
  if (n.type === "author") return "#4A6FA5";
  if (n.type === "manuscript") return "#C4A35A";
  const hon = n.latest_reproduce?.honest;
  if (hon === "measured") return "#0F6E6B";
  if (hon === "awaiting" || hon === "blocked" || hon === "not_run") return "#B7791F";
  return "#6B7280";
}

export default function Graph() {
  const { id } = useParams();
  const nav = useNavigate();
  const me = useSession((s) => s.me);
  const teacher = roleOf(me, id) === "teacher" || !!me?.is_admin;
  const [params, setParams] = useSearchParams();
  const directionId = params.get("direction_id") || undefined;
  const [data, setData] = useState({ nodes: [], edges: [] });
  const [dirs, setDirs] = useState([]);
  const [dirErr, setDirErr] = useState("");
  const [sel, setSel] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [hangBusy, setHangBusy] = useState("");
  const load = () => {
    setLoading(true);
    api
      .citationGraph(id, directionId)
      .then((r) => {
        setData(r.data);
        setErr("");
      })
      .catch((e) => setErr(formatError(e, "图谱加载失败")))
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    load();
  }, [id, directionId]);
  useEffect(() => {
    if (!id) return undefined;
    api
      .directions(id)
      .then((r) => {
        setDirs(asItems(r.data));
        setDirErr("");
      })
      .catch((e) => {
        setDirs([]);
        setDirErr(formatError(e, "方向加载失败"));
      });
    return undefined;
  }, [id]);
  const pos = useMemo(() => {
    const nodes = data.nodes || [];
    const n = Math.max(nodes.length, 1);
    return Object.fromEntries(
      nodes.map((node, i) => {
        const a = (i / n) * Math.PI * 2;
        return [node.id, { x: 220 + Math.cos(a) * 160, y: 200 + Math.sin(a) * 140 }];
      })
    );
  }, [data]);
  const selected = (data.nodes || []).find((n) => n.id === sel);
  const openNode = (node) => {
    if (node?.href) nav(node.href);
    else message.info("这一项还打不开");
  };
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        title="引用图谱"
        hint="点标题打开。"
      >
        这篇和谁有关
      </PageHeader>
      <Select
        allowClear
        placeholder="按方向"
        style={{ minWidth: 220, marginBottom: 12 }}
        value={directionId}
        notFoundContent={<DirectionSelectEmpty labId={id} teacher={teacher} />}
        options={dirs.map((d) => ({
          value: d.id,
          label: `${d.parent_id ? "↳ " : ""}${d.name}${d.includes_children || d.child_count ? " · 含小课题" : ""}`,
        }))}
        onChange={(v) => {
          const next = new URLSearchParams(params);
          if (v) next.set("direction_id", v);
          else next.delete("direction_id");
          setParams(next, { replace: true });
        }}
      />
      <DirectionFilterHint labId={id} teacher={teacher} dirs={dirs} />
      {teacher && (data.keyword_hints || []).length ? (
        <p className="fs-dir-hints">
          <span className="fs-muted">对不上的篇里反复出现{directionId ? "，点一下写进当前方向：" : "："}</span>
          {directionId
            ? data.keyword_hints.map((w) => (
                <Button
                  key={w}
                  size="small"
                  onClick={async () => {
                    const curDir = dirs.find((d) => d.id === directionId);
                    const cur = [...(curDir?.keywords || [])];
                    if (cur.includes(w)) {
                      message.info("这个词已经在方向里");
                      return;
                    }
                    try {
                      await api.patchDirection(directionId, { keywords: [...cur, w] });
                      message.success("已写进当前方向");
                      setDirs((prev) =>
                        prev.map((d) => (d.id === directionId ? { ...d, keywords: [...cur, w] } : d))
                      );
                      load();
                    } catch (e) {
                      message.error(formatError(e, "无法更新关键词"));
                    }
                  }}
                >
                  {w}
                </Button>
              ))
            : (
              <>
                {(data.keyword_hints || []).join("、")}
                {" · "}
                <Link to={`/labs/${id}/directions`}>去方向页写进关键词</Link>
              </>
            )}
        </p>
      ) : null}
      {err ? <LoadFailed message={err} onRetry={load} /> : null}
      {dirErr ? (
        <LoadFailed
          message={dirErr}
          onRetry={() =>
            api
              .directions(id)
              .then((r) => {
                setDirs(asItems(r.data));
                setDirErr("");
              })
              .catch((e) => setDirErr(formatError(e, "方向加载失败")))
          }
        />
      ) : null}
      {loading && !err ? <PageLoading label="正在打开图谱…" /> : null}
      {!loading && !err ? (
      <div className="fs-object">
        <div>
          {(data.nodes || []).length ? (
            <svg className="fs-graph-svg" viewBox="0 0 440 400">
              {(data.edges || []).map((e, i) => {
                const a = pos[e.source];
                const b = pos[e.target];
                if (!a || !b) return null;
                return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#0F6E6B" strokeOpacity="0.45" />;
              })}
              {(data.nodes || []).map((n) => (
                <g
                  key={n.id}
                  onClick={() => setSel(n.id)}
                  onDoubleClick={() => openNode(n)}
                  style={{ cursor: "pointer" }}
                >
                  <circle cx={pos[n.id]?.x} cy={pos[n.id]?.y} r={sel === n.id ? 10 : 7} fill={nodeFill(n)} />
                  <text x={(pos[n.id]?.x || 0) + 10} y={(pos[n.id]?.y || 0) + 4} fontSize="10">
                    {(n.type === "author" ? n.label || n.title : n.title || "").slice(0, 18)}
                  </text>
                </g>
              ))}
            </svg>
          ) : (
            <div>
              <EmptyHint to={(data.empty_next?.steps || [])[0]?.href || (directionId ? `/labs/${id}/literature?direction_id=${directionId}` : `/labs/${id}/literature`)}>
                {data.empty_next?.text
                  || (directionId
                    ? data?.includes_children
                      ? "大方向含下面的小课题，仍没有可画的节点。先把文献或论文挂到本方向或子课题。"
                      : "该方向还没有可画的节点。先把文献或论文挂到方向。"
                    : "先入库文献或创建论文。")}
              </EmptyHint>
              {(data.empty_next?.steps || []).map((s) => (
                <ActionChip key={s.key || s.href} to={s.href}>
                  {s.label}
                </ActionChip>
              ))}
            </div>
          )}
          <Table
            className="fs-click-row"
            rowKey="id"
            size="small"
            dataSource={data.nodes || []}
            pagination={false}
            onRow={(row) => ({ onClick: () => setSel(row.id), onDoubleClick: () => openNode(row) })}
            columns={[
              { title: "类型", dataIndex: "type", render: (t) => (t === "manuscript" ? "论文" : t === "author" ? "作者" : "文献") },
              {
                title: "标题",
                render: (_, row) =>
                  row.href ? (
                    <Link to={row.href} onClick={(e) => e.stopPropagation()}>
                      {row.title}
                    </Link>
                  ) : (
                    row.title
                  ),
              },
              {
                title: "一作",
                render: (_, row) =>
                  row.type === "literature" && row.first_author ? (
                    <Link
                      to={`/labs/${id}/literature?tab=search&q=${encodeURIComponent(row.first_author)}`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {row.first_author}
                    </Link>
                  ) : (
                    "—"
                  ),
              },
              {
                title: "这篇的方法",
                render: (_, row) =>
                  row.type === "literature" ? (
                    row.has_method_card ? (
                      "已有"
                    ) : (
                      <PdfGapLink row={row} />
                    )
                  ) : (
                    "—"
                  ),
              },
              {
                title: "复现",
                render: (_, row) =>
                  row.type === "literature" ? (
                    row.latest_reproduce?.measured ? "已经跑出结果" : row.latest_reproduce ? "还没跑出结果" : "尚未复现"
                  ) : row.type === "author" ? (
                    "—"
                  ) : [row.ungrounded_reproduce ? "写了结果，组里还没跑过" : "", row.unused_research?.text ? "综述没提到" : ""]
                      .filter(Boolean)
                      .join(" · ") || "—",
              },
            ]}
          />
        </div>
        <Card title="对象摘要">
          {selected ? (
            <div>
              <p>
                <Link to={selected.href}>{selected.title}</Link>
                <br />
                <span className="fs-muted">{selected.type === "manuscript" ? "论文" : selected.type === "author" ? "作者" : "文献"}</span>
                {selected.type === "literature" && selected.first_author ? (
                  <span className="fs-muted">
                    {" · 一作 "}
                    <Link to={`/labs/${id}/literature?tab=search&q=${encodeURIComponent(selected.first_author)}`}>
                      {selected.first_author}
                    </Link>
                  </span>
                ) : null}
              </p>
              {selected.type === "manuscript" && selected.ungrounded_reproduce ? (
                <p className="fs-muted">{selected.ungrounded_reproduce}</p>
              ) : null}
              {selected.type === "manuscript" ? <UnusedResearchAlert note={selected.unused_research} /> : null}
              {selected.type === "author" ? (
                <>
                  <p className="fs-muted">{selected.label || `${selected.title || ""} · ${selected.paper_count || 0}篇`}</p>
                  {(selected.papers || []).map((p) => (
                    <p key={p.id} style={{ margin: "4px 0" }}>
                      <Link to={p.href || `/literature/${p.id}`}>{p.title}</Link>
                    </p>
                  ))}
                  <ActionChip to={selected.href || `/labs/${id}/literature?tab=search&q=${encodeURIComponent(selected.title || "")}`}>
                    检索该作者
                  </ActionChip>
                </>
              ) : null}
              {selected.type === "literature" ? (
                <>
                  {selected.has_method_card ? (
                    <p className="fs-muted">已有这篇的方法。</p>
                  ) : selected.card_empty?.kind ? (
                    <p>
                      <PdfGapLink row={selected} />
                    </p>
                  ) : null}
                  {selected.direction_suggestions?.length ? (
                    <p>
                      <DirectionHangChips
                        suggestions={selected.direction_suggestions}
                        busyId={hangBusy}
                        onHang={async (s) => {
                          if (!selected.id || !s?.id || hangBusy) return;
                          setHangBusy(s.id);
                          try {
                            const next = [...new Set([...(selected.direction_ids || []), s.id])];
                            await api.patchLiterature(selected.id, { direction_ids: next });
                            message.success("已挂到方向");
                            load();
                          } catch (e) {
                            message.error(formatError(e, "无法挂方向"));
                          } finally {
                            setHangBusy("");
                          }
                        }}
                      />
                    </p>
                  ) : null}
                  {selected.has_method_card ? (
                    <ActionChip to={selected.card_href || `/literature/${selected.id}?panel=card`}>看这篇的方法</ActionChip>
                  ) : (
                    <AnalyzeChip row={selected} />
                  )}
                  <details className="fs-inline-more">
                    <summary>还有</summary>
                    <p className="fs-muted">
                      {selected.latest_reproduce?.measured
                        ? "已经跑出结果。论文里的数字还不是你们跑出来的。"
                        : selected.latest_reproduce
                          ? "还没跑出结果。"
                          : "还没开始复现。"}
                      <ReproduceHonest ev={selected.latest_reproduce} />
                    </p>
                    <ActionChip
                      to={selected.latest_reproduce?.href || selected.reproduce_href || `/labs/${id}/reproduces?literature_item_id=${encodeURIComponent(selected.id)}`}
                    >
                      {selected.latest_reproduce ? "查看复现" : "去复现"}
                    </ActionChip>
                  </details>
                </>
              ) : null}
            </div>
          ) : (
            <p className="fs-muted">点节点查看摘要，双击打开对象。</p>
          )}
          {(data.edges || [])
            .filter((e) => e.source === sel || e.target === sel)
            .map((e, i) => {
              const mine = e.source === sel;
              const otherId = mine ? e.target : e.source;
              const other = (data.nodes || []).find((n) => n.id === otherId);
              const text = (mine ? e.target_title : e.source_title) || other?.title || other?.label || "";
              const to = (mine ? e.target_href : e.source_href) || other?.href;
              return (
                <p key={i} className="fs-muted">
                  {VIA[e.via] || KIND[e.kind] || e.kind}
                  {text ? (
                    <>
                      {" · "}
                      {to ? <Link to={to}>{text}</Link> : text}
                    </>
                  ) : null}
                </p>
              );
            })}
        </Card>
      </div>
      ) : null}
    </Space>
  );
}
