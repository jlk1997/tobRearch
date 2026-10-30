import axios from "axios";

const client = axios.create({ baseURL: "/api/v1" });

export function storeTokens(pair) {
  if (pair?.access) localStorage.setItem("access", pair.access);
  if (pair?.refresh) localStorage.setItem("refresh", pair.refresh);
}

client.interceptors.request.use((cfg) => {
  const token = localStorage.getItem("access");
  if (token) cfg.headers.Authorization = `Bearer ${token}`;
  return cfg;
});

let refreshWait = null;
client.interceptors.response.use(
  (res) => res,
  async (err) => {
    const orig = err.config || {};
    const url = String(orig.url || "");
    if (err.response?.status !== 401 || orig._retry || url.includes("/auth/refresh") || url.includes("/auth/login")) {
      throw err;
    }
    const refresh = localStorage.getItem("refresh");
    if (!refresh) throw err;
    orig._retry = true;
    if (!refreshWait) {
      refreshWait = axios
        .post("/api/v1/auth/refresh", { refresh })
        .then((r) => {
          storeTokens(r.data);
          return r.data.access;
        })
        .finally(() => {
          refreshWait = null;
        });
    }
    try {
      const access = await refreshWait;
      orig.headers = orig.headers || {};
      orig.headers.Authorization = `Bearer ${access}`;
      return client(orig);
    } catch (e) {
      localStorage.removeItem("access");
      localStorage.removeItem("refresh");
      if (typeof window !== "undefined" && !String(window.location.pathname).startsWith("/login")) {
        window.location.assign("/login");
      }
      throw err;
    }
  }
);

function authHeaders() {
  const token = localStorage.getItem("access");
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export async function readSseJson(resp, { onDelta } = {}) {
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      detail = data.detail || detail;
    } catch {
      /* keep status text */
    }
    const err = new Error(detail);
    err.response = { status: resp.status, data: { detail } };
    throw err;
  }
  const reader = resp.body?.getReader();
  if (!reader) return {};
  const decoder = new TextDecoder();
  let buf = "";
  let last = {};
  let acc = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() || "";
    for (const block of parts) {
      for (const line of block.split("\n")) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const raw = trimmed.slice(5).trim();
        if (!raw || raw === "[DONE]") continue;
        let data;
        try {
          data = JSON.parse(raw);
        } catch {
          continue;
        }
        if (data.error) {
          const err = new Error(data.error);
          err.response = { status: 502, data: { detail: data.error, error_class: data.error_class, next_step: data.next_step } };
          err.partial = data.partial || acc;
          throw err;
        }
        if (data.replace && (data.text || data.answer)) {
          acc = data.text || data.answer;
          onDelta?.("", acc, data);
        } else if (data.delta) {
          acc += data.delta;
          onDelta?.(data.delta, acc, data);
        } else if (data.chunk || data.chunks || (data.tool && !data.done)) {
          // Agent 运行时的工具事件没有 delta，但「正在查证」需要它。
          onDelta?.("", acc, data);
        }
        last = { ...last, ...data, text: data.text || data.answer || acc };
      }
    }
  }
  return last;
}

async function postSse(url, body, cfg) {
  const resp = await fetch(url, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
    signal: cfg?.signal,
  });
  return { data: await readSseJson(resp, { onDelta: cfg?.onDelta }) };
}

