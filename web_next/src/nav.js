/** 壳层信息架构：分组、选中态、面包屑、页题。对照 docs/UI.md §2。 */

export function roleOf(me, labId) {
  const found = (me?.labs || []).find((l) => l.lab_id === labId)?.role;
  if (found) return found;
  if (me?.is_admin) return "teacher";
  return "";
}

export function routeKey(pathname) {
  const p = pathname || "";
  if (p === "/" || p.startsWith("/home")) return "home";
  if (p.includes("/assistant")) return "assistant";
  if (p.includes("/cockpit")) return "cockpit";
  if (p.includes("/members")) return "members";
  if (p.includes("/directions")) return "dirs";
  if (p.includes("/writing")) return "writing";
  if (p.includes("/literature") || p.startsWith("/literature/")) return "lit";
  if (p.includes("/manuscripts") || p.startsWith("/manuscripts/")) return "ms";
  if (p.includes("/language")) return "lang";
  if (p.includes("/kb")) return "kb";
  if (p.includes("/datasets") || p.startsWith("/datasets/")) return "data";
  if (p.includes("/graph")) return "graph";
  if (p.includes("/digest")) return "digest";
  if (p.includes("/reproduces") || p.startsWith("/reproduces/")) return "repro";
  if (p.includes("/runs") || p.startsWith("/runs/")) return "runs";
  if (p.includes("/tasks")) return "tasks";
  if (p.includes("/meetings")) return "meet";
  if (p.includes("/files")) return "files";
  if (p.includes("/stats")) return "stats";
  if (p.includes("/work-logs")) return "logs";
  if (p.startsWith("/notifications")) return "ntf";
  if (p.includes("/settings/llm")) return "llab";
  if (p.startsWith("/settings/keys")) return "keys";
  if (p.startsWith("/admin")) return "admin";
  if (p.startsWith("/pending")) return "pending";
  if (p.startsWith("/apply")) return "apply";
  if (p.startsWith("/me")) return "me";
  return "home";
}

