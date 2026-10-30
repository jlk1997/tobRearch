import { memo } from "react";
import { Card, Collapse } from "antd";
import { EmptyHint, whenBlocked } from "../../ui.jsx";

/** 章节与图表导航。页码来自结构化解析，命不中就不给跳转，不猜页（A9）。 */
function asNode(item) {
  return item && typeof item === "object" ? item : null;
}

function SectionsPanelInner({ structure, onPage, onAskSection, onAskFigure }) {
  const sections = (structure?.sections || []).map(asNode).filter(Boolean);
  const figures = (structure?.figures || []).map(asNode).filter(Boolean);
  if (!structure?.has_structure) {
    return (
      <Card title="章节与图表">
        <EmptyHint>{structure?.next_step || "还没有章节结构：本篇的 PDF 还没被结构化解析，可先按页阅读。"}</EmptyHint>
      </Card>
    );
  }
  const row = (item, key) => (
    <span key={key} className="fs-press-wrap" onClick={whenBlocked(!(item.page_no || item.page), "这一节还对不上页码")}>
    <button
      type="button"
      style={{ paddingLeft: `${((item.level || 1) - 1) * 12}px` }}
      disabled={!(item.page_no || item.page)}
      title={item.page_no || item.page ? `跳到第 ${item.page_no || item.page} 页` : "这一节还对不上页码"}
      onClick={() => (item.page_no || item.page) && onPage(item.page_no || item.page)}
    >
      {item.label ? `${item.label} ` : ""}
      {item.title || item.caption}
      {item.page_no || item.page ? ` · 第 ${item.page_no || item.page} 页` : ""}
    </button>
    {onAskSection && !item.caption ? (
      <button type="button" className="fs-link-btn" onClick={() => onAskSection(item)}>
        讲这一节
      </button>
    ) : null}
    {onAskFigure && item.caption ? (
      <button type="button" className="fs-link-btn" onClick={() => onAskFigure(item)}>
        讲这张图
      </button>
    ) : null}
    </span>
  );
  return (
    <Card title="章节与图表">
      <div className="fs-sec-tree">{sections.map((s, i) => row(s, `s-${i}`))}</div>
      {figures.length ? (
        <Collapse
          ghost
          items={[
            {
              key: "fig",
              label: `图表 ${figures.length} 项`,
              children: (
                <div className="fs-sec-tree">
                  {figures.map((f, i) =>
                    row(
                      {
                        ...f,
                        title: `${f.kind === "table" ? "表" : "图"} ${f.caption || ""}`.trim(),
                        level: 1,
                        page_no: f.page_no || f.page,
                      },
                      `f-${i}`,
                    )
                  )}
                </div>
              ),
            },
          ]}
        />
      ) : null}
    </Card>
  );
}

export const SectionsPanel = memo(SectionsPanelInner);
