import { useCallback, useEffect, useRef, useState } from "react";
import { message } from "antd";
import { api } from "../../api.js";
import { formatError } from "../../err.js";
import { visibleTranslation } from "../../pdfText.js";

/**
 * 本页中文。先问服务端缓存（换浏览器也在、不烧配额），未命中才流式译并回存。
 * 旧的 sessionStorage 只留作同一标签页内的兜底。
 */
export function usePageTranslation({ id, labId, page, source, enabled }) {
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const [progress, setProgress] = useState(null);
  const [stale, setStale] = useState(false);
  const [busy, setBusy] = useState(false);
  const [nonce, setNonce] = useState(0);
  const abortRef = useRef(null);
  const accRef = useRef("");

  const stop = useCallback(() => abortRef.current?.abort(), []);
  const retry = useCallback(() => {
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!enabled || !labId || !id || !page) return undefined;
    if (!source?.ok) {
      setText("");
      setStale(false);
      setErr("这一页还没有可复制文字。请换能选中字的 PDF。");
      return undefined;
    }
    let cancelled = false;
    const ac = new AbortController();
    abortRef.current?.abort();
    abortRef.current = ac;
    setErr("");
    setProgress(null);

    const run = async () => {
      accRef.current = "";
      if (nonce === 0) {
        try {
          const hit = await api.pageTranslation(id, page, "zh");
          if (cancelled) return;
          if (hit.data?.hit && hit.data.text) {
            const vis = visibleTranslation(hit.data.text);
            if (vis) {
              setText(vis);
              setStale(false);
              if (vis !== hit.data.text) {
                api.savePageTranslation(id, { page_no: page, lang: "zh", text: vis }).catch(() => {});
              }
              return;
            }
          }
        } catch {
          /* 缓存读不到就照常译 */
        }
      }
      if (cancelled) return;
      setStale(true);
      setBusy(true);
      const watchdog = window.setTimeout(() => {
        if (cancelled) return;
        ac.abort();
        setBusy(false);
        setStale(false);
        setProgress(null);
        setErr("翻译超时，请再点「译这一页」。");
      }, 90000);
      try {
        const r = await api.translate(
          labId,
          source.text,
          "zh",
          { literature_item_id: id, page_no: page, stream: true },
          {
            signal: ac.signal,
            onDelta: (_d, acc, meta) => {
              if (cancelled) return;
              accRef.current = acc;
              const vis = visibleTranslation(acc);
              if (vis) {
                setText(vis);
                setStale(false);
              }
              if (meta?.chunks || meta?.retrying) {
                setProgress({ chunk: meta.chunk || 1, chunks: meta.chunks, retrying: !!meta.retrying });
              }
            },
          }
        );
        if (cancelled) return;
        const zh = r.data.text || visibleTranslation(accRef.current) || "";
        setText(zh);
        setStale(false);
        setProgress(null);
        if (zh) api.savePageTranslation(id, { page_no: page, lang: "zh", text: zh }).catch(() => {});
        if (r.data.quota_warning) message.warning(r.data.quota_warning);
      } catch (e) {
        if (cancelled || e?.name === "AbortError" || e?.code === "ERR_CANCELED") return;
        const keep = visibleTranslation(e?.partial || accRef.current);
        if (keep) setText(keep);
        setStale(false);
        setProgress(null);
        setErr(
          keep
            ? "模型夹带了说明。已尽量只留下译文，请对照原文。可再点「译这一页」。"
            : formatError(e, "模型没有给出译文，请再点「译这一页」。")
        );
      } finally {
        window.clearTimeout(watchdog);
        if (!cancelled) setBusy(false);
      }
    };
    run();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [enabled, id, labId, page, source?.text, source?.ok, nonce]);

  useEffect(() => {
    setText("");
    setErr("");
  }, [page]);

  return { text, err, progress, stale, busy, stop, retry };
}
