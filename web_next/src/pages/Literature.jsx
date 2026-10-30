import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Collapse, Form, Input, Modal, Progress, Segmented, Select, Space, Table, Tabs, Tag, message, Upload } from "antd";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, saveBlob, asItems } from "../api.js";
import { formatError, READ_STATUS, literatureHref } from "../err.js";
import { shortName } from "../tokens.js";
import { ActionChip, AgentTraceBox, AiResultBar, AnalyzeChip, AskSourceChips, CreateDialog, DirectionFilterHint, DirectionGapAlert, DirectionHangChips, DirectionSelectEmpty, EmptyHint, FeedbackStrip, LabReaderBits, LibraryProgress, LoadFailed, MdPreview, ModelKeyHint, NoCardTitleChips, PageHeader, PageLoading, PdfGapLink, ReadTag, ReproduceChip, ReproduceHonest, ResumeJob, RowMore, UnreadTitleChips, UnusedResearchAlert, WritebackSuccess, whenBlocked } from "../ui.jsx";
import { manuscriptSectionHref } from "../manuscriptSections.js";
import { AuthorDrawer } from "../AuthorDrawer.jsx";

const SEARCH_SOURCES = [
  { value: "arxiv", label: "arXiv" },
  { value: "pubmed", label: "PubMed" },
  { value: "semantic", label: "Semantic Scholar" },
  { value: "medrxiv", label: "medRxiv" },
  { value: "biorxiv", label: "bioRxiv" },
  { value: "iacr", label: "IACR" },
  { value: "scholar", label: "Google Scholar" },
  { value: "lab", label: "本实验室库" },
];

function sourceLabel(s) {
  const hit = SEARCH_SOURCES.find((x) => x.value === String(s || "").toLowerCase());
  return hit?.label || s || "";
}

function SearchEmptyNext({ emptyNext }) {
  if (!emptyNext) return null;
  return (
    <Alert
      className="fs-invite"
      type="info"
      showIcon
      style={{ marginBottom: 12 }}
      message={emptyNext.title || "没有检索结果"}
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
  );
}
import { useSession } from "../store.js";
import { roleOf } from "../nav.js";

