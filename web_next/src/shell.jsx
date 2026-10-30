import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, AutoComplete, Button, Drawer, Dropdown, Input, Layout, Menu, Modal, Select, Badge, message } from "antd";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, asItems } from "./api.js";
import { useSession } from "./store.js";
import { queryClient } from "./query.js";
import { afterLabChange, moreKeysHolding, navPersona, navSelection, pageMeta, roleOf, routeKey, splitNavGroup, NAV_PRIMARY } from "./nav.js";
import { prefetchDaily, prefetchRoute } from "./routes.js";
import { ActionChip, Crumbs, FeedbackStrip, FsGlyph, ReproduceChip, ReproduceHonest, ResumeJob, fmtWhen, spokenName } from "./ui.jsx";
import { formatError, JUMP_TYPE, RUN_STATUS, noticeHref } from "./err.js";
import { AssistantChat } from "./assistant/AssistantChat.jsx";
import { useAssistant } from "./assistant/useAssistant.js";

const { Header, Content, Sider } = Layout;

function noticeLabel(unread, unreadKnown, unreadErr) {
  if (unreadErr) return "通知（未知）";
  if (unreadKnown && unread) return `通知（${unread}）`;
  return "通知";
}

function navItems({ labId, role, unread, unreadKnown, unreadErr, isAdmin, workbenchReady, pendingOrgs, workbenchBlock }) {
  const lab = labId ? `/labs/${labId}` : "";
  const link = (to, label, glyph) => (
    <Link
      to={to}
      className="fs-nav-link"
      onMouseEnter={() => prefetchRoute(routeKey(to))}
      onFocus={() => prefetchRoute(routeKey(to))}
    >
      {glyph ? <FsGlyph name={glyph} /> : null}
      <span>{label}</span>
    </Link>
  );
  if (!workbenchReady && !isAdmin) {
    const account = [
      { key: "pending", label: link("/pending", "开通进度", "pending") },
      { key: "ntf", label: link("/notifications", noticeLabel(unread, unreadKnown, unreadErr), "ntf") },
      { key: "me", label: link("/me", "资料", "me") },
    ];
    if (workbenchBlock === "suspended" && labId) {
      account.splice(1, 0, { key: "members", label: link(`${lab}/members`, "成员与邀请", "members") });
    }
    return [
      {
        type: "group",
        label: "账户",
        children: account,
      },
    ];
  }
  const persona = navPersona(role, isAdmin);
  const prim = NAV_PRIMARY[persona];
  const work = [{ key: "home", label: link("/home", "我的", "home") }];
  if (labId) {
    work.push({ key: "assistant", label: link(`${lab}/assistant`, "助手", "ai") });
    work.push({ key: "cockpit", label: link(`${lab}/cockpit`, "组里", "cockpit") });
  }
  const assets = labId
    ? [
        { key: "lit", label: link(`${lab}/literature`, "文献", "lit") },
        { key: "writing", label: link(`${lab}/writing`, "写作", "writing") },
        { key: "ms", label: link(`${lab}/manuscripts`, "我的论文", "ms") },
        { key: "kb", label: link(`${lab}/kb`, "知识库", "kb") },
        { key: "lang", label: link(`${lab}/language`, "改文字", "lang") },
      ]
    : [];
  const agents = labId
    ? [
        { key: "runs", label: link(`${lab}/runs`, "文献调研", "runs") },
        { key: "repro", label: link(`${lab}/reproduces`, "复现", "repro") },
      ]
    : [];
  const collab = labId
    ? [
        { key: "data", label: link(`${lab}/datasets`, "数据集", "data", "data") },
        { key: "graph", label: link(`${lab}/graph`, "引用图谱", "graph", "graph") },
        { key: "digest", label: link(`${lab}/digest`, "周报", "digest", "digest") },
        { key: "members", label: link(`${lab}/members`, "成员", "members") },
        { key: "dirs", label: link(`${lab}/directions`, "方向", "dirs") },
        { key: "tasks", label: link(`${lab}/tasks`, "任务", "tasks", "tasks") },
        { key: "meet", label: link(`${lab}/meetings`, "组会", "meet", "meet") },
        { key: "files", label: link(`${lab}/files`, "共享文件", "files", "files") },
        { key: "stats", label: link(`${lab}/stats`, "统计", "stats", "stats") },
        { key: "logs", label: link(`${lab}/work-logs`, "工作日志", "logs", "logs") },
      ]
    : [];
  const account = [
    { key: "ntf", label: link("/notifications", noticeLabel(unread, unreadKnown, unreadErr), "ntf") },
    { key: "keys", label: link("/settings/keys", "个人模型", "keys") },
    labId && (role === "teacher" || isAdmin) && { key: "llab", label: link(`${lab}/settings/llm`, "实验室模型", "ai") },
    { key: "me", label: link("/me", "资料", "me") },
    isAdmin && {
      key: "admin",
      label: link("/admin", pendingOrgs ? `管理（待审 ${pendingOrgs}）` : "管理", "admin"),
    },
  ].filter(Boolean);
  if (labId && (persona === "student" || persona === "teacher" || isAdmin)) {
    const teacherish = persona === "teacher" || isAdmin;
    const more = [
      { key: "home", label: link("/home", "我的", "home") },
      { key: "writing", label: link(`${lab}/writing`, "写作", "writing") },
      { key: "lang", label: link(`${lab}/language`, "改文字", "lang") },
      teacherish ? { key: "kb", label: link(`${lab}/kb`, "知识库", "kb") } : null,
      ...agents,
      ...collab.filter((item) => !["dirs", "members", "digest", "stats", "logs"].includes(item.key)),
      ...account.filter((item) => item.key !== "admin" && item.key !== "ntf"),
    ].filter(Boolean);
    const adminItem = account.find((item) => item.key === "admin");
    return [
      {
        type: "group",
        label: " ",
        children: [
          { key: "assistant", label: link(`${lab}/assistant`, "助手", "ai") },
          { key: "lit", label: link(`${lab}/literature`, "文献", "lit") },
          { key: "cockpit", label: link(`${lab}/cockpit`, "组里", "cockpit") },
          { key: "ms", label: link(`${lab}/manuscripts`, "我的论文", "ms") },
          { key: "ntf", label: link("/notifications", noticeLabel(unread, unreadKnown, unreadErr), "ntf") },
          adminItem,
          { key: "lab-more", label: "更多", children: more },
        ].filter(Boolean),
      },
    ];
  }
  return [
    { type: "group", label: "工作台", children: splitNavGroup(work, prim.work, "work-more") },
    assets.length ? { type: "group", label: "科研资产", children: splitNavGroup(assets, prim.assets, "assets-more") } : null,
    agents.length ? { type: "group", label: "实验任务", children: splitNavGroup(agents, prim.agents, "agents-more") } : null,
    collab.length ? { type: "group", label: "协作", children: splitNavGroup(collab, prim.collab, "collab-more") } : null,
    { type: "group", label: "账户", children: splitNavGroup(account, prim.account, "account-more", isAdmin ? ["admin"] : []) },
  ].filter(Boolean);
}