export function pageMeta(pathname, labId, search) {
  const key = routeKey(pathname);
  const lab = labId ? `/labs/${labId}` : "";
  const q = new URLSearchParams(search || "");
  const msFilter =
    q.get("core_missing") === "1"
      ? { title: "正在看：还没写进正文的论文", crumb: "还没写进正文" }
      : q.get("related_work") === "0"
        ? { title: "正在看：还没写相关工作", crumb: "还没写相关工作" }
        : q.get("ungrounded") === "1"
          ? { title: "正在看：写了结果、还没跑过的论文", crumb: "写了结果，还没跑过" }
          : q.get("owner_id")
            ? { title: "正在看：这人的论文", crumb: "这人" }
            : null;
  const litFilter =
    q.get("reader_id") && q.get("reading_status") === "unread"
      ? { title: "正在看：这人还没开始读", crumb: "还没开始读" }
      : q.get("reading_status") === "unread"
        ? { title: "正在看：没人在读的论文", crumb: "没人在读" }
        : q.get("has_card") === "0"
          ? { title: "正在看：还没写怎么做的论文", crumb: "还没写怎么做" }
          : null;
  const runFilter =
    q.get("unused") === "1"
      ? { title: "正在看：综述没提到的论文", crumb: "综述没提到" }
      : q.get("status") === "awaiting_gpu"
        ? { title: "正在看：在等机器的文献调研", crumb: "在等机器" }
        : q.get("status") === "pending" || q.get("status") === "queued"
          ? { title: "正在看：还没开始跑的文献调研", crumb: "还没开始跑" }
          : q.get("status") === "failed"
            ? { title: "正在看：没做成的文献调研", crumb: "没做成" }
            : q.get("status") === "paused"
              ? { title: "正在看：暂停的文献调研", crumb: "已暂停" }
              : q.get("status") === "blocked"
                ? { title: "正在看：缺数据的文献调研", crumb: "缺数据" }
                : q.get("dataset_id")
                  ? { title: "正在看：这份数据的文献调研", crumb: "这份数据" }
                  : null;
  const honest = q.get("honest") === "unmeasured" ? "not_run" : q.get("honest");
  const reproFilter = pathname.startsWith("/reproduces/")
    ? null
    : q.get("literature_item_id")
      ? { title: "该文献的复现", crumb: "该文献" }
      : honest === "measured"
        ? { title: "正在看：已经跑出结果的复现", crumb: "已经跑出结果" }
        : honest === "not_run"
          ? { title: "正在看：还没跑出结果的复现", crumb: "还没跑出结果" }
          : honest === "awaiting" || q.get("status") === "awaiting_gpu"
            ? { title: "正在看：在等机器的复现", crumb: "在等机器" }
            : honest === "blocked" || q.get("status") === "blocked"
              ? { title: "正在看：缺数据的复现", crumb: "缺数据" }
              : honest === "stub"
                ? { title: "正在看：试跑，不是正式结果", crumb: "试跑" }
                : q.get("status") === "failed"
                  ? { title: "正在看：没做成的复现", crumb: "没做成" }
                  : q.get("status") === "pending" || q.get("status") === "queued"
                    ? { title: "正在看：还没开始跑的复现", crumb: "还没开始跑" }
                    : q.get("dataset_id")
                      ? { title: "该数据集的复现", crumb: "该数据集" }
                      : q.get("direction_id")
                        ? { title: "本方向的复现", crumb: "本方向" }
                        : null;
  const titles = {
    home: { title: "我的", crumb: [{ label: "我的" }] },
    assistant: { title: "助手", crumb: [{ label: "助手" }] },
    cockpit: {
      title: pathname.includes("/cockpit/members/") ? "成员进展" : "组里",
      crumb: [
        { label: "组里", to: lab ? `${lab}/cockpit` : undefined },
        ...(pathname.includes("/cockpit/members/") ? [{ label: "成员" }] : []),
      ],
    },
    members: { title: "成员与邀请", crumb: [{ label: "协作" }, { label: "成员" }] },
    dirs: {
      title: pathname.includes("/directions/") && pathname.split("/directions/")[1] ? "方向详情" : "方向",
      crumb: [{ label: "协作" }, { label: "方向", to: lab ? `${lab}/directions` : undefined }],
    },
    lit: {
      title: pathname.startsWith("/literature/") ? "阅读文献" : litFilter?.title || "文献",
      crumb: [
        { label: "科研资产" },
        { label: "文献", to: lab ? `${lab}/literature` : undefined },
        ...(pathname.startsWith("/literature/") ? [{ label: "阅读" }] : litFilter ? [{ label: litFilter.crumb }] : []),
      ],
    },
    ms: {
      title: pathname.startsWith("/manuscripts/") ? "我的论文" : msFilter?.title || "我的论文",
      crumb: [
        { label: "科研资产" },
        { label: "我的论文", to: lab ? `${lab}/manuscripts` : undefined },
        ...(pathname.startsWith("/manuscripts/") ? [{ label: "编辑" }] : msFilter ? [{ label: msFilter.crumb }] : []),
      ],
    },
    kb: { title: "知识库", crumb: [{ label: "科研资产" }, { label: "知识库" }] },
    writing: { title: "帮论文写一节", crumb: [{ label: "科研资产" }, { label: "写作" }] },
    lang: {
      title: "改文字",
      crumb: [
        { label: "科研资产" },
        { label: "改文字", to: lab ? `${lab}/language` : undefined },
        ...(q.get("direction_id") ? [{ label: "本方向" }] : []),
      ],
    },
    data: {
      title: pathname.startsWith("/datasets/") ? "数据集" : q.get("direction_id") ? "本方向的数据集" : "数据集",
      crumb: [
        { label: "协作" },
        { label: "数据集", to: lab ? `${lab}/datasets` : undefined },
        ...(pathname.startsWith("/datasets/") ? [{ label: "对象" }] : q.get("direction_id") ? [{ label: "本方向" }] : []),
      ],
    },
    graph: { title: "引用图谱", crumb: [{ label: "协作" }, { label: "引用图谱" }] },
    digest: {
      title: "周报",
      crumb: [
        { label: "协作" },
        { label: "周报", to: lab ? `${lab}/digest` : undefined },
        ...(q.get("direction_id") ? [{ label: "本方向" }] : []),
      ],
    },
    runs: {
      title: pathname.startsWith("/runs/") ? "文献调研" : runFilter?.title || "文献调研",
      crumb: [
        { label: "实验任务" },
        { label: "文献调研", to: lab ? `${lab}/runs` : undefined },
        ...(pathname.startsWith("/runs/") ? [{ label: "详情" }] : runFilter ? [{ label: runFilter.crumb }] : []),
      ],
    },
    repro: {
      title: pathname.startsWith("/reproduces/") ? "复现" : reproFilter?.title || "复现",
      crumb: [
        { label: "实验任务" },
        { label: "复现", to: lab ? `${lab}/reproduces` : undefined },
        ...(pathname.startsWith("/reproduces/") ? [{ label: "详情" }] : reproFilter ? [{ label: reproFilter.crumb }] : []),
      ],
    },
    tasks: { title: "任务", crumb: [{ label: "协作" }, { label: "任务" }] },
    meet: { title: "组会", crumb: [{ label: "协作" }, { label: "组会" }] },
    files: { title: "共享文件", crumb: [{ label: "协作" }, { label: "共享文件" }] },
    stats: {
      title: "统计",
      crumb: [
        { label: "协作" },
        { label: "统计", to: lab ? `${lab}/stats` : undefined },
        ...(q.get("direction_id") ? [{ label: "本方向" }] : []),
      ],
    },
    logs: { title: "工作日志", crumb: [{ label: "协作" }, { label: "日志" }] },
    ntf: { title: "通知", crumb: [{ label: "账户" }, { label: "通知" }] },
    keys: { title: "个人模型", crumb: [{ label: "账户" }, { label: "个人模型" }] },
    llab: { title: "实验室模型", crumb: [{ label: "账户" }, { label: "实验室模型" }] },
    me: { title: "我的资料", crumb: [{ label: "账户" }, { label: "资料" }] },
    admin: { title: "平台管理", crumb: [{ label: "账户" }, { label: "管理" }] },
    pending: { title: "开通进度", crumb: [{ label: "账户" }, { label: "开通进度" }] },
  };
  return titles[key] || { title: "蜂算科研", crumb: [] };
}