export default function Literature() {
  const { id } = useParams();
  const nav = useNavigate();
  const me = useSession((s) => s.me);
  const teacher = roleOf(me, id) === "teacher" || !!me?.is_admin;
  const [params, setParams] = useSearchParams();
  const [hits, setHits] = useState([]);
  const [recs, setRecs] = useState([]);
  const [labUnread, setLabUnread] = useState([]);
  const [labRelated, setLabRelated] = useState([]);
  const [recAuthors, setRecAuthors] = useState([]);
  const [recMeta, setRecMeta] = useState(null);
  const [dirs, setDirs] = useState([]);
  const [dirsKnown, setDirsKnown] = useState(false);
  const [selected, setSelected] = useState([]);
  const [pickCompare, setPickCompare] = useState(false);
  const [shelf, setShelf] = useState("in");
  const [rewritten, setRewritten] = useState("");
  const [compareOut, setCompareOut] = useState("");
  const [compareRepro, setCompareRepro] = useState("");
  const [compareItems, setCompareItems] = useState([]);
  const [compareJson, setCompareJson] = useState(null);
  const [compareGap, setCompareGap] = useState("");
  const [compareRw, setCompareRw] = useState("");
  const [compareNext, setCompareNext] = useState(null);
  const [compareEmpty, setCompareEmpty] = useState(null);
  const [compareUnused, setCompareUnused] = useState(null);
  const [compareTrace, setCompareTrace] = useState("");
  const [compareSteps, setCompareSteps] = useState([]);
  const [compareDegraded, setCompareDegraded] = useState("");
  const [compareTools, setCompareTools] = useState([]);
  const [compareFollowups, setCompareFollowups] = useState([]);
  const [compareNote, setCompareNote] = useState("");
  const [comparing, setComparing] = useState(false);
  const compareAbort = useRef(null);
  const [writeBusy, setWriteBusy] = useState(false);
  const [wbHint, setWbHint] = useState("");
  const [wbHref, setWbHref] = useState("");
  const [wbNext, setWbNext] = useState(null);
  const [writeDir, setWriteDir] = useState();
  const [writeMs, setWriteMs] = useState();
  const [msList, setMsList] = useState([]);
  const [msErr, setMsErr] = useState("");
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [readers, setReaders] = useState([]);
  const [readerErr, setReaderErr] = useState("");
  const [searchErr, setSearchErr] = useState("");
  const [searchHints, setSearchHints] = useState([]);
  const [searchTick, setSearchTick] = useState(0);
  const [recErr, setRecErr] = useState("");
  const [recHangBusy, setRecHangBusy] = useState("");
  const [ingestForm] = Form.useForm();
  const [ingestDoiForm] = Form.useForm();
  const [bibForm] = Form.useForm();
  const [csvForm] = Form.useForm();
  const [hangBusy, setHangBusy] = useState("");
  const directionId = params.get("direction_id") || undefined;
  const reading = params.get("reading_status") || undefined;
  const year = params.get("year") || undefined;
  const citedPublished = params.get("cited_by_published") === "1";
  const hasPdf = params.get("has_pdf") || undefined;
  const hasCard = params.get("has_card") || undefined;
  const reproduce = params.get("reproduce") || undefined;
  const readerId = params.get("reader_id") || undefined;
  const readingScope = params.get("reading_scope") || undefined;
  const coreOnly = params.get("core") === "1";
  const authorId = params.get("author") || undefined;
  const readLevel = params.get("read_level") || undefined;
  const minCited = params.get("min_cited") || undefined;
  const sortBy = params.get("sort") || undefined;
  const [authorOpen, setAuthorOpen] = useState("");
  const [authorOpts, setAuthorOpts] = useState([]);
  const tabParam = params.get("tab") || "";
  const qParam = params.get("q") || "";
  const langParam = params.get("language") || "zh";
  const sourcesParam = (params.get("sources") || "arxiv,lab")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  const sourcesKey = sourcesParam.join(",");
  const tab = ["search", "recommend", "manual"].includes(tabParam) ? tabParam : qParam ? "search" : "manual";
  const ingestOpen = tabParam === "manual";
  const [searching, setSearching] = useState(false);
  const [searchCounts, setSearchCounts] = useState(null);
  const [emptyNext, setEmptyNext] = useState(null);
  const [dirErr, setDirErr] = useState("");
  const [pdfIngest, setPdfIngest] = useState(null);
  const setFilter = (key, value) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, String(value));
    else {
      next.delete(key);
      if (key === "reading_status") next.delete("reader_id");
    }
    setParams(next, { replace: true });
    setPage(1);
  };
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["literature", id, page, q, reading, directionId, citedPublished, year, hasPdf, hasCard, reproduce, readerId, readingScope, coreOnly, authorId, readLevel, minCited, sortBy],
    queryFn: () =>
      api
        .literature(id, {
          page,
          page_size: 20,
          q: q || undefined,
          reading_status: reading || undefined,
          reading_scope: readingScope || undefined,
          reader_id: readerId,
          direction_id: directionId || undefined,
          cited_by_published: citedPublished || undefined,
          year: year || undefined,
          has_pdf: hasPdf === "0" ? false : hasPdf === "1" ? true : undefined,
          has_card: hasCard === "0" ? false : hasCard === "1" ? true : undefined,
          reproduce: reproduce || undefined,
          core: coreOnly || undefined,
          author_id: authorId,
          read_level: readLevel,
          min_cited: minCited,
          sort: sortBy,
        })
        .then((r) => r.data),
    enabled: !!id,
  });
  const digest = useQuery({
    queryKey: ["digest-status", id],
    queryFn: () => api.digestStatus(id).then((r) => r.data),
    enabled: !!id && teacher,
  });
  const openUploaded = (r) => {
    refetch();
    const items = r.data.items || [];
    const imported = items.filter((x) => x.id);
    const failed = items.filter((x) => x.error);
    const n = imported.filter((x) => x.already_in_library).length;
    if (!imported.length) {
      message.error(failed[0]?.error || "PDF 导入失败");
    } else if (n && n === imported.length) {
      message.success("库中已有，已打开原条目");
    } else if (n) {
      message.success(`已导入 ${imported.length} 个 PDF（其中 ${n} 篇库中已有，已打开原条目）`);
    } else {
      message.success("已经放进组里。过一会儿就能直接问这篇在讲什么");
    }
    if (r.data.next_step) message.info(r.data.next_step);
    const first = imported[0];
    if (imported.length === 1 && first?.id) {
      nav(literatureHref(first.id, undefined, { panel: "card" }));
      if (imported.some((x) => x.has_method_card !== true && x.parse_status !== "failed" && !x.error)) {
        message.info("后台正在提炼，读完就能问。");
      }
    }
  };
  const ingestPdfs = (file, fileList) => {
    if (pdfIngest) return false;
    const last = fileList[fileList.length - 1] || {};
    const lastKey = last.uid || last.name;
    const fileKey = file.uid || file.name;
    if (lastKey && fileKey && lastKey !== fileKey) return false;
    const files = (fileList || []).map((f) => f.originFileObj || f).filter(Boolean);
    if (!files.length) return false;
    const zips = files.filter((f) => /\.zip$/i.test(f.name || ""));
    const pdfs = files.filter((f) => !/\.zip$/i.test(f.name || ""));
    if (zips.length) {
      uploadZips(zips).then(() => {
        if (pdfs.length) uploadMany(pdfs);
      });
      return false;
    }
    if (files.length <= 3) {
      const names = files.map((f) => f.name || "paper.pdf");
      setPdfIngest({ names, pct: 0, phase: "upload" });
      api
        .uploadPdfs(id, files, (pct) => setPdfIngest((s) => (s ? { ...s, pct } : s)), {
          direction_id: directionId,
        })
        .then((r) => {
          setPdfIngest((s) => (s ? { ...s, pct: 100, phase: "open" } : s));
          openUploaded(r);
        })
        .catch((err) => {
          setPdfIngest(null);
          message.error(formatError(err, "PDF 导入失败"));
        });
      return false;
    }
    uploadMany(files);
    return false;
  };
  const uploadMany = async (files) => {
    const size = 20;
    const batches = [];
    for (let i = 0; i < files.length; i += size) batches.push(files.slice(i, i + size));
    let done = 0;
    let added = 0;
    let dup = 0;
    let failed = 0;
    setPdfIngest({ names: [], pct: 0, phase: "bulk", total: files.length, done: 0 });
    for (const batch of batches) {
      try {
        const r = await api.uploadPdfs(
          id,
          batch,
          (pct) =>
            setPdfIngest((s) =>
              s ? { ...s, pct: Math.round(((done + (batch.length * pct) / 100) / files.length) * 100) } : s,
            ),
          { direction_id: directionId },
        );
        const items = r.data?.items || [];
        added += items.filter((x) => x.id && !x.already_in_library).length;
        dup += items.filter((x) => x.id && x.already_in_library).length;
        failed += items.filter((x) => x.error).length;
      } catch {
        failed += batch.length;
      }
      done += batch.length;
      setPdfIngest((s) => (s ? { ...s, done, pct: Math.round((done / files.length) * 100) } : s));
    }
    setPdfIngest(null);
    refetch();
    digest.refetch?.();
    const bits = [`收到 ${added} 篇新论文`];
    if (dup) bits.push(`${dup} 篇组里已有`);
    if (failed) bits.push(`${failed} 篇没传上`);
    message.success(`${bits.join("，")}。后台会接着拆字、建索引、读摘要，进度在上面。`);
  };
  const uploadZips = async (zips) => {
    setPdfIngest({ names: zips.map((f) => f.name), pct: 0, phase: "bulk", total: zips.length, done: 0 });
    let added = 0;
    let failed = 0;
    for (let i = 0; i < zips.length; i += 1) {
      try {
        const r = await api.uploadZip(id, zips[i], (pct) =>
          setPdfIngest((s) => (s ? { ...s, pct: Math.round(((i + pct / 100) / zips.length) * 100) } : s)),
          { direction_id: directionId },
        );
        const items = r.data?.items || [];
        added += items.filter((x) => x.id && !x.already_in_library).length;
        failed += items.filter((x) => x.error).length;
      } catch {
        failed += 1;
      }
      setPdfIngest((s) => (s ? { ...s, done: i + 1, pct: Math.round(((i + 1) / zips.length) * 100) } : s));
    }
    setPdfIngest(null);
    refetch();
    digest.refetch?.();
    message.success(`压缩包里放进 ${added} 篇新论文${failed ? `，${failed} 份没放进去` : ""}。进度在上面。`);
  };
  const rows = Array.isArray(data) ? data : data?.items || [];
  const total = Array.isArray(data) ? data.length : data?.total || 0;
  const narrowed = Boolean(
    q || qParam || directionId || reading || year || citedPublished || hasPdf || hasCard || reproduce || readerId || readingScope || coreOnly || authorId || readLevel || minCited || sortBy
  );
  const bareLibrary = !isLoading && total === 0 && !narrowed;
  const dirExtra = directionId ? { direction_ids: [directionId] } : {};
  const hangPaper = async (row, did) => {
    if (!row?.id || !did || hangBusy) return;
    setHangBusy(`${row.id}:${did}`);
    try {
      const next = [...new Set([...(row.direction_ids || []), did])];
      await api.patchLiterature(row.id, { direction_ids: next });
      message.success("已挂到方向");
      refetch();
    } catch (e) {
      message.error(formatError(e, "无法挂方向"));
    } finally {
      setHangBusy("");
    }
  };
  const loadDirs = () =>
    api
      .directions(id)
      .then((r) => {
        setDirs(asItems(r.data));
        setDirErr("");
        setDirsKnown(true);
      })
      .catch((e) => {
        setDirs([]);
        setDirErr(formatError(e, "方向加载失败"));
        setDirsKnown(true);
      });
  useEffect(() => {
    if (!id) return undefined;
    let alive = true;
    api
      .compareLatest(id)
      .then((r) => {
        if (!alive) return;
        const d = r.data || {};
        const gap = d.gap_md || "";
        const table = d.table_md || d.markdown || "";
        if (!gap && !table && !d.reproduce_gap_md) return;
        setCompareGap((cur) => cur || gap);
        setCompareOut((cur) => cur || table);
        setCompareRepro((cur) => cur || d.reproduce_gap_md || "");
        if (d.session_id) setCompareTrace((cur) => cur || d.session_id);
        if (d.truncated) setCompareNote((cur) => cur || "上次对比较长，这里只保留前一部分。");
        const ids = Array.isArray(d.item_ids) ? d.item_ids.filter(Boolean) : [];
        const titles = Array.isArray(d.titles) ? d.titles : [];
        if (ids.length) {
          setCompareItems((cur) => (cur.length ? cur : ids.map((iid, i) => ({ id: iid, title: titles[i] || "" }))));
          setSelected((cur) => (cur.length ? cur : ids));
        }
      })
      .catch((e) => {
        if (alive) message.warning(formatError(e, "上次的对比没打开"));
      });
    return () => {
      alive = false;
    };
  }, [id]);
  useEffect(() => {
    setDirsKnown(false);
    loadDirs();
    api
      .manuscripts(id)
      .then((r) => {
        setMsList(asItems(r.data));
        setMsErr("");
      })
      .catch((e) => {
        setMsList([]);
        setMsErr(formatError(e, "论文列表加载失败"));
      });
    api
      .members(id)
      .then((r) => {
        setReaders(asItems(r.data));
        setReaderErr("");
      })
      .catch((e) => {
        setReaders([]);
        setReaderErr(formatError(e, "成员列表加载失败"));
      });
  }, [id]);
  const ingest = async (row) => {
    const r = await api.addLiterature(id, {
      title: row.title,
      authors: row.authors || [],
      abstract: row.abstract || "",
      doi: row.doi,
      url: row.url,
      venue: row.venue,
      publish_year: row.year,
      arxiv_id: row.arxiv_id,
      pubmed_id: row.pubmed_id,
      mark_read: true,
      direction_ids: directionId ? [directionId] : [],
    });
    const placed = r.data.auto_placed || [];
    if (placed.length) {
      const names = placed.map((p) => p.name).filter(Boolean).join("、");
      message.success({
        duration: 6,
        content: (
          <span>
            已放进{names}{" "}
            {r.data.id ? <Link to={literatureHref(r.data.id)}>去读这篇</Link> : null}{" "}
            <Button
              type="link"
              size="small"
              onClick={async () => {
                const drop = new Set(placed.map((p) => p.id));
                const keep = (r.data.direction_ids || []).filter((did) => !drop.has(did));
                await api.patchLiterature(r.data.id, { direction_ids: keep });
                message.success("已撤销");
                refetch();
              }}
            >
              撤销
            </Button>
          </span>
        ),
      });
    } else {
      message.success(r.data.already_in_library ? "库中已有该篇，已标为在读并挂上方向" : directionId ? "已入库并放进这个方向" : "已入库并标为在读");
    }
    refetch();
  };
  const runRecommend = async (quiet = false) => {
    if (dirErr) {
      if (!quiet) message.error(dirErr);
      return;
    }
    if (!dirsKnown && !directionId) return;
    setSearching(true);
    setRecErr("");
    try {
      const r = await api.recommendLiterature(id, directionId);
      setRecs(r.data.items || []);
      setLabUnread(r.data.lab_unread || []);
      setLabRelated(r.data.lab_related || []);
      setRecAuthors(r.data.authors || []);
      setRecMeta({
        query: r.data.query,
        rerank: r.data.rerank,
        llm_rerank: r.data.llm_rerank,
        embed_fallback: r.data.embed_fallback,
        sources: r.data.sources_used,
        hint: r.data.corpus_hint,
        empty_next: r.data.empty_next || null,
        query_fallback: !!r.data.query_fallback,
        query_via: r.data.query_via || "",
        cached: !!r.data.cached,
        keyword_hints: r.data.keyword_hints || [],
      });
      if (
        !quiet &&
        !(r.data.items || []).length &&
        !(r.data.lab_unread || []).length &&
        !(r.data.lab_related || []).length
      ) {
        message.info(
          r.data.empty_next?.text ||
            (teacher
              ? "暂无未入库推荐、本方向未读或组内相关题录。先在方向里填写关键词或入库文献。"
              : "暂无未入库推荐、本方向未读或组内相关题录。可去检索入库，关键词由导师填写。")
        );
      }
    } catch (e) {
      setRecs([]);
      setLabUnread([]);
      setLabRelated([]);
      setRecAuthors([]);
      setRecMeta(null);
      setRecErr(formatError(e, "推荐失败"));
      if (!quiet) message.error(formatError(e, "推荐失败"));
    } finally {
      setSearching(false);
    }
  };
  useEffect(() => {
    if (tab !== "recommend" || !id || dirErr) return;
    if (!dirsKnown && !directionId) return;
    runRecommend(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, id, directionId, dirsKnown, dirs.length, dirErr]);
  const runSearch = (query, sources, language) => {
    const nextQ = (query || "").trim();
    if (!nextQ) return;
    const next = new URLSearchParams(params);
    if (tabParam === "recommend" || tabParam === "manual") next.delete("tab");
    next.set("q", nextQ);
    const src = (sources && sources.length ? sources : sourcesParam).filter(Boolean);
    if (src.length) next.set("sources", src.join(","));
    else next.delete("sources");
    next.set("language", language || langParam);
    setParams(next, { replace: true });
  };
  useEffect(() => {
    if (!id || !qParam || tab !== "search") return undefined;
    let alive = true;
    setSearching(true);
    setEmptyNext(null);
    setSearchErr("");
    api
      .searchLiterature(id, qParam, langParam, sourcesParam, { direction_id: directionId })
      .then((r) => {
        if (!alive) return;
        const items = r.data.items || [];
        setHits(items);
        setSearchCounts({
          local: r.data.local_count || 0,
          external: r.data.external_count || 0,
          version_note: r.data.version_note || "",
        });
        setRewritten(r.data.rewritten_q || qParam);
        setEmptyNext(items.length ? null : r.data.empty_next || null);
        setSearchHints(r.data.keyword_hints || []);
        setSearchErr("");
      })
      .catch((e) => {
        if (!alive) return;
        setHits([]);
        setSearchCounts(null);
        setEmptyNext(null);
        setSearchHints([]);
        setSearchErr(formatError(e, "检索失败"));
      })
      .finally(() => {
        if (alive) setSearching(false);
      });
    return () => {
      alive = false;
    };
  }, [id, qParam, tab, langParam, sourcesKey, searchTick, directionId]);
  const hitCols = [
    {
      title: "标题",
      dataIndex: "title",
      render: (t, row) => (
        <span>
          {row.id || row.href ? (
            <Link className="fs-lit-title" title={t || ""} to={row.href || literatureHref(row.id, row.last_page_no, directionId ? { direction: directionId } : undefined)}>
              {t}
            </Link>
          ) : (
            t
          )}
          {row.already_in_library && row.direction_suggestions?.length ? (
            <details className="fs-inline-more" onClick={(e) => e.stopPropagation()}>
              <summary>更多</summary>
              <DirectionHangChips
                suggestions={row.direction_suggestions}
                busyId={recHangBusy.startsWith(`${row.id}:`) ? recHangBusy.slice(String(row.id).length + 1) : ""}
                onHang={async (s) => {
                  if (!row.id || !s?.id || recHangBusy) return;
                  setRecHangBusy(`${row.id}:${s.id}`);
                  try {
                    const next = [...new Set([...(row.direction_ids || []), s.id])];
                    await api.patchLiterature(row.id, { direction_ids: next });
                    message.success("已挂到方向");
                    setSearchTick((n) => n + 1);
                    if (tab === "recommend") await runRecommend(true);
                  } catch (e) {
                    message.error(formatError(e, "无法挂方向"));
                  } finally {
                    setRecHangBusy("");
                  }
                }}
              />
            </details>
          ) : null}
        </span>
      ),
    },
    {
      title: "组内写过",
      render: (_, row) =>
        (row.cited_in_lab || []).length
          ? (row.cited_in_lab || []).map((c) => (
              <span key={c.item_id || c.href} style={{ marginRight: 8 }}>
                <Link to={c.href || literatureHref(c.item_id)}>组内这篇的方法写过</Link>
              </span>
            ))
          : "—",
    },
    { title: "来源", dataIndex: "source", render: (s) => sourceLabel(s) || s || "—" },
    ...(directionId ? [{ title: "对上了哪些词", dataIndex: "why", render: (w) => w || "—" }] : []),
    { title: "年份", dataIndex: "year" },
    {
      title: "库内",
      render: (_, row) => (row.already_in_library || row.source === "lab" ? "已有" : "可入库"),
    },
    {
      title: "全文",
      render: (_, row) => (row.card_empty?.kind ? <PdfGapLink row={row} /> : row.pdf_hint || row.id ? "—" : "链接"),
    },
    {
      title: "入库",
      render: (_, row) => (
        <Space size={4}>
          <Button
            size="small"
            type={row.already_in_library ? "default" : "primary"}
            onClick={async () => {
              if (row.already_in_library && row.id) {
                nav(literatureHref(row.id, row.last_page_no, directionId ? { direction: directionId } : undefined));
                return;
              }
              try {
                await ingest(row);
              } catch (e) {
                message.error(formatError(e, "入库失败"));
              }
            }}
          >
            {row.already_in_library ? "去读这篇" : directionId ? "入库并放进这个方向" : "入库并在读"}
          </Button>
        </Space>
      ),
    },
  ];
  const leftoverHintBar = (hints) =>
    teacher && (hints || []).length ? (
      <p className="fs-dir-hints">
        <span className="fs-muted">对不上的篇里反复出现{directionId ? "，点一下写进当前方向：" : "："}</span>
        {directionId
          ? hints.map((w) => (
              <Button
                key={w}
                size="small"
                onClick={async () => {
                  const curDir = dirs.find((d) => d.id === directionId);
                  const cur = [...(curDir?.keywords || [])];
                  if (cur.includes(w)) return;
                  try {
                    await api.patchDirection(directionId, { keywords: [...cur, w] });
                    message.success("已写进当前方向");
                    setDirs((prev) =>
                      prev.map((d) => (d.id === directionId ? { ...d, keywords: [...cur, w] } : d))
                    );
                    setSearchTick((n) => n + 1);
                    refetch();
                    if (tab === "recommend") await runRecommend(true);
                  } catch (e) {
                    message.error(formatError(e, "无法更新关键词"));
                  }
                }}
              >
                {w}
              </Button>
            ))
          : (
            <>
              {hints.join("、")}
              {" · "}
              <Link to={`/labs/${id}/directions`}>去方向页写进关键词</Link>
            </>
          )}
      </p>
    ) : null;
  return (
    <Space direction="vertical" className="fs-lit-page" style={{ width: "100%" }}>
      <Modal open={!!pdfIngest} footer={null} closable={false} maskClosable={false} centered title="正在放进组里">
        {pdfIngest?.phase === "bulk" ? (
          <p>
            已传 {pdfIngest.done || 0} / {pdfIngest.total} 篇
          </p>
        ) : (
          <p>{(pdfIngest?.names || []).join("、") || "paper.pdf"}</p>
        )}
        <Progress percent={pdfIngest?.phase === "open" ? 100 : pdfIngest?.pct || 0} status="active" />
        <p className="fs-muted">
          {pdfIngest?.phase === "bulk"
            ? "每次传 20 篇，传完这个窗口会自己关。拆字和建索引在后台做，不用等。"
            : pdfIngest?.phase === "open"
              ? "已入库，正在打开阅读器…"
              : pdfIngest?.pct >= 99
                ? "文件已传到服务器，正在解析题名，请稍候。"
                : "正在上传。大文件可能需要十几秒，请不要重复拖入。"}
        </p>
      </Modal>
      <PageHeader
        tone={reading === "unread" || hasCard === "0" ? "unused" : undefined}
        extra={
          <Button type="primary" onClick={() => setFilter("tab", ingestOpen ? undefined : "manual")}>
            添加文献
          </Button>
        }
        title={
          reading === "unread" && readerId
            ? "正在看：这人还没开始读"
            : reading === "unread"
              ? "正在看：没人在读的论文"
              : hasCard === "0"
                ? "正在看：还没写怎么做的论文"
                : "文献"
        }
        hint="写题目里的词。库里有的直接打开，没有的再入库。"
      >
        {reading === "unread" && readerId
          ? `${(readers.find((m) => m.user_id === readerId) || {}).display_name || "这人"}还没开始读。`
          : reading === "unread"
            ? "组里没人在读。"
            : hasCard === "0"
              ? "打开一篇，点提炼。"
              : reading === "reading" && readingScope === "lab"
                ? "组里正在读的。"
                : reading === "want_read" && readingScope === "lab"
                  ? "组里想读的。"
                  : reading === "reading" && !readingScope && !readerId
                    ? "我正在读的。"
                    : reading === "want_read" && !readingScope && !readerId
                      ? "我想读的。"
                      : coreOnly
                        ? "已钉成核心的。"
                        : directionId
                          ? `正在看「${(dirs.find((d) => d.id === directionId) || {}).name || "这个方向"}」里的论文。`
                          : "写几个词，找出库里和外面的文献。"}
        {reading === "unread" || hasCard === "0" ? (
          <>
            {" "}
            <ActionChip to={`/labs/${id}/literature`}>全部文献</ActionChip>
          </>
        ) : null}
      </PageHeader>
      {teacher && digest.data ? <LibraryProgress status={digest.data} labId={id} /> : null}
      {(() => {
        const gap = !Array.isArray(data) ? data?.direction_gap : null;
        const drafts = !Array.isArray(data) ? data?.direction_drafts : null;
        if (bareLibrary || (!gap && !(drafts || []).length)) return null;
        return (
          <Collapse
            ghost
            items={[{
              key: "gap",
              label: "还没放进方向的论文",
              children: (
                <DirectionGapAlert
                  gap={gap}
                  drafts={drafts}
                  labId={id}
                  teacher={teacher}
                  onAdopted={() => {
                    refetch();
                    loadDirs();
                  }}
                />
              ),
            }]}
          />
        );
      })()}
      {leftoverHintBar(!Array.isArray(data) ? data?.keyword_hints || [] : [])}
      <div className="fs-lit-search" id="fs-lit-out">
        <Input.Search
          allowClear
          placeholder="在组里的文献里找"
          style={{ width: 280 }}
          onSearch={(v) => {
            setShelf("in");
            setQ(v);
            setPage(1);
          }}
        />
        <Button type="link" onClick={() => setShelf(shelf === "out" ? "in" : "out")}>
          {shelf === "out" ? "回到组里" : "去外面找"}
        </Button>
        {shelf === "out" ? (
        <Form
          key={`head-search-${qParam}-${langParam}-${sourcesKey}`}
          layout="inline"
          initialValues={{ q: qParam, language: langParam, sources: sourcesParam }}
          onFinish={(v) => runSearch(v.q, v.sources, v.language)}
        >
          <Form.Item name="q" rules={[{ required: true, message: "先写要找的词" }]}>
            <Input placeholder="例如：残差检测、小目标" style={{ width: 320 }} />
          </Form.Item>
          <Button htmlType="submit" loading={searching}>
            搜索
          </Button>
          <details className="fs-inline-more">
            <summary>换语言或来源</summary>
            <Form.Item name="language">
              <Select
                style={{ width: 120 }}
                options={[
                  { value: "zh", label: "中文优先" },
                  { value: "en", label: "英文源" },
                ]}
              />
            </Form.Item>
            <Form.Item name="sources">
              <Select
                mode="multiple"
                style={{ minWidth: 220 }}
                options={SEARCH_SOURCES}
              />
            </Form.Item>
          </details>
        </Form>
        ) : null}
      </div>
      {shelf === "out" && rewritten && rewritten !== qParam ? <p className="fs-muted">这次也带上了这些词：{rewritten}</p> : null}
      {searchErr ? <LoadFailed message={searchErr} onRetry={() => setSearchTick((n) => n + 1)} /> : null}
      {leftoverHintBar(searchHints)}
      {tab === "recommend" ? (
        <Card title="库里有的和外面找到的">
          {directionId && !(dirs.find((d) => d.id === directionId)?.keywords || []).length ? (
            <p>
              先写这个方向在找什么。{" "}
              <Link to={`/labs/${id}/directions/${directionId}`}>去写</Link>
            </p>
          ) : (
            <p>
              库里已有的直接打开。外面找到的可以放进这个方向。
            </p>
          )}
          {recErr ? <LoadFailed message={recErr} onRetry={() => runRecommend()} /> : null}
          <div className="fs-read-list">
            {(recs || []).length ? null : <p className="fs-muted">还没有找到外面的论文。先把这个方向在找什么写清楚。</p>}
            {(recs || []).map((row) => (
              <article key={row.doi || row.url || row.title} className="fs-read-row">
                <div>
                  {row.id || row.href ? (
                    <Link className="fs-lit-title" to={row.href || literatureHref(row.id, row.last_page_no, directionId ? { direction: directionId } : undefined)}>
                      {row.title}
                    </Link>
                  ) : (
                    <span className="fs-lit-title">{row.title}</span>
                  )}
                  <p className="fs-muted fs-lit-why">{[row.why, row.year].filter(Boolean).join(" · ") || "外面找到的"}</p>
                </div>
                <Button
                  size="small"
                  onClick={async () => {
                    if (row.already_in_library && row.id) {
                      nav(literatureHref(row.id, row.last_page_no, directionId ? { direction: directionId } : undefined));
                      return;
                    }
                    try {
                      await ingest(row);
                    } catch (e) {
                      message.error(formatError(e, "入库失败"));
                    }
                  }}
                >
                  {row.already_in_library ? "打开" : "放进这个方向"}
                </Button>
              </article>
            ))}
          </div>
        </Card>
      ) : null}
      {directionId && qParam && searchCounts && !searching ? (
        <p>库里 {searchCounts.local} 篇，外面又找到 {searchCounts.external} 篇。库里有的直接打开，外面的点入库。{searchCounts.version_note ? ` ${searchCounts.version_note}` : ""}</p>
      ) : null}
      {shelf === "out" && hits.length ? <p><b>外面找到的</b></p> : null}
      {shelf === "out" && qParam && !hits.length && !searching && !searchErr ? <SearchEmptyNext emptyNext={emptyNext} /> : null}
      {shelf === "out" && hits.length ? (
        <div className="fs-read-list">
          {hits.map((row) => (
            <article key={row.id || row.doi || row.url || row.title} className="fs-read-row">
              <div>
                {row.id || row.href ? (
                  <Link className="fs-lit-title" to={row.href || literatureHref(row.id, row.last_page_no, directionId ? { direction: directionId } : undefined)}>
                    {row.title}
                  </Link>
                ) : (
                  <span className="fs-lit-title">{row.title}</span>
                )}
                <p className="fs-muted fs-lit-why">
                  {[row.why, row.year, sourceLabel(row.source) || row.source].filter(Boolean).join(" · ") || "外面找到的"}
                </p>
              </div>
              <Button
                size="small"
                onClick={async () => {
                  if (row.already_in_library && row.id) {
                    nav(literatureHref(row.id, row.last_page_no, directionId ? { direction: directionId } : undefined));
                    return;
                  }
                  try {
                    await ingest(row);
                  } catch (e) {
                    message.error(formatError(e, "入库失败"));
                  }
                }}
              >
                {row.already_in_library ? "打开" : directionId ? "放进这个方向" : "打开"}
              </Button>
            </article>
          ))}
        </div>
      ) : null}
      {dirErr ? <LoadFailed message={dirErr} onRetry={loadDirs} /> : null}
      {isError ? <LoadFailed message={formatError(error, "文献列表加载失败")} onRetry={refetch} /> : null}
      <ModelKeyHint err={dirErr || (isError ? formatError(error, "文献列表加载失败") : "")} />
      {isLoading ? <PageLoading label="正在打开文献库…" /> : null}
      {!isLoading && !rows.length && data?.empty_kind === "library" && !data?.empty_next ? (
        <Alert
          className="fs-invite"
          type="info"
          showIcon
          message="还没有文献"
          description={
            <span>
              入库不需要配置模型。提炼方法和辅导才要用密钥。{" "}
              <Button type="link" onClick={() => setFilter("tab", "manual")}>
                入库
              </Button>
            </span>
          }
        />
      ) : null}
      <CreateDialog
        open={ingestOpen}
        onClose={() => setFilter("tab", undefined)}
        title="添加文献"
        width={720}
        footer={null}
      >
      <Tabs
        activeKey="manual"
        renderTabBar={() => null}
        items={[
          {
            key: "search",
            label: "检索入库",
            children: (
              <Space direction="vertical" style={{ width: "100%" }}>
                <Form
                  key={`tab-search-${qParam}-${langParam}-${sourcesKey}`}
                  layout="inline"
                  initialValues={{ q: qParam, language: langParam, sources: sourcesParam }}
                  onFinish={(v) => runSearch(v.q, v.sources, v.language)}
                >
                  <Form.Item name="q" rules={[{ required: true }]}>
                    <Input placeholder="检索词（会叠方向关键词）" style={{ width: 280 }} />
                  </Form.Item>
                  <Form.Item name="language">
                    <Select
                      style={{ width: 120 }}
                      options={[
                        { value: "zh", label: "中文优先" },
                        { value: "en", label: "英文源" },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="sources">
                    <Select
                      mode="multiple"
                      style={{ minWidth: 260 }}
                      placeholder="来源"
                      options={[...SEARCH_SOURCES, { value: "tavily", label: "Tavily 网页" }]}
                    />
                  </Form.Item>
                  <Button htmlType="submit" type="primary" loading={searching}>
                    检索
                  </Button>
                </Form>
                {rewritten ? <Alert type="info" showIcon message={`实际检索词：${rewritten}`} /> : null}
                {searchErr ? <LoadFailed message={searchErr} onRetry={() => setSearchTick((n) => n + 1)} /> : null}
                {leftoverHintBar(searchHints)}
                {directionId && qParam && searchCounts && !searching ? (
                  <p>库里 {searchCounts.local} 篇，外面又找到 {searchCounts.external} 篇。{searchCounts.version_note ? ` ${searchCounts.version_note}` : ""}</p>
                ) : null}
                {qParam && !hits.length && !searching && !searchErr ? <SearchEmptyNext emptyNext={emptyNext} /> : null}
                <Table
                  rowKey={(r) => r.doi || r.url || r.title}
                  dataSource={hits}
                  loading={searching}
                  locale={{ emptyText: <EmptyHint>输入检索词后显示外面的论文和本实验室库，重复的只留一篇。</EmptyHint> }}
                  columns={[
                    ...hitCols,
                    ...(directionId ? [{ title: "对上了哪些词", dataIndex: "why", render: (w) => w || "—" }] : []),
                  ]}
                />
              </Space>
            ),
          },
          {
            key: "recommend",
            label: "按方向推荐",
            children: (
              <Space direction="vertical" style={{ width: "100%" }}>
                <span className="fs-press-wrap" onClick={whenBlocked(!!dirErr, "方向列表没打开，先重试")}>
                <Button type="primary" loading={searching} disabled={!!dirErr} onClick={() => runRecommend()}>
                  按当前方向推荐未读
                </Button>
                </span>
                {dirErr ? <LoadFailed message={dirErr} onRetry={loadDirs} /> : null}
                {recErr ? <LoadFailed message={recErr} onRetry={() => runRecommend()} /> : null}
                <p className="fs-muted">
                  {directionId ? "按上方筛选的方向。" : "未选方向时按实验室全部方向。"}
                  {recMeta?.rerank === "lab_corpus"
                    ? ` 已按组里已有文献排过顺序。外面来源有 ${(recMeta.sources || []).map(sourceLabel).filter(Boolean).join(" / ") || "arXiv"}。`
                    : ""}
                  {recMeta?.llm_rerank ? " 已用模型微调外面论文的顺序。" : recMeta ? " 按组里已有文献排序。" : ""}
                  {recMeta?.embed_fallback === "hash" ? " 未接向量模型，用普通搜索。" : ""}
                  {recMeta?.query ? ` 检索词：${recMeta.query}` : ""}
                  {recMeta?.query_via === "direction"
                    ? " （检索来自方向词）"
                    : recMeta?.query_via === "titles"
                      ? " （检索来自已挂题名）"
                      : recMeta && !recMeta.query
                        ? " （未向外检索）"
                        : ""}
                  {recMeta?.query_fallback ? " （方向没有关键词，已用已挂题名回退）" : ""}
                  {recMeta?.cached ? " （这次用的是上次检索；改方向词后会重新检索）" : recMeta && recMeta.query ? " （这次是新检索）" : ""}
                </p>
                {teacher && (recMeta?.keyword_hints || []).length ? (
                  <p className="fs-dir-hints">
                    <span className="fs-muted">对不上的篇里反复出现{directionId ? "，点一下写进当前方向：" : "："}</span>
                    {directionId
                      ? recMeta.keyword_hints.map((w) => (
                          <Button
                            key={w}
                            size="small"
                            onClick={async () => {
                              const curDir = dirs.find((d) => d.id === directionId);
                              const cur = [...(curDir?.keywords || [])];
                              if (cur.includes(w)) return;
                              try {
                                await api.patchDirection(directionId, { keywords: [...cur, w] });
                                message.success("已写进当前方向");
                                setDirs((prev) =>
                                  prev.map((d) => (d.id === directionId ? { ...d, keywords: [...cur, w] } : d))
                                );
                                await runRecommend(true);
                              } catch (e) {
                                message.error(formatError(e, "无法更新关键词"));
                              }
                            }}
                          >
                            {w}
                          </Button>
                        ))
                      : (
                        <>
                          {(recMeta.keyword_hints || []).join("、")}
                          {" · "}
                          <Link to={`/labs/${id}/directions`}>去方向页写进关键词</Link>
                        </>
                      )}
                  </p>
                ) : null}
                {directionId && !(dirs.find((d) => d.id === directionId)?.keywords || []).length ? (
                  <p>先写这个方向在找什么。</p>
                ) : recMeta && (directionId || recMeta.query) ? (
                  <p>
                    库里 {recMeta.local_count || 0} 篇，外面又找到 {recMeta.external_count ?? recs.length} 篇。
                  </p>
                ) : null}
                {!recs.length && !labUnread.length && !labRelated.length && !recErr && recMeta?.empty_next ? (
                  <SearchEmptyNext emptyNext={recMeta.empty_next} />
                ) : null}
                <Table
                  rowKey={(r) => r.doi || r.url || r.title}
                  dataSource={recs}
                  loading={searching}
                  locale={{
                    emptyText: recErr ? (
                      <EmptyHint>推荐加载失败，可重试。</EmptyHint>
                    ) : teacher ? (
                      <EmptyHint
                        to={
                          recMeta?.empty_next?.steps?.[0]?.href ||
                          (directionId ? `/labs/${id}/directions/${directionId}` : `/labs/${id}/directions`)
                        }
                      >
                        {recMeta?.empty_next?.text || "还没有外面的推荐。先在方向里填写关键词。"}
                      </EmptyHint>
                    ) : (
                      <EmptyHint to={`/labs/${id}/literature?tab=search`}>
                        {recMeta?.empty_next?.text || "还没有外面的推荐。可去检索入库，关键词由导师填写。"}
                      </EmptyHint>
                    ),
                  }}
                  columns={[
                    ...hitCols,
                    { title: "对上了哪些词", dataIndex: "why", render: (w) => w || "—" },
                    {
                      title: "被引",
                      dataIndex: "citation_count",
                      render: (n) => (Number(n) > 0 ? n : "—"),
                    },
                    {
                      title: "刊物",
                      dataIndex: "journal",
                      render: (j) => j || "—",
                    },
                  ]}
                />
                <p className="fs-muted">本方向还没读</p>
                <Table
                  rowKey="id"
                  dataSource={labUnread}
                  pagination={false}
                  locale={{
                    emptyText: recErr ? (
                      <EmptyHint>本方向未读加载失败，可重试。</EmptyHint>
                    ) : (
                      <EmptyHint>本方向没有未读篇目，或尚未把文献挂到该方向。</EmptyHint>
                    ),
                  }}
                  columns={[
                    {
                      title: "标题",
                      dataIndex: "title",
                      render: (t, row) => (
                        <Link className="fs-lit-title" title={t || ""} to={row.href || literatureHref(row.id, row.last_page_no, directionId ? { direction: directionId } : undefined)}>
                          {t}
                        </Link>
                      ),
                    },
                    { title: "原因", dataIndex: "reason" },
                    { title: "匹配", dataIndex: "why" },
                    {
                      title: "全文",
                      render: (_, row) => (row.card_empty?.kind ? <PdfGapLink row={row} /> : "—"),
                    },
                    {
                      title: "方法",
                      dataIndex: "has_method_card",
                      render: (_, row) => (v ? "已写" : <AnalyzeChip row={row} />),
                    },
                  ]}
                />
                <p className="fs-muted">组内相关（未挂本方向）</p>
                <Table
                  rowKey="id"
                  dataSource={labRelated}
                  pagination={false}
                  locale={{
                    emptyText: recErr ? (
                      <EmptyHint>组内相关加载失败，可重试。</EmptyHint>
                    ) : (
                      <EmptyHint>没有和当前方向对得上、且还没挂上的已入库篇目。</EmptyHint>
                    ),
                  }}
                  columns={[
                    {
                      title: "标题",
                      dataIndex: "title",
                      render: (t, row) => (
                        <span>
                          <Link className="fs-lit-title" title={t || ""} to={row.href || literatureHref(row.id, row.last_page_no, directionId ? { direction: directionId } : undefined)}>
                            {t}
                          </Link>
                          {row.direction_suggestions?.length ? (
                            <details className="fs-inline-more" onClick={(e) => e.stopPropagation()}>
                              <summary>更多</summary>
                              <DirectionHangChips
                                suggestions={row.direction_suggestions}
                                busyId={recHangBusy.startsWith(`${row.id}:`) ? recHangBusy.slice(String(row.id).length + 1) : ""}
                                onHang={async (s) => {
                                  if (!row.id || !s?.id || recHangBusy) return;
                                  setRecHangBusy(`${row.id}:${s.id}`);
                                  try {
                                    const next = [...new Set([...(row.direction_ids || []), s.id])];
                                    await api.patchLiterature(row.id, { direction_ids: next });
                                    message.success("已挂到方向");
                                    await runRecommend(true);
                                  } catch (e) {
                                    message.error(formatError(e, "无法挂方向"));
                                  } finally {
                                    setRecHangBusy("");
                                  }
                                }}
                              />
                            </details>
                          ) : null}
                        </span>
                      ),
                    },
                    { title: "原因", dataIndex: "reason" },
                    { title: "匹配", dataIndex: "why" },
                    {
                      title: "全文",
                      render: (_, row) => (row.card_empty?.kind ? <PdfGapLink row={row} /> : "—"),
                    },
                    {
                      title: "方法",
                      render: (_, row) => (row.has_method_card ? "已写" : <AnalyzeChip row={row} />),
                    },
                    {
                      title: "",
                      render: (_, row) =>
                        directionId ? (
                          <Button
                            size="small"
                            onClick={async () => {
                              try {
                                const next = [...new Set([...(row.direction_ids || []), directionId])];
                                await api.patchLiterature(row.id, { direction_ids: next });
                                message.success("已挂到本方向");
                                const r = await api.recommendLiterature(id, directionId);
                                setRecs(r.data.items || []);
                                setLabUnread(r.data.lab_unread || []);
                                setLabRelated(r.data.lab_related || []);
                                setRecAuthors(r.data.authors || []);
                              } catch (e) {
                                message.error(formatError(e, "无法挂到方向"));
                              }
                            }}
                          >
                            挂到本方向
                          </Button>
                        ) : null,
                    },
                  ]}
                />
                <p className="fs-muted">该方向相关作者</p>
                <Table
                  rowKey="name"
                  dataSource={recAuthors}
                  pagination={false}
                  locale={{
                    emptyText: recErr ? (
                      <EmptyHint>作者表加载失败，可重试。</EmptyHint>
                    ) : (
                      <EmptyHint>还没有可聚合的作者。先把文献挂到方向，或检索入库带作者的篇目。</EmptyHint>
                    ),
                  }}
                  columns={[
                    {
                      title: "作者",
                      dataIndex: "name",
                      render: (name, row) => (
                        <Link to={row.href || `/labs/${id}/literature?tab=search&q=${encodeURIComponent(name || "")}`}>
                          {name}
                        </Link>
                      ),
                    },
                    { title: "组内篇数", dataIndex: "lab_count", render: (n) => n ?? 0 },
                    { title: "外面命中", dataIndex: "hit_count", render: (n) => n ?? 0 },
                    {
                      title: "原因",
                      dataIndex: "why",
                      render: (w, row) =>
                        row.sample_href && row.sample_title ? (
                          <Link to={row.sample_href}>{w || row.sample_title}</Link>
                        ) : (
                          w || "—"
                        ),
                    },
                    {
                      title: "",
                      render: (_, row) => (
                        <Button
                          size="small"
                          onClick={() => {
                            const next = new URLSearchParams(params);
                            next.set("tab", "search");
                            next.set("q", row.name || "");
                            setParams(next);
                          }}
                        >
                          检索入库
                        </Button>
                      ),
                    },
                  ]}
                />
              </Space>
            ),
          },
          {
            key: "manual",
            label: "手工 / 导入",
            children: (
              <Space direction="vertical" style={{ width: "100%" }}>
                <Upload.Dragger
                  multiple
                  accept=".pdf,.zip,application/pdf,application/zip"
                  showUploadList={false}
                  beforeUpload={ingestPdfs}
                >
                  <p>将多个 PDF，或一个装了 PDF 的压缩包，拖到此处入库（默认标为在读）</p>
                  <p className="fs-muted">扫描件无法抽文本时会标记失败，请换可复制 PDF。</p>
                </Upload.Dragger>
                <Form
                  form={ingestForm}
                  layout="vertical"
                  onFinish={async (v) => {
                    try {
                      await api.addLiterature(id, {
                        title: v.title,
                        authors: [v.author].filter(Boolean),
                        mark_read: true,
                        direction_ids: directionId ? [directionId] : [],
                      });
                      ingestForm.resetFields();
                      message.success("已入库并标为在读");
                      refetch();
                    } catch (e) {
                      message.error(formatError(e, "入库失败"));
                    }
                  }}
                >
                  <Form.Item name="title" label="标题" rules={[{ required: true, message: "请填写标题" }]}>
                    <Input placeholder="标题" />
                  </Form.Item>
                  <Form.Item name="author" label="作者">
                    <Input placeholder="作者" />
                  </Form.Item>
                  <Button htmlType="submit" type="primary">
                    录入并在读
                  </Button>
                </Form>
                <Form
                  form={ingestDoiForm}
                  layout="vertical"
                  onFinish={async (v) => {
                    try {
                      await api.importDoi(id, v.doi, dirExtra);
                      ingestDoiForm.resetFields();
                      message.success("已经放进组里。过一会儿就能问这篇在讲什么");
                      refetch();
                    } catch (e) {
                      message.error(formatError(e, "DOI 导入失败"));
                    }
                  }}
                >
                  <Form.Item name="doi" label="DOI" rules={[{ required: true, message: "请填写 DOI" }]}>
                    <Input placeholder="例如 10.1234/xxxx" />
                  </Form.Item>
                  <Button htmlType="submit">导入 DOI</Button>
                </Form>
                <Form
                  form={bibForm}
                  layout="vertical"
                  onFinish={async (v) => {
                    try {
                      await api.importBibtex(id, v.bibtex, dirExtra);
                      bibForm.resetFields();
                      message.success("已经放进组里。过一会儿就能问这些在讲什么");
                      refetch();
                    } catch (e) {
                      message.error(formatError(e, "BibTeX 导入失败"));
                    }
                  }}
                >
                  <Form.Item name="bibtex" label="BibTeX" rules={[{ required: true, message: "请粘贴 BibTeX" }]}>
                    <Input.TextArea rows={4} placeholder="粘贴 BibTeX" />
                  </Form.Item>
                  <Button htmlType="submit">导入 BibTeX</Button>
                </Form>
                <Form
                  form={csvForm}
                  layout="vertical"
                  onFinish={async (v) => {
                    try {
                      await api.importCsv(id, v.csv_text, dirExtra);
                      csvForm.resetFields();
                      message.success("已经放进组里。过一会儿就能问这些在讲什么");
                      refetch();
                    } catch (e) {
                      message.error(formatError(e, "CSV 导入失败"));
                    }
                  }}
                >
                  <Form.Item name="csv_text" label="CSV" rules={[{ required: true, message: "请粘贴 CSV" }]}>
                    <Input.TextArea rows={3} placeholder="CSV：title,authors,year,doi" />
                  </Form.Item>
                  <Button htmlType="submit">导入 CSV</Button>
                </Form>
                <p className="fs-hint">可选：用浏览器扩展从论文页导入。令牌在扩展选项页登录后自动取得，不要在本页粘贴。</p>
              </Space>
            ),
          },
        ]}
      />
      </CreateDialog>
      {bareLibrary ? null : (
      <div className="fs-filterbar">
        <div className="fs-lit-quick" role="tablist" aria-label="文献范围">
          {[
            ["all", "全部", !reading && !coreOnly],
            ["mine", "我在读", reading === "reading" && !readingScope && !readerId],
            ["lab", "组里在读", reading === "reading" && readingScope === "lab"],
            ["core", "核心", coreOnly && !reading],
            ["unread", "没人读", reading === "unread" && !readerId],
          ].map(([key, label, on]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={on}
              className={`fs-side-tab${on ? " is-on" : ""}`}
              onClick={() => {
                const next = new URLSearchParams(params);
                ["reading_status", "reading_scope", "reader_id", "core"].forEach((name) => next.delete(name));
                if (key === "mine") next.set("reading_status", "reading");
                if (key === "lab") {
                  next.set("reading_status", "reading");
                  next.set("reading_scope", "lab");
                }
                if (key === "core") next.set("core", "1");
                if (key === "unread") next.set("reading_status", "unread");
                setParams(next, { replace: true });
                setPage(1);
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <Select
          allowClear
          placeholder="方向"
          style={{ width: 180 }}
          value={directionId}
          onChange={(v) => setFilter("direction_id", v)}
          notFoundContent={<DirectionSelectEmpty labId={id} teacher={teacher} />}
          options={dirs.map((d) => ({
            value: d.id,
            label: d.parent_id ? `↳ ${d.name}` : d.includes_children || d.child_count ? `${d.name}（含小课题）` : d.name,
          }))}
        />
        <DirectionFilterHint labId={id} teacher={teacher} dirs={dirs} />
        {teacher && digest.data?.intake?.paused ? <span className="fs-muted">{digest.data.intake.paused}</span> : null}
        {authorId ? (
          <Tag closable onClose={() => setFilter("author", undefined)}>
            只看
            <button type="button" className="fs-link-btn" onClick={() => setAuthorOpen(authorId)}>
              {data?.author?.name || "这位作者"}
            </button>
            的论文
          </Tag>
        ) : null}
        {(() => {
          const chips = [
            directionId ? { k: "direction_id", t: (dirs.find((d) => d.id === directionId) || {}).name || "这个方向" } : null,
            year ? { k: "year", t: `${year} 年` } : null,
            reading ? { k: "reading_status", t: READ_STATUS[reading] || (reading === "unread" ? "没人在读" : reading) } : null,
            readLevel === "deep" ? { k: "read_level", t: "精读" } : readLevel === "full" ? { k: "read_level", t: "读过全文" } : readLevel === "abstract" ? { k: "read_level", t: "读过摘要" } : null,
            hasPdf ? { k: "has_pdf", t: "有全文" } : null,
            coreOnly ? { k: "core", t: "核心" } : null,
          ].filter(Boolean);
          if (!chips.length) return null;
          return chips.map((c) => (
            <Tag key={c.k} closable onClose={() => setFilter(c.k, undefined)}>
              {c.t}
            </Tag>
          ));
        })()}
        {readerErr ? (
          <LoadFailed
            message={readerErr}
            onRetry={() =>
              api
                .members(id)
                .then((r) => {
                  setReaders(asItems(r.data));
                  setReaderErr("");
                })
                .catch((e) => setReaderErr(formatError(e, "成员列表加载失败")))
            }
          />
        ) : null}
        <Collapse
          ghost
          className="fs-lit-more-filters"
          items={[
            {
              key: "more",
              label: "更多筛选",
              children: (
                <Space wrap>
                  {teacher ? (
                    <>
                      {digest.data ? (
                        <span className="fs-muted">
                          {digest.data.intake_line ||
                            `读过 ${digest.data.read || 0} 篇，还有 ${Math.max(0, (digest.data.total || 0) - (digest.data.read || 0))} 篇没读完。`}
                          {digest.data.note && !digest.data.intake?.paused ? digest.data.note : ""}
                        </span>
                      ) : null}
                      <Button
                        onClick={() =>
                          api.digestAll(id).then((r) => {
                            const n = r.data || {};
                            const line = `读完 ${n.read || 0} 篇，一共 ${n.total || 0} 篇`;
                            message.success(n.note ? `${line}。${n.note}` : line);
                            refetch();
                            digest.refetch();
                          }).catch((e) => message.error(formatError(e, "还没排上")))
                        }
                      >
                        把以前的论文也读一遍
                      </Button>
                    </>
                  ) : null}
                  <Select
                    allowClear
                    placeholder="在读状态"
                    style={{ width: 140 }}
                    value={reading}
                    onChange={(v) => setFilter("reading_status", v)}
                    options={[
                      { value: "unread", label: readerId ? "这人还没开始读" : "没人在读" },
                      ...Object.entries(READ_STATUS).map(([value, label]) => ({ value, label })),
                    ]}
                  />
                  <Select
                    allowClear
                    showSearch
                    optionFilterProp="label"
                    placeholder="读者"
                    style={{ width: 180 }}
                    value={readerId}
                    onChange={(v) => {
                      const next = new URLSearchParams(params);
                      if (v) {
                        next.set("reader_id", v);
                        if (!reading) next.set("reading_status", "unread");
                      } else next.delete("reader_id");
                      setParams(next, { replace: true });
                      setPage(1);
                    }}
                    options={readers.map((m) => ({ value: m.user_id, label: m.display_name || m.email || m.user_id }))}
                  />
                  <Button
                    onClick={async () => {
                      try {
                        const r = await api.exportBibtex(id, directionId);
                        saveBlob(r.data, "lab.bib");
                      } catch (e) {
                        message.error(formatError(e, "无法导出 BibTeX"));
                      }
                    }}
                  >
                    导出 BibTeX
                  </Button>
                  {pickCompare ? null : (
                    <Button onClick={() => setPickCompare(true)}>从名单里选几篇来对比</Button>
                  )}
                  <Input.Search
                    allowClear
                    placeholder="年份"
                    style={{ width: 110 }}
                    defaultValue={year}
                    onSearch={(v) => setFilter("year", v && /^\d{4}$/.test(String(v).trim()) ? String(v).trim() : undefined)}
                  />
                  <Select
                    allowClear
                    placeholder="全文"
                    style={{ width: 140 }}
                    value={hasPdf}
                    onChange={(v) => setFilter("has_pdf", v)}
                    options={[
                      { value: "0", label: "无 PDF" },
                      { value: "1", label: "有 PDF" },
                    ]}
                  />
                  <Select
                    allowClear
                    placeholder="这篇的方法"
                    style={{ width: 150 }}
                    value={hasCard}
                    onChange={(v) => setFilter("has_card", v)}
                    options={[
                      { value: "0", label: "还没写怎么做" },
                      { value: "1", label: "已经写了" },
                    ]}
                  />
                  <Select
                    allowClear
                    placeholder="被论文引用过"
                    style={{ width: 180 }}
                    value={citedPublished ? "cited" : undefined}
                    onChange={(v) => setFilter("cited_by_published", v === "cited" ? "1" : undefined)}
                    options={[{ value: "cited", label: "已发表论文引用" }]}
                  />
                  <Select
                    allowClear
                    showSearch
                    filterOption={false}
                    placeholder="作者"
                    style={{ width: 200 }}
                    value={authorId}
                    onFocus={() => {
                      if (!authorOpts.length) api.labAuthors(id, "", 20).then((r) => setAuthorOpts(r.data?.items || [])).catch(() => {});
                    }}
                    onSearch={(v) => api.labAuthors(id, v, 20).then((r) => setAuthorOpts(r.data?.items || [])).catch(() => {})}
                    onChange={(v) => setFilter("author", v)}
                    notFoundContent={<span className="fs-muted">输入名字找作者</span>}
                    options={[
                      ...(authorId && data?.author && !authorOpts.some((a) => a.id === authorId)
                        ? [{ value: authorId, label: data.author.name }]
                        : []),
                      ...authorOpts.map((a) => ({
                        value: a.id,
                        label: `${a.name} · ${a.paper_count || 0} 篇${(a.affiliations || [])[0] ? ` · ${a.affiliations[0].slice(0, 16)}` : ""}`,
                      })),
                    ]}
                  />
                  {teacher ? (
                  <Select
                    allowClear
                    placeholder="助手读到哪"
                    style={{ width: 150 }}
                    value={readLevel}
                    onChange={(v) => setFilter("read_level", v)}
                    options={[
                      { value: "none", label: "还没读" },
                      { value: "abstract", label: "只读了摘要" },
                      { value: "full", label: "读过全文" },
                      { value: "deep", label: "精读过" },
                    ]}
                  />
                  ) : null}
                  <Select
                    allowClear
                    placeholder="被引次数"
                    style={{ width: 130 }}
                    value={minCited}
                    onChange={(v) => setFilter("min_cited", v)}
                    options={[
                      { value: "10", label: "被引 10 次以上" },
                      { value: "50", label: "被引 50 次以上" },
                      { value: "200", label: "被引 200 次以上" },
                    ]}
                  />
                  <Select
                    allowClear
                    placeholder="排序：最新进来"
                    style={{ width: 150 }}
                    value={sortBy}
                    onChange={(v) => setFilter("sort", v)}
                    options={[
                      { value: "cited", label: "被引最多的在前" },
                      { value: "year", label: "年份新的在前" },
                    ]}
                  />
                  <Select
                    allowClear
                    placeholder="复现"
                    style={{ width: 160 }}
                    value={reproduce}
                    onChange={(v) => setFilter("reproduce", v)}
                    options={[
                      { value: "measured", label: "已经跑出结果" },
                      { value: "not_run", label: "还没跑出结果" },
                      { value: "blocked", label: "缺数据" },
                      { value: "awaiting", label: "在等机器" },
                      { value: "none", label: "还没开始复现" },
                    ]}
                  />
                </Space>
              ),
            },
          ]}
        />
      </div>
      )}
      {compareOut || compareItems.length || compareJson ? (
        <Card title="对比结果">
          <p className="fs-muted">可以写进方向说明，或写进论文的相关工作。</p>
          {compareItems.length ? (
            <div className="fs-compare-items">
              {compareItems.map((it) => {
                const lid = it.id || it.literature_item_id;
                return (
                  <div key={lid} className="fs-compare-item">
                    <p>
                      <Link to={it.href || literatureHref(lid, it.last_page_no)}>
                        {it.title || "打开文献"}
                      </Link>
                      {it.card_quality?.weak ? <span className="fs-muted"> · 只根据摘要</span> : null}
                      <PdfGapLink row={it} /> <AnalyzeChip row={it} />
                    </p>
                    <ReproduceChip
                      ev={it.reproduce || it.latest_reproduce}
                      to={(it.reproduce || it.latest_reproduce)?.href || it.reproduce_href || `/labs/${id}/reproduces?literature_item_id=${encodeURIComponent(lid)}`}
                    />
                  </div>
                );
              })}
            </div>
          ) : null}
          <AskSourceChips
            citations={(compareItems || []).map((it, i) => ({
              label: `S${i + 1}`,
              title: it.title || "文献",
              href: it.href || (it.id ? literatureHref(it.id, it.last_page_no) : ""),
              item_id: it.id,
              literature_item_id: it.id,
              kind: "literature",
              kind_label: "文献",
              excerpt: it.why || it.abstract || "",
            }))}
          />
          <UnusedResearchAlert note={compareUnused} />
          {compareNote ? <p className="fs-muted">{compareNote}</p> : null}
          <AiResultBar
            text={compareGap || compareOut}
            copyLabel="复制短综述"
            busy={comparing}
            traceId={compareTrace}
            steps={compareSteps}
            degraded={compareDegraded}
            availableTools={compareTools}
            followups={compareFollowups}
            retryLabel="再对比所选"
            onRetry={selected.length >= 2 ? () => document.getElementById("fs-compare-run")?.click() : undefined}
          />
          {compareGap ? (
            <div className="fs-compare-card" style={{ marginTop: 12 }}>
              <p className="fs-compare-label">可写回短综述</p>
              <MdPreview text={compareGap} literatureId={(compareItems[0] || {}).id} />
            </div>
          ) : (
            <SearchEmptyNext emptyNext={compareEmpty || (compareNext ? {
              title: "这次没有写出可写回的短综述",
              text: compareNext.label,
              steps: [{ key: compareNext.kind || "next", label: compareNext.label, href: compareNext.href }],
            } : null)} />
          )}
          {compareRepro ? (
            <div className="fs-compare-card" style={{ marginTop: 12 }}>
              <p className="fs-compare-label">组内复现差距（单独一块，不是声称对照表）</p>
              <MdPreview text={compareRepro} literatureId={(compareItems[0] || {}).id} />
            </div>
          ) : null}
          {compareOut && compareOut !== compareGap ? (
            <div className="fs-compare-card" style={{ marginTop: 12 }}>
              <p className="fs-compare-label">对照表</p>
              <MdPreview text={compareOut} literatureId={(compareItems[0] || {}).id} />
            </div>
          ) : null}
          {compareJson && typeof compareJson === "object"
            ? ["methods", "data", "metrics", "claims"].map((k) => {
                const rows = compareJson[k];
                if (!Array.isArray(rows) || !rows.length) return null;
                return (
                  <div key={k} style={{ marginTop: 12 }}>
                    <p>
                      <b>{{ methods: "方法", data: "数据", metrics: "指标", claims: "主张" }[k]}</b>
                    </p>
                    <div className="fs-compare-items">
                      {rows.map((row, i) => (
                        <div key={i} className="fs-compare-item">
                          <p>
                            {typeof row === "string" ? row : row.text || row.claim || JSON.stringify(row)}
                            {row.item_id || row.literature_item_id ? (
                              <>
                                {" "}
                                <Link to={row.href || literatureHref(row.item_id || row.literature_item_id, row.page_no || row.last_page_no)}>来源</Link>
                              </>
                            ) : null}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })
            : null}
          <Space wrap style={{ marginTop: 12 }}>
            {teacher ? (
              <>
                <Select
                  allowClear
                  placeholder="写入方向说明"
                  style={{ width: 200 }}
                  value={writeDir}
                  onChange={setWriteDir}
                  options={dirs.map((d) => ({ value: d.id, label: d.name }))}
                />
                <span className="fs-press-wrap" onClick={whenBlocked(!writeDir || (!compareGap && !compareRepro), !writeDir ? "先选一个方向" : "先对比所选")}>
                <Button
                  disabled={!writeDir || (!compareGap && !compareRepro)}
                  onClick={async () => {
                    try {
                      const note = [
                        compareGap ? `## 文献对比与差距\n\n${compareGap}` : "",
                        compareRepro ? `## 组内复现差距\n\n${compareRepro}` : "",
                      ]
                        .filter(Boolean)
                        .join("\n\n");
                      await api.patchDirection(writeDir, { append_note: note });
                      message.success("已写入方向说明（相同内容不会重复追加）");
                    } catch (e) {
                      message.error(formatError(e, "无法写入方向"));
                    }
                  }}
                >
                  写入方向说明
                </Button>
                </span>
              </>
            ) : null}
            <Select
              allowClear
              placeholder="写入论文"
              style={{ width: 200 }}
              value={writeMs}
              onChange={setWriteMs}
              options={msList.filter((m) => m.owner_id === me?.id).map((m) => ({ value: m.id, label: m.title }))}
            />
            {msErr ? (
              <LoadFailed
                message={msErr}
                onRetry={() =>
                  api
                    .manuscripts(id)
                    .then((r) => {
                      setMsList(asItems(r.data));
                      setMsErr("");
                    })
                    .catch((e) => setMsErr(formatError(e, "论文列表加载失败")))
                }
              />
            ) : null}
            <span className="fs-press-wrap" onClick={whenBlocked(writeBusy || !writeMs || (!compareRw && !compareGap && !compareRepro), !writeMs ? "先选论文" : "先对比所选")}>
            <Button
              type="primary"
              disabled={writeBusy || !writeMs || (!compareRw && !compareGap && !compareRepro)}
              onClick={async () => {
                if (writeBusy) return;
                setWriteBusy(true);
                try {
                  const ms = await api.getManuscript(writeMs);
                  const text = [compareRw || compareGap, compareRepro].filter(Boolean).join("\n\n");
                  const r = await api.writeback(id, {
                    manuscript_id: writeMs,
                    text,
                    write_mode: "append_heading",
                    heading: "相关工作",
                    expected_updated_at: ms.data.updated_at,
                    literature_item_ids: (compareItems || []).map((it) => it.id || it.literature_item_id).filter(Boolean),
                  });
                  const cov = r.data.core_coverage || {};
                  const named =
                    cov.core_total > 0
                      ? `已点名 ${cov.core_mentioned_count || 0} / ${cov.core_total} 篇核心文献`
                      : "";
                  setWbHint([r.data.writeback?.summary || "已写入相关工作", named].filter(Boolean).join(" · "));
                  setWbHref(manuscriptSectionHref(writeMs, "相关工作"));
                  setWbNext(r.data.next_step || null);
                  message.success(r.data.writeback?.summary || "相关工作已写入论文");
                } catch (e) {
                  message.error(formatError(e, e?.response?.status === 409 ? "这篇刚被改过，请刷新后再写回" : "无法写入论文"));
                } finally {
                  setWriteBusy(false);
                }
              }}
            >
              写回相关工作
            </Button>
            </span>
            <span className="fs-press-wrap" onClick={whenBlocked(writeBusy || !writeMs || (!compareGap && !compareRepro), !writeMs ? "先选论文" : "先对比所选")}>
            <Button
              disabled={writeBusy || !writeMs || (!compareGap && !compareRepro)}
              onClick={async () => {
                if (writeBusy) return;
                setWriteBusy(true);
                try {
                  const ms = await api.getManuscript(writeMs);
                  const text = [compareGap, compareRepro].filter(Boolean).join("\n\n");
                  const r = await api.writeback(id, {
                    manuscript_id: writeMs,
                    text,
                    write_mode: "append_heading",
                    heading: compareGap ? "文献对比与差距" : "组内复现差距",
                    expected_updated_at: ms.data.updated_at,
                  });
                  setWbHint(r.data.writeback?.summary || "已写入论文");
                  setWbHref(manuscriptSectionHref(writeMs, compareGap ? "文献对比与差距" : "组内复现差距"));
                  setWbNext(r.data.next_step || null);
                  message.success(r.data.writeback?.summary || "已写入论文");
                } catch (e) {
                  message.error(formatError(e, e?.response?.status === 409 ? "这篇刚被改过，请刷新后再写回" : "无法写入论文"));
                } finally {
                  setWriteBusy(false);
                }
              }}
            >
              写入对比节
            </Button>
            </span>
          </Space>
          {wbHint ? <WritebackSuccess summary={wbHint} href={wbHref} next={wbNext} /> : null}
        </Card>
      ) : null}
      {!rows.length && data?.empty_next ? <SearchEmptyNext emptyNext={data.empty_next} /> : null}
      {pickCompare ? (
        <div className="fs-lit-compare-bar fs-float-bar">
          <span>已选 {selected.length} 篇</span>
          {comparing ? (
            <Button onClick={() => compareAbort.current?.abort()}>停止对比</Button>
          ) : (
            <span className="fs-press-wrap" onClick={whenBlocked(selected.length < 2, "至少选两篇再对比")}>
              <Button
                id="fs-compare-run"
                type="primary"
                disabled={selected.length < 2}
                onClick={async () => {
                  setComparing(true);
                  setCompareUnused(null);
                  setCompareNote("");
                  const ac = new AbortController();
                  compareAbort.current = ac;
                  try {
                    const r = await api.compare(id, selected, { signal: ac.signal });
                    setCompareOut(r.data.table_md || "");
                    setCompareGap(r.data.gap_md || "");
                    setCompareRw(r.data.related_work_md || r.data.gap_md || "");
                    setCompareRepro(r.data.reproduce_gap_md || "");
                    setCompareItems(r.data.items || selected.map((sid) => ({ id: sid })));
                    setCompareJson(r.data.json || null);
                    setCompareNext(r.data.next_step || null);
                    setCompareEmpty(r.data.empty_next || null);
                    setCompareUnused(r.data.unused_research || null);
                    if (r.data.trace_id || r.data.session_id) setCompareTrace(r.data.trace_id || r.data.session_id);
                    setCompareSteps(r.data.steps || []);
                    setCompareDegraded(r.data.degraded || r.data.degraded_to || "");
                    setCompareTools(r.data.available_tools || []);
                    setCompareFollowups(r.data.followups || []);
                  } catch (e) {
                    if (e?.name === "AbortError" || e?.code === "ERR_CANCELED") return;
                    message.error(formatError(e, "对比失败"));
                  } finally {
                    setComparing(false);
                    compareAbort.current = null;
                  }
                }}
              >
                对比所选
              </Button>
            </span>
          )}
          <Button
            onClick={() => {
              setPickCompare(false);
              setSelected([]);
            }}
          >
            取消
          </Button>
        </div>
      ) : null}
      <Table
        className={`fs-click-row fs-read-table${!isLoading && !rows.length ? " is-empty" : ""}`}
        showHeader={false}
        rowKey="id"
        rowSelection={pickCompare ? { selectedRowKeys: selected, onChange: setSelected } : undefined}
        dataSource={rows}
        locale={{ emptyText: rows.length ? null : <span className="fs-muted">入库后会出现在这里。</span> }}
        pagination={{
          current: page,
          pageSize: 20,
          total,
          onChange: setPage,
          showTotal: (n) => `共 ${n} 篇`,
        }}
        onRow={(row) => ({ onClick: () => nav(row.href || literatureHref(row.id, row.last_page_no, directionId ? { direction: directionId } : undefined)) })}
        columns={[
          {
            title: "标题",
            dataIndex: "title",
            render: (t, row) => {
              const names = (row.direction_ids || [])
                .map((did) => (dirs.find((d) => d.id === did) || {}).name)
                .filter(Boolean);
              const why = [
                row.last_page_no ? `读到第 ${row.last_page_no} 页` : "",
                names[0] ? shortName(names[0]) : "",
                names.length > 1 ? `+${names.length - 1}` : "",
                row.publish_year || "",
                row.cited_by_count ? `被引 ${row.cited_by_count}` : "",
                row.read_level === 3
                  ? "精读过"
                  : row.digest_state === "read"
                  ? "读过了"
                  : row.digest_state === "abstract_only"
                    ? "只看到摘要"
                    : row.digest_state === "unreadable"
                      ? "这份打不开"
                      : row.digest_state === "waiting_budget"
                        ? "这个月先不读"
                        : row.digest_state === "queued"
                          ? "还在读"
                          : "",
              ].filter(Boolean).join(" · ");
              return (
                <span className="fs-paper-row">
                  <span className="fs-paper-main">
                    <Link className="fs-lit-title" title={t || ""} to={row.href || literatureHref(row.id, row.last_page_no, directionId ? { direction: directionId } : undefined)} onClick={(e) => e.stopPropagation()}>
                      {t}
                    </Link>
                    {["reading", "want_read", "finished", "dropped"].includes(row.my_reading) ? (
                      <span className="fs-lit-read" onClick={(e) => e.stopPropagation()}>
                        <ReadTag status={row.my_reading} />
                      </span>
                    ) : null}
                    {row.lab_reader_total ? (
                      <span className="fs-muted">
                        组里在读：
                        <LabReaderBits itemId={row.id} names={row.lab_readers} marks={row.lab_reader_marks} />
                        {row.lab_reader_total > (row.lab_readers || []).length ? ` +${row.lab_reader_total - row.lab_readers.length}` : ""}
                      </span>
                    ) : null}
                    {(row.author_links || []).length ? (
                      <span className="fs-lit-authors" onClick={(e) => e.stopPropagation()}>
                        {row.author_links.slice(0, 4).map((p, n) => (
                          <button key={`${p.author_id}-${n}`} type="button" className="fs-link-btn" onClick={() => setAuthorOpen(p.author_id)}>
                            {p.name}
                          </button>
                        ))}
                        {row.author_links.length > 4 ? <span className="fs-muted">等</span> : null}
                      </span>
                    ) : null}
                    {why ? <span className="fs-muted fs-lit-why">{why}</span> : null}
                  </span>
                  {row.progress_pct ? (
                    <span className="fs-meter" title={`读到 ${Math.round(row.progress_pct)}%`}>
                      <i style={{ width: `${Math.min(100, Math.round(row.progress_pct))}%` }} />
                    </span>
                  ) : null}
                </span>
              );
            },
          },
          {
            title: "方向",
            width: 140,
            className: "fs-col-fold",
            render: (_, row) => {
              const names = (row.direction_ids || [])
                .map((did) => (dirs.find((d) => d.id === did) || {}).name)
                .filter(Boolean);
              return names.length ? names.join("、") : "—";
            },
          },
          { title: "年份", dataIndex: "publish_year", className: "fs-col-fold" },
          { title: "DOI", dataIndex: "doi", className: "fs-col-fold" },
          {
            title: "全文",
            dataIndex: "has_pdf",
            className: "fs-col-fold",
            render: (v, row) => {
              const kind = row.card_empty?.kind;
              const label =
                kind === "running"
                  ? "正在提炼"
                  : kind === "unusable"
                    ? "扫描件"
                    : kind === "abstract_only"
                      ? row.has_pdf
                        ? "仅摘要"
                        : "无全文"
                      : kind === "ungrounded"
                        ? "摘录待核对"
                        : v
                          ? "有 PDF"
                          : "无全文";
              return (
                <span onClick={(e) => e.stopPropagation()}>
                  <Link to={row.card_href || `/literature/${row.id}?panel=card`}>{label}</Link>
                </span>
              );
            },
          },
          ...(directionId
            ? [
                {
                  title: "核心",
                  dataIndex: "is_core",
                  width: 88,
                  className: "fs-col-fold",
                  render: (v, row) =>
                    teacher ? (
                      <Button
                        size="small"
                        type={v ? "primary" : "default"}
                        onClick={async (e) => {
                          e.stopPropagation();
                          try {
                            await api.pinLiteratureCore(id, row.id, { direction_id: directionId, is_core: !v });
                            message.success(v ? "已取消核心" : "已钉为核心");
                            refetch();
                          } catch (err) {
                            message.error(formatError(err, "无法钉核心"));
                          }
                        }}
                      >
                        {v ? "已钉" : "钉为核心"}
                      </Button>
                    ) : v ? (
                      <span className="fs-muted">核心</span>
                    ) : (
                      "—"
                    ),
                },
              ]
            : []),
          {
            title: "在读",
            dataIndex: "my_reading",
            className: "fs-col-fold",
            render: (s, row) => (
              <>
                <ReadTag status={s} />
                {row.progress_pct ? <span className="fs-muted"> {Math.round(row.progress_pct)}%</span> : null}
              </>
            ),
          },
          {
            title: "",
            width: 120,
            className: "fs-row-actions",
            render: (_, row) => (
              <span onClick={(e) => e.stopPropagation()}>
                {(() => {
                  const inDir = directionId && (row.direction_ids || []).includes(directionId);
                  if (!row.has_method_card) {
                    return <Link to={row.card_href || `/literature/${row.id}?panel=card`}>提炼方法</Link>;
                  }
                  if (directionId && !inDir) {
                    return (
                      <Button
                        type="link"
                        size="small"
                        onClick={async () => {
                          try {
                            const next = [...new Set([...(row.direction_ids || []), directionId])];
                            await api.patchLiterature(row.id, { direction_ids: next });
                            message.success("已放进这个方向");
                            refetch();
                          } catch (err) {
                            message.error(formatError(err, "没放进这个方向"));
                          }
                        }}
                      >
                        放进这个方向
                      </Button>
                    );
                  }
                  const did = directionId || (row.direction_ids || [])[0];
                  return did ? (
                    <Link to={literatureHref(row.id, row.last_page_no, { panel: "tutor", direction: did })}>问这篇</Link>
                  ) : (
                    <Link to={`/literature/${row.id}?panel=card`}>这篇的方法</Link>
                  );
                })()}
                <RowMore
                  items={[
                    {
                      key: "repro",
                      label: row.latest_reproduce ? "查看复现" : "去复现",
                      to: row.latest_reproduce?.href || row.reproduce_href || `/labs/${id}/reproduces?literature_item_id=${encodeURIComponent(row.id)}`,
                    },
                    row.card_href || row.id
                      ? { key: "card", label: "这篇的方法", to: row.card_href || `/literature/${row.id}?panel=card` }
                      : null,
                    {
                      key: "del",
                      label: "删除",
                      onClick: () => {
                        Modal.confirm({
                          title: "删除该文献？",
                          onOk: async () => {
                            try {
                              await api.deleteLiterature(row.id);
                              message.success("已删除");
                              refetch();
                            } catch (err) {
                              message.error(formatError(err, "无法删除"));
                            }
                          },
                        });
                      },
                    },
                  ]}
                />
              </span>
            ),
          },
        ]}
      />
      <AuthorDrawer
        labId={id}
        authorId={authorOpen}
        me={me}
        teacher={teacher}
        onClose={() => setAuthorOpen("")}
        onOpenAuthor={setAuthorOpen}
        onOnlyThis={(a) => {
          setAuthorOpen("");
          setFilter("author", a.id);
        }}
      />
    </Space>
  );
}
