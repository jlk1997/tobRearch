import { useEffect, useState } from "react";
import { Button, Card, Form, Input, Modal, Select, Space, Switch, Table, Tabs, message, Popconfirm, Tag } from "antd";
import { Link, useNavigate } from "react-router-dom";
import { api, asItems } from "../api.js";
import { formatError, AUDIT_LABEL } from "../err.js";
import { CreateDialog, FeedbackStrip, LoadFailed, PageHeader, ResumeJob, fmtWhen, tellFeedback, whenBlocked } from "../ui.jsx";
import { LlmKeyForm } from "../LlmKeyForm.jsx";

const ORG_STATUS = {
  pending: { color: "processing", label: "待审" },
  approved: { color: "success", label: "已开通" },
  rejected: { color: "error", label: "已驳回" },
  suspended: { color: "warning", label: "已停用" },
};

export default function Admin() {
  const nav = useNavigate();
  const [users, setUsers] = useState([]);
  const [orgs, setOrgs] = useState([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [orgFilter, setOrgFilter] = useState();
  const [userOrgFilter, setUserOrgFilter] = useState();
  const [audit, setAudit] = useState([]);
  const [usage, setUsage] = useState([]);
  const [ops, setOps] = useState(null);
  const [gpu, setGpu] = useState({
    mode: "off",
    awaiting_research: 0,
    awaiting_reproduce: 0,
    waiting: [],
    pending_research: 0,
    pending_reproduce: 0,
    pending_without_worker: [],
    feedback: null,
  });
  const [gpuKnown, setGpuKnown] = useState(false);
  const [gpuErr, setGpuErr] = useState("");
  const [gpuBusy, setGpuBusy] = useState(false);
  const [labs, setLabs] = useState([]);
  const [labsErr, setLabsErr] = useState("");
  const [auditAction, setAuditAction] = useState();
  const [auditUser, setAuditUser] = useState("");
  const [err, setErr] = useState("");
  const [section, setSection] = useState("onboard");
  const [platOn, setPlatOn] = useState(null);
  const [platErr, setPlatErr] = useState("");
  const [review, setReview] = useState(null);
  const [reviewNote, setReviewNote] = useState("");
  const [labOpen, setLabOpen] = useState(false);
  const [labForm] = Form.useForm();
  const loadAudit = (action, userId) =>
    api
      .adminAudit({ action: action || undefined, user_id: userId || undefined })
      .then((r) => setAudit(r.data.items || []))
      .catch((e) => setErr(formatError(e, "审计加载失败")));
  const loadOrgs = (status) =>
    api
      .adminOrgs(status ? { status } : {})
      .then((r) => {
        setOrgs(r.data.items || []);
        setPendingCount(r.data.pending_count || 0);
      })
      .catch((e) => setErr(formatError(e, "机构列表加载失败")));
  const loadUsers = (status) =>
    api
      .adminUsers(status ? { org_status: status } : {})
      .then((r) => setUsers(r.data))
      .catch((e) => setErr(formatError(e, "管理页加载失败")));
  const reload = () => {
    setErr("");
    setLabsErr("");
    loadUsers(userOrgFilter);
    loadOrgs(orgFilter);
    loadAudit(auditAction, auditUser);
    api
      .adminUsage()
      .then((r) => {
        setUsage(r.data.items || []);
        setOps({ version: r.data.version, worker: r.data.worker, product: r.data.product });
      })
      .catch((e) => setErr(formatError(e, "用量加载失败")));
    api
      .gpuProfiles()
      .then((r) => {
        const row = r.data?.[0] || {};
        setGpu({
          mode: row.mode || "off",
          awaiting_research: row.awaiting_research || 0,
          awaiting_reproduce: row.awaiting_reproduce || 0,
          waiting: row.waiting || [],
          pending_research: row.pending_research || 0,
          pending_reproduce: row.pending_reproduce || 0,
          pending_without_worker: row.pending_without_worker || [],
          feedback: row.feedback || null,
        });
        setGpuKnown(true);
        setGpuErr("");
      })
      .catch((e) => {
        setGpuKnown(false);
        setGpuErr(formatError(e, "实验算力状态加载失败"));
      });
    api
      .labs()
      .then((r) => {
        setLabs(asItems(r.data));
        setLabsErr("");
      })
      .catch((e) => {
        setLabs([]);
        setLabsErr(formatError(e, "实验室列表加载失败"));
      });
    api
      .platformKey()
      .then((r) => {
        setPlatOn(!!(r.data?.configured && r.data?.api_key_masked));
        setPlatErr("");
      })
      .catch((e) => {
        setPlatOn(null);
        setPlatErr(formatError(e, "平台模型状态加载失败"));
      });
  };
  useEffect(() => {
    reload();
  }, []);

  const doReview = async (org, action, note = "") => {
    if (action === "reject" && !String(note).trim()) {
      message.error("驳回须填写原因");
      return;
    }
    try {
      const r = await api.reviewOrg(org.id, { action, note });
      if (action === "approve") {
        message.success(org.status === "approved" ? "已修复实验室成员" : "已通过并开通实验室");
      } else {
        message.success("已更新");
      }
      if ((action === "approve" || action === "resume") && r.data.members_href) {
        message.success({
          content: action === "resume" ? "已恢复。可前往成员页。" : "可前往成员页邀请导师/学生。",
          duration: 4,
        });
        nav(r.data.members_href);
        return;
      }
      reload();
    } catch (e) {
      message.error(formatError(e, "审批失败"));
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="large">
      <PageHeader
        title="平台管理"
        hint="先处理待审机构。模型和机器分开配。"
        extra={
          section === "onboard" ? (
            <Button type="primary" onClick={() => setLabOpen(true)}>
              新建空库
            </Button>
          ) : null
        }
      >
        先审待开通的机构
      </PageHeader>
      <div className="fs-admin-check">
        {pendingCount ? (
          <Button type="link" onClick={() => setSection("onboard")}>
            待审机构 {pendingCount}
          </Button>
        ) : (
          <span className="fs-muted">开通队列已清</span>
        )}
        <Button type="link" onClick={() => setSection("model")}>
          {platErr ? "平台模型状态未知" : platOn === null ? "平台模型读取中" : platOn ? "平台模型已保存" : "平台模型未保存"}
        </Button>
        <Button type="link" onClick={() => setSection("model")}>
          {gpuErr
            ? "实验算力状态未知"
            : !gpuKnown
              ? "实验算力读取中"
              : gpu.mode === "off"
                ? "实验算力关闭（无集群请保持）"
                : `实验算力：${gpu.mode === "internal" ? "实验室集群" : "外接集群"}`}
        </Button>
      </div>
      <Modal
        title={review?.action === "reject" ? "驳回申请" : "停用机构"}
        open={!!review}
        onCancel={() => setReview(null)}
        onOk={async () => {
          if (!review) return;
          await doReview(review.org, review.action, reviewNote);
          setReview(null);
          setReviewNote("");
        }}
        okText="确认"
      >
        <Input.TextArea
          rows={3}
          value={reviewNote}
          onChange={(e) => setReviewNote(e.target.value)}
          placeholder={review?.action === "reject" ? "驳回原因（申请人可见）" : "停用说明（可选）"}
        />
      </Modal>
      {ops ? (
        <Card title="后台任务">
          <p>
            {ops.product || "蜂算科研"} {ops.version ? `v${ops.version}` : ""}
            {ops.worker
              ? ` · 后台${ops.worker.enabled ? "开着" : "关着"}${
                  ops.worker.lag_seconds == null ? "" : ` · 上次心跳 ${ops.worker.lag_seconds} 秒前`
                }`
              : ""}
          </p>
          {ops.worker?.last_tick_at ? <p className="fs-muted">上次检查 {fmtWhen(ops.worker.last_tick_at)}</p> : null}
          {ops.worker?.next_step ? <p className="fs-muted">{ops.worker.next_step}</p> : null}
        </Card>
      ) : null}
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}

      <Tabs
        activeKey={section}
        onChange={setSection}
        items={[
          {
            key: "onboard",
            label: pendingCount ? `开通（待审 ${pendingCount}）` : "开通",
            children: (
              <Space direction="vertical" style={{ width: "100%" }} size="large">
      <Card
        title={`机构申请${pendingCount ? `（待审 ${pendingCount}）` : ""}`}
        extra={
          <Select
            allowClear
            placeholder="状态"
            style={{ width: 140 }}
            value={orgFilter}
            onChange={(v) => {
              setOrgFilter(v);
              loadOrgs(v);
            }}
            options={[
              { value: "pending", label: "待审" },
              { value: "approved", label: "已开通" },
              { value: "rejected", label: "已驳回" },
              { value: "suspended", label: "已停用" },
            ]}
          />
        }
      >
        <Table
          rowKey="id"
          dataSource={orgs}
          pagination={{ pageSize: 8 }}
          columns={[
            {
              title: "状态",
              dataIndex: "status",
              width: 90,
              render: (s) => {
                const m = ORG_STATUS[s] || { color: "default", label: s };
                return <Tag color={m.color}>{m.label}</Tag>;
              },
            },
            { title: "机构", dataIndex: "name" },
            { title: "类型", dataIndex: "kind_label", width: 100 },
            { title: "申请人", render: (_, r) => r.applicant_email || r.applicant_name || "—" },
            {
              title: "联系人",
              width: 140,
              render: (_, r) => r.contact_name || r.contact_phone || "—",
            },
            { title: "说明", dataIndex: "apply_note", ellipsis: true },
            {
              title: "审核备注",
              dataIndex: "review_note",
              ellipsis: true,
              render: (v, r) => (r.status === "rejected" || r.status === "suspended" ? v || "—" : "—"),
            },
            { title: "申请时间", dataIndex: "created_at", render: fmtWhen, width: 160 },
            {
              title: "操作",
              width: 300,
              render: (_, row) => (
                <Space wrap size="small">
                  {row.status === "pending" ? (
                    <Button size="small" type="primary" onClick={() => doReview(row, "approve")}>
                      通过
                    </Button>
                  ) : null}
                  {row.status === "approved" ? (
                    <Button size="small" onClick={() => doReview(row, "approve")}>
                      修复
                    </Button>
                  ) : null}
                  {row.status === "pending" ? (
                    <Button size="small" danger onClick={() => { setReviewNote(""); setReview({ org: row, action: "reject" }); }}>
                      驳回
                    </Button>
                  ) : null}
                  {row.status === "approved" ? (
                    <Button size="small" onClick={() => { setReviewNote(""); setReview({ org: row, action: "suspend" }); }}>
                      停用
                    </Button>
                  ) : null}
                  {row.status === "suspended" ? (
                    <Button size="small" onClick={() => doReview(row, "resume")}>
                      恢复
                    </Button>
                  ) : null}
                  {row.status === "rejected" ? (
                    <span className="fs-muted">待申请人重提</span>
                  ) : null}
                  {row.members_href ? <Link to={row.members_href}>成员</Link> : null}
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Card title="实验室（引导实验室可删除）">
        <CreateDialog
          open={labOpen}
          onClose={() => setLabOpen(false)}
          title="新建空库"
          okText="新建空库"
          form={labForm}
        >
          <Form
            form={labForm}
            layout="vertical"
            onFinish={async (v) => {
              try {
                await api.createLab(v);
                labForm.resetFields();
                message.success("已创建空实验室");
                setLabOpen(false);
                reload();
              } catch (e) {
                message.error(formatError(e, "无法创建实验室"));
              }
            }}
          >
            <Form.Item name="name" label="名称" rules={[{ required: true, message: "请填写名称" }]}>
              <Input placeholder="名称" />
            </Form.Item>
            <Form.Item name="code" label="代码" rules={[{ required: true, message: "请填写代码" }]}>
              <Input placeholder="代码" />
            </Form.Item>
            <Form.Item name="org_id" label="所属机构">
              <Select
                allowClear
                placeholder="所属机构（默认可选已开通）"
                options={(orgs || [])
                  .filter((o) => o.status === "approved")
                  .map((o) => ({ value: o.id, label: o.name }))}
              />
            </Form.Item>
          </Form>
        </CreateDialog>
        {labsErr ? <LoadFailed message={labsErr} onRetry={reload} /> : null}
        <Table
          rowKey="id"
          style={{ marginTop: 12 }}
          dataSource={labs}
          locale={{ emptyText: labsErr ? "实验室列表加载失败，可重试。" : "还没有实验室。" }}
          columns={[
            { title: "名称", dataIndex: "name" },
            { title: "代码", dataIndex: "code" },
            {
              title: "删除",
              render: (_, row) => (
                <Popconfirm
                  title="确定删除该实验室？出厂引导实验室也可以删除。"
                  onConfirm={async () => {
                    try {
                      await api.deleteLab(row.id);
                      message.success("已删除");
                      reload();
                    } catch (e) {
                      message.error(formatError(e, "无法删除"));
                    }
                  }}
                >
                  <Button size="small" danger>
                    删除
                  </Button>
                </Popconfirm>
              ),
            },
          ]}
        />
      </Card>
              </Space>
            ),
          },
          {
            key: "model",
            label: "模型与算力",
            children: (
              <Space direction="vertical" style={{ width: "100%" }} size="large">
      <Card title="实验 GPU（文献调研 / 复现算力，不是大模型 Key）">
        {gpuErr ? <LoadFailed message={gpuErr} onRetry={reload} /> : null}
        <p className="fs-muted">
          这里只开实验集群。大模型密钥在下一栏。文献调研等算力 {gpu.awaiting_research || 0} · 复现等 GPU{" "}
          {gpu.awaiting_reproduce || 0}
          {gpu.pending_research || gpu.pending_reproduce
            ? ` · 还没开始跑 ${gpu.pending_research || 0}/${gpu.pending_reproduce || 0}`
            : ""}
          。改为实验室集群或外接集群后重新入队，不会伪造指标。
        </p>
        {gpu.feedback ? <FeedbackStrip row={gpu} /> : null}
        {(gpu.waiting || []).length ? (
          <ul className="fs-gpu-waiting">
            {(gpu.waiting || []).map((w) => (
              <li key={`${w.kind}-${w.id}`}>
                <Link to={w.href}>
                  {w.kind === "reproduce" ? "复现" : "文献调研"} · {w.title || w.id}
                </Link>
                {w.next_step || w.feedback ? <FeedbackStrip row={w} /> : null}
              </li>
            ))}
          </ul>
        ) : null}
        {(gpu.pending_without_worker || []).length ? (
          <ul className="fs-gpu-waiting">
            {(gpu.pending_without_worker || []).map((w) => (
              <li key={`p-${w.kind}-${w.id}`}>
                <Link to={w.href}>
                  {w.kind === "reproduce" ? "复现" : "文献调研"} · {w.title || w.id}
                </Link>
                {w.next_step || w.feedback ? <FeedbackStrip row={w} /> : null}
                <ResumeJob row={w} onDone={reload} />
              </li>
            ))}
          </ul>
        ) : null}
        <span className="fs-press-wrap" onClick={whenBlocked(gpuBusy || !gpuKnown, gpuErr ? "实验算力状态未知，请刷新" : gpuBusy ? "正在保存" : "还在读取")}>
        <Select
          value={gpuKnown ? gpu.mode : undefined}
          placeholder={gpuErr ? "状态未知" : "读取中"}
          style={{ width: 200 }}
          disabled={gpuBusy || !gpuKnown}
          onChange={async (mode) => {
            if (gpuBusy) return;
            setGpuBusy(true);
            try {
              const r = await api.putGpu({ mode });
              setGpu({
                mode: r.data.mode || mode,
                awaiting_research: r.data.awaiting_research || 0,
                awaiting_reproduce: r.data.awaiting_reproduce || 0,
                waiting: r.data.waiting || [],
                pending_research: r.data.pending_research || 0,
                pending_reproduce: r.data.pending_reproduce || 0,
                pending_without_worker: r.data.pending_without_worker || [],
                feedback: r.data.feedback || null,
              });
              setGpuKnown(true);
              setGpuErr("");
              tellFeedback(r.data, "已更新实验算力");
            } catch (e) {
              message.error(formatError(e, "无法更新 GPU"));
            } finally {
              setGpuBusy(false);
            }
          }}
          options={[
            { value: "off", label: "关闭（没有机器，不会编造结果）" },
            { value: "internal", label: "实验室集群" },
            { value: "external", label: "外接集群" },
          ]}
        />
        </span>
      </Card>
      <Card title="平台模型" id="platform-llm">
        {platErr ? <LoadFailed message={platErr} onRetry={reload} /> : null}
        <p className="fs-hint">
          全站兜底：成员没填个人模型、实验室也没配时，提炼方法、辅导、对比、知识库问答走这里。文献检索不需要这项。不要把模型地址填进实验算力。
        </p>
        <ol className="fs-llm-uses">
          <li>选服务商（本机 MiniMax 选第一项即可）</li>
          <li>把控制台里的密钥贴到「API 密钥」</li>
          <li>保存后再点「测通已保存的配置」</li>
        </ol>
        <LlmKeyForm
          load={() => api.platformKey()}
          save={async (v) => {
            const r = await api.putPlatformKey(v);
            setPlatOn(!!(r.data?.configured && r.data?.api_key_masked));
            return r;
          }}
          test={() => api.testPlatformKey()}
          saveLabel="保存平台默认"
        />
      </Card>
              </Space>
            ),
          },
          {
            key: "people",
            label: "人员与记录",
            children: (
              <Space direction="vertical" style={{ width: "100%" }} size="large">
      <Card
        title="用户"
        extra={
          <Select
            allowClear
            placeholder="机构状态"
            style={{ width: 140 }}
            value={userOrgFilter}
            onChange={(v) => {
              setUserOrgFilter(v);
              loadUsers(v);
            }}
            options={[
              { value: "pending", label: "待审" },
              { value: "approved", label: "已开通" },
              { value: "rejected", label: "已驳回" },
              { value: "suspended", label: "已停用" },
            ]}
          />
        }
      >
        <Table
          rowKey="id"
          dataSource={users}
          columns={[
            { title: "邮箱", dataIndex: "email" },
            { title: "姓名", dataIndex: "display_name" },
            { title: "机构", dataIndex: "org_name" },
            {
              title: "机构状态",
              dataIndex: "org_status",
              render: (s) => {
                const m = ORG_STATUS[s];
                return m ? <Tag color={m.color}>{m.label}</Tag> : s || "—";
              },
            },
            { title: "最后活跃", dataIndex: "last_seen_at", render: fmtWhen },
            {
              title: "启用",
              dataIndex: "is_active",
              render: (v, row) => (
                <Switch
                  checked={v}
                  onChange={async (is_active) => {
                    try {
                      await api.patchAdminUser(row.id, { is_active });
                      reload();
                    } catch (e) {
                      message.error(formatError(e, "无法更改启用状态"));
                      reload();
                    }
                  }}
                />
              ),
            },
          ]}
        />
      </Card>
      <Card title="审计">
        <Form
          layout="inline"
          style={{ marginBottom: 12 }}
          onFinish={(v) => {
            setAuditAction(v.action);
            setAuditUser(v.user_id || "");
            loadAudit(v.action, v.user_id);
          }}
        >
          <Form.Item name="action">
            <Select
              allowClear
              placeholder="动作"
              style={{ width: 220 }}
              options={[
                { value: "org.*", label: "机构相关" },
                ...Object.entries(AUDIT_LABEL)
                  .filter(([k]) => k !== "org.*")
                  .map(([value, label]) => ({ value, label })),
              ]}
            />
          </Form.Item>
          <Form.Item name="user_id">
            <Input placeholder="用户 ID（可选）" />
          </Form.Item>
          <Button htmlType="submit">筛选</Button>
        </Form>
        <Table
          rowKey="id"
          dataSource={audit}
          columns={[
            { title: "动作", dataIndex: "action", render: (a) => AUDIT_LABEL[a] || a },
            { title: "资源", dataIndex: "resource" },
            { title: "对象", dataIndex: "resource_id", ellipsis: true },
            { title: "用户", dataIndex: "user_id", ellipsis: true },
            { title: "时间", dataIndex: "created_at", render: fmtWhen },
          ]}
        />
      </Card>
      <Card title="用量">
      <Table
        rowKey={(r, i) => `${r.user_id}-${i}`}
        dataSource={usage}
        columns={[
          { title: "功能", dataIndex: "feature" },
          { title: "模型", dataIndex: "model" },
          { title: "提问用量", dataIndex: "prompt_tokens" },
          { title: "回答用量", dataIndex: "completion_tokens" },
          { title: "范围", dataIndex: "credential_scope" },
          { title: "实验室", dataIndex: "lab_id", ellipsis: true },
          { title: "时间", dataIndex: "created_at", render: fmtWhen },
        ]}
      />
      </Card>
              </Space>
            ),
          },
        ]}
      />
    </Space>
  );
}
