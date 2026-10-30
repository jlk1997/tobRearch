import { useEffect, useState } from "react";
import { Alert, Button, Card, Form, Input, Select, Space, Table, message, Popconfirm, Upload } from "antd";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, asItems } from "../api.js";
import { formatError, literatureHref, RUN_STATUS } from "../err.js";
import { ActionChip, CreateDialog, DirectionFilterHint, DirectionSelectEmpty, EmptyHint, FeedbackStrip, LoadFailed, PageHeader, PageLoading, ResumeJob, StatusTag, useComposeOpen } from "../ui.jsx";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";

export function Datasets() {
  const { id } = useParams();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const directionId = params.get("direction_id") || undefined;
  const [compose, setCompose] = useComposeOpen(params, setParams);
  const [dsForm] = Form.useForm();
  const [rows, setRows] = useState([]);
  const [papers, setPapers] = useState([]);
  const [dirs, setDirs] = useState([]);
  const [err, setErr] = useState("");
  const [emptyNext, setEmptyNext] = useState(null);
  const me = useSession((s) => s.me);
  const teacher = roleOf(me, id) === "teacher" || !!me?.is_admin;
  const reload = () => {
    setErr("");
    api
      .datasets(id, { direction_id: directionId })
      .then((r) => {
        setRows(asItems(r.data));
        setEmptyNext(r.data?.empty_next || null);
      })
      .catch((e) => setErr(formatError(e, "数据集加载失败")));
  };
  useEffect(() => {
    reload();
    api.literature(id, { page: 1, page_size: 100 }).then((r) => setPapers(asItems(r.data))).catch((e) => setErr(formatError(e, "文献列表加载失败")));
    api.directions(id).then((r) => setDirs(asItems(r.data))).catch((e) => {
      setDirs([]);
      message.error(formatError(e, "方向列表没打开"));
    });
  }, [id, directionId]);
  const dirMeta = dirs.find((d) => d.id === directionId);
  const scopedKids = Boolean(emptyNext?.includes_children || dirMeta?.includes_children || dirMeta?.child_count);
  const hero = directionId
    ? {
        title: emptyNext?.title || (scopedKids ? "这个大方向（含小课题）的数据集" : "本方向的数据集"),
        lead: scopedKids ? "这个大方向，含小课题。" : "只看这个方向。",
      }
    : null;
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        tone={hero || (emptyNext && !rows.length) ? "unused" : undefined}
        title={hero?.title || (emptyNext && !rows.length ? emptyNext.title || "还没有数据集" : "数据集")}
        hint="缺数据时复现会停住。"
        extra={
          <Button type="primary" onClick={() => setCompose(true)}>
            登记数据集
          </Button>
        }
      >
        {hero?.lead ||
          (emptyNext && !rows.length
            ? emptyNext.text || "先登记，复现和文献调研才能选。"
            : "登记一份数据")}
        {hero ? (
          <>
            {" "}
            <ActionChip to={`/labs/${id}/datasets`}>全部数据集</ActionChip>
          </>
        ) : null}
      </PageHeader>
      <div className="fs-filterbar">
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="按方向"
          style={{ width: 280 }}
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
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}
      {emptyNext && !rows.length ? (
        <Alert
          type="info"
          showIcon
          message={emptyNext.title || "还没有数据集"}
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
      <CreateDialog open={compose} onClose={() => setCompose(false)} title="登记数据集" okText="登记" form={dsForm}>
        <Form
          form={dsForm}
          layout="vertical"
          onFinish={async (v) => {
            try {
              await api.createDataset(id, {
                name: v.name,
                description: v.description || "",
                license: v.license || "",
                local_path: v.local_path || "",
                source_note: v.source_note || "",
                literature_item_ids: v.literature_item_ids || [],
              });
              dsForm.resetFields();
              setCompose(false);
              reload();
            } catch (e) {
              message.error(formatError(e, "无法登记"));
            }
          }}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请填写名称" }]}>
            <Input placeholder="例如：VisDrone" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input placeholder="可选" />
          </Form.Item>
          <Form.Item name="license" label="许可">
            <Input placeholder="可选" />
          </Form.Item>
          <Form.Item name="local_path" label="相对路径">
            <Input placeholder="可选" />
          </Form.Item>
          <Form.Item name="literature_item_ids" label="关联文献">
            <Select
              mode="multiple"
              allowClear
              placeholder="可选"
              options={papers.map((p) => ({ value: p.id, label: p.title }))}
            />
          </Form.Item>
        </Form>
      </CreateDialog>
      <Table
        className="fs-click-row"
        rowKey="id"
        dataSource={rows}
        locale={{ emptyText: <EmptyHint>{emptyNext?.text || "还没有数据集。登记后复现可从下拉选择，缺对象时仍为 blocked。"}</EmptyHint> }}
        onRow={(row) => ({ onClick: () => nav(`/datasets/${row.id}`) })}
        columns={[
          {
            title: "名称",
            dataIndex: "name",
            render: (n, row) => <Link to={`/datasets/${row.id}`}>{n}</Link>,
          },
          {
            title: "说明",
            dataIndex: "description",
            render: (v, row) => v || row.source_note || "—",
          },
          { title: "许可", dataIndex: "license" },
          {
            title: "文献调研",
            dataIndex: "research_count",
            render: (v, row) => (
              <Link to={row.research_href || `/labs/${id}/runs?dataset_id=${row.id}`} onClick={(e) => e.stopPropagation()}>
                {v || 0}
              </Link>
            ),
          },
          {
            title: "复现",
            dataIndex: "reproduce_count",
            render: (v, row) => (
              <Link to={row.reproduce_href || `/labs/${id}/reproduces?dataset_id=${row.id}`} onClick={(e) => e.stopPropagation()}>
                {v ?? row.usage_count ?? 0}
              </Link>
            ),
          },
          {
            title: "关联文献",
            dataIndex: "literature",
            render: (list) =>
              (list || []).length
                ? (list || []).map((p) => (
                    <span key={p.id} style={{ marginRight: 8 }}>
                      <Link to={p.href || literatureHref(p.id, p.last_page_no)} onClick={(e) => e.stopPropagation()}>
                        {p.title}
                      </Link>
                    </span>
                  ))
                : "—",
          },
          { title: "有文件", dataIndex: "has_file", render: (v) => (v ? "是" : "") },
          {
            title: "",
            width: 72,
            render: (_, row) => (
              <Popconfirm
                title="删除该数据集？"
                onConfirm={async (e) => {
                  e?.stopPropagation?.();
                  try {
                    await api.deleteDataset(row.id);
                    reload();
                  } catch (err2) {
                    message.error(formatError(err2, "无法删除"));
                  }
                }}
              >
                <Button size="small" danger onClick={(e) => e.stopPropagation()}>
                  删
                </Button>
              </Popconfirm>
            ),
          },
        ]}
      />
    </Space>
  );
}

