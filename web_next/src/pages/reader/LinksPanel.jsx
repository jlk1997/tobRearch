import { useState } from "react";
import { Button, Card, message } from "antd";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api.js";
import { formatError, literatureHref } from "../../err.js";
import { EmptyHint } from "../../ui.jsx";
import { AuthorDrawer, EVIDENCE_LABEL } from "../../AuthorDrawer.jsx";

const OTHER_KINDS = ["same_author", "co_cited", "bib_coupled", "topic", "same_direction"];

function RefRow({ labId, row, onAdded }) {
  const [busy, setBusy] = useState(false);
  const meta = [row.first_author, row.year].filter(Boolean).join(" · ");
  if (row.in_lab_item_id) {
    return (
      <li>
        <Link to={literatureHref(row.in_lab_item_id)}>{row.title || "无题"}</Link>
        {meta ? <span className="fs-muted"> · {meta}</span> : null}
      </li>
    );
  }
  return (
    <li>
      <span>{row.title || "无题"}</span>
      {meta ? <span className="fs-muted"> · {meta}</span> : null}
      <Button
        size="small"
        type="link"
        loading={busy}
        disabled={!row.title}
        onClick={async () => {
          setBusy(true);
          try {
            await api.addLiterature(labId, {
              title: row.title,
              authors: row.first_author ? [row.first_author] : [],
              doi: row.doi || null,
              arxiv_id: row.arxiv_id || null,
              publish_year: row.year || null,
            });
            message.success("已放进组里");
            onAdded();
          } catch (e) {
            message.error(formatError(e, "没放进组里"));
          } finally {
            setBusy(false);
          }
        }}
      >
        放进组里
      </Button>
    </li>
  );
}

export function LinksPanel({ id, labId, me, teacher, onAskLandscape, githubUrl, hfUrl, onFindCode }) {
  const [all, setAll] = useState(false);
  const [authorOpen, setAuthorOpen] = useState("");
  const [finding, setFinding] = useState(false);
  const nav = useNavigate();
  const q = useQuery({
    queryKey: ["relations", id],
    queryFn: () => api.literatureRelations(id).then((r) => r.data),
    enabled: !!id,
  });
  const d = q.data;
  if (q.isLoading) return <Card title="引用"><p className="fs-muted">正在找…</p></Card>;
  if (q.isError) return <Card title="引用"><EmptyHint>{formatError(q.error, "没找到这篇的引用")}</EmptyHint></Card>;
  const refs = d?.references || [];
  const inLab = refs.filter((r) => r.in_lab_item_id).length;
  const shown = all ? refs : refs.slice(0, 30);
  const groups = Object.fromEntries((d?.groups || []).map((g) => [g.kind, g]));
  const citedBy = groups.cited_by?.items || [];
  const others = OTHER_KINDS.filter((k) => groups[k]?.items?.length);
  const lab = d?.lab_id || labId;
  return (
    <Card
      title="引用"
      className="fs-links"
      extra={
        <span>
          <Button
            size="small"
            type="link"
            onClick={async () => {
              const other = (refs || []).find((row) => row.in_lab_item_id);
              if (!other) {
                message.info("引用里还没有另一篇已经在组里的，先放进组里再对比。");
                return;
              }
              try {
                await api.compare(lab, [id, other.in_lab_item_id]);
                message.success("已和组里的一篇对照。");
                nav(`/labs/${lab}/literature`);
              } catch (e) {
                message.error(formatError(e, "没对比成"));
              }
            }}
          >
            和这篇对比
          </Button>
          <Button size="small" type="link" onClick={() => onAskLandscape?.()}>
            上下游论文
          </Button>
        </span>
      }
    >
      <p className="fs-muted">
        {githubUrl ? <a href={githubUrl} target="_blank" rel="noreferrer">代码仓库</a> : "还没有对上的代码仓库"}
        {" · "}
        {hfUrl ? <a href={hfUrl} target="_blank" rel="noreferrer">Hugging Face</a> : "还没有对上的 Hugging Face 页"}
        {" · "}
        <Button
          size="small"
          type="link"
          loading={finding}
          onClick={async () => {
            setFinding(true);
            try {
              const r = await api.findCodeLinks(id);
              message.info(r.data?.note || "已记下代码和数据链接");
              onFindCode?.();
            } catch (e) {
              message.error(formatError(e, "这次没对上代码"));
            } finally {
              setFinding(false);
            }
          }}
        >
          找代码
        </Button>
      </p>
      {(d?.authors || []).length ? (
        <section>
          <h4 className="fs-author-h">作者</h4>
          <div className="fs-author-chips">
            {d.authors.map((a) =>
              a.author_id ? (
                <button
                  key={`${a.position}-${a.name}`}
                  type="button"
                  className="fs-author-chip"
                  title={EVIDENCE_LABEL[a.evidence] || "只按名字"}
                  onClick={() => setAuthorOpen(a.author_id)}
                >
                  {a.name}
                  {a.paper_count > 1 ? <em>组里 {a.paper_count} 篇</em> : null}
                </button>
              ) : (
                <span key={`${a.position}-${a.name}`} className="fs-author-chip">{a.name}</span>
              )
            )}
          </div>
        </section>
      ) : null}

      <section>
        <h4 className="fs-author-h">
          它引了谁
          {refs.length ? <span className="fs-muted">（{refs.length} 条，组里有 {inLab} 篇）</span> : null}
        </h4>
        {refs.length ? (
          <>
            <ol className="fs-links-list">
              {shown.map((r) => (
                <RefRow key={r.id} labId={lab} row={r} onAdded={() => q.refetch()} />
              ))}
            </ol>
            {refs.length > shown.length ? (
              <Button size="small" type="link" onClick={() => setAll(true)}>
                展开其余 {refs.length - shown.length} 条
              </Button>
            ) : null}
          </>
        ) : (
          <p className="fs-muted">还没拆出参考文献。</p>
        )}
      </section>

      <section>
        <h4 className="fs-author-h">
          谁引了它
          {d?.cited_by_count ? <span className="fs-muted">（全世界被引 {d.cited_by_count} 次）</span> : null}
        </h4>
        {citedBy.length ? (
          <ul className="fs-links-list">
            {citedBy.map((r) => (
              <li key={r.item_id}>
                <Link to={literatureHref(r.item_id)}>{r.title || "无题"}</Link>
                {r.year ? <span className="fs-muted"> · {r.year}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="fs-muted">组里还没有论文引它。</p>
        )}
      </section>

      {others.map((k) => (
        <section key={k}>
          <h4 className="fs-author-h">{groups[k].label}</h4>
          <ul className="fs-links-list">
            {groups[k].items.map((r) => (
              <li key={r.item_id}>
                <Link to={literatureHref(r.item_id)}>{r.title || "无题"}</Link>
                {r.why ? <span className="fs-muted"> · {r.why}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
      <AuthorDrawer
        labId={lab}
        authorId={authorOpen}
        me={me}
        teacher={teacher}
        onClose={() => setAuthorOpen("")}
        onOpenAuthor={setAuthorOpen}
        onOnlyThis={(a) => nav(`/labs/${lab}/literature?author=${a.id}`)}
      />
    </Card>
  );
}
