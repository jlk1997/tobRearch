import { useState } from "react";
import { Button, Checkbox, Drawer, Modal, Tag, message } from "antd";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api.js";
import { formatError, literatureHref } from "./err.js";

export const EVIDENCE_LABEL = {
  manual: "导师确认过",
  orcid: "ORCID 对上",
  openalex: "OpenAlex 编号对上",
  s2: "Semantic Scholar 编号对上",
  coauthor: "合作者对上",
  affiliation: "单位对上",
  name_only: "只按名字",
};

const SURE = new Set(["manual", "orcid", "openalex", "s2"]);

function sureLine(counts) {
  const total = Object.values(counts || {}).reduce((a, b) => a + b, 0);
  if (!total) return "";
  const sure = Object.entries(counts).filter(([k]) => SURE.has(k)).reduce((a, [, n]) => a + n, 0);
  const weak = counts.name_only || 0;
  if (sure === total) return `${total} 篇都有编号或导师确认，是同一个人。`;
  if (weak === total) return `${total} 篇都只是名字一样，可能混了同名的人。`;
  const bits = [`${sure} 篇有编号或导师确认`];
  const mid = total - sure - weak;
  if (mid) bits.push(`${mid} 篇靠合作者或单位对上`);
  if (weak) bits.push(`${weak} 篇只是名字一样`);
  return `${bits.join("，")}。`;
}

export function AuthorDrawer({ labId, authorId, me, teacher, onClose, onOnlyThis, onOpenAuthor }) {
  const qc = useQueryClient();
  const [picked, setPicked] = useState([]);
  const [busy, setBusy] = useState("");
  const q = useQuery({
    queryKey: ["author", authorId],
    queryFn: () => api.author(authorId).then((r) => r.data),
    enabled: !!authorId,
  });
  const a = q.data;
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["author"] });
    qc.invalidateQueries({ queryKey: ["literature", labId] });
  };
  const mine = a?.member && me?.id && a.member.user_id === me.id;

  const claim = async (on) => {
    setBusy("claim");
    try {
      await api.claimAuthor(labId, on ? a.id : null);
      message.success(on ? "认领好了，以后问“我的论文”就按这个人算" : "已撤销认领");
      refresh();
    } catch (e) {
      message.error(formatError(e, "没认领上"));
    } finally {
      setBusy("");
    }
  };

  const merge = (other) =>
    Modal.confirm({
      title: `把「${other.name}」并进来？`,
      content: `确认这是同一个人后，他名下的 ${other.paper_count || 0} 篇会算到这里。以后不会再自动拆开，拿错了还能再拆。`,
      okText: "是同一个人",
      cancelText: "先不并",
      onOk: async () => {
        try {
          await api.mergeAuthor(a.id, other.id);
          message.success("并好了");
          refresh();
        } catch (e) {
          message.error(formatError(e, "没并上"));
        }
      },
    });

  const split = () =>
    Modal.confirm({
      title: `这 ${picked.length} 篇不是这个人写的？`,
      content: "会把它们拆给一个同名的新作者。拆错了可以再合回来。",
      okText: "拆出去",
      cancelText: "先不拆",
      onOk: async () => {
        try {
          const r = await api.splitAuthor(a.id, picked);
          setPicked([]);
          message.success("拆好了");
          refresh();
          if (r.data?.new?.id) onOpenAuthor?.(r.data.new.id);
        } catch (e) {
          message.error(formatError(e, "没拆开"));
        }
      },
    });

  return (
    <Drawer
      open={!!authorId}
      width={440}
      onClose={() => {
        setPicked([]);
        onClose();
      }}
      title={a ? a.name : "作者"}
      extra={a && onOnlyThis ? <Button size="small" onClick={() => onOnlyThis(a)}>名单只看他</Button> : null}
      destroyOnClose
    >
      {q.isLoading ? <p className="fs-muted">正在打开…</p> : null}
      {q.isError ? <p className="fs-muted">{formatError(q.error, "没打开这个作者")}</p> : null}
      {a ? (
        <div className="fs-author">
          <p className="fs-author-meta">
            <span>组里 {a.paper_count || 0} 篇</span>
            {(a.affiliations || []).slice(0, 2).map((x) => (
              <span key={x} className="fs-muted">{x}</span>
            ))}
            {a.orcid ? <Tag>ORCID</Tag> : null}
            {a.verified ? <Tag color="green">导师确认过</Tag> : null}
          </p>
          {a.member ? (
            <p className="fs-author-member">
              组里的 <b>{a.member.name}</b> 认领了这个作者
              {mine ? (
                <Button size="small" type="link" loading={busy === "claim"} onClick={() => claim(false)}>
                  不是我，撤销
                </Button>
              ) : null}
            </p>
          ) : (me?.labs || []).some((l) => l.lab_id === labId) ? (
            <p className="fs-author-member">
              <Button size="small" loading={busy === "claim"} onClick={() => claim(true)}>
                这是我
              </Button>
              <span className="fs-muted"> 认领后，助手说“某某的论文”会按你算。</span>
            </p>
          ) : null}

          <h4 className="fs-author-h">是不是同一个人</h4>
          <p>{sureLine(a.evidence_counts) || "还没有署名记录。"}</p>

          {(a.coauthors || []).length ? (
            <>
              <h4 className="fs-author-h">常一起署名</h4>
              <div className="fs-author-chips">
                {a.coauthors.map((c) => (
                  <button key={c.id} type="button" className="fs-author-chip" onClick={() => onOpenAuthor?.(c.id)}>
                    {c.name}
                    <em>{c.papers_together} 篇</em>
                  </button>
                ))}
              </div>
            </>
          ) : null}

          <h4 className="fs-author-h">组里的论文</h4>
          <ul className="fs-author-papers">
            {(a.papers || []).map((p) => (
              <li key={p.id}>
                {teacher && (a.papers || []).length > 1 ? (
                  <Checkbox
                    checked={picked.includes(p.id)}
                    onChange={(e) => setPicked((prev) => (e.target.checked ? [...prev, p.id] : prev.filter((x) => x !== p.id)))}
                  />
                ) : null}
                <span>
                  <Link to={literatureHref(p.id)}>{p.title || "无题"}</Link>
                  <span className="fs-muted">
                    {" · "}第 {Number(p.position || 0) + 1} 作者
                    {p.year ? ` · ${p.year}` : ""}
                    {" · "}
                    {EVIDENCE_LABEL[p.evidence] || "只按名字"}
                  </span>
                </span>
              </li>
            ))}
          </ul>
          {teacher && picked.length ? (
            <Button danger size="small" onClick={split}>
              选中的 {picked.length} 篇不是他，拆出去
            </Button>
          ) : null}

          {(a.same_name_others || []).length ? (
            <>
              <h4 className="fs-author-h">同名的其他人</h4>
              <p className="fs-muted">名字一样但没有别的证据，先当成不同的人。</p>
              <ul className="fs-author-papers">
                {a.same_name_others.map((o) => (
                  <li key={o.id}>
                    <span>
                      <button type="button" className="fs-link-btn" onClick={() => onOpenAuthor?.(o.id)}>
                        {o.name}
                      </button>
                      <span className="fs-muted">
                        {" · "}
                        {o.paper_count || 0} 篇
                        {(o.affiliations || [])[0] ? ` · ${o.affiliations[0]}` : ""}
                      </span>
                    </span>
                    {teacher ? (
                      <Button size="small" type="link" onClick={() => merge(o)}>
                        是同一个人
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
    </Drawer>
  );
}