export function DatasetDetail() {
  const { id } = useParams();
  const me = useSession((s) => s.me);
  const [doc, setDoc] = useState(null);
  const [err, setErr] = useState("");
  const [papers, setPapers] = useState([]);
  const load = () =>
    api
      .getDataset(id)
      .then((r) => {
        setDoc(r.data);
        setErr("");
        if (r.data.lab_id) {
          api
            .literature(r.data.lab_id, { page: 1, page_size: 100 })
            .then((x) => setPapers(asItems(x.data)))
            .catch((e) => setErr(formatError(e, "文献列表加载失败")));
        }
      })
      .catch((e) => setErr(formatError(e, "数据集加载失败")));
  useEffect(() => {
    load();
  }, [id]);
  if (err && !doc) return <LoadFailed message={err} onRetry={load} />;
  if (!doc) return <PageLoading label="正在打开数据集…" />;
  const teacher = roleOf(me, doc.lab_id) === "teacher" || !!me?.is_admin;
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <PageHeader
        title={doc.name}
        extra={
          <>
            <Link to={`/labs/${doc.lab_id}/runs?dataset_id=${doc.id}`}>文献调研 {doc.usages?.research_runs?.length || 0}</Link>
            {" · "}
            <Link to={`/labs/${doc.lab_id}/reproduces?dataset_id=${doc.id}`}>复现 {doc.usages?.reproduce_runs?.length || 0}</Link>
          </>
        }
      >
        {doc.description || "这份数据"}
        {(doc.direction_ids || []).length ? (
          <span className="fs-muted"> · 关联了 {doc.direction_ids.length} 个方向</span>
        ) : null}
      </PageHeader>
      <Card title="说明">
        <p>{doc.description || doc.source_note || "暂无说明。"}</p>
        {teacher && doc.local_path ? <p className="fs-muted">路径：{doc.local_path}</p> : null}
        <Upload
          showUploadList={false}
          beforeUpload={async (file) => {
            try {
              await api.uploadDataset(id, file);
              message.success("已上传");
              load();
            } catch (e) {
              message.error(formatError(e, "上传失败"));
            }
            return false;
          }}
        >
          <Button>上传数据包</Button>
        </Upload>
      </Card>
      <Card title="关联文献">
        <Select
          mode="multiple"
          allowClear
          style={{ width: "100%", marginBottom: 12 }}
          placeholder="选择本实验室文献"
          value={(doc.literature || []).map((p) => p.id)}
          options={papers.map((p) => ({ value: p.id, label: p.title }))}
          onChange={async (ids) => {
            try {
              await api.patchDataset(id, { literature_item_ids: ids });
              message.success("已更新关联");
              load();
            } catch (e) {
              message.error(formatError(e, "无法关联文献"));
            }
          }}
        />
        {(doc.literature || []).length ? (
          (doc.literature || []).map((p) => (
            <p key={p.id}>
              <Link to={p.href || literatureHref(p.id, p.last_page_no)}>{p.title}</Link>
              <details className="fs-inline-more">
                <summary>还有</summary>
                <ActionChip to={p.reproduce_href || `/labs/${doc.lab_id}/reproduces?literature_item_id=${encodeURIComponent(p.id)}`}>
                  去复现
                </ActionChip>
              </details>
            </p>
          ))
        ) : (
          <p className="fs-muted">尚未关联文献。先到文献里打开一篇。</p>
        )}
      </Card>
      <Card title="被哪些任务引用">
        {(doc.usages?.research_runs || []).map((r) => (
          <p key={r.id}>
            <Link to={r.href || `/runs/${r.id}`}>{r.question || r.id}</Link>{" "}
            {r.status ? <StatusTag status={r.status} map={RUN_STATUS} /> : null}
            {r.next_step || r.feedback ? <FeedbackStrip row={r} /> : null}
            <ResumeJob row={{ ...r, kind: "research" }} onDone={load} />
          </p>
        ))}
        {(doc.usages?.reproduce_runs || []).map((r) => (
          <p key={r.id}>
            <Link to={r.href || `/reproduces/${r.id}`}>复现</Link>{" "}
            {r.status ? <StatusTag status={r.status} map={RUN_STATUS} /> : null}
            {r.next_step || r.feedback ? <FeedbackStrip row={r} /> : null}
            <ResumeJob row={{ ...r, kind: "reproduce" }} onDone={load} />
          </p>
        ))}
        {!doc.usages?.research_runs?.length && !doc.usages?.reproduce_runs?.length ? <p className="fs-muted">尚未被任务引用。</p> : null}
      </Card>
    </Space>
  );
}
