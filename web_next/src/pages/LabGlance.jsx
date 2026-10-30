import { useEffect, useState } from "react";
import { Button, Form, Input, message } from "antd";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { formatError } from "../err.js";
import { LabReaderBits, PageHeader } from "../ui.jsx";
import Members from "./Members.jsx";

const OUTPUT = { manuscript: "论文", reproduce: "复现", run: "文献调研", work_log: "工作日志", activity: "动态" };

export function LabTabs({ tab, teacher, onChange }) {
  const items = [
    ["directions", "方向"],
    ["people", "成员"],
    ["feed", "动态"],
  ];
  if (teacher) items.push(["progress", "进度"]);
  return (
    <div className="fs-side-tabs" role="tablist" aria-label="组里">
      {items.map(([key, label]) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={tab === key}
          className={`fs-side-tab${tab === key ? " is-on" : ""}`}
          onClick={() => onChange(key)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function countsLine(counts) {
  if (!counts) return "";
  return `方向 ${counts.directions || 0} · 成员 ${counts.members || 0} · 文献 ${counts.literature || 0}`;
}

export function DigestMail({ labId }) {
  const [on, setOn] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    api
      .getLab(labId)
      .then((r) => {
        if (alive) setOn(!!r.data.settings?.weekly_digest_email);
      })
      .catch(() => {
        if (alive) setOn(false);
      });
    return () => {
      alive = false;
    };
  }, [labId]);
  return (
    <p className="fs-muted">
      邮件周报{on ? "已开" : "默认关"}。{" "}
      <button
        type="button"
        className="fs-link-btn"
        disabled={busy || on === null}
        onClick={async () => {
          setBusy(true);
          try {
            await api.patchLab(labId, { settings: { weekly_digest_email: !on } });
            setOn(!on);
          } catch (e) {
            message.error(formatError(e, "无法保存"));
          } finally {
            setBusy(false);
          }
        }}
      >
        {on ? "关掉" : "打开"}
      </button>
    </p>
  );
}

export default function LabGlance({ labId, teacher, overview, tab, setTab, onRefresh }) {
  const [name, setName] = useState("");
  const [making, setMaking] = useState(false);
  const [log, setLog] = useState("");
  const [logging, setLogging] = useState(false);
  const [who, setWho] = useState("");
  const [placing, setPlacing] = useState("");
  const counts = overview.counts || {};
  const directions = overview.directions || [];
  const people = overview.people || [];
  const feed = (overview.feed || []).filter((row) => !who || row.actor === who || row.user_id === who);
  const actors = [...new Set((overview.feed || []).map((row) => row.actor).filter(Boolean))];

  const placePaper = async (directionId, paper) => {
    const ids = [...new Set([...(paper.direction_ids || []), directionId])];
    setPlacing(paper.item_id);
    try {
      await api.patchLiterature(paper.item_id, { direction_ids: ids });
      message.success("已放进这个方向");
      onRefresh?.();
    } catch (err) {
      message.error(formatError(err, "没放进这个方向"));
    } finally {
      setPlacing("");
    }
  };

  return (
    <div className="fs-lab">
      <PageHeader title="组里" hint={overview.one_line || "方向、谁在做、在读哪几篇。"}>
        <LabTabs tab={tab} teacher={teacher} onChange={setTab} />
      </PageHeader>
      <p className="fs-muted">{countsLine(counts)}</p>

      {tab === "directions" ? (
        <>
          {teacher ? (
            <form
              className="fs-lab-create"
              onSubmit={async (e) => {
                e.preventDefault();
                const title = name.trim();
                if (!title) return;
                setMaking(true);
                try {
                  await api.createDirection(labId, { name: title });
                  setName("");
                  message.success("已新建方向");
                  onRefresh?.();
                } catch (err) {
                  message.error(formatError(err, "没建成"));
                } finally {
                  setMaking(false);
                }
              }}
            >
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="新方向的名字" />
              <Button htmlType="submit" loading={making}>
                新建方向
              </Button>
            </form>
          ) : null}
          {!directions.length ? <p className="fs-muted">还没有方向。</p> : null}
          <div className="fs-card-grid">
            {directions.map((d) => (
              <article key={d.id} className="fs-lab-card">
                <h3>{d.name}</h3>
                {d.one_line ? <p>{d.one_line}</p> : null}
                {d.owner_name ? <p className="fs-muted">负责人 {d.owner_name}</p> : null}
                {d.members?.length ? (
                  <p>{d.members.map((m) => `${m.name} · ${m.role}`).join("、")}</p>
                ) : (
                  <p className="fs-muted">还没有人挂在这个方向。</p>
                )}
                {(d.reading || []).length ? (
                  <ul>
                    {d.reading.map((paper) => (
                      <li key={paper.item_id}>
                        <Link to={paper.href || `/literature/${paper.item_id}`}>{paper.title}</Link>
                        {paper.last_page_no > 1 ? <span className="fs-muted"> 第 {paper.last_page_no} 页</span> : null}
                        {paper.readers?.length || paper.reader_marks?.length ? (
                          <span className="fs-muted">
                            {" "}
                            在读：
                            <LabReaderBits itemId={paper.item_id} names={paper.readers} marks={paper.reader_marks} />
                          </span>
                        ) : null}
                        {paper.unplaced ? (
                          <>
                            <span className="fs-muted"> 还没放进这个方向</span>
                            <button
                              type="button"
                              className="fs-link-btn"
                              disabled={placing === paper.item_id}
                              onClick={() => placePaper(d.id, paper)}
                            >
                              放进这个方向
                            </button>
                          </>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="fs-muted">还没有人在读。</p>
                )}
                {(d.outputs || []).length ? (
                  <ul>
                    {d.outputs.map((row) => (
                      <li key={`${row.kind}-${row.href}`}>
                        <span className="fs-muted">{OUTPUT[row.kind] || "产出"} · </span>
                        {row.href ? <Link to={row.href}>{row.title}</Link> : row.title}
                      </li>
                    ))}
                  </ul>
                ) : null}
                <p className="fs-lab-actions">
                  <Link to={`/labs/${labId}/directions/${d.id}`}>进入方向</Link>
                  <Link
                    to={`/labs/${labId}/assistant?direction_id=${encodeURIComponent(d.id)}&q=${encodeURIComponent(`讲讲${d.name}这个方向在做什么`)}`}
                  >
                    问这个方向
                  </Link>
                </p>
              </article>
            ))}
          </div>
        </>
      ) : null}

      {tab === "people" ? (
        <>
          <div className="fs-card-grid">
            {people.map((person) => (
              <article key={person.user_id} className="fs-lab-card">
                <h3>
                  <Link to={person.href}>{person.name}</Link>
                  <span className="fs-muted"> {person.role}</span>
                </h3>
                {person.directions?.length ? <p>{person.directions.join("、")}</p> : <p className="fs-muted">还没选方向</p>}
                {person.work_line ? <p>{person.work_line}</p> : null}
                {(person.reading || []).map((paper) => (
                  <p key={paper.item_id}>
                    在读 <Link to={paper.href || `/literature/${paper.item_id}`}>{paper.title}</Link>
                    {paper.last_page_no > 1 ? <span className="fs-muted"> 第 {paper.last_page_no} 页</span> : null}
                  </p>
                ))}
                {person.latest_output ? (
                  <p>
                    <span className="fs-muted">{OUTPUT[person.latest_output.kind] || "最近"} · </span>
                    <Link to={person.latest_output.href}>{person.latest_output.title}</Link>
                  </p>
                ) : null}
              </article>
            ))}
          </div>
          {teacher ? (
            <details className="fs-lab-manage">
              <summary>邀请和改角色</summary>
              {teacher ? <Members /> : null}
            </details>
          ) : null}
        </>
      ) : null}

      {tab === "feed" ? (
        <>
          <Form
            className="fs-lab-create"
            onFinish={async () => {
              setLogging(true);
              try {
                await api.addWorkLog(labId, log);
                setLog("");
                message.success("已记下");
                onRefresh?.();
              } catch (err) {
                message.error(formatError(err, "没记下"));
              } finally {
                setLogging(false);
              }
            }}
          >
            <Input.TextArea rows={3} value={log} onChange={(e) => setLog(e.target.value)} placeholder="写三句工作日志" />
            <Button htmlType="submit" loading={logging} disabled={!log.trim()}>
              记下
            </Button>
          </Form>
          {actors.length ? (
            <p className="fs-lab-filters">
              <button type="button" className={!who ? "is-on" : ""} onClick={() => setWho("")}>
                全部
              </button>
              {actors.map((name) => (
                <button key={name} type="button" className={who === name ? "is-on" : ""} onClick={() => setWho(name)}>
                  {name}
                </button>
              ))}
            </p>
          ) : null}
          {!feed.length ? <p className="fs-muted">最近没有动态。</p> : null}
          <ul className="fs-lab-feed">
            {feed.map((row, i) => (
              <li key={`${row.kind}-${row.at}-${i}`}>
                <span className="fs-muted">{row.actor ? `${row.actor} · ` : ""}{OUTPUT[row.kind] || "动态"}</span>
                {row.href ? <Link to={row.href}>{row.title}</Link> : <span>{row.title}</span>}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}
