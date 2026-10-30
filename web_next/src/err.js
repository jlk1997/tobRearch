export function errDetail(e, fallback = "请求失败") {
  const d = e?.response?.data?.detail;
  if (typeof d === "string" && d) return d;
  if (d && typeof d === "object") {
    const msg = d.message || fallback;
    return d.next_step && d.next_step !== msg ? `${msg} ${d.next_step}` : msg;
  }
  if (Array.isArray(d)) return d.map((x) => x.msg || x.message || JSON.stringify(x)).join("；");
  return e?.response?.data?.message || e?.message || fallback;
}

export function errClass(e) {
  return e?.response?.data?.error_class || "";
}

export const ERROR_CLASS_LABEL = {
  llm: "模型服务",
  docker: "容器环境",
  gpu: "机器",
  parse: "解析失败",
  permission: "权限或密钥",
  quota: "额度",
  need_data: "缺少数据",
  awaiting_gpu: "在等机器",
  stub: "试跑，不是正式结果",
  no_docker: "没有容器",
  no_gpu: "没有机器",
};

const HUMANIZE_RULES = [
  ["未注入组内文献或数据集。图仍按单机问题运行。", "还没带上组里的文献或数据。这次只按你写的问题查。"],
  ["未注入", "还没带上"],
  ["还没复现就写成成功", "写了结果，组里还没跑过"],
  ["核心文献还没写进正文", "这几篇还没写进正文"],
  ["禁止把论文声称填成成功结果", "论文里的数字还不是你们跑出来的"],
  ["禁止把论文声称写成成功", "论文里的数字还不是你们跑出来的"],
  ["禁止把声称写成成功", "论文里的数字还不是你们跑出来的"],
  ["声称不是实测", "论文里的数字还不是你们跑出来的"],
  ["不会把声称当实测", "不会把论文数字当成你们跑出来的"],
  ["工人未开。已入队不是失败。", "还没开始跑。点恢复接着做。"],
  ["已入队不是失败", "还没开始跑"],
  ["工人未开", "还没开始跑"],
  ["等待实验算力", "在等机器"],
  ["综述没写到这些篇", "综述没提到这些论文"],
  ["综述没写到", "综述没提到"],
  ["尚无实测", "还没跑出结果"],
  ["已有实测", "已经跑出结果"],
  ["测试桩", "试跑，不是正式结果"],
  ["简易检索", "普通搜索"],
  ["已入队", "还没开始跑"],
  ["，不是排名", ""],
  ["。不是排名", "。"],
  ["不是排名。", ""],
  ["不是排名", ""],
];

export function humanize(text) {
  let s = String(text ?? "");
  if (!s) return s;
  for (const [from, to] of HUMANIZE_RULES) {
    if (s.includes(from)) s = s.split(from).join(to);
  }
  return s.replace(/[ \t]{2,}/g, " ").replace(/ · · /g, " · ").trim();
}

export function stripModelReasoning(text) {
  let out = String(text || "");
  out = out.replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, "");
  out = out.replace(/```(?:reasoning|think|thoughts)\s*\n[\s\S]*?```/gi, "");
  out = out.replace(/<think\b[^>]*>([\s\S]*)/gi, (_, rest) => {
    const blank = rest.split(/\n\s*\n/);
    if (blank.length > 1) return blank.slice(1).join("\n\n");
    const nl = rest.indexOf("\n");
    return nl >= 0 ? rest.slice(nl + 1) : "";
  });
  out = out.replace(/<\/think>/gi, "");
  return out.replace(/\n{3,}/g, "\n\n").trim();
}

function looksLikeGateway(e) {
  const status = e?.response?.status;
  if (status !== 502 && status !== 504) return false;
  if (e?.response?.data?.error_class) return false;
  const data = e?.response?.data;
  const raw = typeof data === "string" ? data : "";
  const d = errDetail(e, "");
  if (/<!DOCTYPE|<html|bad gateway|gateway time-out|nginx/i.test(`${raw} ${d}`)) return true;
  const ctype = e?.response?.headers?.["content-type"] || e?.response?.headers?.["Content-Type"] || "";
  if (String(ctype).includes("text/html")) return true;
  return !d || /status code 502|status code 504/i.test(d);
}

export function formatError(e, fallback = "请求失败") {
  if (e?.code === "ECONNABORTED" || /timeout/i.test(e?.message || "")) {
    return fallback && fallback !== "请求失败" ? fallback : "请求超时，请再试一次。";
  }
  if (looksLikeGateway(e)) {
    return fallback && fallback !== "请求失败" ? fallback : "服务忙或刚在更新，请再点一次。";
  }
  const c = errClass(e);
  const label = ERROR_CLASS_LABEL[c] || c;
  const d = errDetail(e, fallback);
  const next = e?.response?.data?.next_step;
  const text = next && next !== d && !(d || "").includes(next) ? `${d} ${next}` : d;
  return humanize(c ? `[${label}] ${text}` : text);
}