export const api = {
  login: (email, password) => client.post("/auth/login", { email, password }),
  guestLogin: () => client.post("/auth/guest"),
  register: (payload) => client.post("/auth/register", payload),
  applyOrg: (payload) => client.post("/auth/apply-org", payload),
  resubmitOrg: (payload) => client.post("/auth/orgs/resubmit", payload),
  publicMeta: () => client.get("/auth/public"),
  acceptInvite: (token) => client.post(`/auth/invites/${token}/accept`),
  refresh: (refresh) => client.post("/auth/refresh", { refresh }),
  invitePreview: (token) => client.get(`/auth/invites/${token}`),
  me: () => client.get("/auth/me"),
  patchMe: (data) => client.patch("/auth/me", data),
  changePassword: (data) => client.post("/auth/me/password", data),
  heartbeat: (labId) => client.post("/auth/heartbeat", { lab_id: labId || null }),
  home: (labId) => client.get("/me/home", { params: labId ? { lab_id: labId } : {} }),
  labOnboarding: (labId) => client.get(`/labs/${labId}/onboarding`),
  memberProfile: (labId, userId) => client.get(`/labs/${labId}/members/${userId}/profile`),
  patchMemberProfile: (labId, userId, data) => client.patch(`/labs/${labId}/members/${userId}/profile`, data),
  suggestProfile: (labId, userId) => client.post(`/labs/${labId}/members/${userId}/profile/suggest`, {}),
  refreshProfileSources: (labId, userId) => client.post(`/labs/${labId}/members/${userId}/profile/refresh`, {}),
  confirmIdentity: (labId, userId, data) => client.post(`/labs/${labId}/members/${userId}/profile/identity`, data || {}),
  importCheck: (labId, data) => client.post(`/labs/${labId}/literature/import-check`, data),

  labs: () => client.get("/labs"),
  getLab: (labId) => client.get(`/labs/${labId}`),
  createLab: (data) => client.post("/labs", data),
  deleteLab: (labId) => client.delete(`/labs/${labId}`),
  patchLab: (labId, data) => client.patch(`/labs/${labId}`, data),
  members: (labId) => client.get(`/labs/${labId}/members`),
  listInvites: (labId) => client.get(`/labs/${labId}/invites`),
  invites: (labId, email, role) => client.post(`/labs/${labId}/invites`, { email, role }),
  deleteInvite: (id) => client.delete(`/invites/${id}`),
  patchMember: (labId, userId, data) => client.patch(`/labs/${labId}/members/${userId}`, data),
  deleteMember: (labId, userId) => client.delete(`/labs/${labId}/members/${userId}`),
  pickDirection: (labId, direction_id, extra = {}) => {
    const body =
      direction_id && typeof direction_id === "object"
        ? direction_id
        : { direction_id: direction_id ?? null, ...extra };
    return client.put(`/labs/${labId}/me/direction`, body);
  },

  directions: (labId) => client.get(`/labs/${labId}/directions`),
  directionOverview: (labId, directionId) => client.get(`/labs/${labId}/directions/${directionId}/overview`),
  createDirection: (labId, data) => client.post(`/labs/${labId}/directions`, data),
  patchDirection: (id, data) => client.patch(`/directions/${id}`, data),
  draftDirectionRoute: (labId, directionId) => client.post(`/labs/${labId}/directions/${directionId}/route-draft`),
  deleteDirection: (id) => client.delete(`/directions/${id}`),
  jump: (labId, q) => client.get(`/labs/${labId}/jump`, { params: { q } }),
  directionMilestones: (labId, directionId) => client.get(`/labs/${labId}/directions/${directionId}/milestones`),
  addMilestone: (labId, directionId, data) => client.post(`/labs/${labId}/directions/${directionId}/milestones`, data),
  patchMilestone: (labId, milestoneId, data) => client.patch(`/labs/${labId}/milestones/${milestoneId}`, data),

  cockpit: (labId, extra = {}) =>
    client.get(`/labs/${labId}/cockpit`, {
      params: extra.direction_id ? { direction_id: extra.direction_id } : {},
    }),
  cockpitMember: (labId, userId, extra = {}) =>
    client.get(`/labs/${labId}/cockpit/members/${userId}`, {
      params: extra.direction_id ? { direction_id: extra.direction_id } : {},
    }),
  stats: (labId, params) => client.get(`/labs/${labId}/stats`, { params }),
  progress: (labId, userId, page, extra = {}) =>
    client.get(`/labs/${labId}/progress`, {
      params: {
        ...(userId ? { user_id: userId } : {}),
        page: page || 1,
        page_size: 50,
        ...(extra.direction_id ? { direction_id: extra.direction_id } : {}),
      },
    }),
  workLogs: (labId) => client.get(`/labs/${labId}/work-logs`),
  addWorkLog: (labId, content) => client.post(`/labs/${labId}/work-logs`, { content }),

  literature: (labId, params) => client.get(`/labs/${labId}/literature`, { params }),
  pinLiteratureCore: (labId, itemId, data) => client.patch(`/labs/${labId}/literature/${itemId}/core`, data),
  addLiterature: (labId, data) => client.post(`/labs/${labId}/literature`, data),
  labAuthors: (labId, q, limit = 20) => client.get(`/labs/${labId}/authors`, { params: { q: q || undefined, limit } }),
  author: (authorId) => client.get(`/authors/${authorId}`),
  mergeAuthor: (authorId, otherId) => client.post(`/authors/${authorId}/merge`, { other_id: otherId }),
  splitAuthor: (authorId, itemIds) => client.post(`/authors/${authorId}/split`, { item_ids: itemIds }),
  myAuthor: (labId) => client.get(`/labs/${labId}/me/author`),
  claimAuthor: (labId, authorId) => client.post(`/labs/${labId}/me/author`, { author_id: authorId || null }),
  literatureRelations: (itemId) => client.get(`/literature/${itemId}/relations`),
  uploadZip: (labId, file, onProgress, extra = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    return client.post(`/labs/${labId}/literature/upload-zip`, fd, {
      timeout: 300000,
      params: extra.direction_id ? { direction_id: extra.direction_id } : {},
      onUploadProgress: (ev) => {
        if (ev.total) onProgress?.(Math.min(99, Math.round((100 * ev.loaded) / ev.total)));
      },
    });
  },
  uploadPdfs: (labId, fileList, onProgress, extra = {}) => {
    const fd = new FormData();
    [...fileList].forEach((f) => fd.append("files", f));
    return client.post(`/labs/${labId}/literature/upload-pdfs`, fd, {
      timeout: 180000,
      params: extra.direction_id ? { direction_id: extra.direction_id } : {},
      onUploadProgress: (ev) => {
        if (ev.total) onProgress?.(Math.min(99, Math.round((100 * ev.loaded) / ev.total)));
      },
    });
  },
  searchLiterature: (labId, q, language, sources, extra = {}) =>
    client.post(`/labs/${labId}/literature/search`, {
      q,
      sources: sources || ["arxiv", "lab"],
      language,
      ...(extra.direction_id ? { direction_id: extra.direction_id } : {}),
    }),
  recommendLiterature: (labId, directionId) =>
    client.post(`/labs/${labId}/literature/recommend`, null, { params: directionId ? { direction_id: directionId } : {} }),
  importBibtex: (labId, bibtex, extra = {}) =>
    client.post("/literature/import-bibtex", {
      lab_id: labId,
      bibtex,
      direction_ids: extra.direction_ids || [],
    }),
  importDoi: (labId, doi, extra = {}) =>
    client.post("/literature/import-doi", { lab_id: labId, doi, direction_ids: extra.direction_ids || [] }),
  importCsv: (labId, csv_text, extra = {}) =>
    client.post("/literature/import-csv", {
      lab_id: labId,
      csv_text,
      direction_ids: extra.direction_ids || [],
    }),
  getLiterature: (id) => client.get(`/literature/${id}`),
  literatureDetail: (id, params) => client.get(`/literature/${id}/detail`, { params }),
  findCodeLinks: (id) => client.post(`/literature/${id}/code-links`),
  patchLiterature: (id, data) => client.patch(`/literature/${id}`, data),
  deleteLiterature: (id) => client.delete(`/literature/${id}`),
  markRead: (id, status) => client.post(`/literature/${id}/mark-read`, { status }),
  readingProgress: (id, data) => client.post(`/literature/${id}/progress`, data),
  notes: (id) => client.get(`/literature/${id}/notes`),
  addNote: (id, content_md, page_no, anchor) =>
    client.post(`/literature/${id}/notes`, { content_md, page_no: page_no || null, anchor: anchor || null }),
  literatureStructure: (id) => client.get(`/literature/${id}/structure`),
  pageTranslation: (id, page, lang = "zh") =>
    client.get(`/literature/${id}/page-translation`, { params: { page, lang } }),
  savePageTranslation: (id, data) => client.put(`/literature/${id}/page-translation`, data),
  patchNote: (id, data) => client.patch(`/literature/notes/${id}`, typeof data === "string" ? { content_md: data } : data),
  deleteNote: (id) => client.delete(`/literature/notes/${id}`),
  analyze: (id, analysis_type = "method_card") =>
    client.post(`/literature/${id}/analyze`, { analysis_type }, { timeout: 270000 }),
  tutorLiterature: (id, data, cfg) =>
    data?.stream
      ? postSse(`/api/v1/literature/${id}/tutor`, data, cfg)
      : client.post(`/literature/${id}/tutor`, data, cfg || {}),
  compare: (labId, item_ids, cfg) => client.post("/literature/compare", { lab_id: labId, item_ids }, cfg || {}),
  compareLatest: (labId) => client.get(`/labs/${labId}/literature/compare/latest`),
  manuscriptClaims: (id) => client.get(`/manuscripts/${id}/claims`),
  addToKb: (id) => client.post(`/literature/${id}/add-to-kb`),
  // 渐进加载：交给 pdf.js 自己发 Range，首屏不再等整份下载完（A2）。
  literaturePdfSource: (id) => {
    const token = localStorage.getItem("access");
    return {
      url: `/api/v1/literature/${id}/pdf`,
      httpHeaders: token ? { Authorization: `Bearer ${token}` } : {},
      withCredentials: false,
    };
  },
  literaturePdf: (id, onProgress) =>
    client.get(`/literature/${id}/pdf`, {
      responseType: "blob",
      timeout: 180000,
      onDownloadProgress: (ev) => {
        if (ev.total) onProgress?.(Math.min(99, Math.round((100 * ev.loaded) / ev.total)));
      },
    }),
  uploadPdf: (id, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return client.post(`/literature/${id}/upload-pdf`, fd);
  },

  manuscripts: (labId, params) => client.get(`/labs/${labId}/manuscripts`, { params }),
  addManuscript: (labId, data) => client.post(`/labs/${labId}/manuscripts`, data),
  getManuscript: (id) => client.get(`/manuscripts/${id}`),
  patchManuscript: (id, data) => client.patch(`/manuscripts/${id}`, data),
  deleteManuscript: (id) => client.delete(`/manuscripts/${id}`),
  comments: (id) => client.get(`/manuscripts/${id}/comments`),
  addComment: (id, body, anchor_json) => client.post(`/manuscripts/${id}/comments`, { body, anchor_json: anchor_json || {} }),
  manuscriptVersions: (id) => client.get(`/manuscripts/${id}/versions`),
  restoreVersion: (id, versionId, expected) =>
    client.post(`/manuscripts/${id}/versions/${versionId}/restore`, {}, { params: expected ? { expected_updated_at: expected } : {} }),
  citations: (id) => client.get(`/manuscripts/${id}/citations`),
  addCitation: (id, literature_item_id, citation_key) =>
    client.post(`/manuscripts/${id}/citations`, { literature_item_id, citation_key }),
  deleteCitation: (id, citationId) => client.delete(`/manuscripts/${id}/citations/${citationId}`),
  manuscriptBibtex: (id) => client.get(`/manuscripts/${id}/bibtex`),
  exportZip: (id) => client.post(`/manuscripts/${id}/export-zip`, {}, { responseType: "blob" }),
  overleafSync: (id, data) => client.post(`/manuscripts/${id}/overleaf-sync`, data || {}),
  exportBibtex: (labId, directionId) =>
    client.get(`/labs/${labId}/literature/export-bibtex`, {
      responseType: "blob",
      params: directionId ? { direction_id: directionId } : {},
    }),
  statsCsv: (labId, params) => client.get(`/labs/${labId}/stats.csv`, { responseType: "blob", params }),

  putMyKey: (data) => client.put("/me/llm-credentials", data),
  getMyKey: () => client.get("/me/llm-credentials"),
  testMyKey: () => client.post("/me/llm-credentials/test"),
  getLabKey: (labId) => client.get(`/labs/${labId}/llm-credentials`),
  putLabKey: (labId, data) => client.put(`/labs/${labId}/llm-credentials`, data),
  testLabKey: (labId) => client.post(`/labs/${labId}/llm-credentials/test`),

  kbMeta: (labId) => client.get(`/labs/${labId}/kb`),
  kbMeta: (labId) => client.get(`/labs/${labId}/kb`),
  kbDocs: (labId, params) => client.get(`/labs/${labId}/kb/documents`, { params }),
  kbUpload: (labId, file, directionId) => {
    const fd = new FormData();
    fd.append("file", file);
    if (directionId) fd.append("direction_id", directionId);
    return client.post(`/labs/${labId}/kb/documents`, fd);
  },
  kbQuery: (labId, q, cfg, extra = {}) => client.post(`/labs/${labId}/kb/query`, { q, ...extra }, cfg || {}),
  askLab: (labId, q, cfg, extra = {}) => client.post(`/labs/${labId}/ask`, { q, ...extra }, cfg || {}),
  askLabStream: (labId, q, cfg, extra = {}) => postSse(`/api/v1/labs/${labId}/ask`, { q, stream: true, ...extra }, cfg),
  assistantHome: (labId) => client.get(`/labs/${labId}/assistant/home`),
  renameAskSession: (labId, sessionId, title) =>
    client.patch(`/labs/${labId}/ask/sessions/${encodeURIComponent(sessionId)}`, { title }),
  deleteAskSession: (labId, sessionId) =>
    client.delete(`/labs/${labId}/ask/sessions/${encodeURIComponent(sessionId)}`),
  saveAssistantStanding: (labId, data) => client.put(`/labs/${labId}/assistant/standing`, data),
  directionFrontier: (labId, directionId) => client.get(`/labs/${labId}/directions/${directionId}/frontier`),
  refreshFrontier: (labId, directionId) => client.post(`/labs/${labId}/directions/${directionId}/frontier`),
  digestAll: (labId) => client.post(`/labs/${labId}/literature/digest-all`),
  digestStatus: (labId) => client.get(`/labs/${labId}/literature/digest-status`),
  agentTrace: (traceId) => client.get(`/agent/traces/${encodeURIComponent(traceId)}`),
  kbSummarize: (labId, cfg, extra = {}) =>
    client.post(`/labs/${labId}/kb/summarize`, {}, { ...(cfg || {}), params: extra }),
  kbFeedback: (labId, data) => client.post(`/labs/${labId}/kb/feedback`, data),
  glossary: (labId) => client.get(`/labs/${labId}/glossary`),
  addGlossary: (labId, data) => client.post(`/labs/${labId}/glossary`, data),
  patchGlossary: (id, data) => client.patch(`/glossary/${id}`, data),
  deleteGlossary: (id) => client.delete(`/glossary/${id}`),
  deleteKbDoc: (id) => client.delete(`/kb/documents/${id}`),
  kbReindex: (id) => client.post(`/kb/documents/${id}/reindex`),

  translate: (labId, text, target, extra = {}, cfg) =>
    extra?.stream
      ? postSse(`/api/v1/labs/${labId}/language/translate`, { text, target, ...extra }, cfg)
      : client.post(`/labs/${labId}/language/translate`, { text, target, ...extra }, cfg || {}),
  polish: (labId, text, extra = {}, cfg) =>
    extra?.stream
      ? postSse(`/api/v1/labs/${labId}/language/polish`, { text, ...extra }, cfg)
      : client.post(`/labs/${labId}/language/polish`, { text, ...extra }, cfg || {}),
  expand: (labId, text, extra = {}, cfg) =>
    extra?.stream
      ? postSse(`/api/v1/labs/${labId}/language/expand`, { text, ...extra }, cfg)
      : client.post(`/labs/${labId}/language/expand`, { text, ...extra }, cfg || {}),
  outline: (labId, data, cfg) => client.post(`/labs/${labId}/writing/outline`, data, cfg || {}),
  writingLatest: (labId) => client.get(`/labs/${labId}/writing/latest`),
  writingSectionLatest: (labId) => client.get(`/labs/${labId}/writing/section/latest`),
  languageLatest: (labId) => client.get(`/labs/${labId}/language/latest`),
  writeSection: (labId, data, cfg) => client.post(`/labs/${labId}/writing/section`, data, cfg || {}),
  writeback: (labId, data) => client.post(`/labs/${labId}/writing/writeback`, data),

  researchRuns: (labId, params) => client.get(`/labs/${labId}/research-runs`, { params }),
  researchPreflight: (labId, params) => client.get(`/labs/${labId}/research-preflight`, { params }),
  createRun: (labId, data) => client.post(`/labs/${labId}/research-runs`, data),
  getRun: (id) => client.get(`/research-runs/${id}`),
  cancelRun: (id) => client.post(`/research-runs/${id}/cancel`),
  pauseRun: (id) => client.post(`/research-runs/${id}/pause`),
  resumeRun: (id, data) => client.post(`/research-runs/${id}/resume`, data || {}),
  runArtifactFile: (id, path) =>
    client.get(`/research-runs/${id}/artifacts/file`, { params: { path }, responseType: "blob" }),

  reproduceRuns: (labId, params) => client.get(`/labs/${labId}/reproduce-runs`, { params }),
  reproducePreflight: (labId, params) => client.get(`/labs/${labId}/reproduce-preflight`, { params }),
  createReproduce: (labId, data) => client.post(`/labs/${labId}/reproduce-runs`, data),
  getReproduce: (id) => client.get(`/reproduce-runs/${id}`),
  improveReproduce: (id) => client.post(`/reproduce-runs/${id}/improve`),
  resumeReproduce: (id) => client.post(`/reproduce-runs/${id}/resume`),
  cancelReproduce: (id) => client.post(`/reproduce-runs/${id}/cancel`),
  reproArtifactFile: (id, path) =>
    client.get(`/reproduce-runs/${id}/artifacts/file`, { params: { path }, responseType: "blob" }),

  tasks: (labId, params) => client.get(`/labs/${labId}/tasks`, { params }),
  createTask: (labId, data) => client.post(`/labs/${labId}/tasks`, data),
  patchTask: (id, data) => client.patch(`/tasks/${id}`, data),
  meetings: (labId) => client.get(`/labs/${labId}/meetings`),
  createMeeting: (labId, data) => client.post(`/labs/${labId}/meetings`, data),
  minutes: (id, notes, assign_to) => client.post(`/meetings/${id}/minutes`, { notes_raw: notes, assign_to: assign_to || [] }),
  files: (labId) => client.get(`/labs/${labId}/files`),
  uploadFile: (labId, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return client.post(`/labs/${labId}/files`, fd);
  },
  downloadFile: (id) => client.get(`/files/${id}`, { responseType: "blob" }),
  getMeeting: (id) => client.get(`/meetings/${id}`),
  notifications: () => client.get("/notifications"),
  unreadCount: () => client.get("/notifications/unread-count"),
  readNotification: (id) => client.post(`/notifications/${id}/read`),
  readAllNotifications: () => client.post("/notifications/read-all"),

  datasets: (labId, params) => client.get(`/labs/${labId}/datasets`, { params }),
  createDataset: (labId, data) => client.post(`/labs/${labId}/datasets`, data),
  getDataset: (id) => client.get(`/datasets/${id}`),
  patchDataset: (id, data) => client.patch(`/datasets/${id}`, data),
  deleteDataset: (id) => client.delete(`/datasets/${id}`),
  uploadDataset: (id, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return client.post(`/datasets/${id}/upload`, fd);
  },
  digest: (labId, days, extra) =>
    client.get(`/labs/${labId}/digest`, { params: { ...(days ? { days } : {}), ...(extra || {}) } }),
  emailDigest: (labId) => client.post(`/labs/${labId}/digest/email`),
  citationGraph: (labId, directionId) =>
    client.get(`/labs/${labId}/citation-graph`, { params: directionId ? { direction_id: directionId } : {} }),
  reviewSimulate: (id) => client.post(`/manuscripts/${id}/review-simulate`),
  reviewLatest: (id) => client.get(`/manuscripts/${id}/review/latest`),
  addManuscriptToLibrary: (id, addToKb = true) =>
    client.post(`/manuscripts/${id}/add-to-library`, null, { params: { add_to_kb: addToKb } }),

  adminUsers: (params) => client.get("/admin/users", { params }),
  patchAdminUser: (id, data) => client.patch(`/admin/users/${id}`, data),
  adminOrgs: (params) => client.get("/admin/orgs", { params }),
  reviewOrg: (orgId, data) => client.post(`/admin/orgs/${orgId}/review`, data),
  adminAudit: (params) => client.get("/admin/audit", { params }),
  adminUsage: () => client.get("/admin/usage"),
  gpuProfiles: () => client.get("/admin/gpu-profiles"),
  putGpu: (data) => client.put("/admin/gpu-profiles", data),
  platformKey: () => client.get("/admin/llm-credentials"),
  putPlatformKey: (data) => client.put("/admin/llm-credentials", data),
  testPlatformKey: () => client.post("/admin/llm-credentials/test"),
};

export { client };

export function asItems(data) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.items)) return data.items;
  return [];
}

export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function consumeSse(path, onEvent, { signal } = {}) {
  const token = localStorage.getItem("access");
  const res = await fetch(`/api/v1${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  });
  if (!res.ok) {
    const err = new Error(res.status === 404 ? "任务不存在" : "实时流连接失败");
    err.status = res.status;
    throw err;
  }
  if (!res.body) {
    throw new Error("实时流为空");
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const chunks = buf.split("\n\n");
    buf = chunks.pop() || "";
    for (const block of chunks) {
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      try {
        onEvent(event, JSON.parse(data));
      } catch {
        onEvent(event, data);
      }
    }
  }
}
