import { useEffect, useState } from "react";
import { Button, Form, Input, Modal, Select, Space, Table, Tabs, message, Popconfirm, Alert, Collapse } from "antd";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { api, asItems } from "../api.js";
import { formatError } from "../err.js";
import { ActionChip, AgentTraceBox, AskWeekChip, ContinueAskChip, CreateDialog, DirectionGapAlert, DirectionFilterHint, DirectionSelectEmpty, EmptyHint, FsGlyph, LoadFailed, PageHeader, RoleTag, WorkLine, fmtWhen, spokenName, useComposeOpen, whenBlocked } from "../ui.jsx";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";

export default function Members() {
  const { id } = useParams();
  const embedded = useLocation().pathname.includes("/cockpit");
  const [params, setParams] = useSearchParams();
  const [compose, setCompose] = useComposeOpen(params, setParams);
  const me = useSession((s) => s.me);
  const teacher = roleOf(me, id) === "teacher" || !!me?.is_admin;
  const orgLocked = me?.org?.status === "suspended" || me?.workbench_block === "suspended";
  const [rows, setRows] = useState([]);
  const [dirs, setDirs] = useState([]);
  const [invites, setInvites] = useState([]);
  const [err, setErr] = useState("");
  const [dirErr, setDirErr] = useState("");
  const [inviteErr, setInviteErr] = useState("");
  const [inviteHint, setInviteHint] = useState("");
  const [inviting, setInviting] = useState(false);
  const [inviteForm] = Form.useForm();
  const [statusFilter, setStatusFilter] = useState("active");
  const [dirFilter, setDirFilter] = useState();
  const [drafts, setDrafts] = useState([]);
  const [dirGap, setDirGap] = useState(null);
  const [profileUid, setProfileUid] = useState(null);
  const [profile, setProfile] = useState(null);
  const [profileForm] = Form.useForm();
  const mine = rows.find((r) => r.user_id === me?.id);
  const childIdsOf = (pid) => dirs.filter((d) => d.parent_id === pid).map((d) => d.id);
  const visible = rows.filter((r) => {
    if (statusFilter === "historical") return r.status && r.status !== "active";
    if (statusFilter && (r.status || "active") !== statusFilter) return false;
    if (dirFilter) {
      const scope = [dirFilter, ...childIdsOf(dirFilter)];
      const hung = [...(r.directions || []).map((d) => d.id), r.direction_id].filter(Boolean);
      if (!scope.some((sid) => hung.includes(sid))) return false;
    }
    return true;
  });
  const STATUS_LABEL = { active: "在组内", on_leave: "休学", graduated: "已毕业" };
  const pendingInvites = invites.filter((x) => !x.accepted_at);
  const reload = () => {
    setErr("");
    setDirErr("");
    setInviteErr("");
    api
      .members(id)
      .then((r) => setRows(asItems(r.data)))
      .catch((e) => setErr(formatError(e, "成员加载失败")));
    if (!orgLocked) {
      api
        .directions(id)
        .then((r) => {
          setDirs(asItems(r.data));
          setDirErr("");
        })
        .catch((e) => {
          setDirs([]);
          setDirErr(formatError(e, "方向列表加载失败"));
        });
    } else {
      setDirs([]);
    }
    api
      .listInvites(id)
      .then((r) => {
        setInvites(asItems(r.data));
        setInviteErr("");
      })
      .catch((e) => {
        if (teacher) setInviteErr(formatError(e, "邀请列表加载失败"));
      });
  };
  useEffect(() => {
    reload();
  }, [id, orgLocked]);
  useEffect(() => {
    if (!id || orgLocked) return undefined;
    let alive = true;
    api
      .literature(id, { page: 1, page_size: 1 })
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
          message.warning(formatError(e, "方向建议没打开"));
        }
      });
    return () => {
      alive = false;
    };
  }, [id, orgLocked, dirs.length]);

  const copyUrl = async (url) => {
    try {
      await navigator.clipboard.writeText(url);
      return true;
    } catch {
      return false;
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <PageHeader
        title={embedded ? "邀请和改角色" : "成员"}
        hint={embedded ? "上面是每个人在做的事。这里改名册。" : "点姓名看这个人的进展。"}
        extra={
          teacher && !orgLocked ? (
            <Button type="primary" onClick={() => setCompose(true)}>
              邀请加入
            </Button>
          ) : null
        }
      >
        {embedded ? null : teacher ? "邀请谁进组" : "谁在组里"}
        {teacher && pendingInvites.length ? (
          <>
            {" "}
            <ActionChip
              to={`/labs/${id}/members#invites`}
              onClick={(e) => {
                const el = document.getElementById("invites");
                if (!el) return;
                e.preventDefault();
                el.scrollIntoView({ behavior: "smooth", block: "start" });
              }}
            >
              待接受邀请 {pendingInvites.length}
            </ActionChip>
          </>
        ) : null}
      </PageHeader>
      {orgLocked ? (
        <Alert type="warning" showIcon message="机构已停用，只能撤销未用邀请，不能再邀请或新建实验室。" />
      ) : null}
      {err ? <LoadFailed message={err} onRetry={reload} /> : null}
      {dirErr ? <LoadFailed message={dirErr} onRetry={reload} /> : null}
      {inviteErr ? <LoadFailed message={inviteErr} onRetry={reload} /> : null}

      {teacher && !orgLocked ? (
        <CreateDialog
          open={compose}
          onClose={() => setCompose(false)}
          title="邀请加入"
          okText={inviting ? "正在发出" : "发出邀请"}
          form={inviteForm}
          loading={inviting}
          extra={
            inviteHint ? (
              <Alert type="success" showIcon style={{ marginBottom: 12 }} message={inviteHint} />
            ) : (
              <p className="fs-muted">只能邀请本机构邮箱。发出后把链接发给对方。</p>
            )
          }
        >
          <Form
            form={inviteForm}
            layout="vertical"
            initialValues={{ role: "student" }}
            onFinish={async (v) => {
              setInviting(true);
              try {
                const r = await api.invites(id, v.email, v.role || "student");
                const url = r.data.invite_url || `${window.location.origin}/invite/${r.data.token}`;
                const copied = await copyUrl(url);
                setInviteHint(copied ? `链接已复制：${url}` : `请复制链接：${url}`);
                message.success(copied ? "已发出，链接已复制" : "已发出。链接没复制上，请在下面复制");
                inviteForm.resetFields();
                reload();
              } catch (e) {
                message.error(formatError(e, "没发出"));
              } finally {
                setInviting(false);
              }
            }}
          >
            <Form.Item name="email" label="对方邮箱" rules={[{ required: true, type: "email", message: "请填写邮箱" }]}>
              <Input placeholder="name@lab.com" autoComplete="off" />
            </Form.Item>
            <Form.Item name="role" label="角色" initialValue="student">
              <Select
                options={[
                  { value: "student", label: "学生" },
                  { value: "teacher", label: "导师" },
                ]}
              />
            </Form.Item>
          </Form>
        </CreateDialog>
      ) : null}

      {!orgLocked ? (
        <div className="fs-me-strip">
          <span className="fs-me-strip-label">我的</span>
          {dirErr ? (
            <span className="fs-muted">
              方向未知（加载失败）
              {mine?.title ? ` · ${mine.title}` : ""}
              。职称在下表改。
            </span>
          ) : (
            <>
              <span className="fs-press-wrap" onClick={whenBlocked(!dirs.length, teacher ? "先新建一个方向" : "等导师建好方向再选")}>
              <Select
                size="small"
                allowClear
                style={{ width: 200 }}
                placeholder={dirs.length ? "主方向（快照）" : teacher ? "还没有方向" : "导师还没创建方向"}
                disabled={!dirs.length}
                value={mine?.direction_id || undefined}
                options={dirs.map((d) => ({
                  value: d.id,
                  label: d.parent_id ? `↳ ${d.name}` : d.includes_children || d.child_count ? `${d.name}（含小课题）` : d.name,
                }))}
                onChange={async (v) => {
                  try {
                    const hung = (mine?.directions || []).map((d) => d.id).filter((x) => x && x !== v);
                    const ids = v ? [v, ...hung] : hung;
                    await api.pickDirection(id, { direction_id: v || hung[0] || null, direction_ids: ids });
                    message.success("已保存方向");
                    reload();
                  } catch (e) {
                    message.error(formatError(e, "无法保存方向"));
                  }
                }}
              />
              <Select
                size="small"
                mode="multiple"
                allowClear
                style={{ minWidth: 220 }}
                placeholder="还可挂其他方向"
                disabled={!dirs.length}
                value={(mine?.directions || []).filter((d) => d.id && d.id !== mine?.direction_id).map((d) => d.id)}
                options={dirs.map((d) => ({
                  value: d.id,
                  label: d.parent_id ? `↳ ${d.name}` : d.includes_children || d.child_count ? `${d.name}（含小课题）` : d.name,
                }))}
                onChange={async (extras) => {
                  try {
                    const primary = mine?.direction_id || extras[0] || null;
                    const ids = primary ? [...new Set([primary, ...extras])] : extras;
                    await api.pickDirection(id, { direction_id: primary, direction_ids: ids });
                    message.success("已保存方向");
                    reload();
                  } catch (e) {
                    message.error(formatError(e, "无法保存方向"));
                  }
                }}
              />
              </span>
              <span className="fs-muted">
                {mine?.title ? ` · ${mine.title}` : ""}
                。主方向给文献调研用。职称在下表改。
              </span>
            </>
          )}
        </div>
      ) : null}

      {teacher && !orgLocked ? (
        <Collapse
          ghost
          className="fs-members-more"
          items={[
            {
              key: "lab",
              label: "还要再建一个实验室？",
              children: (
                <Form
                  layout="inline"
                  onFinish={async (v) => {
                    try {
                      const code = (v.code || "").trim() || `lab-${Date.now().toString(36)}`;
                      const r = await api.createLab({ name: v.name, code, description: v.description || "" });
                      message.success("已新建空实验室");
                      const me2 = await api.me();
                      useSession.getState().setMe(me2.data);
                      useSession.getState().setLabId(r.data.id);
                      window.location.assign(`/labs/${r.data.id}/members`);
                    } catch (e) {
                      message.error(formatError(e, "无法新建实验室"));
                    }
                  }}
                >
                  <Form.Item name="name" rules={[{ required: true, message: "请填写名称" }]}>
                    <Input placeholder="新实验室名称" />
                  </Form.Item>
                  <Button htmlType="submit">创建</Button>
                </Form>
              ),
            },
          ]}
        />
      ) : null}

      <Space wrap>
        <Select
          size="small"
          style={{ width: 140 }}
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: "active", label: "在组内" },
            { value: "on_leave", label: "休学" },
            { value: "graduated", label: "已毕业" },
            { value: "historical", label: "历史成员" },
          ]}
        />
        <Select
          size="small"
          allowClear
          style={{ width: 180 }}
          placeholder="按方向"
          value={dirFilter}
          onChange={setDirFilter}
          notFoundContent={<DirectionSelectEmpty labId={id} teacher={teacher} />}
          options={dirs.map((d) => ({ value: d.id, label: d.parent_id ? `↳ ${d.name}` : d.name }))}
        />
        <DirectionFilterHint labId={id} teacher={teacher} dirs={dirs} />
      </Space>
      <DirectionGapAlert
        gap={dirGap}
        drafts={drafts}
        labId={id}
        teacher={teacher}
        onAdopted={() => {
          reload();
          api
            .literature(id, { page: 1, page_size: 1 })
            .then((r) => {
              setDrafts(r.data?.direction_drafts || []);
              setDirGap(r.data?.direction_gap || null);
            })
            .catch((e) => {
              setDrafts([]);
              setDirGap(null);
              message.warning(formatError(e, "方向建议没打开"));
            });
        }}
      />
      <Table
        rowKey="user_id"
        dataSource={visible}
        locale={{
          emptyText: err ? (
            <EmptyHint>成员加载失败，可重试。</EmptyHint>
          ) : (
            <EmptyHint>还没有成员。{teacher ? "用上方邀请加入。" : ""}</EmptyHint>
          ),
        }}
        columns={[
          {
            title: "姓名",
            dataIndex: "display_name",
            render: (v, row) => {
              const href = row.href || `/labs/${id}/cockpit/members/${row.user_id}`;
              return (
                <span>
                  <Link to={href}>{v || row.email}</Link>{" "}
                  <WorkLine text={row.work_line} sources={row.work_sources} />
                  <ActionChip to={href}>进展</ActionChip>
                  {row.literature_href ? (
                    <ActionChip to={row.literature_href}>{row.literature_label || "该成员文献"}</ActionChip>
                  ) : null}
                  {row.manuscripts_href ? (
                    <ActionChip to={row.manuscripts_href}>{row.manuscripts_label || "该成员论文"}</ActionChip>
                  ) : null}
                </span>
              );
            },
          },
          { title: "邮箱", dataIndex: "email" },
          { title: "角色", dataIndex: "role", render: (v) => <RoleTag role={v} /> },
          {
            title: "状态",
            dataIndex: "status",
            render: (v, row) =>
              teacher ? (
                <Select
                  size="small"
                  style={{ width: 96 }}
                  value={v || "active"}
                  options={[
                    { value: "active", label: "在组内" },
                    { value: "on_leave", label: "休学" },
                    { value: "graduated", label: "已毕业" },
                  ]}
                  onChange={async (status) => {
                    try {
                      await api.patchMember(id, row.user_id, { status });
                      message.success("已更新状态");
                      reload();
                    } catch (e) {
                      message.error(formatError(e, "仅导师可改状态"));
                    }
                  }}
                />
              ) : (
                <span className="fs-status-chip">{STATUS_LABEL[v || "active"]}</span>
              ),
          },
          {
            title: "职称/年级",
            dataIndex: "title",
            render: (v, row) =>
              teacher || row.user_id === me?.id ? (
                <Input
                  size="small"
                  defaultValue={v}
                  maxLength={80}
                  placeholder="职称或年级"
                  onBlur={async (e) => {
                    const next = e.target.value.trim();
                    if (next === (v || "")) return;
                    try {
                      await api.patchMember(id, row.user_id, { title: next });
                      message.success("已更新");
                      reload();
                    } catch (err) {
                      message.error(formatError(err, "无法更新职称"));
                    }
                  }}
                />
              ) : (
                v || "—"
              ),
          },
          {
            title: "档案",
            dataIndex: "bio",
            render: (v, row) => (
              <span>
                {v ? <span className="fs-muted">{String(v).slice(0, 24)}{v.length > 24 ? "…" : ""}</span> : <span className="fs-muted">未填</span>}
                {" "}
                <Button
                  type="link"
                  size="small"
                  onClick={async () => {
                    try {
                      const r = await api.memberProfile(id, row.user_id);
                      setProfile(r.data);
                      setProfileUid(row.user_id);
                      const academic = r.data.academic || {};
                      const links = academic.links || {};
                      profileForm.setFieldsValue({
                        bio: r.data.bio || "",
                        interests: (r.data.interests || []).join("，"),
                        skills: (r.data.skills || []).join("，"),
                        homepage: r.data.homepage || "",
                        name_en: academic.name_en || "",
                        aliases: (academic.aliases || []).join("，"),
                        affiliation: academic.affiliation || "",
                        orcid: links.orcid || "",
                        openalex: links.openalex || "",
                        scholar: links.scholar || "",
                        dblp: links.dblp || "",
                        ai_note: academic.ai_note || "",
                        sources: (academic.sources || []).map((s) => s.url || s).join("\n"),
                      });
                    } catch (e) {
                      message.error(formatError(e, "无法打开档案"));
                    }
                  }}
                >
                  {teacher || row.user_id === me?.id ? "编辑" : "查看"}
                </Button>
              </span>
            ),
          },
          {
            title: "方向",
            dataIndex: "direction_name",
            render: (v, row) => {
              const chips = (row.directions || []).length
                ? row.directions
                : row.direction_id
                  ? [{ id: row.direction_id, name: v, href: row.direction_href, is_primary: true }]
                  : [];
              return (
                <Space size={4} wrap>
                  {teacher ? (
                    <Select
                      size="small"
                      allowClear
                      mode="multiple"
                      style={{ minWidth: 180 }}
                      placeholder="挂方向"
                      value={chips.map((d) => d.id)}
                      options={dirs.map((d) => ({ value: d.id, label: d.name }))}
                      onChange={async (ids) => {
                        try {
                          await api.patchMember(id, row.user_id, { direction_ids: ids, direction_id: ids[0] || null });
                          message.success("已更新方向");
                          reload();
                        } catch (e) {
                          message.error(formatError(e, "无法改方向"));
                        }
                      }}
                    />
                  ) : chips.length ? (
                    chips.map((d) => (
                      <Link key={d.id} to={d.href || `/labs/${id}/directions/${d.id}`}>
                        {d.name}
                        {d.is_primary ? " · 主" : ""}
                      </Link>
                    ))
                  ) : (
                    "未选"
                  )}
                </Space>
              );
            },
          },
          {
            title: "最近问",
            key: "last_ai",
            render: (_, row) => {
              if (!row.last_ai?.question && !row.week_never_ai) return "—";
              return (
                <span onClick={(e) => e.stopPropagation()}>
                  {row.week_never_ai ? <div className="fs-muted">本周没问过</div> : null}
                  {row.last_ai?.question ? (
                    <span className="fs-muted">
                      {row.last_ai.question}
                      {row.last_ai.ground_why ? ` · ${row.last_ai.ground_why}` : ""}
                      <AgentTraceBox traceId={row.last_ai.trace_id} />
                    </span>
                  ) : null}
                </span>
              );
            },
          },
          { title: "最后活跃", dataIndex: "last_seen_at", render: fmtWhen },
          ...(teacher
            ? [
                {
                  title: "调整",
                  render: (_, row) => (
                    <Select
                      size="small"
                      value={row.role}
                      style={{ width: 96 }}
                      onChange={async (role) => {
                        try {
                          await api.patchMember(id, row.user_id, { role });
                          message.success("已更新角色");
                          reload();
                        } catch (e) {
                          message.error(formatError(e, "仅导师可改角色"));
                        }
                      }}
                      options={[
                        { value: "student", label: "学生" },
                        { value: "teacher", label: "导师" },
                      ]}
                    />
                  ),
                },
                {
                  title: "",
                  render: (_, row) =>
                    row.user_id === me?.id ? null : (
                      <Popconfirm
                        title="移出该成员？"
                        onConfirm={async () => {
                          try {
                            await api.deleteMember(id, row.user_id);
                            message.success("已移出");
                            reload();
                          } catch (e) {
                            message.error(formatError(e, "无法移出"));
                          }
                        }}
                      >
                        <Button size="small" danger>
                          移出
                        </Button>
                      </Popconfirm>
                    ),
                },
              ]
            : []),
        ]}
      />

      {teacher ? (
        <>
          <h2 className="fs-subhead" id="invites">
            <FsGlyph name="members" /> 待接受邀请{pendingInvites.length ? `（${pendingInvites.length}）` : ""}
          </h2>
          <Table
            rowKey="id"
            dataSource={pendingInvites}
            locale={{ emptyText: inviteErr ? "邀请加载失败，可重试。" : "还没有待接受的邀请" }}
            pagination={pendingInvites.length > 8 ? { pageSize: 8 } : false}
            columns={[
              { title: "邮箱", dataIndex: "email" },
              { title: "角色", dataIndex: "role", render: (v) => (v === "teacher" ? "导师" : "学生") },
              { title: "过期", dataIndex: "expires_at", render: fmtWhen },
              {
                title: "",
                render: (_, row) => (
                  <Space>
                    <Button
                      size="small"
                      onClick={async () => {
                        const url = row.invite_url || `${window.location.origin}/invite/${row.token}`;
                        const copied = await copyUrl(url);
                        message[copied ? "success" : "info"](copied ? "已复制链接" : url);
                      }}
                    >
                      复制链接
                    </Button>
                    <Popconfirm
                      title="撤销这封邀请？"
                      onConfirm={async () => {
                        await api.deleteInvite(row.id);
                        message.success("已撤销");
                        reload();
                      }}
                    >
                      <Button size="small" danger>
                        撤销
                      </Button>
                    </Popconfirm>
                  </Space>
                ),
              },
            ]}
          />
        </>
      ) : null}
      <Modal
        title={profile?.display_name ? `${profile.display_name} 的档案` : "成员档案"}
        open={!!profileUid}
        onCancel={() => {
          setProfileUid(null);
          setProfile(null);
        }}
        footer={null}
      >
        {profile ? (
          <Form
            form={profileForm}
            layout="vertical"
            onFinish={async (v) => {
              try {
                await api.patchMemberProfile(id, profileUid, {
                  bio: v.bio || "",
                  interests: String(v.interests || "").split(/[,，]/).map((s) => s.trim()).filter(Boolean),
                  skills: String(v.skills || "").split(/[,，]/).map((s) => s.trim()).filter(Boolean),
                  homepage: v.homepage || "",
                  name_en: v.name_en || "",
                  aliases: String(v.aliases || "").split(/[,，]/).map((s) => s.trim()).filter(Boolean),
                  affiliation: v.affiliation || "",
                  ai_note: v.ai_note || "",
                  links: {
                    orcid: v.orcid || "",
                    openalex: v.openalex || "",
                    scholar: v.scholar || "",
                    dblp: v.dblp || "",
                  },
                  sources: String(v.sources || "")
                    .split(/\n+/)
                    .map((s) => s.trim())
                    .filter(Boolean)
                    .map((url) => ({ url, allow: "1" })),
                });
                message.success("已保存档案");
                const urls = String(v.sources || "")
                  .split(/\n+/)
                  .map((s) => s.trim())
                  .filter(Boolean);
                if (urls.length) {
                  message.info("正在读留给助手的网站");
                  api.refreshProfileSources(id, profileUid).catch(() => {
                    message.warning("网站这次没读完，可以过一会儿再保存一次。");
                  });
                }
                setProfileUid(null);
                setProfile(null);
                reload();
              } catch (e) {
                message.error(formatError(e, "无法保存档案"));
              }
            }}
          >
            <Tabs
              items={[
                {
                  key: "base",
                  label: "基本",
                  forceRender: true,
                  children: (
                    <>
                      <Form.Item name="bio" label="自我介绍">
                        <Input.TextArea rows={3} maxLength={2000} disabled={!profile.can_edit} />
                      </Form.Item>
                      <Form.Item name="interests" label="研究兴趣">
                        <Input placeholder="逗号分隔" disabled={!profile.can_edit} />
                      </Form.Item>
                      <Form.Item name="skills" label="技能">
                        <Input placeholder="逗号分隔" disabled={!profile.can_edit} />
                      </Form.Item>
                      <Form.Item name="homepage" label="主页">
                        <Input disabled={!profile.can_edit} />
                      </Form.Item>
                    </>
                  ),
                },
                {
                  key: "academic",
                  label: "学术身份",
                  forceRender: true,
                  children: (
                    <>
                      <Form.Item name="name_en" label="英文名">
                        <Input disabled={!profile.can_edit} />
                      </Form.Item>
                      <Form.Item name="aliases" label="论文署名">
                        <Input placeholder="逗号分隔，例如 Zhang, San" disabled={!profile.can_edit} />
                      </Form.Item>
                      <Form.Item name="affiliation" label="单位">
                        <Input disabled={!profile.can_edit} />
                      </Form.Item>
                      <Form.Item name="orcid" label="ORCID">
                        <Input disabled={!profile.can_edit} />
                      </Form.Item>
                      <Form.Item name="openalex" label="OpenAlex">
                        <Input disabled={!profile.can_edit} />
                      </Form.Item>
                      <Form.Item name="scholar" label="谷歌学术主页">
                        <Input disabled={!profile.can_edit} />
                      </Form.Item>
                      <Form.Item name="dblp" label="DBLP">
                        <Input disabled={!profile.can_edit} />
                      </Form.Item>
                    </>
                  ),
                },
                {
                  key: "ai",
                  label: "给助手看的",
                  forceRender: true,
                  children: (
                    <>
                      <p className="fs-muted">组里的人和助手都能看到。不填邮箱和电话。</p>
                      <Form.Item name="ai_note" label="给助手的说明">
                        <Input.TextArea rows={2} disabled={!profile.can_edit} />
                      </Form.Item>
                      <Form.Item name="sources" label="网站，一行一个">
                        <Input.TextArea rows={3} placeholder="https://" disabled={!profile.can_edit} />
                      </Form.Item>
                      {(profile.sources_status || []).map((row, i) => (
                        <p className="fs-muted" key={`${row.url || "s"}-${i}`}>
                          {row.state === "read" ? "已读" : row.state === "failed" ? "没读成" : "跳过"}
                          {row.url ? ` · ${row.url}` : ""}
                        </p>
                      ))}
                      {profile.can_edit ? (
                        <Button
                          htmlType="button"
                          onClick={async () => {
                            const r = await api.suggestProfile(id, profileUid);
                            const links = r.data?.links || {};
                            profileForm.setFieldsValue({
                              affiliation: r.data?.affiliation || profileForm.getFieldValue("affiliation"),
                              orcid: links.orcid || "",
                              openalex: links.openalex || "",
                            });
                            message.info(r.data?.note || "这是草稿，保存后才会写入");
                          }}
                        >
                          让助手帮我填
                        </Button>
                      ) : null}
                      {profile.can_edit ? (
                        <Button
                          htmlType="button"
                          style={{ marginLeft: 8 }}
                          onClick={async () => {
                            message.info("正在读留给助手的网站");
                            try {
                              await api.refreshProfileSources(id, profileUid);
                              const again = await api.memberProfile(id, profileUid);
                              setProfile((prev) => ({ ...(prev || {}), ...(again.data || {}) }));
                              message.success("读完了");
                            } catch (e) {
                              message.warning(formatError(e, "网站这次没读完"));
                            }
                          }}
                        >
                          重新读取
                        </Button>
                      ) : null}
                    </>
                  ),
                },
              ]}
            />
            {spokenName(profile.advisor_name) ? <p className="fs-muted">导师：{spokenName(profile.advisor_name)}</p> : null}
            {profile.can_edit ? <Button htmlType="submit">保存</Button> : null}
          </Form>
        ) : null}
      </Modal>
    </Space>
  );
}
