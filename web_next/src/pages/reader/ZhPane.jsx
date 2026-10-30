import { memo } from "react";
import { Alert, Button } from "antd";
import { chunkProgressLabel } from "../../pdfText.js";
import { EmptyHint, MdPreview, whenBlocked } from "../../ui.jsx";

function ZhPaneInner({ page, zh, hits, canTranslate, onRetry, onStop, onPolish }) {
  return (
    <aside className="fs-page-zh">
      <div className="fs-page-zh-bar">
        <strong>本页中文 · p.{page}</strong>
        {zh.busy ? (
          <>
            <span className="fs-muted">{chunkProgressLabel(zh.progress?.chunk, zh.progress?.chunks, zh.progress?.retrying)}</span>
            <Button size="small" onClick={onStop}>
              停止
            </Button>
          </>
        ) : (
          <span className="fs-press-wrap" onClick={whenBlocked(!canTranslate, "这一页还没有可复制文字")}>
          <Button size="small" disabled={!canTranslate} onClick={onRetry}>
            译这一页
          </Button>
          </span>
        )}
      </div>
      {zh.err ? (
        <Alert type={zh.text ? "warning" : "error"} showIcon message={zh.err} style={{ marginBottom: 8 }} />
      ) : null}
      {zh.stale && zh.text && zh.busy ? <p className="fs-zh-stale">上一页译文，正在译本页…</p> : null}
      {hits.length ? (
        <div className="fs-page-hits">
          <p className="fs-muted" style={{ margin: "0 0 4px", width: "100%" }}>
            本页锁定词，译时会保住原文。
          </p>
          {hits.map((t) => (
            <span
              key={t.src}
              className={t.do_not_translate ? "fs-chip is-lock" : "fs-chip"}
              title={t.do_not_translate ? `${t.src} 锁定不译` : t.tgt ? `${t.src} → ${t.tgt}` : t.src}
            >
              {t.src}
              {t.tgt ? ` → ${t.tgt}` : ""}
              {t.do_not_translate ? " · 不译" : ""}
            </span>
          ))}
        </div>
      ) : null}
      {!canTranslate ? (
        <EmptyHint>这一页还没有可复制文字。请换能选中字的 PDF。</EmptyHint>
      ) : zh.text ? (
        <>
          <MdPreview text={zh.text} />
          <Button size="small" style={{ marginTop: 8 }} onClick={onPolish}>
            拿去改
          </Button>
        </>
      ) : zh.busy ? (
        <p className="fs-muted">{chunkProgressLabel(zh.progress?.chunk, zh.progress?.chunks, zh.progress?.retrying)}</p>
      ) : null}
    </aside>
  );
}

export const ZhPane = memo(ZhPaneInner);
