import { Suspense, useEffect, useRef, useState } from "react";
import { Form, Input, Button, Checkbox, Spin, Result, Select, Space, message } from "antd";
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, storeTokens } from "./api.js";
import { useSession } from "./store.js";
import { formatError } from "./err.js";
import { afterAuth } from "./nav.js";
import { AuthFrame, HintTip, PageHeader, PageLoading } from "./ui.jsx";
import Shell from "./shell.jsx";
import {
  Admin,
  Assistant,
  Cockpit,
  CockpitMember,
  DatasetDetail,
  Datasets,
  Directions,
  Files,
  Graph,
  Home,
  KB,
  Keys,
  LabLlm,
  Language,
  Literature,
  LiteratureReader,
  MePage,
  Meetings,
  ManuscriptEditor,
  Manuscripts,
  Notifications,
  ReproduceDetail,
  Reproduces,
  RunDetail,
  Runs,
  Tasks,
  Writing,
} from "./routes.js";

function LabPaperRedirect() {
  const { itemId } = useParams();
  const { search } = useLocation();
  return <Navigate to={`/literature/${itemId}${search}`} replace />;
}

function RootHome({ me, labId }) {
  if (labId) return <Navigate to={`/labs/${labId}/assistant`} replace />;
  return <Home me={me} labId={labId} />;
}

function CockpitTabRedirect({ tab }) {
  const { id } = useParams();
  const { search } = useLocation();
  const q = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  q.set("tab", tab);
  return <Navigate to={`/labs/${id}/cockpit?${q.toString()}`} replace />;
}

function useHydrateSession() {
  const setMe = useSession((s) => s.setMe);
  const setLabId = useSession((s) => s.setLabId);
  const [loading, setLoading] = useState(!!localStorage.getItem("access"));
  useEffect(() => {
    if (!localStorage.getItem("access")) {
      setLoading(false);
      return;
    }
    api
      .me()
      .then((r) => {
        setMe(r.data);
        const stored = localStorage.getItem("labId");
        const next = stored || r.data.labs?.[0]?.lab_id || "";
        if (next) setLabId(next);
      })
      .catch(() => {
        useSession.getState().logout();
      })
      .finally(() => setLoading(false));
  }, [setMe, setLabId]);
  return loading;
}

