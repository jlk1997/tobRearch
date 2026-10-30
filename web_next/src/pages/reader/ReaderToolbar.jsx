import { memo, useState } from "react";
import { Button, Collapse, Popconfirm, Select, Space, Upload, message } from "antd";
import { Link } from "react-router-dom";
import { api, saveBlob } from "../../api.js";
import { formatError } from "../../err.js";
import { whenBlocked } from "../../ui.jsx";
import { manuscriptSectionHref, suggestCiteKey } from "../../manuscriptSections.js";
import { methodCardText } from "../../methodCard.js";

export function shouldAnalyzeAfterUpload(doc) {
  if (!doc) return false;
  if (doc.analysis_status === "running") return false;
  const kind = (doc.card_empty && doc.card_empty.kind) || "";
  if (kind === "running") return false;
  if (doc.has_method_card) return false;
  return kind === "abstract_only" || kind === "unusable" || kind === "ungrounded" || !kind;
}

async function runAnalyze({ id, onAnalysis, onPanel, onReload }) {
  const r = await api.analyze(id);
  onAnalysis(r.data);
  onPanel("card");
  if (r.data.status === "running" || r.data.result?.status === "running") {
    message.info("正在后台提炼，写完会出现在侧栏。");
  } else if (r.data.result?.warning) message.warning(r.data.result.warning);
  else if (r.data.reused) message.warning("这次没提炼出来，仍显示上次写好的这篇的方法。");
  if (r.data.quota_warning) message.warning(r.data.quota_warning);
  const placed = r.data.auto_placed || [];
  if (placed.length) {
    const names = placed.map((p) => p.name).filter(Boolean).join("、");
    message.success({
      duration: 6,
      content: (
        <span>
          已放进{names}{" "}
          <Button
            type="link"
            size="small"
            onClick={async () => {
              const drop = new Set(placed.map((p) => p.id));
              const keep = (r.data.direction_ids || []).filter((did) => !drop.has(did));
              await api.patchLiterature(id, { direction_ids: keep });
              message.success("已撤销");
              onReload();
            }}
          >
            撤销
          </Button>
        </span>
      ),
    });
  }
}

function readerNext(doc, fromMs, fromDir, ownMs) {
  const ids = doc?.direction_ids || [];
  if (fromDir && !ids.includes(fromDir)) {
    const hit = (doc.direction_suggestions || []).find((s) => s.id === fromDir) || { id: fromDir, name: "" };
    return { key: "place", label: "放进这个方向", suggestion: hit };
  }
  if (!ids.length) {
    const hit = (doc.direction_suggestions || [])[0];
    return hit ? { key: "place", label: "放进这个方向", suggestion: hit } : null;
  }
  if (ownMs?.id && !fromMs) return { key: "write", label: "写进相关工作" };
  return null;
}

