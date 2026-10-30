import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api.js";
import { formatError } from "../../err.js";

/** 文献主体 + 辅导历史。页码不进这里，翻页不该重取整篇。 */
export function useReaderDoc(id, { session } = {}) {
  const [doc, setDoc] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [tutor, setTutor] = useState({ items: [], more: false, page: 1, total: 0, sessions: [] });

  const load = useCallback(
    (opts = {}) => {
      if (!id) return Promise.resolve();
      const sid = opts.session !== undefined ? opts.session : session || "";
      const tpage = opts.tutorPage || 1;
      const append = tpage > 1;
      const include = opts.include || "core";
      return api
        .literatureDetail(id, {
          session: sid || undefined,
          tutor_page: tpage,
          tutor_page_size: 8,
          include,
        })
        .then((r) => {
          setDoc((prev) => {
            if (!prev || include === "tutor" || include === "all") return r.data;
            return {
              ...prev,
              ...r.data,
              tutor_history: prev.tutor_history,
              tutor_sessions: prev.tutor_sessions,
            };
          });
          setLoadError("");
          if (include === "tutor" || include === "all") {
            const hist = r.data.tutor_history || {};
            const items = [...(hist.items || [])].reverse();
            setTutor((prev) => ({
              items: append ? [...items, ...prev.items] : items,
              more: !!hist.has_more,
              page: hist.page || tpage,
              total: hist.total || items.length,
              sessions: r.data.tutor_sessions || [],
            }));
          }
          return r.data;
        })
        .catch((e) => {
          setLoadError(formatError(e, "文献加载失败"));
        });
    },
    [id, session]
  );

  useEffect(() => {
    if (!id) {
      setLoadError("文献不存在");
      return;
    }
    setDoc(null);
    setTutor({ items: [], more: false, page: 1, total: 0, sessions: [] });
    load({ session: session || "" });
    // 换文献才重取；会话切换由 applyTutorSession 显式触发。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const analysis = useMemo(() => {
    const analyses = doc?.analyses || [];
    const cards = analyses.filter((a) => a.analysis_type === "method_card");
    const running = cards.find((a) => a.is_running || a.result?.status === "running");
    const written = (row) => {
      const result = row?.result;
      if (!result || result.status === "running") return false;
      const text = JSON.stringify(result.plain || result.problem || result.methods || result.claims || "");
      return text.length > 12;
    };
    const card = cards.find((a) => a.is_best && written(a)) || cards.find(written) || running || cards[0] || analyses.find((a) => a.result && (a.result.methods || a.result.source));
    const result = card?.result;
    if (!result && doc?.analysis_status !== "running") return null;
    return {
      ...(result || {}),
      status: running && !written(card) ? "running" : result?.status === "running" ? "" : result?.status || doc?.analysis_status || "",
      upgrading: Boolean(running && written(card)),
      card_quality: card?.card_quality,
      session_id: card?.session_id || result?.session_id,
      trace_id: card?.trace_id || result?.trace_id,
      steps: card?.steps || result?.steps,
      followups: card?.followups || result?.followups,
      degraded: card?.degraded || result?.degraded,
      available_tools: card?.available_tools || result?.available_tools,
    };
  }, [doc]);

  return { doc, setDoc, analysis, loadError, load, tutor, setTutor };
}

/** 当前页文本。缓存命中后翻页就是一次索引查询，别再拉整篇详情。 */
export function usePageText(id, page, { enabled = true, pages = 0 } = {}) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["literature-page-text", id, page],
    queryFn: () => api.literatureDetail(id, { page, include: "page_text" }).then((r) => r.data),
    enabled: !!id && !!page && enabled,
    staleTime: 10 * 60 * 1000,
  });
  useEffect(() => {
    if (!enabled || !id || !page) return undefined;
    for (const p of [page - 1, page + 1]) {
      if (p < 1 || (pages && p > pages)) continue;
      qc.prefetchQuery({
        queryKey: ["literature-page-text", id, p],
        queryFn: () => api.literatureDetail(id, { page: p, include: "page_text" }).then((r) => r.data),
        staleTime: 10 * 60 * 1000,
      });
    }
    return undefined;
  }, [enabled, id, page, pages, qc]);
  return {
    pageText: data?.page_text || "",
    pageTextMissing: Boolean(data?.page_text_missing),
    ready: !!data,
  };
}

/** 章节与图表树。没有 GROBID 结果时如实为空，不造假目录。 */
export function useStructure(id, { enabled = true } = {}) {
  const { data } = useQuery({
    queryKey: ["literature-structure", id],
    queryFn: () => api.literatureStructure(id).then((r) => r.data),
    enabled: !!id && enabled,
    staleTime: 30 * 60 * 1000,
  });
  return data || { sections: [], figures: [], has_structure: false, next_step: "" };
}

/** 术语表。跟着实验室走，不跟着页码走。 */
export function useGlossary(labId) {
  const { data } = useQuery({
    queryKey: ["glossary", labId],
    queryFn: () => api.glossary(labId).then((r) => (Array.isArray(r.data) ? r.data : r.data?.items || [])),
    enabled: !!labId,
    staleTime: 10 * 60 * 1000,
  });
  return data || [];
}