export function reportPing(data, fallback = "测通失败") {
  if (data?.ok) return { ok: true, text: `测通（${data.mode || "live"}）` };
  const klass = data?.error_class || "permission";
  const label = ERROR_CLASS_LABEL[klass] || klass;
  return { ok: false, text: `[${label}] ${data?.detail || fallback}` };
}

export const MS_STATUS = {
  draft: "草稿",
  internal_review: "组内评审",
  submitted: "已投稿",
  revision: "返修",
  published: "已发表",
  rejected: "拒稿",
};

export const DEGREE_KIND = {
  proposal: "开题",
  midterm: "中期",
  defense: "答辩",
};

export const DEGREE_STATUS = {
  planned: "计划",
  in_progress: "进行中",
  done: "完成",
};

export const JUMP_TYPE = {
  literature: "文献",
  manuscript: "论文",
  task: "任务",
  direction: "方向",
  meeting: "组会",
  dataset: "数据集",
  file: "资料",
  work_log: "日志",
  research_run: "文献调研",
  reproduce_run: "复现",
};

export const MS_KIND = {
  conference: "会议",
  journal: "期刊",
  survey: "综述",
  proposal: "开题/提案",
  thesis: "学位论文",
  other: "其他",
};

export function labReaderLine(names, marks) {
  const rows = Array.isArray(marks) && marks.length ? marks : (names || []).map((name) => ({ name }));
  return rows
    .map((row) => {
      const name = row?.name || row;
      const page = Number(row?.last_page_no || 0);
      if (!name) return "";
      return page >= 2 ? `${name} 第${page}页` : String(name);
    })
    .filter(Boolean)
    .join("、");
}

export function literatureHref(id, page, extra) {
  if (!id) return "/literature";
  const q = new URLSearchParams();
  const p = Number(page);
  if (Number.isFinite(p) && p >= 1) q.set("page", String(Math.trunc(p)));
  if (extra && extra.ms) q.set("ms", String(extra.ms));
  if (extra && extra.section) q.set("section", String(extra.section));
  if (extra && extra.note) q.set("note", String(extra.note));
  const panel = extra && extra.panel;
  if (panel === "card" || panel === "notes" || panel === "tutor") q.set("panel", panel);
  else if (extra && extra.note) q.set("panel", "notes");
  if (extra && extra.session) q.set("session", String(extra.session));
  if (extra && extra.direction) q.set("direction_id", String(extra.direction));
  const qs = q.toString();
  return qs ? `/literature/${id}?${qs}` : `/literature/${id}`;
}

export function askDirectionHref(labId, directionId, extra = {}) {
  if (!labId) return "/";
  const q = new URLSearchParams();
  q.set("tab", "ask");
  if (directionId) q.set("direction_id", String(directionId));
  if (extra.lit) q.set("lit", String(extra.lit));
  const question = String(extra.q || "").trim();
  if (question) q.set("q", question.slice(0, 180));
  return `/labs/${labId}/kb?${q.toString()}`;
}

export function languageHref(labId, extra = {}) {
  if (!labId) return "/";
  const q = new URLSearchParams();
  if (extra.lit) q.set("lit", String(extra.lit));
  if (extra.note) q.set("note", String(extra.note));
  if (extra.ms) q.set("ms", String(extra.ms));
  if (extra.heading) q.set("heading", String(extra.heading));
  const p = Number(extra.page);
  if (Number.isFinite(p) && p >= 1) q.set("page", String(Math.trunc(p)));
  const qs = q.toString();
  return qs ? `/labs/${labId}/language?${qs}` : `/labs/${labId}/language`;
}

export function manuscriptHref(id, extra) {
  if (!id) return "/manuscripts";
  const q = new URLSearchParams();
  if (extra && extra.comment) q.set("comment", String(extra.comment));
  const tab = extra && extra.tab;
  if (tab === "claims" || tab === "cites" || tab === "comments" || tab === "review" || tab === "versions") {
    q.set("tab", tab);
  } else if (extra && extra.comment) {
    q.set("tab", "comments");
  }
  const qs = q.toString();
  return qs ? `/manuscripts/${id}?${qs}` : `/manuscripts/${id}`;
}

export function readingPctLabel(row) {
  if (row == null || row.progress_pct == null || row.progress_pct === "") return "";
  const pct = Math.round(Number(row.progress_pct));
  if (!Number.isFinite(pct)) return "";
  if (row.my_reading === "finished") return `已读 ${pct}%`;
  if (row.my_reading === "want_read") return pct ? `想读 ${pct}%` : "想读";
  if (row.my_reading === "dropped") return "已放弃";
  return `在读 ${pct}%`;
}

