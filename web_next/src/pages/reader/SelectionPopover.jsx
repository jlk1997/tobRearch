import { memo } from "react";
import { Alert, Button, Space, message } from "antd";
import { clampPopover, clipPreview } from "../../pdfText.js";
import { MdPreview } from "../../ui.jsx";

function SelectionPopoverInner({ pick, hits, onAsk, onExplainFormula, onWrite, canWrite, onPolish, onRetranslate, onSaveNote, onTakeAway, onClose }) {
  if (!pick) return null;
  return (
    <div className="fs-sel-zh" style={clampPopover(pick.x, pick.y)}>
      <p className="fs-sel-quote">{clipPreview(pick.text, 96)}</p>
      {hits.length ? (
        <div className="fs-page-hits">
          {hits.map((t) => (
            <span key={t.src} className={t.do_not_translate ? "fs-chip is-lock" : "fs-chip"}>
              {t.src}
              {t.do_not_translate ? " · 不译" : t.tgt ? ` → ${t.tgt}` : ""}
            </span>
          ))}
        </div>
      ) : null}
      {pick.err ? <Alert type="error" showIcon message={pick.err} style={{ marginBottom: 8 }} /> : null}
      {pick.polishErr ? <Alert type="error" showIcon message={pick.polishErr} style={{ marginBottom: 8 }} /> : null}
      {pick.zh ? (
        <div className="fs-sel-zh-body">
          <p className="fs-muted" style={{ margin: "0 0 4px" }}>
            这几句的中文
          </p>
          <MdPreview text={pick.zh} />
        </div>
      ) : null}
      {pick.polished ? (
        <div className="fs-sel-zh-body">
          <p className="fs-muted" style={{ margin: "0 0 4px" }}>
            改后措辞
          </p>
          <MdPreview text={pick.polished} />
        </div>
      ) : null}
      <Space wrap>
        {pick.busy ? <span className="fs-muted">正在译这几句…</span> : null}
        {pick.polishBusy ? <span className="fs-muted">正在改措辞…</span> : null}
        {canWrite ? (
          <Button size="small" type="primary" onClick={() => onWrite?.(pick.text)}>
            写进这一节
          </Button>
        ) : null}
        <Button size="small" disabled={!pick.text || pick.busy} onClick={onRetranslate}>
          翻译
        </Button>
        <Button size="small" type="primary" onClick={() => onAsk(pick.text)}>
          问这句
        </Button>
        <Button size="small" disabled={!pick.text} onClick={() => onExplainFormula?.(pick.text)}>
          讲这个公式
        </Button>
        <Button size="small" onClick={onSaveNote}>
          记笔记
        </Button>
        <Button size="small" onClick={onClose}>
          关闭
        </Button>
      </Space>
    </div>
  );
}

export const SelectionPopover = memo(SelectionPopoverInner);
