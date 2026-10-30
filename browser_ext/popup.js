async function metaFromTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      const dc = (name) => document.querySelector(`meta[name="${name}"]`)?.content;
      const prop = (name) => document.querySelector(`meta[property="${name}"]`)?.content;
      const jsonld = [...document.querySelectorAll('script[type="application/ld+json"]')]
        .map((s) => {
          try {
            return JSON.parse(s.textContent);
          } catch {
            return null;
          }
        })
        .find(Boolean);
      const host = location.hostname;
      let site_adapter = "generic";
      if (host.includes("arxiv")) site_adapter = "arxiv";
      else if (host.includes("semanticscholar")) site_adapter = "s2";
      else if (host.includes("nih.gov") || host.includes("pubmed")) site_adapter = "pubmed";
      else if (host.includes("ieee")) site_adapter = "ieee";
      else if (host.includes("cnki") || host.includes("wanfang")) site_adapter = "cnki";
      const strip = (t) =>
        String(t || "")
          .replace(/\s*[-_|]\s*(中国知网|知网空间|万方数据).*$/, "")
          .replace(/\s*[|\-]\s*IEEE Xplore.*$/i, "")
          .replace(/\s*\|\s*arXiv.*$/i, "")
          .trim();
      const title = strip(
        dc("citation_title") ||
          prop("og:title") ||
          dc("dc.title") ||
          dc("dc.Title") ||
          jsonld?.name ||
          jsonld?.headline ||
          document.querySelector("h1.document-title, .document-title, h1[class*='document-title']")?.innerText ||
          document.querySelector(".wx-tit h1, #chTitle, .brief h1")?.innerText ||
          document.querySelector("h1")?.innerText ||
          document.title,
      );
      const authors = [...document.querySelectorAll('meta[name="citation_author"]')].map((x) => x.content);
      if (!authors.length) {
        document.querySelectorAll(".author a, #authorlist a, .authors a").forEach((el) => {
          const name = (el.innerText || "").trim();
          if (name) authors.push(name);
        });
      }
      if ((!authors || !authors.length) && jsonld?.author) {
        const a = jsonld.author;
        if (typeof a === "string") authors.push(a);
        else if (Array.isArray(a)) a.forEach((x) => authors.push(x?.name || x));
        else if (a?.name) authors.push(a.name);
      }
      let pdf_url = dc("citation_pdf_url") || "";
      if (!pdf_url && host.includes("arxiv") && location.pathname.startsWith("/abs/")) {
        pdf_url = location.href.replace("/abs/", "/pdf/");
      }
      const doi = dc("citation_doi") || prop("citation_doi") || (String(dc("dc.identifier") || "").match(/10\.\d{4,}\/\S+/) || [])[0];
      return {
        page_url: location.href,
        page_html: document.documentElement.outerHTML.slice(0, 180000),
        title,
        authors,
        doi,
        abstract: dc("citation_abstract") || dc("description") || "",
        venue: dc("citation_journal_title") || dc("citation_conference_title") || dc("citation_conference") || "",
        publish_year: dc("citation_publication_date") ? parseInt(String(dc("citation_publication_date")).slice(0, 4), 10) : undefined,
        site_adapter,
        pdf_url,
      };
    },
  });
  return result;
}

async function arrayBufferToBase64(buf) {
  const bytes = new Uint8Array(buf);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function looksLikeJwt(t) {
  return typeof t === "string" && t.split(".").length === 3 && t.length > 20;
}

async function apiFetch(origin, path, opts = {}) {
  const stored = await chrome.storage.local.get(["token", "refresh"]);
  const headers = { ...(opts.headers || {}) };
  if (stored.token) headers.Authorization = "Bearer " + stored.token;
  let res = await fetch(`${origin}${path}`, { ...opts, headers });
  if (res.status === 401 && stored.refresh) {
    const rr = await fetch(`${origin}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh: stored.refresh }),
    });
    if (rr.ok) {
      const data = await rr.json();
      if (looksLikeJwt(data.access || "")) {
        await chrome.storage.local.set({ token: data.access, refresh: data.refresh || stored.refresh });
        headers.Authorization = "Bearer " + data.access;
        res = await fetch(`${origin}${path}`, { ...opts, headers });
      }
    }
  }
  return res;
}

async function loadLabs() {
  const stored = await chrome.storage.local.get(["token", "origin", "labId"]);
  const origin = (stored.origin || "http://127.0.0.1:8000").replace(/\/$/, "");
  const sel = document.getElementById("lab");
  sel.innerHTML = "";
  if (!stored.token || !looksLikeJwt(stored.token)) {
    const opt = document.createElement("option");
    opt.textContent = "请先在选项页登录或填写 JWT";
    sel.appendChild(opt);
    return stored;
  }
  try {
    const res = await apiFetch(origin, "/api/v1/auth/me");
    const me = await res.json();
    for (const lab of me.labs || []) {
      const opt = document.createElement("option");
      opt.value = lab.lab_id;
      opt.textContent = lab.name || lab.lab_id;
      if (lab.lab_id === stored.labId) opt.selected = true;
      sel.appendChild(opt);
    }
    if (!(me.labs || []).length) {
      const opt = document.createElement("option");
      opt.textContent = "没有实验室";
      sel.appendChild(opt);
    }
  } catch {
    const opt = document.createElement("option");
    opt.textContent = "无法读取实验室列表";
    sel.appendChild(opt);
  }
  return stored;
}

document.getElementById("go").onclick = async () => {
  const stored = await chrome.storage.local.get(["token", "origin"]);
  const lab = document.getElementById("lab").value;
  const token = stored.token;
  const origin = (stored.origin || "http://127.0.0.1:8000").replace(/\/$/, "");
  const msg = document.getElementById("msg");
  if (!lab || !token || !looksLikeJwt(token)) {
    msg.textContent = "请先在选项页登录或粘贴有效 JWT，并选择实验室";
    return;
  }
  await chrome.storage.local.set({ labId: lab });
  try {
    const payload = await metaFromTab();
    if (!payload.title) {
      msg.textContent = "未能解析题录，请改用 PDF 上传";
      return;
    }
    let pdf_fetch_failed = false;
    if (payload.pdf_url) {
      try {
        const pdfRes = await fetch(payload.pdf_url);
        if (!pdfRes.ok) throw new Error("pdf");
        const buf = await pdfRes.arrayBuffer();
        payload.pdf_base64 = await arrayBufferToBase64(buf);
        payload.pdf_filename = "capture.pdf";
      } catch {
        pdf_fetch_failed = true;
      }
    }
    delete payload.pdf_url;
    const res = await apiFetch(origin, "/api/v1/literature/import-capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lab_id: lab, mark_read: true, pdf_fetch_failed, ...payload }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      msg.textContent = data.detail || JSON.stringify(data);
      return;
    }
    if (data.degraded) {
      msg.textContent = data.hint || "已降级入库，请核对题录或上传 PDF";
    } else if (data.already_in_library) {
      msg.textContent = "库中已有该篇，已标为在读";
    } else {
      msg.textContent = "已导入";
    }
  } catch (e) {
    msg.textContent = String(e);
  }
};

loadLabs();
chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
  document.getElementById("url").textContent = tab?.url || "";
});