export function noticeHref(n) {
  if (n && typeof n.href === "string" && n.href.startsWith("/") && !n.href.startsWith("//")) return n.href;
  const lab = n.lab_id;
  if (n.object_type === "literature" && n.object_id) {
    return literatureHref(n.object_id, n.page_no || n.last_page_no, n.note_id ? { note: n.note_id } : undefined);
  }
  if (n.object_type === "manuscript" && n.object_id) {
    return manuscriptHref(n.object_id, n.comment_id ? { comment: n.comment_id } : undefined);
  }
  if (n.object_type === "research_run" && n.object_id) return `/runs/${n.object_id}`;
  if (n.object_type === "reproduce_run" && n.object_id) return `/reproduces/${n.object_id}`;
  if (n.object_type === "task" && lab) {
    return n.object_id ? `/labs/${lab}/tasks?open=${n.object_id}` : `/labs/${lab}/tasks`;
  }
  if (n.object_type === "meeting" && lab) {
    return n.object_id ? `/labs/${lab}/meetings?open=${n.object_id}` : `/labs/${lab}/meetings`;
  }
  if (n.object_type === "kb" && lab) return `/labs/${lab}/kb`;
  if (n.object_type === "direction" && lab) {
    return n.object_id ? `/labs/${lab}/directions/${n.object_id}` : `/labs/${lab}/directions`;
  }
  if (n.object_type === "dataset" && n.object_id) return `/datasets/${n.object_id}`;
  if (n.object_type === "note" && n.object_id) {
    return literatureHref(n.object_id, n.page_no || n.last_page_no, n.note_id ? { note: n.note_id } : undefined);
  }
  if (n.object_type === "file" && lab) {
    return n.object_id ? `/labs/${lab}/files?open=${n.object_id}` : `/labs/${lab}/files`;
  }
  if (n.object_type === "language" && lab) return `/labs/${lab}/language`;
  if (n.object_type === "writing" && lab) return `/labs/${lab}/writing`;
  if ((n.object_type === "work_log" || n.verb === "work_log.created") && lab) {
    return n.object_id ? `/labs/${lab}/work-logs?open=${n.object_id}` : `/labs/${lab}/work-logs`;
  }
  if ((n.verb || "").startsWith("run.") && n.object_id) return `/runs/${n.object_id}`;
  if ((n.verb || "").startsWith("reproduce.") && n.object_id) return `/reproduces/${n.object_id}`;
  if ((n.verb || "").startsWith("manuscript.") && n.object_id) {
    return manuscriptHref(n.object_id, n.comment_id ? { comment: n.comment_id } : undefined);
  }
  if ((n.verb || "").startsWith("literature.") || (n.verb || "").startsWith("reading.") || n.verb === "note.created" || n.verb === "note.updated" || n.verb === "analysis.ready" || n.verb === "analysis.tutor") {
    if (n.object_id) return literatureHref(n.object_id, n.page_no || n.last_page_no, n.note_id ? { note: n.note_id } : undefined);
  }
  if (n.object_type === "org") {
    if (n && typeof n.href === "string" && n.href.startsWith("/")) return n.href;
    return "/admin";
  }
  if (lab) return `/labs/${lab}/cockpit`;
  return "/notifications";
}

export const VERB_LABEL = {
  "literature.added": "入库文献",
  "literature.imported_ext": "捕手导入",
  "reading.started": "开始阅读",
  "reading.finished": "读完",
  "note.created": "写了笔记",
  "note.updated": "更新了笔记",
  "file.uploaded": "上传了资料",
  "analysis.ready": "这篇的方法",
  "analysis.tutor": "问这一页",
  "manuscript.created": "创建论文",
  "manuscript.updated": "更新论文",
  "manuscript.status_changed": "论文状态",
  "manuscript.commented": "论文批注",
  "manuscript.library": "已发表入库",
  "kb.indexed": "入知识库",
  "kb.query": "知识库问答",
  "ask.lab": "实验室问",
  "kb.summarize": "知识库综述",
  "language.done": "改文字",
  "dataset.created": "登记数据集",
  "writing.outline": "综述大纲",
  "writing.section": "撰写章节",
  "run.started": "文献调研开始",
  "run.subgraph_complete": "阶段完成",
  "run.failed": "文献调研失败",
  "run.succeeded": "文献调研完成",
  "run.awaiting_gpu": "文献调研在等机器",
  "run.released": "文献调研还没开始跑",
  "reproduce.stage": "复现阶段",
  "reproduce.failed": "复现失败",
  "reproduce.succeeded": "复现完成",
  "reproduce.awaiting_gpu": "复现在等机器",
  "reproduce.released": "复现还没开始跑",
  "task.assigned": "指派任务",
  "task.done": "完成任务",
  "meeting.minutes_ready": "组会纪要",
  "work_log.created": "工作日志",
  "direction.created": "新建方向",
  "direction.updated": "更新方向",
  "milestone.created": "学位节点",
};

