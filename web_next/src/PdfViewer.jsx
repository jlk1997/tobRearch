import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Space, Spin } from "antd";
import { whenBlocked } from "./ui.jsx";
import { Document, Page, pdfjs } from "react-pdf";
import pdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { clampPopover, joinPdfTextItems, rectsForQuote } from "./pdfText.js";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker;

// 只抓当前页要用的片段，不预取整份。配合后端 Accept-Ranges 与 nginx 缓存（A2）。
const LOAD_OPTIONS = { disableAutoFetch: true, disableStream: false };

export default function PdfViewer({
  source,
  fallbackUrl,
  page = 1,
  onPageChange,
  onNumPages,
  onError,
  onPageText,
  onSelectText,
  highlights,
  onHighlightClick,
  extra,
}) {
  const box = useRef(null);
  const [numPages, setNumPages] = useState(0);
  const [fallback, setFallback] = useState(false);
  const [width, setWidth] = useState(640);
  const [textItems, setTextItems] = useState([]);
  const current = Math.max(1, page || 1);
  const sourceKey = typeof source === "string" ? source : source?.url || "";
  useEffect(() => {
    setFallback(false);
    setNumPages(0);
    setTextItems([]);
  }, [sourceKey]);
  useEffect(() => {
    setTextItems([]);
  }, [current]);
  useEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const fit = () => setWidth(Math.max(280, Math.floor(el.clientWidth - 28)));
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [sourceKey, fallback]);
  const pageHighlights = useMemo(() => {
    const rows = [];
    for (const mark of highlights || []) {
      if (Number(mark?.page_no) !== current) continue;
      if (Array.isArray(mark.rects) && mark.rects.length) {
        rows.push(mark);
        continue;
      }
      const rects = mark.quote ? rectsForQuote(textItems, mark.quote) : [];
      if (rects.length) rows.push({ ...mark, rects });
    }
    return rows;
  }, [highlights, current, textItems]);
  const onMouseUp = () => {
    if (!onSelectText) return;
    const sel = window.getSelection();
    const text = (sel?.toString() || "").replace(/\s+/g, " ").trim();
    if (text.length < 8 || !sel?.rangeCount) return;
    const range = sel.getRangeAt(0);
    const node = range.commonAncestorContainer;
    if (!box.current?.contains(node.nodeType === 1 ? node : node.parentElement)) return;
    const rect = range.getBoundingClientRect();
    const pos = clampPopover(rect.left, rect.bottom + 6);
    const layer = box.current.querySelector(".fs-pdf-page");
    const frame = layer?.getBoundingClientRect();
    onSelectText({
      text,
      x: pos.left,
      y: pos.top,
      page_no: current,
      rects:
        frame && frame.width && frame.height
          ? Array.from(range.getClientRects()).map((r) => ({
              x: (r.left - frame.left) / frame.width,
              y: (r.top - frame.top) / frame.height,
              w: r.width / frame.width,
              h: r.height / frame.height,
            }))
          : [],
    });
  };
  if (!source && !fallbackUrl) return null;
  if (fallback) {
    return (
      <div className="fs-pdf-wrap" ref={box}>
        {extra ? <Space wrap className="fs-pdf-nav">{extra}</Space> : null}
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 8 }}
          message="阅读器无法排版，已改用原文件预览。"
          description="可继续阅读；需要复制文字时请下载原文件。"
        />
        {fallbackUrl ? (
          <iframe title="PDF 预览" src={fallbackUrl} className="fs-pdf-frame" />
        ) : (
          <Spin tip="正在准备原文件预览" />
        )}
      </div>
    );
  }
  return (
    <div className="fs-pdf-wrap" ref={box} onMouseUp={onMouseUp}>
      <Space wrap className="fs-pdf-nav">
        <span className="fs-press-wrap" onClick={whenBlocked(current <= 1, "已经是第一页")}>
        <Button size="small" disabled={current <= 1} onClick={() => onPageChange?.(current - 1)}>
          上一页
        </Button>
        </span>
        <span>
          第 {current} / {numPages || "?"} 页
        </span>
        <span className="fs-press-wrap" onClick={whenBlocked(!!numPages && current >= numPages, "已经是最后一页")}>
        <Button size="small" disabled={numPages && current >= numPages} onClick={() => onPageChange?.(current + 1)}>
          下一页
        </Button>
        </span>
        {extra}
      </Space>
      <Document
        file={source}
        options={LOAD_OPTIONS}
        onLoadSuccess={({ numPages: n }) => {
          setNumPages(n);
          onNumPages?.(n);
        }}
        loading={<Spin tip="正在排版当前页" />}
        error={<Alert type="error" showIcon message="无法渲染 PDF" description="请下载原文件，或上传可复制的文字版。" />}
        onLoadError={() => {
          setFallback(true);
          onError?.();
        }}
      >
        {numPages ? (
          <div className="fs-pdf-page">
            <Page
              pageNumber={Math.min(current, numPages)}
              width={width}
              renderTextLayer
              renderAnnotationLayer
              onGetTextSuccess={({ items }) => {
                setTextItems(items || []);
                onPageText?.(joinPdfTextItems(items));
              }}
              onGetTextError={() => onPageText?.("")}
            />
            {pageHighlights.map((h) =>
              h.rects.map((r, i) => (
                <button
                  key={`${h.id}-${i}`}
                  type="button"
                  className="fs-pdf-highlight"
                  title={h.label || "打开这条笔记"}
                  onClick={() => onHighlightClick?.(h)}
                  style={{
                    left: `${r.x * 100}%`,
                    top: `${r.y * 100}%`,
                    width: `${r.w * 100}%`,
                    height: `${r.h * 100}%`,
                  }}
                />
              ))
            )}
          </div>
        ) : null}
      </Document>
    </div>
  );
}
