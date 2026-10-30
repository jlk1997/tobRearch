import { useCallback, useEffect, useState } from "react";
import { api } from "../../api.js";
import { formatError } from "../../err.js";
import { getLastSel, getSelZh, setLastSel, setSelZh, surroundingContext, visibleTranslation } from "../../pdfText.js";

/** 选区弹层：译这几句、改措辞，以及给笔记用的锚点。 */
export function useSelection({ id, labId, page, pageText }) {
  const [pick, setPick] = useState(null);
  const [kept, setKept] = useState(null);

  useEffect(() => {
    setKept(getLastSel(id));
  }, [id]);
  useEffect(() => {
    setPick(null);
  }, [page]);
  useEffect(() => {
    if (!pick) return undefined;
    const close = (e) => {
      if (e.target?.closest?.(".fs-sel-zh")) return;
      setPick(null);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setPick(null);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [pick]);

  const remember = useCallback(
    (text, anchor) => {
      const pack = { text, page, context: surroundingContext(pageText, text), anchor: anchor || null };
      setKept(pack);
      setLastSel(id, pack);
      return pack;
    },
    [id, page, pageText]
  );

  const translatePick = useCallback(
    (text, x, y, force = false, anchor = null) => {
      if (!labId || !text) return;
      const cached = force ? "" : getSelZh(id, page, text);
      setPick((cur) => ({ text, x, y, anchor: anchor || cur?.anchor || null, zh: cached, err: "", busy: !cached }));
      if (cached && !force) return;
      api
        .translate(
          labId,
          text,
          "zh",
          { literature_item_id: id, page_no: page, stream: true, context: surroundingContext(pageText, text) },
          {
            onDelta: (_d, acc) =>
              setPick((cur) => {
                if (!cur || cur.text !== text) return cur;
                const vis = visibleTranslation(acc);
                return vis ? { ...cur, zh: vis } : cur;
              }),
          }
        )
        .then((r) => {
          const zh = r.data.text || "";
          setSelZh(id, page, text, zh);
          setPick((cur) => (cur && cur.text === text ? { ...cur, zh, busy: false } : cur));
        })
        .catch((e) => {
          if (e?.name === "AbortError" || e?.code === "ERR_CANCELED") return;
          const keep = visibleTranslation(e?.partial || "");
          setPick((cur) =>
            cur && cur.text === text
              ? {
                  ...cur,
                  zh: keep || cur.zh,
                  busy: false,
                  err: keep ? "模型夹带了说明。已尽量只留下译文。" : formatError(e, "选区未能译出"),
                }
              : cur
          );
        });
    },
    [id, labId, page, pageText]
  );

  const polishPick = useCallback(() => {
    if (!labId || !pick?.text || pick.polishBusy) return;
    const src = pick.text;
    const ctx = surroundingContext(pageText, src);
    setPick((cur) => (cur && cur.text === src ? { ...cur, polishBusy: true, polishErr: "" } : cur));
    api
      .polish(
        labId,
        src,
        { literature_item_id: id, page_no: page, stream: true, context: ctx },
        {
          onDelta: (_d, acc) =>
            setPick((cur) => {
              if (!cur || cur.text !== src) return cur;
              const vis = visibleTranslation(acc);
              return vis ? { ...cur, polished: vis } : cur;
            }),
        }
      )
      .then((r) => {
        const polished = visibleTranslation(r.data.text || "") || r.data.text || "";
        setPick((cur) => (cur && cur.text === src ? { ...cur, polished, polishBusy: false } : cur));
      })
      .catch((e) => {
        if (e?.name === "AbortError" || e?.code === "ERR_CANCELED") return;
        setPick((cur) =>
          cur && cur.text === src ? { ...cur, polishBusy: false, polishErr: formatError(e, "改措辞未完成") } : cur
        );
      });
  }, [id, labId, page, pageText, pick]);

  const onSelectText = useCallback(
    ({ text, x, y, page_no, rects }) => {
      const anchor = rects?.length ? { page_no: page_no || page, quote: text.slice(0, 400), rects } : null;
      remember(text, anchor);
      setPick({ text, x, y, anchor, zh: "", busy: false, err: "" });
    },
    [page, remember, translatePick]
  );

  return { pick, setPick, kept, remember, translatePick, polishPick, onSelectText };
}
