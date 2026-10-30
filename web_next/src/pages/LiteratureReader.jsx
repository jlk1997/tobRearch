import { Component, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Space, message } from "antd";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api.js";
import { formatError, languageHref } from "../err.js";
import { glossaryHitsOnPage, setLangDraft, surroundingContext, zhCacheKey } from "../pdfText.js";
import { DirectionHangChips, EmptyHint, LabReaderBits, LoadFailed, MethodCard, ModelKeyHint, PageHeader, PageLoading, ReadTag, ResumeJob, feedbackAlertType } from "../ui.jsx";
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";
import { LinksPanel } from "./reader/LinksPanel.jsx";
import { NotesPanel } from "./reader/NotesPanel.jsx";
import { PdfPane } from "./reader/PdfPane.jsx";
import { ReaderToolbar } from "./reader/ReaderToolbar.jsx";
import { SectionsPanel } from "./reader/SectionsPanel.jsx";
import { SelectionPopover } from "./reader/SelectionPopover.jsx";
import { TutorPanel } from "./reader/TutorPanel.jsx";
import { ZhPane } from "./reader/ZhPane.jsx";
import { useGlossary, usePageText, useReaderDoc, useStructure } from "./reader/useReaderDoc.js";
import { usePageTranslation } from "./reader/usePageTranslation.js";
import { useSelection } from "./reader/useSelection.js";
import { useTutor } from "./reader/useTutor.js";

const PANELS = ["card", "notes", "tutor", "links"];
const NO_BODY_NEXT = "请先上传可复制 PDF。";

class SideGuard extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidUpdate(prev) {
    if (prev.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false });
  }

  render() {
    if (this.state.failed) {
      return (
        <p className="fs-muted">
          这一栏这次没打开。{" "}
          <button type="button" className="fs-link-btn" onClick={this.props.onClose}>
            回到这一页
          </button>
        </p>
      );
    }
    return this.props.children;
  }
}

function hasChineseLead(text) {
  return (String(text || "").match(/[\u4e00-\u9fff]/g) || []).length >= 8;
}