/** 组内主路径。其余收入「更多」。管理员按导师默认，并钉住「管理」。 */
export const NAV_PRIMARY = {
  student: {
    work: ["assistant", "cockpit"],
    assets: ["lit", "ms"],
    agents: [],
    collab: [],
    account: ["ntf"],
  },
  teacher: {
    work: ["assistant", "cockpit"],
    assets: ["lit", "ms"],
    agents: [],
    collab: [],
    account: ["ntf"],
  },
};

export function navSelection(pathname) {
  const key = routeKey(pathname);
  if (["dirs", "members", "digest", "stats", "logs"].includes(key)) return "cockpit";
  return key;
}

export function navPersona(role, isAdmin) {
  return role === "teacher" || isAdmin ? "teacher" : "student";
}

export function splitNavGroup(children, primaryKeys, moreKey, alwaysPrimary = []) {
  const primary = [];
  const more = [];
  for (const item of children.filter(Boolean)) {
    if (alwaysPrimary.includes(item.key) || primaryKeys.includes(item.key)) primary.push(item);
    else more.push(item);
  }
  if (!more.length) return primary;
  return [...primary, { key: moreKey, label: "更多", children: more }];
}

export function moreKeysHolding(selected, groups) {
  const open = [];
  for (const g of groups) {
    const more = (g.children || []).find((c) => c && String(c.key || "").endsWith("-more"));
    if (!more?.children) continue;
    if (more.children.some((c) => c.key === selected)) open.push(more.key);
  }
  return open;
}

export function afterLabChange(_role, labId) {
  if (!labId) return "/";
  return `/labs/${labId}/assistant`;
}

export function afterAuth(me) {
  if (me?.is_admin) return "/admin";
  if (!me?.workbench_ready) return "/pending";
  const lab = me?.labs?.[0];
  if (!lab) return "/";
  return afterLabChange(lab.role, lab.lab_id);
}