function LoginPage() {
  const nav = useNavigate();
  const setMe = useSession((s) => s.setMe);
  const setLabId = useSession((s) => s.setLabId);
  const [busy, setBusy] = useState(false);
  const [guestBusy, setGuestBusy] = useState(false);
  const enter = async (pair) => {
    storeTokens(pair);
    const me = await api.me();
    setMe(me.data);
    const lab = me.data.labs?.[0];
    if (lab) setLabId(lab.lab_id);
    message.success("已进入");
    nav(afterAuth(me.data));
  };
  return (
    <AuthFrame title="登录" subtitle="没有账号可以先体验，或申请开通机构。">
      <Form
        layout="vertical"
        onFinish={async (v) => {
          setBusy(true);
          try {
            const r = await api.login(v.email, v.password);
            await enter(r.data);
          } catch (e) {
            message.error(formatError(e, "登录失败"));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Form.Item name="email" rules={[{ required: true, message: "请填写邮箱" }]}>
          <Input placeholder="邮箱" autoComplete="username" />
        </Form.Item>
        <Form.Item name="password" rules={[{ required: true, message: "请填写密码" }]}>
          <Input.Password placeholder="密码" autoComplete="current-password" />
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={busy}>
          {busy ? "正在进入" : "进入工作台"}
        </Button>
      </Form>
      <Button
        block
        style={{ marginTop: 12 }}
        loading={guestBusy}
        onClick={async () => {
          setGuestBusy(true);
          try {
            const r = await api.guestLogin();
            await enter(r.data);
          } catch (e) {
            message.error(formatError(e, "暂时进不去体验"));
          } finally {
            setGuestBusy(false);
          }
        }}
      >
        {guestBusy ? "正在进入" : "先以游客体验"}
      </Button>
      <p className="fs-muted" style={{ marginTop: 16, textAlign: "center" }}>
        <Link to="/apply">申请开通机构</Link>
      </p>
    </AuthFrame>
  );
}

function ApplyPage() {
  const nav = useNavigate();
  const setMe = useSession((s) => s.setMe);
  const [busy, setBusy] = useState(false);
  return (
    <AuthFrame title="申请开通机构" subtitle="提交后用此邮箱登录查看进度。">
      <Form
        layout="vertical"
        requiredMark={false}
        onFinish={async (v) => {
          if (!v.consent) {
            message.error("请勾选：组里的人能看到阅读和论文");
            return;
          }
          setBusy(true);
          try {
            const r = await api.applyOrg({ ...v, consent: true });
            storeTokens(r.data);
            const me = await api.me();
            setMe(me.data);
            message.success("已提交，请等待审核");
            nav(afterAuth(me.data));
          } catch (e) {
            message.error(formatError(e, "申请失败"));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Form.Item name="org_name" rules={[{ required: true, message: "请填写机构名称" }]}>
          <Input placeholder="机构名称" />
        </Form.Item>
        <Space.Compact block style={{ marginBottom: 24 }}>
          <Form.Item name="kind" initialValue="university" style={{ width: "40%", marginBottom: 0 }}>
            <Select
              options={[
                { value: "university", label: "高校" },
                { value: "hospital", label: "医院" },
                { value: "company", label: "企业" },
                { value: "independent", label: "课题组" },
                { value: "other", label: "其他" },
              ]}
            />
          </Form.Item>
          <Form.Item name="contact_name" style={{ width: "60%", marginBottom: 0 }}>
            <Input placeholder="联系人" />
          </Form.Item>
        </Space.Compact>
        <Form.Item name="apply_note" rules={[{ required: true, message: "请填写申请说明" }]}>
          <Input.TextArea rows={2} placeholder="研究方向或用途" />
        </Form.Item>
        <Form.Item name="email" rules={[{ required: true, type: "email", message: "请填写有效邮箱" }]}>
          <Input placeholder="登录邮箱" autoComplete="username" />
        </Form.Item>
        <Form.Item
          name="password"
          rules={[
            { required: true, min: 8, message: "至少 8 位" },
            {
              validator: (_, v) =>
                /[A-Za-z]/.test(v || "") && /\d/.test(v || "")
                  ? Promise.resolve()
                  : Promise.reject(new Error("须同时包含字母和数字")),
            },
          ]}
        >
          <Input.Password placeholder="密码（字母+数字，至少 8 位）" autoComplete="new-password" />
        </Form.Item>
        <div className="fs-consent-row">
          <Form.Item name="consent" valuePropName="checked" style={{ marginBottom: 0 }}>
            <Checkbox>组里的人能看到阅读进度和论文</Checkbox>
          </Form.Item>
          <HintTip title="组里都能看见">组员可看彼此在读、阅读进度和论文。只有作者能改论文。</HintTip>
        </div>
        <Button type="primary" htmlType="submit" block loading={busy}>
          {busy ? "正在提交" : "提交申请"}
        </Button>
      </Form>
      <p className="fs-muted" style={{ marginTop: 16, textAlign: "center" }}>
        <Link to="/login">已有账号，去登录</Link>
      </p>
    </AuthFrame>
  );
}

function PendingPage({ me, setMe }) {
  const nav = useNavigate();
  const setLabId = useSession((s) => s.setLabId);
  const warned = useRef(false);
  const [refreshing, setRefreshing] = useState(false);
  const [resubmitting, setResubmitting] = useState(false);
  const org = me?.org || {};
  const status = org.status || "pending";
  const block = me?.workbench_block || "";
  const statusText =
    block === "no_membership"
      ? "已开通但未加入实验室"
      : status === "pending"
        ? "审核中"
        : status === "rejected"
          ? "未通过"
          : status === "suspended"
            ? "已停用"
            : status === "approved"
              ? "已开通"
              : status;

  useEffect(() => {
    if (me?.workbench_ready) return undefined;
    const tick = () => {
      api
        .me()
        .then((r) => {
          setMe(r.data);
          if (r.data.workbench_ready) {
            const lab = r.data.labs?.[0];
            if (lab) setLabId(lab.lab_id);
            message.success(r.data.org?.status === "approved" ? "机构已开通" : "工作台已恢复");
            nav(afterAuth(r.data));
          }
        })
        .catch((e) => {
          if (warned.current) return;
          warned.current = true;
          message.warning(formatError(e, "开通进度没刷新，可点刷新"));
        });
    };
    const t = setInterval(tick, 15000);
    return () => clearInterval(t);
  }, [me?.workbench_ready, setMe, setLabId, nav]);

  const kickerClass =
    status === "rejected" || status === "suspended"
      ? "is-bad"
      : block === "no_membership"
        ? "is-warn"
        : status === "approved"
          ? "is-ok"
          : "";
  const detail =
    block === "no_membership"
      ? "机构已开通，但你还没加入实验室。请打开导师发来的邀请链接。"
      : status === "pending"
        ? "运营审核中，页面会自动刷新。请勿重复申请。"
        : status === "rejected"
          ? org.review_note || "请修改说明后重新提交。"
          : status === "suspended"
            ? org.review_note || "机构已停用，请联系平台运营。"
            : me?.workbench_ready
              ? "机构已开通，可进入工作台。"
              : "机构已开通，正在准备实验室，请稍候或点刷新。";

  const refresh = async () => {
    setRefreshing(true);
    try {
      const r = await api.me();
      setMe(r.data);
      if (r.data.workbench_ready) {
        const lab = r.data.labs?.[0];
        if (lab) setLabId(lab.lab_id);
        nav(afterAuth(r.data));
      } else if (r.data.workbench_block === "no_membership") message.info("请打开邀请链接加入实验室");
      else if (r.data.org?.status === "rejected") message.info("申请未通过，请修改说明后重提");
      else if (r.data.org?.status === "suspended") message.info("机构已停用");
      else if (r.data.org?.status === "approved") message.info("机构已开通，正在准备实验室");
      else message.info("仍在审核中");
    } catch (e) {
      message.error(formatError(e, "刷新失败"));
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="fs-pending">
      <PageHeader title="开通进度" hint="页面会自己刷新。也可以点下面的刷新。">等审核通过</PageHeader>
      <div className="fs-pending-card">
        <span className={`fs-pending-kicker ${kickerClass}`}>{statusText}</span>
        <h2>{org.name || "机构"}</h2>
        <p>{detail}</p>
        {status === "rejected" ? (
          <Form
            layout="vertical"
            requiredMark={false}
            initialValues={{
              apply_note: org.apply_note,
              contact_name: org.contact_name,
              contact_phone: org.contact_phone,
            }}
            onFinish={async (v) => {
              setResubmitting(true);
              try {
                await api.resubmitOrg(v);
                const me2 = await api.me();
                setMe(me2.data);
                message.success("已重新提交");
              } catch (e) {
                message.error(formatError(e, "没提交上"));
              } finally {
                setResubmitting(false);
              }
            }}
          >
            <Form.Item name="contact_name">
              <Input placeholder="联系人" />
            </Form.Item>
            <Form.Item name="apply_note" rules={[{ required: true, message: "请填写补充说明" }]}>
              <Input.TextArea rows={3} placeholder="补充说明" />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={resubmitting}>
              {resubmitting ? "正在提交" : "重新提交"}
            </Button>
          </Form>
        ) : (
          <Space>
            {status === "approved" && me?.workbench_ready ? (
              <Button
                type="primary"
                onClick={() => {
                  const lab = me.labs?.[0];
                  if (lab) setLabId(lab.lab_id);
                  nav(afterAuth(me));
                }}
              >
                进入工作台
              </Button>
            ) : null}
            <Button type={status === "approved" && me?.workbench_ready ? "default" : "primary"} loading={refreshing} onClick={refresh}>
              {refreshing ? "正在刷新" : "刷新进度"}
            </Button>
          </Space>
        )}
      </div>
    </div>
  );
}

function InvitePage() {
  const { token } = useParams();
  const nav = useNavigate();
  const me = useSession((s) => s.me);
  const setMe = useSession((s) => s.setMe);
  const setLabId = useSession((s) => s.setLabId);
  const [preview, setPreview] = useState(null);
  const [previewErr, setPreviewErr] = useState("");
  const [joining, setJoining] = useState(false);
  useEffect(() => {
    api
      .invitePreview(token)
      .then((r) => setPreview(r.data))
      .catch((e) => setPreviewErr(formatError(e, "邀请无效")));
  }, [token]);

  const finishJoin = async (accessPair) => {
    storeTokens(accessPair);
    const meRes = await api.me();
    setMe(meRes.data);
    const lab = meRes.data.labs?.[0];
    if (lab) setLabId(lab.lab_id);
    message.success("已加入");
    nav(afterAuth(meRes.data));
  };

  if (previewErr) {
    return (
      <AuthFrame title="无法加入">
        <Result status="error" title="邀请无效" subTitle={previewErr} extra={<Link to="/login">去登录</Link>} />
      </AuthFrame>
    );
  }
  if (!preview) {
    return (
      <AuthFrame title="打开邀请">
        <Spin tip="正在核对邀请" />
      </AuthFrame>
    );
  }
  if (!preview.usable) {
    const orgBlocked = preview.org_status && preview.org_status !== "approved";
    return (
      <AuthFrame title="无法加入">
        <Result
          status="warning"
          title={
            orgBlocked
              ? "机构暂不可加入"
              : preview.accepted
                ? "邀请已使用"
                : "邀请已过期"
          }
          subTitle={
            orgBlocked
              ? `${preview.org_name || preview.lab_name || "机构"} · 状态 ${preview.org_status === "suspended" ? "已停用" : preview.org_status === "pending" ? "审核中" : "未开通"}。请联系导师或平台运营。`
              : `${preview.lab_name || "实验室"} · ${preview.email}`
          }
          extra={<Link to="/login">去登录</Link>}
        />
      </AuthFrame>
    );
  }

  // 已有账号：登录后点接受，或当前已登录且邮箱匹配
  if (preview.account_exists) {
    const loggedInMatch = me?.email && me.email.toLowerCase() === preview.email.toLowerCase();
    return (
      <AuthFrame
        title={`加入 ${preview.lab_name || "实验室"}`}
        subtitle={`${preview.role === "teacher" ? "导师" : "学生"} · ${preview.email} 已有账号`}
      >
        <p className="fs-muted" style={{ marginBottom: 16 }}>
          {preview.consent_text}
        </p>
        {loggedInMatch ? (
          <Button
            type="primary"
            block
            loading={joining}
            onClick={async () => {
              setJoining(true);
              try {
                const r = await api.acceptInvite(token);
                await finishJoin(r.data);
              } catch (e) {
                message.error(formatError(e, "没加入"));
              } finally {
                setJoining(false);
              }
            }}
          >
            {joining ? "正在加入" : "同意并加入"}
          </Button>
        ) : (
          <Form
            layout="vertical"
            initialValues={{ email: preview.email }}
            onFinish={async (v) => {
              setJoining(true);
              try {
                const login = await api.login(v.email, v.password);
                storeTokens(login.data);
                const r = await api.acceptInvite(token);
                await finishJoin(r.data);
              } catch (e) {
                message.error(formatError(e, "没加入"));
              } finally {
                setJoining(false);
              }
            }}
          >
            <Form.Item name="email" rules={[{ required: true, type: "email" }]}>
              <Input disabled />
            </Form.Item>
            <Form.Item name="password" rules={[{ required: true }]}>
              <Input.Password placeholder="密码" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={joining}>
              {joining ? "正在加入" : "登录并加入"}
            </Button>
          </Form>
        )}
      </AuthFrame>
    );
  }

  return (
    <AuthFrame
      title={`加入 ${preview.lab_name || "实验室"}`}
      subtitle={`${preview.role === "teacher" ? "导师" : "学生"} · 请使用 ${preview.email}`}
    >
      <p className="fs-muted" style={{ marginBottom: 16 }}>
        {preview.consent_text}
      </p>
      <Form
        layout="vertical"
        initialValues={{ email: preview.email }}
        onFinish={async (v) => {
          if (!v.consent) {
            message.error("请勾选：组里的人能看到阅读和论文");
            return;
          }
          setJoining(true);
          try {
            const r = await api.register({
              invite_token: token,
              email: v.email,
              password: v.password,
              display_name: v.display_name,
              consent: true,
            });
            await finishJoin(r.data);
          } catch (e) {
            message.error(formatError(e, "没加入"));
          } finally {
            setJoining(false);
          }
        }}
      >
        <Form.Item name="display_name">
          <Input placeholder="显示名" />
        </Form.Item>
        <Form.Item name="email" rules={[{ required: true, type: "email" }]}>
          <Input placeholder="邮箱（须与邀请一致）" />
        </Form.Item>
        <Form.Item
          name="password"
          rules={[
            { required: true, min: 8, message: "至少 8 位" },
            {
              validator: (_, v) =>
                /[A-Za-z]/.test(v || "") && /\d/.test(v || "")
                  ? Promise.resolve()
                  : Promise.reject(new Error("须同时包含字母和数字")),
            },
          ]}
        >
          <Input.Password placeholder="密码至少 8 位，须含字母和数字" />
        </Form.Item>
        <div className="fs-consent-row">
          <Form.Item name="consent" valuePropName="checked" style={{ marginBottom: 0 }}>
            <Checkbox>组里的人能看到阅读进度和论文</Checkbox>
          </Form.Item>
          <HintTip title="组里都能看见">组员可看彼此在读、阅读进度和论文。只有作者能改论文。</HintTip>
        </div>
        <Button type="primary" htmlType="submit" block loading={joining}>
          {joining ? "正在加入" : "同意并加入"}
        </Button>
      </Form>
    </AuthFrame>
  );
}

export default function App() {
  const loading = useHydrateSession();
  const me = useSession((s) => s.me);
  const setMe = useSession((s) => s.setMe);
  const labId = useSession((s) => s.labId) || me?.labs?.[0]?.lab_id;
  if (loading) {
    return (
      <div className="fs-auth">
        <Spin tip="正在进入蜂算科研" />
      </div>
    );
  }
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/apply" element={<ApplyPage />} />
      <Route path="/invite/:token" element={<InvitePage />} />
      <Route
        path="/*"
        element={
          me ? (
            <Shell me={me}>
              <Suspense fallback={<PageLoading label="正在打开这一页" />}>
              <Routes>
                <Route path="/" element={<RootHome me={me} labId={labId} />} />
                <Route path="/home" element={<Home me={me} labId={labId} />} />
                <Route path="/pending" element={<PendingPage me={me} setMe={setMe} />} />
                <Route path="/me" element={<MePage me={me} setMe={setMe} />} />
                <Route path="/labs/:id/cockpit" element={<Cockpit />} />
                <Route path="/labs/:id/cockpit/members/:userId" element={<CockpitMember />} />
                <Route path="/labs/:id/members" element={<CockpitTabRedirect tab="people" />} />
                <Route path="/labs/:id/directions" element={<CockpitTabRedirect tab="directions" />} />
                <Route path="/labs/:id/directions/:dirId" element={<Directions />} />
                <Route path="/labs/:id/literature" element={<Literature />} />
                <Route path="/labs/:id/literature/:itemId" element={<LabPaperRedirect />} />
                <Route path="/literature/:id" element={<LiteratureReader />} />
                <Route path="/labs/:id/manuscripts" element={<Manuscripts />} />
                <Route path="/manuscripts/:id" element={<ManuscriptEditor />} />
                <Route path="/labs/:id/assistant" element={<Assistant />} />
                <Route path="/labs/:id/kb" element={<KB />} />
                <Route path="/labs/:id/language" element={<Language />} />
                <Route path="/labs/:id/writing" element={<Writing />} />
                <Route path="/labs/:id/runs" element={<Runs />} />
                <Route path="/runs/:id" element={<RunDetail />} />
                <Route path="/labs/:id/reproduces" element={<Reproduces />} />
                <Route path="/reproduces/:id" element={<ReproduceDetail />} />
                <Route path="/labs/:id/tasks" element={<Tasks />} />
                <Route path="/labs/:id/meetings" element={<Meetings />} />
                <Route path="/labs/:id/files" element={<Files />} />
                <Route path="/labs/:id/stats" element={<CockpitTabRedirect tab="progress" />} />
                <Route path="/labs/:id/work-logs" element={<CockpitTabRedirect tab="feed" />} />
                <Route path="/labs/:id/datasets" element={<Datasets />} />
                <Route path="/datasets/:id" element={<DatasetDetail />} />
                <Route path="/labs/:id/digest" element={<CockpitTabRedirect tab="progress" />} />
                <Route path="/labs/:id/graph" element={<Graph />} />
                <Route path="/notifications" element={<Notifications />} />
                <Route path="/settings/keys" element={<Keys />} />
                <Route path="/labs/:id/settings/llm" element={<LabLlm />} />
                <Route path="/admin" element={<Admin />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
              </Suspense>
            </Shell>
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />
    </Routes>
  );
}