export const SUBGRAPH_LABEL = {
  literature_reviewer: "文献综述",
  data_analyzer: "数据分析",
  supervisor: "规划",
  coder: "实验代码",
  latex_writer: "LaTeX",
  artifact_publisher: "打包工件",
};

export const SUBGRAPH_WAIT = {
  literature_reviewer: "正在检索与综述文献。",
  data_analyzer: "正在分析数据。",
  supervisor: "正在规划下一步。",
  coder: "正在写实验代码。",
  latex_writer: "正在撰写 LaTeX。",
  artifact_publisher: "正在打包工件。",
};

export const REPRO_STAGE_LABEL = {
  ingest: "读取论文",
  methodCard: "这篇的方法",
  envCheck: "环境检查",
  implement: "实现实测",
  runExp: "跑实验",
  compare: "对照指标",
  diagnose: "诊断差距",
  fix: "纠错",
  improve: "改进",
  succeed: "完成",
  blocked: "缺数据",
  awaiting_gpu: "在等机器",
};

export const REPRO_STAGE_WAIT = {
  ingest: "正在读取论文。",
  methodCard: "正在提炼这篇的方法。",
  envCheck: "正在检查数据与 GPU。",
  implement: "正在实现并跑实验。",
  runExp: "正在跑实验。",
  compare: "正在对照声称指标。",
  diagnose: "正在诊断差距。",
  fix: "正在按诊断纠错再实现。",
  improve: "正在按上一轮的问题改。论文里的数字还不是你们跑出来的。",
  succeed: "已经跑完。",
  blocked: "缺数据或环境。",
  awaiting_gpu: "在等机器。还没有跑出结果。",
};

export function nodeLabel(name) {
  if (!name) return "";
  return SUBGRAPH_LABEL[name] || REPRO_STAGE_LABEL[name] || name;
}

export function runWaitHint(run) {
  if (!run) return "";
  const st = run.status;
  const fb = run.feedback;
  if (fb?.title || fb?.next_step) {
    if (["awaiting_gpu", "blocked", "pending", "queued", "paused", "failed", "succeeded"].includes(st)) {
      return [fb.title, fb.next_step].filter(Boolean).join("。");
    }
  }
  if (st === "paused") return "已暂停，可点恢复继续。";
  if (st === "awaiting_gpu") return REPRO_STAGE_WAIT.awaiting_gpu;
  if (st === "blocked") return REPRO_STAGE_WAIT.blocked;
  if (st === "failed") return "已失败。原因见下方分类标签。";
  if (st === "succeeded" || st === "cancelled") return "";
  if (run.current_stage && REPRO_STAGE_WAIT[run.current_stage]) return REPRO_STAGE_WAIT[run.current_stage];
  const cur = (run.subgraphs || []).find((s) => s.current);
  if (cur) return SUBGRAPH_WAIT[cur.name] || `正在执行${SUBGRAPH_LABEL[cur.name] || cur.name}。`;
  if (st === "running" || st === "pending" || st === "queued") return run.next_step || "任务已提交，完成后进度会出现在下方。";
  return "";
}

export const READ_STATUS = {
  want_read: "想读",
  reading: "在读",
  finished: "已读",
  dropped: "放弃",
};

export const TASK_STATUS = {
  todo: "待办",
  doing: "进行中",
  done: "完成",
};

export const AUDIT_LABEL = {
  "password.change": "修改密码",
  "credential.update": "更新密钥",
  "invite.create": "发出邀请",
  "gpu.update": "更新 GPU",
  "manuscript.read": "阅读论文",
  "manuscript.export": "导出论文",
  "literature.pdf": "阅读 PDF",
  "literature.delete": "删除文献",
  "literature.export": "导出 BibTeX",
  "run.start": "发起文献调研",
  "reproduce.start": "开始复现",
  "stats.export": "导出统计",
  "user.disable": "停用用户",
  "user.enable": "启用用户",
  "org.apply": "申请开通机构",
  "org.resubmit": "重新提交机构申请",
  "org.approve": "通过机构申请",
  "org.heal": "修复机构实验室",
  "org.reject": "驳回机构申请",
  "org.suspend": "停用机构",
  "org.resume": "恢复机构",
  "org.*": "机构相关",
  "invite.accept": "接受邀请",
};

export const RUN_STATUS = {
  pending: "排队",
  queued: "排队",
  running: "运行中",
  paused: "已暂停",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
  blocked: "缺数据",
  awaiting_gpu: "在等机器",
};
