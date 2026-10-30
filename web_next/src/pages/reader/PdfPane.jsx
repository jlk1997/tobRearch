import { lazy, memo, Suspense } from "react";
import { Alert, Button } from "antd";
import { EmptyHint, PageLoading, whenBlocked } from "../../ui.jsx";

const PdfViewer = lazy(() => import("../../PdfViewer.jsx"));

/** PDF 面板。渐进加载由 PdfViewer 里的 pdf.js 负责，这里不再整份下载（A2）。 */
function PdfPaneInner({
  source,
  fallbackUrl,
  page,
  onPageChange,
  onNumPages,
  onPageText,
  onSelectText,
  onError,
  error,
  hasFile,
  highlights,
  onHighlightClick,
  showZh,
  onToggleZh,
  canTranslate,
}) {
  if (!hasFile && !source) {
    return <EmptyHint>还没有可复制的 PDF。扫描件读不了正文。</EmptyHint>;
  }
  if (error && !source) {
    return (
      <Alert
        type="error"
        showIcon
        message={error}
        description="请上传可复制的文字版 PDF。若文件还在，可先点工具条「下载 PDF」用系统阅读器打开。"
      />
    );
  }
  if (!source) {
    return (
      <div className="fs-pdf-wait">
        <PageLoading label="正在打开 PDF…" />
      </div>
    );
  }
  return (
    <Suspense fallback={<PageLoading label="正在打开 PDF…" />}>
    <PdfViewer
      source={source}
      fallbackUrl={fallbackUrl}
      page={page}
      onPageChange={onPageChange}
      onNumPages={onNumPages}
      onPageText={onPageText}
      onSelectText={onSelectText}
      highlights={highlights}
      onHighlightClick={onHighlightClick}
      onError={onError}
      extra={
        <span className="fs-press-wrap" onClick={whenBlocked(canTranslate === false, "这一页还没有可复制文字")}>
        <Button
          size="small"
          type={showZh ? "primary" : "default"}
          disabled={canTranslate === false}
          onClick={onToggleZh}
        >
          {showZh ? "收起中文" : "看中文"}
        </Button>
        </span>
      }
    />
    </Suspense>
  );
}

export const PdfPane = memo(PdfPaneInner);