export default function Shell({ me, children }) {
  const nav = useNavigate();
  const loc = useLocation();
  const labId = useSession((s) => s.labId) || me?.labs?.[0]?.lab_id;
  const setLabId = useSession((s) => s.setLabId);
  const unread = useSession((s) => s.unread);
  const unreadKnown = useSession((s) => s.unreadKnown);
  const unreadErr = useSession((s) => s.unreadErr);
  const setUnread = useSession((s) => s.setUnread);
  const setUnreadErr = useSession((s) => s.setUnreadErr);
  const [collapsed, setCollapsed] = useState(typeof window !== "undefined" && window.innerWidth < 900);
  const [narrow, setNarrow] = useState(typeof window !== "undefined" && window.innerWidth < 900);
  const [askOpen, setAskOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const askChat = useAssistant(labId);
  const askHome = useQuery({
    queryKey: ["assistant-home", labId],
    queryFn: () => api.assistantHome(labId).then((r) => r.data),
    enabled: !!labId && askOpen,
  });
  const recentAsk = (askHome.data?.ask_sessions || []).filter((s) => s.question).slice(0, 4);
  const [openKeys, setOpenKeys] = useState(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem("fs-nav-more") || "[]");
      return Array.isArray(saved) ? saved : [];
    } catch {
      return [];
    }
  });
  const role = roleOf(me, labId);
  const selected = navSelection(loc.pathname);
  const meta = pageMeta(loc.pathname, labId, loc.search);
  const workbenchReady = !!me?.workbench_ready || !!me?.is_admin;

  useEffect(() => {
    document.title = `${meta.title} · 蜂算科研`;
  }, [meta.title]);

  useEffect(() => {
    if (!workbenchReady && !me?.is_admin) return undefined;
    const warm = () => prefetchDaily();
    if (typeof window.requestIdleCallback === "function") {
      const id = window.requestIdleCallback(warm, { timeout: 2000 });
      return () => window.cancelIdleCallback(id);
    }
    const id = window.setTimeout(warm, 800);
    return () => window.clearTimeout(id);
  }, [workbenchReady, me?.is_admin]);

  useEffect(() => {
    const onKey = (e) => {
      const tag = (e.target?.tagName || "").toLowerCase();
      const typing = tag === "input" || tag === "textarea" || tag === "select" || e.target?.isContentEditable;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        if (/^\/literature\/[^/]+/.test(loc.pathname)) {
          const next = new URLSearchParams(loc.search);
          next.set("panel", "tutor");
          nav(`${loc.pathname}?${next.toString()}`);
          return;
        }
        setAskOpen(true);
        return;
      }
      const slash = e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey;
      const chord = (e.metaKey || e.ctrlKey) && String(e.key || "").toLowerCase() === "k";
      if ((slash || chord) && !typing && workbenchReady && labId) {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    const onResize = () => setNarrow(window.innerWidth < 900);
    const onScroll = () => setScrolled(window.scrollY > 4);
    onResize();
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onScroll);
    };
  }, [loc.pathname, loc.search, nav, workbenchReady, labId]);

  useEffect(() => {
    if (workbenchReady || me?.is_admin) return;
    const allow = ["/pending", "/me", "/notifications", "/login"];
    const suspendedMembers =
      me?.workbench_block === "suspended" && /^\/labs\/[^/]+\/members\/?$/.test(loc.pathname);
    if (
      !suspendedMembers &&
      !allow.some((p) => loc.pathname === p || loc.pathname.startsWith(`${p}/`))
    ) {
      nav("/pending", { replace: true });
    }
  }, [workbenchReady, me?.is_admin, me?.workbench_block, loc.pathname, nav]);

  useEffect(() => {
    const beat = () => api.heartbeat(labId).catch(() => {});
    beat();
    const t = setInterval(beat, 60000);
    const n = () =>
      api
        .unreadCount()
        .then((r) => setUnread(r.data.unread || 0))
        .catch((e) => setUnreadErr(formatError(e, "未读数加载失败")));
    n();
    const tn = setInterval(n, 30000);
    let tm = null;
    if (me?.is_admin) {
      const refreshMe = () =>
        api
          .me()
          .then((r) => useSession.getState().setMe(r.data))
          .catch(() => {});
      refreshMe();
      tm = setInterval(refreshMe, 60000);
    }
    return () => {
      clearInterval(t);
      clearInterval(tn);
      if (tm) clearInterval(tm);
    };
  }, [labId, setUnread, setUnreadErr, me?.is_admin]);

  const items = useMemo(
    () =>
      navItems({
        labId,
        role,
        unread,
        unreadKnown,
        unreadErr,
        isAdmin: me.is_admin,
        workbenchReady,
        pendingOrgs: me.org_pending_count || 0,
        workbenchBlock: me.workbench_block || "",
      }),
    [labId, role, unread, unreadKnown, unreadErr, me.is_admin, workbenchReady, me.org_pending_count, me.workbench_block]
  );

  const accountName = spokenName(me.display_name) || spokenName(String(me.email || "").split("@")[0]) || "账号";
  const initial = accountName.slice(0, 1);
  const askHere = (q, extra = {}) => {
    const m = loc.pathname.match(/\/directions\/([^/]+)/);
    const member = loc.pathname.match(/\/cockpit\/members\/([^/]+)/);
    const paper = loc.pathname.match(/\/literature\/([^/]+)/);
    const context = paper
      ? { kind: "paper", id: paper[1] }
      : m
        ? { kind: "direction", id: m[1] }
        : member
          ? { kind: "member", id: member[1] }
          : undefined;
    askChat.ask(q, { context, replace: extra.replace });
  };

  return (
    <Layout className="fs-shell">
      <a className="fs-skip" href="#fs-main">
        跳到内容
      </a>
      <Layout>
        {narrow ? (
          <Drawer
            className="fs-sider-drawer"
            placement="left"
            width={232}
            open={!collapsed}
            onClose={() => setCollapsed(true)}
            title={null}
            closable={false}
          >
            <SiderBody
              collapsed={false}
              role={role}
              labId={labId}
              me={me}
              setLabId={setLabId}
              nav={nav}
              initial={initial}
              items={items}
              selected={selected}
              openKeys={openKeys}
              setOpenKeys={setOpenKeys}
              unread={unread}
              unreadKnown={unreadKnown}
              unreadErr={unreadErr}
              setUnread={setUnread}
              setUnreadErr={setUnreadErr}
              onNavigate={() => setCollapsed(true)}
            />
          </Drawer>
        ) : (
          <Sider className="fs-sider" width={232} collapsedWidth={60} theme="light" collapsible collapsed={collapsed} onCollapse={setCollapsed} trigger={null}>
            <SiderBody
              collapsed={collapsed}
              role={role}
              labId={labId}
              me={me}
              setLabId={setLabId}
              nav={nav}
              initial={initial}
              items={items}
              selected={selected}
              openKeys={openKeys}
              setOpenKeys={setOpenKeys}
              unread={unread}
              unreadKnown={unreadKnown}
              unreadErr={unreadErr}
              setUnread={setUnread}
              setUnreadErr={setUnreadErr}
            />
          </Sider>
        )}
        <Layout>
          <Header className={`fs-topbar${scrolled ? " is-scrolled" : ""}`}>
            {narrow || collapsed ? (
              <button type="button" className="fs-nav-toggle" aria-label="菜单" onClick={() => setCollapsed((v) => !v)}>
                菜单
              </button>
            ) : null}
            <strong className="fs-topbar-title">{meta.title}</strong>
            <span className="fs-header-spacer" />
            {workbenchReady && labId ? (
              <button type="button" className="fs-palette-btn" onClick={() => setPaletteOpen(true)}>
                <FsGlyph name="search" />
                <span>搜文献、我的论文、方向…</span>
                <kbd className="fs-jump-kbd">Ctrl K</kbd>
              </button>
            ) : (
              <span className="fs-header-lock">
                {me?.workbench_block === "no_membership"
                  ? "已开通 · 尚未加入实验室"
                  : me?.org?.status === "suspended"
                    ? "机构已停用 · 工作台锁定"
                    : me?.org?.status === "rejected"
                      ? "申请未通过 · 工作台锁定"
                      : "开通审核中 · 工作台锁定"}
              </span>
            )}
            {labId && !loc.pathname.includes("/assistant") ? (
              <Button
                className="fs-ask-entry"
                type="text"
                aria-label="问助手"
                onClick={() => {
                  if (/^\/literature\/[^/]+/.test(loc.pathname)) {
                    const next = new URLSearchParams(loc.search);
                    next.set("panel", "tutor");
                    nav(`${loc.pathname}?${next.toString()}`);
                    return;
                  }
                  setAskOpen(true);
                }}
              >
                问助手
              </Button>
            ) : null}
          </Header>
        <Content>
          <div className={`fs-page${loc.pathname.startsWith("/literature/") ? " is-wide" : ""}${loc.pathname.includes("/assistant") ? " is-assistant" : ""}`} id="fs-main">
            {me?.is_guest ? (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message="这是共用的演示账号"
                description={
                  <span>
                    你写下的提问、笔记和上传，后来的人也能看到。
                    <Link to="/apply"> 正式使用请申请开通。</Link>
                  </span>
                }
              />
            ) : null}
            <Crumbs items={meta.crumb} />
            {!workbenchReady && !me?.is_admin && loc.pathname !== "/pending" ? (
              <Alert
                type={
                  me?.workbench_block === "no_membership"
                    ? "warning"
                    : me?.org?.status === "rejected" || me?.org?.status === "suspended"
                      ? "error"
                      : "info"
                }
                showIcon
                style={{ marginBottom: 16 }}
                message={
                  me?.workbench_block === "no_membership"
                    ? "机构已开通，但您尚未加入实验室"
                    : me?.org?.status === "suspended"
                      ? "机构已停用，工作台锁定"
                      : me?.org?.status === "rejected"
                        ? "机构申请未通过"
                        : "机构开通申请审核中"
                }
                description={
                  me?.workbench_block === "no_membership"
                    ? "请打开导师发来的邀请链接，或到开通进度查看说明。"
                    : me?.org?.status === "rejected" || me?.org?.status === "suspended"
                      ? me?.org?.review_note || "请到开通进度查看说明。"
                      : "通过前无法使用文献、论文与实验任务。可在开通进度查看状态。"
                }
                action={
                  <Button size="small" type="link" onClick={() => nav("/pending")}>
                    开通进度
                  </Button>
                }
              />
            ) : null}
            {children}
          </div>
        </Content>
        </Layout>
      </Layout>
      <Modal
        className="fs-palette"
        open={paletteOpen}
        onCancel={() => setPaletteOpen(false)}
        footer={null}
        width={640}
        destroyOnClose
        closable={false}
        title={null}
      >
        {labId ? (
          <LabJump
            labId={labId}
            nav={(href) => {
              setPaletteOpen(false);
              nav(href);
            }}
          />
        ) : null}
      </Modal>
      <Drawer
        title="问助手"
        placement="right"
        width={460}
        open={askOpen}
        onClose={() => setAskOpen(false)}
        extra={
          <>
            {askChat.turns.length ? (
              <Button type="link" onClick={() => askChat.reset()}>
                新对话
              </Button>
            ) : null}
            <Button
              type="link"
              onClick={() => {
                const sid = askChat.sessionId;
                nav(sid ? `/labs/${labId}/assistant?session=${encodeURIComponent(sid)}` : `/labs/${labId}/assistant`);
                setAskOpen(false);
              }}
            >
              打开完整对话
            </Button>
          </>
        }
      >
        {labId ? (
          <AssistantChat
            labId={labId}
            turns={askChat.turns}
            busy={askChat.busy}
            quiet={!askChat.turns.length}
            placeholder={
              /\/directions\//.test(loc.pathname)
                ? "问这个方向"
                : /\/cockpit\/members\//.test(loc.pathname)
                  ? "问这个人"
                  : "问这个组"
            }
            welcome={
              askChat.turns.length ? null : (
                <div className="fs-ask-welcome is-compact">
                  <p className="fs-ask-lead">问这个组在做什么。</p>
                  <div className="fs-ask-starters">
                    {["这个组在做什么方向？", "谁在做什么？", "组里有哪些文献？", "最近做了什么？"].map((q) => (
                      <button
                        key={q}
                        type="button"
                        className="fs-ask-suggest"
                        onClick={() => askHere(q)}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                  {askHome.data?.my_role === "teacher" && !/\/assistant\/?$/.test(loc.pathname) ? (
                    <Link className="fs-ask-voice-open" to={`/labs/${labId}/assistant`}>
                      {askHome.data.agent_intro ? "改组里要记住的" : "去填写组里要记住的"}
                    </Link>
                  ) : null}
                  {recentAsk.length ? (
                    <>
                      <p className="fs-ask-bucket">最近问过</p>
                      {recentAsk.map((s) => (
                        <button
                          key={s.session_id || s.question}
                          type="button"
                          className="fs-ask-suggest"
                          title={s.question}
                          onClick={() => askChat.openPast(s)}
                        >
                          {s.title || s.question}
                        </button>
                      ))}
                    </>
                  ) : null}
                </div>
              )
            }
            onAsk={askHere}
            onStop={askChat.stop}
            pendingConfirm={askChat.pendingConfirm}
            onConfirm={askChat.confirm}
          />
        ) : null}
      </Drawer>
      <div className="fs-footer">
        蜂算科研 {me.version ? `v${me.version}` : ""} · {me.engine_credit || "powered by OpenLens"}
      </div>
    </Layout>
  );
}

function SiderBody({
  collapsed,
  role,
  labId,
  me,
  setLabId,
  nav,
  initial,
  items,
  selected,
  openKeys,
  setOpenKeys,
  unread,
  unreadKnown,
  unreadErr,
  setUnread,
  setUnreadErr,
  onNavigate,
}) {
  const face = spokenName(me?.display_name) || spokenName(String(me?.email || "").split("@")[0]) || "账号";
  return (
    <div className="fs-sider-body">
      <div
        className="fs-brand"
        onClick={() => nav(afterLabChange(role, labId))}
        onKeyDown={(e) => e.key === "Enter" && nav(afterLabChange(role, labId))}
        role="link"
        tabIndex={0}
      >
        <span className="fs-mark" aria-hidden>
          蜂
        </span>
        {collapsed ? null : (
          <div>
            <strong>蜂算科研</strong>
            <p>{role === "teacher" ? "导师工作台" : role === "student" ? "学生工作台" : "实验室工作台"}</p>
          </div>
        )}
      </div>
      {!collapsed && (me.labs || []).length ? (
        <Select
          aria-label="当前实验室"
          className="fs-lab-select"
          placeholder="选择实验室"
          value={labId || undefined}
          onChange={(v) => {
            const nextRole = (me.labs || []).find((l) => l.lab_id === v)?.role;
            setLabId(v);
            queryClient.invalidateQueries();
            nav(afterLabChange(nextRole, v));
          }}
          options={(me.labs || []).map((l) => ({
            value: l.lab_id,
            label: `${l.name} · ${l.via_admin ? "管理" : l.role === "teacher" ? "导师" : "学生"}`,
          }))}
          popupMatchSelectWidth={false}
        />
      ) : null}
      <div className="fs-sider-menu">
        <Menu
          mode="inline"
          inlineCollapsed={collapsed}
          selectedKeys={[selected]}
          openKeys={collapsed ? [] : Array.from(new Set([...openKeys, ...moreKeysHolding(selected, items)]))}
          onOpenChange={(keys) => {
            const next = keys.filter((k) => String(k).endsWith("-more"));
            setOpenKeys(next);
            try {
              window.localStorage.setItem("fs-nav-more", JSON.stringify(next));
            } catch {
              /* ignore */
            }
          }}
          onClick={() => onNavigate?.()}
          items={items}
        />
      </div>
      <div className="fs-sider-foot">
        <Dropdown
          trigger={["click"]}
          menu={{
            items: [
              { key: "me", label: "我的资料", onClick: () => nav("/me") },
              { key: "keys", label: "个人模型", onClick: () => nav("/settings/keys") },
              { type: "divider" },
              {
                key: "out",
                label: "退出",
                onClick: () => {
                  useSession.getState().logout();
                  queryClient.clear();
                  nav("/login");
                },
              },
            ],
          }}
        >
          <button type="button" className="fs-userchip" aria-label="账户菜单">
            <span className="fs-avatar">{initial}</span>
            <span className="fs-userchip-name">{collapsed ? "" : face}</span>
          </button>
        </Dropdown>
      </div>
    </div>
  );
}

function jumpHintOption() {
  return {
    value: "__hint__",
    disabled: true,
    label: "输入题名、作者、DOI 或任务。回车打开第一条，没有则去文献检索。",
  };
}

function jumpRowExtra(it) {
  if (it.latest_reproduce?.measured) return "已经跑出结果";
  if (it.latest_reproduce) return "还没跑出结果";
  if (it.ungrounded_reproduce) return "写了结果，组里还没跑过";
  if (it.feedback?.title) return it.feedback.title;
  if (it.next_step) return it.next_step;
  if (it.subtitle) return it.subtitle;
  if (it.status && RUN_STATUS[it.status]) return RUN_STATUS[it.status];
  return "";
}

function jumpRowOption(it) {
  const extra = jumpRowExtra(it);
  return {
    value: it.href,
    key: `${it.type}-${it.id}`,
    label: (
      <div className="fs-jump-opt">
        <span className="fs-jump-opt-title">{it.title}</span>
        {extra ? <span className="fs-jump-opt-sub">{extra}</span> : null}
      </div>
    ),
  };
}

function groupJumpOptions(items, emptySteps, searchHref, q, labId) {
  const ask =
    q && labId
      ? { value: `/labs/${labId}/assistant?q=${encodeURIComponent(q)}`, label: `问助手：${q}` }
      : null;
  if (!items.length) {
    const opts = [];
    (emptySteps || []).forEach((s) => {
      if (s?.href) opts.push({ value: s.href, label: s.label });
    });
    if (!opts.length && searchHref) {
      opts.push({ value: searchHref, label: `去文献检索 · ${q}` });
    }
    if (ask) opts.push(ask);
    return opts;
  }
  const buckets = {};
  items.forEach((it) => {
    (buckets[it.type] ||= []).push(it);
  });
  const groups = Object.keys(JUMP_TYPE)
    .filter((t) => buckets[t]?.length)
    .map((t) => ({
      label: JUMP_TYPE[t],
      options: buckets[t].map(jumpRowOption),
    }));
  if (ask) groups.push({ label: "问", options: [ask] });
  return groups;
}

function LabJump({ labId, nav }) {
  const [q, setQ] = useState("");
  const [opts, setOpts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [searchHref, setSearchHref] = useState("");
  const timer = useRef(null);
  const boxRef = useRef(null);
  const skipChange = useRef(false);
  const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || "");

  useEffect(() => {
    setQ("");
    setOpts([]);
    setSearchHref("");
  }, [labId]);

  const showHint = () => setOpts([jumpHintOption()]);

  const runSearch = (value) => {
    if (timer.current) window.clearTimeout(timer.current);
    const needle = (value || "").trim();
    if (!needle || !labId) {
      setSearchHref("");
      setLoading(false);
      if (!needle) showHint();
      else setOpts([]);
      return;
    }
    timer.current = window.setTimeout(() => {
      setLoading(true);
      api
        .jump(labId, needle)
        .then((r) => {
          const items = r.data.items || [];
          const href = r.data.search_href || `/labs/${labId}/literature?tab=search&q=${encodeURIComponent(needle)}`;
          setSearchHref(href);
          setOpts(groupJumpOptions(items, r.data.empty_next?.steps || [], href, needle, labId));
        })
        .catch(() => {
          const href = `/labs/${labId}/literature?tab=search&q=${encodeURIComponent(needle)}`;
          setSearchHref(href);
          setOpts([{ value: href, label: `查找失败，去文献检索 · ${needle}` }]);
        })
        .finally(() => setLoading(false));
    }, 200);
  };

  const goHref = (href) => {
    if (!href || href === "__hint__") return;
    skipChange.current = true;
    setQ("");
    setOpts([]);
    setSearchHref("");
    nav(href);
  };

  return (
    <AutoComplete
      className="fs-lab-jump"
      value={q}
      options={opts}
      filterOption={false}
      defaultActiveFirstOption
      popupClassName="fs-lab-jump-drop"
      notFoundContent={loading ? "正在找组内对象…" : q.trim() ? "组内没有这个" : null}
      onChange={(value) => {
        if (skipChange.current) {
          skipChange.current = false;
          return;
        }
        if (value === "__hint__" || (value && String(value).startsWith("/"))) return;
        setQ(value);
        runSearch(value);
      }}
      onSelect={goHref}
      onFocus={() => {
        if (!q.trim()) showHint();
      }}
    >
      <Input
        ref={boxRef}
        allowClear
        prefix={<FsGlyph name="search" />}
        suffix={<kbd className="fs-jump-kbd">{mac ? "⌘K" : "Ctrl K"}</kbd>}
        aria-label="实验室内跳转"
        autoFocus
        placeholder="搜文献、我的论文、方向…"
        onKeyDown={(e) => {
          if (e.key !== "Enter") return;
          const needle = q.trim();
          if (!needle) return;
          const first = opts.find((o) => o?.value && o.value !== "__hint__" && !o.disabled);
          const grouped = opts.find((o) => Array.isArray(o?.options) && o.options[0]?.value);
          const href = first?.value || grouped?.options?.[0]?.value || searchHref;
          if (href) {
            e.preventDefault();
            e.stopPropagation();
            goHref(href);
          }
        }}
      />
    </AutoComplete>
  );
}

function NoticeBell({ unread, unreadKnown, unreadErr, setUnread, setUnreadErr, nav }) {
  const [notes, setNotes] = useState([]);
  const [noteErr, setNoteErr] = useState("");
  const load = () =>
    api
      .notifications()
      .then((r) => {
        setNotes(asItems(r.data).slice(0, 8));
        setNoteErr("");
      })
      .catch((e) => {
        setNotes([]);
        setNoteErr(formatError(e, "通知加载失败"));
      });
  return (
    <Dropdown
      trigger={["click"]}
      onOpenChange={(open) => {
        if (open) load();
      }}
      dropdownRender={() => (
        <div className="fs-notice-pop">
          {noteErr ? (
            <div className="fs-notice-item">
              <div>{noteErr}</div>
              <Button type="link" size="small" onClick={load}>
                重试
              </Button>
            </div>
          ) : notes.length ? (
            notes.map((n) => {
              const markRead = async () => {
                if (n.is_read) return;
                try {
                  await api.readNotification(n.id);
                  const r = await api.unreadCount();
                  setUnread(r.data.unread || 0);
                  message.success("已标为已读");
                } catch (e) {
                  message.error(formatError(e, "没标上已读，仍可打开"));
                }
              };
              return (
                <div key={n.id} className={`fs-notice-item${n.is_read ? "" : " is-unread"}`}>
                  <Link to={noticeHref(n)} onClick={markRead}>
                    {n.title}
                  </Link>
                  {n.next_step || n.feedback ? (
                    <div>
                      <FeedbackStrip row={n} />
                    </div>
                  ) : null}
                  {(n.followups || []).map((f) => (
                    <ActionChip key={f.href || f.label} to={f.href} onClick={markRead}>
                      {f.label}
                    </ActionChip>
                  ))}
                  <ReproduceHonest ev={n.latest_reproduce} />
                  {null}
                  <ResumeJob row={n} onDone={load} />
                  {n.reproduce_href || n.latest_reproduce?.href ? (
                    <ReproduceChip ev={n.latest_reproduce} to={n.latest_reproduce?.href || n.reproduce_href} onClick={markRead} />
                  ) : null}
                  <div className="fs-muted">{fmtWhen(n.created_at)}</div>
                </div>
              );
            })
          ) : (
            <div className="fs-notice-item fs-muted">没有通知</div>
          )}
          <div className="fs-notice-foot">
            <Button type="link" size="small" onClick={() => nav("/notifications")}>
              全部通知
            </Button>
          </div>
        </div>
      )}
    >
      <Badge
        count={unreadErr ? "!" : unreadKnown ? unread : 0}
        size="small"
        overflowCount={99}
        title={unreadErr ? "未读数未知，可打开通知页重试" : undefined}
      >
        <Button className="fs-bell" aria-label={unreadErr ? "通知（未读数未知）" : "通知"}>
          通知
        </Button>
      </Badge>
    </Dropdown>
  );
}