function ReaderToolbarInner({ id, doc, fromMs, fromSection, fromDir, ownMs, ownReady, analysis, page, nav, onReload, onAnalysis, onPdfReload, onPanel, noBody, running }) {
  const [busy, setBusy] = useState("");
  const blocked = !!noBody;
  const next = readerNext(doc, fromMs, fromDir, ownMs);
  const placedLine = (doc.placed_directions || []).map((d) => d.name).filter(Boolean).join("、");
  const analyzeButton = (
    <span className="fs-press-wrap" onClick={whenBlocked(blocked || !!running, blocked ? "请先上传可复制 PDF。" : "正在提炼，请稍等")}>
      <Button
        id="fs-analyze-run"
        type={next?.key === "analyze" ? "primary" : "default"}
        loading={busy === "analyze" || !!running}
        disabled={blocked || !!running}
        title={blocked ? "请先上传可复制 PDF。" : undefined}
        onClick={async () => {
          if (blocked) {
            message.warning("请先上传可复制 PDF。");
            return;
          }
          setBusy("analyze");
          try {
            await runAnalyze({ id, onAnalysis, onPanel, onReload });
          } catch (e) {
            message.error(formatError(e, "提炼方法未完成，请再点一次。不要把网关超时当分析成功。"));
          } finally {
            setBusy("");
          }
        }}
      >
        {busy === "analyze" || running ? "正在提炼…" : "提炼方法"}
      </Button>
    </span>
  );
  return (
    <div className="fs-toolbar fs-toolbar-groups">
      <span className="fs-tool-group">
        <Select
          value={doc.my_reading || "want_read"}
          style={{ width: 140 }}
          onChange={async (status) => {
            try {
              await api.markRead(id, status);
              message.success("已记下");
              onReload();
            } catch (e) {
              message.error(formatError(e, "无法更新在读状态"));
            }
          }}
          options={[
            { value: "want_read", label: "想读" },
            { value: "reading", label: "在读" },
            { value: "finished", label: "已读" },
            { value: "dropped", label: "放弃" },
          ]}
        />
      </span>
      <span className="fs-tool-group">
        {placedLine ? <span className="fs-muted">已在{placedLine}</span> : null}
        <span hidden>{analyzeButton}</span>
        {next?.key === "place" ? (
          <Button
            type="primary"
            onClick={async () => {
              const s = next.suggestion;
              try {
                const ids = [...new Set([...(doc.direction_ids || []), s.id])];
                await api.patchLiterature(id, { direction_ids: ids });
                message.success(`已放进${s.name || "这个方向"}`);
                onReload();
              } catch (e) {
                message.error(formatError(e, "没放进这个方向"));
              }
            }}
          >
            放进这个方向
          </Button>
        ) : null}
        {next?.key === "write" && ownMs?.id ? (
          <Button
            type="primary"
            loading={busy === "write"}
            onClick={async () => {
              setBusy("write");
              try {
                const text = methodCardText(analysis) || doc.title || "";
                await api.writeback(doc.lab_id, {
                  manuscript_id: ownMs.id,
                  text,
                  write_mode: "append_heading",
                  heading: fromSection || "相关工作",
                  expected_updated_at: ownMs.updated_at,
                  literature_item_ids: [id],
                });
                const section = fromSection || "相关工作";
                nav(`/manuscripts/${ownMs.id}?section=${encodeURIComponent(section)}&wrote=1&from_lit=${encodeURIComponent(id)}&from_page=${page || 1}`);
              } catch (e) {
                message.error(formatError(e, "没写进这一节"));
              } finally {
                setBusy("");
              }
            }}
          >
            写进相关工作
          </Button>
        ) : null}
        {(doc.direction_ids || [])[0] ? (
          <Link to={`/labs/${doc.lab_id}/directions/${(fromDir && (doc.direction_ids || []).includes(fromDir) ? fromDir : doc.direction_ids[0])}`}>
            这个方向
          </Link>
        ) : null}
      </span>
      <span className="fs-tool-group">
        {fromMs ? (
          <>
            <Button
              loading={busy === "cite"}
              onClick={async () => {
                setBusy("cite");
                try {
                  const existing = await api.citations(fromMs);
                  const rows = Array.isArray(existing.data) ? existing.data : [];
                  if (rows.some((c) => c.literature_item_id === id)) {
                    message.info("这篇已经在参考文献里");
                    return;
                  }
                  const key = suggestCiteKey(doc, rows);
                  await api.addCitation(fromMs, id, key);
                  message.success(key ? `已加入这篇的参考文献，引用写成 [@${key}]` : "已加入这篇的参考文献");
                } catch (e) {
                  message.error(formatError(e, "没加进这篇"));
                } finally {
                  setBusy("");
                }
              }}
            >
              加进这篇
            </Button>
            <Link to={manuscriptSectionHref(fromMs, fromSection || "")}>
              <Button type={next?.key === "write" || next?.key === "analyze" ? "default" : "primary"}>回到这一节</Button>
            </Link>
          </>
        ) : null}
      </span>
      <Collapse
        ghost
        className="fs-reader-more"
        items={[
          {
            key: "more",
            label: "更多",
            children: (
              <Space wrap>
                <Button
                  onClick={() => nav(`/labs/${doc.lab_id}/reproduces?literature_item_id=${encodeURIComponent(id)}`)}
                >
                  去复现
                </Button>
                {doc.files?.length ? (
                  <Button
                    onClick={async () => {
                      try {
                        const r = await api.literaturePdf(id);
                        saveBlob(r.data, `${doc.title || "paper"}.pdf`);
                      } catch (e) {
                        message.error(formatError(e, "无法下载 PDF"));
                      }
                    }}
                  >
                    下载 PDF
                  </Button>
                ) : null}
                <Popconfirm
                  title="删除该文献？"
                  onConfirm={async () => {
                    try {
                      await api.deleteLiterature(id);
                      message.success("已删除");
                      nav(`/labs/${doc.lab_id}/literature`);
                    } catch (e) {
                      message.error(formatError(e, "无法删除"));
                    }
                  }}
                >
                  <Button danger>删除</Button>
                </Popconfirm>
                <Upload
                  accept="application/pdf"
                  showUploadList={false}
                  beforeUpload={async (file) => {
                    try {
                      await api.uploadPdf(id, file);
                      message.success("PDF 已上传");
                      const next = await onReload();
                      onPdfReload();
                      if (shouldAnalyzeAfterUpload(next)) {
                        setBusy("analyze");
                        try {
                          await runAnalyze({ id, onAnalysis, onPanel, onReload });
                        } catch (e) {
                          message.error(formatError(e, "提炼方法未完成，请再点一次。不要把网关超时当分析成功。"));
                        } finally {
                          setBusy("");
                        }
                      }
                    } catch (err) {
                      message.error(formatError(err, "上传失败"));
                    }
                    return false;
                  }}
                >
                  <Button>上传 PDF</Button>
                </Upload>
              </Space>
            ),
          },
        ]}
      />
    </div>
  );
}

export const ReaderToolbar = memo(ReaderToolbarInner);