export default function LiteratureReader() {
  const routeParams = useParams();
  const id = routeParams.id || routeParams.itemId;
  const nav = useNavigate();
  const qc = useQueryClient();
  const me = useSession((s) => s.me);
  const [params, setParams] = useSearchParams();
  const page = Number(params.get("page") || 1) || 1;
  const fromMs = params.get("ms") || "";
  const fromSection = params.get("section") || "";
  const fromNote = params.get("note") || "";
  const fromDir = params.get("direction_id") || "";
  const urlSession = params.get("session") || "";

  const [session, setSessionState] = useState(urlSession);
  const [scope, setScope] = useState("paper");
  const [showZh, setShowZh] = useState(false);
  const [numPages, setNumPages] = useState(0);
  const [viewerPageText, setViewerPageText] = useState("");
  const [pdfErr, setPdfErr] = useState("");
  const [pdfNonce, setPdfNonce] = useState(0);
  const [pdfFallbackUrl, setPdfFallbackUrl] = useState(null);
  const [overrideAnalysis, setOverrideAnalysis] = useState(null);
  const [pdfFold, setPdfFold] = useState(false);
  const [ownMs, setOwnMs] = useState(null);
  const [ownReady, setOwnReady] = useState(false);

  const { doc, setDoc, analysis: docAnalysis, loadError, load, tutor, setTutor } = useReaderDoc(id, { session });
  useEffect(() => {
    const asked = params.get("panel") || "";
    if (asked !== "tutor" && !urlSession) return;
    if (!doc || doc.tutor_included) return;
    load({ include: "tutor", session: session || urlSession || "" });
  }, [doc?.id, doc?.tutor_included, params, urlSession, session, load]);
  useEffect(() => {
    const lab = doc?.lab_id;
    if (!lab) return undefined;
    let alive = true;
    const wait = window.setTimeout(() => {
      const dir = fromDir || (doc.direction_ids || [])[0];
      api.manuscripts(lab, dir ? { direction_id: dir } : {})
        .then((r) => {
          if (!alive) return;
          const raw = r.data?.items || r.data || [];
          const list = Array.isArray(raw) ? raw : [];
          setOwnMs(list[0] || null);
          setOwnReady(true);
        })
        .catch(() => {
          if (!alive) return;
          setOwnMs(null);
          setOwnReady(true);
        });
    }, 1200);
    return () => {
      alive = false;
      window.clearTimeout(wait);
    };
  }, [doc?.lab_id, fromDir, (doc?.direction_ids || []).join(",")]);
  const analysis = docAnalysis?.status === "running" ? docAnalysis : overrideAnalysis || docAnalysis;
  const { pageText: serverPageText, pageTextMissing } = usePageText(id, page, { enabled: !!doc, pages: numPages });
  const structure = useStructure(id, { enabled: !!doc && (params.get("panel") || "") === "card" });
  const terms = useGlossary(doc?.lab_id);

  const setPage = useCallback(
    (p) => {
      setParams(
        (cur) => {
          const next = new URLSearchParams(cur);
          next.set("page", String(p));
          return next;
        },
        { replace: true }
      );
    },
    [setParams]
  );

  // pdf.js 的 file 必须稳定，否则每次渲染都会重开文档。
  const pdfSource = useMemo(() => (id ? api.literaturePdfSource(id) : null), [id, pdfNonce]);

  useEffect(() => {
    setPdfErr("");
    setPdfFallbackUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  }, [id, pdfNonce]);

  useEffect(() => {
    if (params.get("page")) return;
    const n = fromNote && (doc?.notes || []).find((x) => x.id === fromNote);
    if (n?.page_no) {
      setPage(n.page_no);
      return;
    }
    if (doc?.last_page_no) setPage(doc.last_page_no);
  }, [doc?.id, doc?.last_page_no, doc?.notes, fromNote, params, setPage]);

  useEffect(() => {
    setViewerPageText("");
  }, [page]);

  useEffect(() => {
    if (!id || !numPages) return undefined;
    const t = window.setTimeout(() => {
      api.readingProgress(id, { page_no: page, page_count: numPages }).catch(() => {});
    }, 800);
    return () => window.clearTimeout(t);
  }, [id, page, numPages]);

  const pageSource = useMemo(() => {
    const txt = (viewerPageText || serverPageText || "").trim();
    return { text: txt, ok: !!txt };
  }, [viewerPageText, serverPageText]);
  const wasRunning = useRef(false);
  useEffect(() => {
    wasRunning.current = false;
    setPdfFold(false);
  }, [id]);
  useEffect(() => {
    if (!id || analysis?.status !== "running") return undefined;
    wasRunning.current = true;
    const t = window.setInterval(() => load({ include: "core" }), 8000);
    return () => window.clearInterval(t);
  }, [id, analysis?.status, load]);
  useEffect(() => {
    if (!id || !wasRunning.current || analysis?.status === "running") return;
    wasRunning.current = false;
    if ((analysis?.source || "") !== "parse_failed") {
      message.success("已写出这篇的方法");
    }
  }, [id, analysis?.status, analysis?.source]);

  const zh = usePageTranslation({ id, labId: doc?.lab_id, page, source: pageSource, enabled: showZh });
  const sel = useSelection({ id, labId: doc?.lab_id, page, pageText: pageSource.text });

  const reload = useCallback((opts) => load(opts), [load]);
  const setSession = useCallback(
    (sid) => {
      setSessionState(sid);
      setParams(
        (cur) => {
          const next = new URLSearchParams(cur);
          if (sid) next.set("session", sid);
          else next.delete("session");
          next.set("panel", "tutor");
          return next;
        },
        { replace: true }
      );
    },
    [setParams]
  );
  const ctl = useTutor({
    id,
    page,
    session,
    setSession,
    tutor,
    setTutor,
    pageText: pageSource.text,
    reload,
    scope,
  });

  const pageHits = useMemo(() => glossaryHitsOnPage(pageSource.text, terms), [pageSource.text, terms]);
  const pickHits = useMemo(() => glossaryHitsOnPage(sel.pick?.text || "", terms), [sel.pick?.text, terms]);
  const [located, setLocated] = useState(null);
  const highlights = useMemo(() => {
    const rows = (doc?.notes || [])
      .filter((n) => n.anchor?.rects?.length && (n.anchor?.quote || "").trim())
      .map((n) => ({
        id: n.id,
        page_no: n.anchor.page_no || n.page_no,
        rects: n.anchor.rects,
        label: (n.content_md || "").slice(0, 60),
      }));
    if (located?.quote && located.page_no) {
      rows.push({
        id: "located-quote",
        page_no: located.page_no,
        quote: located.quote,
        rects: [],
        label: located.quote,
      });
    }
    return rows;
  }, [doc?.notes, located]);

  const setPanel = useCallback(
    (p) => {
      setParams(
        (cur) => {
          const next = new URLSearchParams(cur);
          if (p) next.set("panel", p);
          else next.delete("panel");
          return next;
        },
        { replace: true }
      );
    },
    [setParams]
  );

  const loadPdfFallback = useCallback(() => {
    setPdfErr("无法渲染 PDF，已改用原文件预览。");
    api
      .literaturePdf(id)
      .then((r) => {
        const raw = r.data;
        const typed = raw instanceof Blob ? raw : new Blob([raw], { type: "application/pdf" });
        setPdfFallbackUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return URL.createObjectURL(typed);
        });
      })
      .catch((e) => setPdfErr(formatError(e, "无法打开 PDF")));
  }, [id]);

  const applyTutorSession = useCallback(
    (sid) => {
      ctl.reset();
      setSession(sid);
      load({ session: sid, tutorPage: 1 });
    },
    [ctl, load, setSession]
  );

  const goPolishText = useCallback(
    (text, mode = "polish", context) => {
      if (!text || !doc?.lab_id) return;
      setLangDraft(text, mode, context || surroundingContext(pageSource.text, text));
      nav(languageHref(doc.lab_id, { lit: id, page, ms: fromMs || undefined }));
    },
    [doc?.lab_id, fromMs, id, nav, page, pageSource.text]
  );

  const askSelection = useCallback(
    (text, context) => {
      if (!text) return;
      sel.remember(text, sel.pick?.anchor || null);
      sel.setPick(null);
      const clip = text.trim().slice(0, 80);
      setPanel("tutor");
      ctl.send(`这句话在说什么？「${clip}」`, text, context || surroundingContext(pageSource.text, text));
    },
    [ctl, pageSource.text, sel, setPanel]
  );

  const askCardLine = useCallback(
    (item) => {
      const clip = String(item?.text || "").trim().slice(0, 80);
      if (!clip) return;
      const quote = String(item.quote || item.text || "").trim();
      const pageNo = item.pages?.[0];
      if (pageNo) setPage(pageNo);
      setPanel("tutor");
      ctl.send(`这句话在说什么？「${clip}」`, quote, "", { pageNo: pageNo || page });
    },
    [ctl, page, setPage, setPanel]
  );

  const savePickNote = useCallback(async () => {
    const quote = (sel.pick?.text || "").trim();
    const zh = (sel.pick?.zh || "").trim();
    const body = zh && zh !== quote ? `${quote}\n\n${zh}` : quote;
    if (!body) return;
    try {
      await api.addNote(id, body, page, sel.pick?.anchor || null);
      message.success("已写入本页笔记");
      sel.setPick(null);
      load({ session, tutorPage: 1 });
    } catch (e) {
      message.error(formatError(e, "无法保存笔记"));
    }
  }, [id, load, page, sel, session]);

  const openSource = useCallback(
    (source) => {
      const iid = source?.item_id || source?.literature_item_id || "";
      const pageNo = source?.page ?? source?.page_no;
      if (iid && iid !== id) {
        nav(source.href || `/literature/${iid}?panel=tutor${pageNo ? `&page=${pageNo}` : ""}`);
        return;
      }
      const excerpt = String(source?.excerpt || "").replace(/\s+/g, " ").trim();
      if (pageNo) setPage(Number(pageNo));
      else if (source?.href) nav(source.href);
      if (excerpt) setLocated({ quote: excerpt.slice(0, 80), page_no: Number(pageNo) || page });
    },
    [id, nav, page, setPage]
  );

  const retryPageZh = useCallback(() => {
    try {
      sessionStorage.removeItem(zhCacheKey(id, page, pageSource.text));
    } catch {
      /* ignore */
    }
    zh.retry();
  }, [id, page, pageSource.text, zh]);

  if (loadError) {
    return (
      <>
        <LoadFailed message={loadError} onRetry={() => load()} />
        <ModelKeyHint err={loadError} />
      </>
    );
  }
  if (!doc) {
    return (
      <div className="fs-object is-side-closed">
        <div className="fs-object-main">
          <PdfPane
            source={pdfSource}
            fallbackUrl={pdfFallbackUrl}
            page={page}
            onPageChange={setPage}
            onNumPages={setNumPages}
            onPageText={setViewerPageText}
            onError={loadPdfFallback}
            error={pdfErr}
            hasFile
          />
        </div>
      </div>
    );
  }

  const teacher = roleOf(me, doc.lab_id) === "teacher" || !!me?.is_admin;
  const files = doc.files || [];
  const unusable = files.some((f) => f.parse_status === "unusable" || f.parse_status === "failed");
  const hangDirs = async (s) => {
    try {
      const next = [...new Set([...(doc.direction_ids || []), s.id])];
      const r = await api.patchLiterature(id, { direction_ids: next });
      setDoc({
        ...doc,
        direction_ids: r.data.direction_ids || next,
        direction_suggestions: r.data.direction_suggestions || [],
      });
      message.success(`已挂到${s.name}`);
    } catch (e) {
      message.error(formatError(e, "无法挂方向"));
    }
  };
  const metaLine = [doc.venue, doc.publish_year, doc.doi].filter(Boolean).join(" · ");
  const pageHint = numPages ? `共 ${numPages} 页` : files[0]?.page_count ? `共 ${files[0].page_count} 页` : "";
  const lead =
    metaLine ||
    (files.length ? ["已上传 PDF", pageHint, files[0]?.filename].filter(Boolean).join(" · ") : "上传 PDF 后可阅读全文");
  const noBody = !pageSource.ok;
  const running = analysis?.status === "running";
  const rawPanel = (params.get("panel") || "").trim();
  const askedPanel = rawPanel === "sections" ? "card" : rawPanel;
  const panel = PANELS.includes(askedPanel) ? askedPanel : fromNote ? "notes" : urlSession ? "tutor" : "";
  const tutorLead = panel === "tutor" && !!(urlSession || session);

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="large">
      <PageHeader
        title={doc.title}
        extra={<ReadTag status={doc.my_reading} />}
        hint=""
      >
        {doc.lab_reader_total ? (
          <p className="fs-muted">
            组里在读：
            <LabReaderBits names={doc.lab_readers} marks={doc.lab_reader_marks} onPage={setPage} />
            {doc.lab_reader_total > (doc.lab_readers || []).length ? ` +${doc.lab_reader_total - doc.lab_readers.length}` : ""}
          </p>
        ) : null}
        {hasChineseLead(analysis?.plain?.one_line?.text) ? <p>{analysis.plain.one_line.text}</p> : lead}
        {doc.direction_suggestions?.length ? (
          <details className="fs-inline-more">
            <summary>更多</summary>
            <DirectionHangChips suggestions={doc.direction_suggestions} onHang={hangDirs} />
          </details>
        ) : null}
      </PageHeader>
      {hasChineseLead(analysis?.plain?.one_line?.text) || (analysis?.plain?.start_pages || []).length ? (
        <details className="fs-inline-more">
          <summary>这篇在讲什么</summary>
          {hasChineseLead(analysis?.plain?.one_line?.text) ? <p>{analysis.plain.one_line.text}</p> : null}
          {hasChineseLead(analysis?.plain?.problem?.text) ? <p>{analysis.plain.problem.text}</p> : null}
          {hasChineseLead(analysis?.plain?.how?.text) ? <p>{analysis.plain.how.text}</p> : null}
          {hasChineseLead(analysis?.plain?.result?.text) ? <p>{analysis.plain.result.text}</p> : null}
          {(analysis.plain.start_pages || []).length ? (
            <p>
              先看
              {analysis.plain.start_pages.map((n) => (
                <button key={n} type="button" className="fs-chip" onClick={() => setPage(n)}>
                  第 {n} 页
                </button>
              ))}
            </p>
          ) : null}
          {analysis.source === "abstract_only" ? <p className="fs-muted">只看到摘要，正文还没读到。</p> : null}
        </details>
      ) : null}
      {doc.source_channel === "manuscript" ? (
        <p className="fs-muted">这篇是组里发表的，已经在文献库里。</p>
      ) : null}
      {doc.latest_reproduce ? (
        <details className="fs-inline-more">
          <summary>还有</summary>
        <Alert
          type={feedbackAlertType(
            doc.latest_reproduce,
            doc.latest_reproduce.status === "awaiting_gpu" ? "warning" : "info"
          )}
          showIcon
          message={
            doc.latest_reproduce.measured
              ? "已经跑出结果。论文里的数字还不是你们跑出来的。"
              : doc.latest_reproduce.feedback?.title
                ? `${doc.latest_reproduce.feedback.title}${doc.latest_reproduce.next_step ? ` · ${doc.latest_reproduce.next_step}` : ""}`
                : doc.latest_reproduce.next_step || "还没跑出结果。"
          }
          action={
            doc.latest_reproduce.can_resume ? (
              <ResumeJob row={doc.latest_reproduce} onDone={() => load()} />
            ) : (
              <Button
                size="small"
                onClick={() => nav(doc.latest_reproduce.href || `/reproduces/${doc.latest_reproduce.id}`)}
              >
                查看任务
              </Button>
            )
          }
        />
        </details>
      ) : null}
      <ReaderToolbar
        id={id}
        doc={doc}
        fromMs={fromMs}
        fromSection={fromSection}
        fromDir={fromDir}
        ownMs={ownMs}
        ownReady={ownReady}
        analysis={analysis}
        page={page}
        nav={nav}
        noBody={noBody}
        running={running}
        onReload={() => load()}
        onPanel={setPanel}
        onAnalysis={(data) => {
          const result = data.result || {};
          const runningNow = data.status === "running" || result.status === "running";
          if (runningNow) {
            setOverrideAnalysis(null);
            load({ include: "core" });
          } else {
            setOverrideAnalysis({
              ...result,
              session_id: data.session_id || result.session_id,
              trace_id: data.trace_id || result.trace_id,
              steps: data.steps || result.steps,
              followups: data.followups || result.followups,
              degraded: data.degraded || result.degraded,
              available_tools: data.available_tools || result.available_tools,
            });
          }
          setDoc((prev) =>
            prev
              ? {
                  ...prev,
                  card_empty: data.card_empty || prev.card_empty,
                  has_method_card: data.has_method_card ?? prev.has_method_card,
                  direction_ids: data.direction_ids || prev.direction_ids,
                  direction_suggestions: data.direction_suggestions ?? prev.direction_suggestions,
                  placed_directions: data.placed_directions || prev.placed_directions,
                }
              : prev
          );
        }}
        onPdfReload={() => {
          setPdfNonce((n) => n + 1);
          qc.invalidateQueries({ queryKey: ["literature-page-text", id] });
          qc.invalidateQueries({ queryKey: ["literature-structure", id] });
        }}
      />
      {unusable || noBody ? (
        <Alert
          type="warning"
          showIcon
          message={NO_BODY_NEXT}
          description={
            unusable
              ? "扫描件或加密文件无法复制文字。看中文、选区、辅导和提炼方法都要先换成可复制 PDF。"
              : "这一页还没有可复制文字。看中文、选区、辅导和提炼方法都要先上传可复制 PDF。"
          }
        />
      ) : null}
      <div
        className={`fs-object${panel ? "" : " is-side-closed"}${panel === "tutor" ? " is-tutor" : ""}${tutorLead ? " is-tutor-main" : ""}${
          pdfFold ? " is-pdf-collapsed" : ""
        }`}
      >
        <div className="fs-object-main">
          {!panel ? (
            <div className="fs-read-open">
              <button type="button" className="fs-chip" onClick={() => setPanel("links")}>引用</button>
              <button type="button" className="fs-chip" onClick={() => setPanel("notes")}>笔记</button>
              <button type="button" className="fs-chip" onClick={() => setPanel("card")}>速读</button>
            </div>
          ) : null}
          <div className={showZh ? "fs-read-split" : undefined}>
            <div>
              {located?.quote ? (
                <p className="fs-muted">
                  {pageSource.text && pageSource.text.replace(/\s+/g, "").includes(located.quote.replace(/\s+/g, "").slice(0, 24))
                    ? "这一页对上了这句，并标在原文上："
                    : "先跳到这一页。正文里还没对上这句："}
                  {located.quote}
                </p>
              ) : null}
              <PdfPane
                source={pdfSource}
                fallbackUrl={pdfFallbackUrl}
                page={page}
                onPageChange={setPage}
                onNumPages={setNumPages}
                onPageText={setViewerPageText}
                onSelectText={noBody ? undefined : sel.onSelectText}
                canTranslate={pageSource.ok}
                onError={loadPdfFallback}
                error={pdfErr}
                hasFile={files.length > 0}
                highlights={highlights}
                onHighlightClick={(h) => {
                  setPanel("notes");
                  if (h.page_no) setPage(h.page_no);
                }}
                showZh={showZh}
                onToggleZh={() => setShowZh((v) => !v)}
              />
            </div>
            {showZh ? (
              <ZhPane
                page={page}
                zh={zh}
                hits={pageHits}
                canTranslate={pageSource.ok}
                onRetry={retryPageZh}
                onStop={zh.stop}
                onPolish={() => goPolishText(zh.text, "polish", pageSource.text)}
              />
            ) : null}
          </div>
        </div>
        <div className="fs-object-side">
          {panel ? (
            <button type="button" className="fs-chip" style={{ marginBottom: 8 }} onClick={() => setPanel("")}>
              回到这一页
            </button>
          ) : null}
          {tutorLead ? (
            <button type="button" className="fs-chip" style={{ marginBottom: 8 }} onClick={() => setPdfFold((v) => !v)}>
              {pdfFold ? "展开 PDF" : "收起 PDF"}
            </button>
          ) : null}
          <div className="fs-side-read">
            <span className="fs-muted">
              第 {page} 页
              {numPages || doc.progress_pct
                ? ` · ${Math.round(numPages ? (100 * page) / numPages : doc.progress_pct || 0)}%`
                : ""}
            </span>
          </div>
          <div className="fs-side-tabs is-wrap" role="tablist" aria-label="阅读侧栏">
            {[
              ["card", "速读"],
              ["tutor", "问这篇"],
              ["notes", "笔记"],
              ["links", "引用"],
            ].map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={panel === key}
                className={`fs-side-tab${panel === key ? " is-on" : ""}`}
                onClick={() => setPanel(key)}
              >
                {label}
              </button>
            ))}
          </div>
          {panel === "links" ? (
            <LinksPanel
              id={id}
              labId={doc.lab_id}
              me={me}
              teacher={teacher}
              onAskLandscape={() => {
                setPanel("tutor");
                ctl.send("这篇的上下游论文有哪些");
              }}
              githubUrl={doc.github_url}
              hfUrl={doc.hf_url}
              onFindCode={() => load()}
            />
          ) : null}
          {panel === "notes" ? (
            <NotesPanel
              id={id}
              labId={doc.lab_id}
              notes={doc.notes}
              page={page}
              me={me}
              teacher={teacher}
              fromNote={fromNote}
              fromMs={fromMs}
              onPage={setPage}
              onChanged={() => load()}
              nav={nav}
            />
          ) : null}
          {panel === "tutor" ? (
            <TutorPanel
              id={id}
              doc={doc}
              page={page}
              scope={scope}
              onScope={setScope}
              analysis={analysis}
              pageTextMissing={pageTextMissing}
              pageText={pageSource.text}
              tutor={tutor}
              session={session}
              onSession={applyTutorSession}
              onNewSession={() => {
                const sid = (crypto.randomUUID && crypto.randomUUID()) || `s-${Date.now()}`;
                setTutor({ items: [], more: false, page: 1, total: 0, sessions: tutor.sessions });
                applyTutorSession(sid);
              }}
              onMore={() => load({ session, tutorPage: tutor.page + 1 })}
              onPage={setPage}
              onOpenSource={openSource}
              onPolish={goPolishText}
              ctl={ctl}
            />
          ) : null}
          {panel === "card" ? (
            <SideGuard resetKey={panel} onClose={() => setPanel("")}>
            <Card title="速读">
              {doc.card_empty?.kind === "unusable" || doc.card_empty?.kind === "abstract_only" ? (
                <p className="fs-muted">{doc.card_empty.next_step}</p>
              ) : null}
              {structure.has_structure ? (
                <details className="fs-inline-more">
                  <summary>跳到章节</summary>
                  <SectionsPanel
                    structure={structure}
                    onPage={setPage}
                    onAskSection={(item) => {
                      const start = item.page_no || item.page;
                      if (start) setPage(start);
                      setPanel("tutor");
                      ctl.send(`讲这一节：${item.title || "这一节"}（从第 ${start || "?"} 页起）`);
                    }}
                    onAskFigure={(item) => {
                      const start = item.page_no || item.page;
                      if (start) setPage(start);
                      setPanel("tutor");
                      const caption = item.caption || item.title || "这张图";
                      ctl.send(
                        `讲这张图。图注：${caption}。请按图注和这一页正文讲，并写明按图注和正文讲的，没看图像本身。`
                      );
                    }}
                    onAskFigure={(item) => {
                      const start = item.page_no || item.page;
                      if (start) setPage(start);
                      setPanel("tutor");
                      ctl.send(
                        `讲这张图。图注：${item.caption || item.title || ""}。请根据图注和这一页正文讲，并写明按图注和正文讲的，没看图像本身。`
                      );
                    }}
                  />
                </details>
              ) : null}
              {analysis ? (
                <MethodCard
                  analysis={analysis}
                  onPage={setPage}
                  onAsk={askCardLine}
                  onAskPaper={() => setPanel("tutor")}
                  onRetry={running || noBody ? undefined : () => document.getElementById("fs-analyze-run")?.click()}
                  retryBusy={running}
                />
              ) : (
                <p className="fs-muted">
                  还没有速读。{" "}
                  <button type="button" className="fs-link-btn" disabled={noBody} onClick={() => document.getElementById("fs-analyze-run")?.click()}>
                    生成速读
                  </button>
                </p>
              )}
            </Card>
            </SideGuard>
          ) : null}
        </div>
      </div>
      <SelectionPopover
        pick={sel.pick}
        hits={pickHits}
        onAsk={askSelection}
        onExplainFormula={(text) => {
          const clip = String(text || "").trim().slice(0, 200);
          if (!clip) return;
          setPanel("tutor");
          ctl.send(`讲这个公式。先逐个解释符号，再说这一步在做什么。原文：「${clip}」`, text);
        }}
        canWrite={!!ownMs?.id}
        onWrite={async (text) => {
          if (!ownMs?.id || !doc?.lab_id) return;
          try {
            const section = fromSection || "相关工作";
            await api.writeback(doc.lab_id, {
              manuscript_id: ownMs.id,
              text,
              write_mode: "append_heading",
              heading: section,
              expected_updated_at: ownMs.updated_at,
              literature_item_ids: [id],
            });
            nav(`/manuscripts/${ownMs.id}?section=${encodeURIComponent(section)}&wrote=1&from_lit=${encodeURIComponent(id)}&from_page=${page}`);
          } catch (e) {
            message.error(formatError(e, "没写进这一节"));
          }
        }}
        onPolish={sel.polishPick}
        onRetranslate={() => sel.translatePick(sel.pick.text, sel.pick.x, sel.pick.y, true, sel.pick.anchor)}
        onSaveNote={savePickNote}
        onTakeAway={() => goPolishText(sel.pick.polished || sel.pick.zh, "polish", sel.pick.text)}
        onClose={() => sel.setPick(null)}
      />
    </Space>
  );
}
