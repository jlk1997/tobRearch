import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Select, message } from "antd";
import { api } from "../api.js";
import { formatError } from "../err.js";
import { PageHeader, PageLoading } from "../ui.jsx";
import { AssistantChat } from "../assistant/AssistantChat.jsx";
import { useAssistant } from "../assistant/useAssistant.js";
import { shortName } from "../tokens.js";

function shortTitle(name) {
  return shortName(name, 18);
}

function clock(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function whenBucket(iso) {
  if (!iso) return "更早";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "更早";
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const day = start(d);
  const today = start(new Date());
  if (day === today) return "今天";
  if (day === today - 86400000) return "昨天";
  return "更早";
}

function mostlyEnglish(line) {
  const latin = (line.match(/[A-Za-z]/g) || []).length;
  const cjk = (line.match(/[\u4e00-\u9fff]/g) || []).length;
  return latin > 12 && latin > cjk * 2;
}

function spokenPerson(name) {
  const text = String(name || "").trim();
  if (!text || /^[\d._\-@]+$/.test(text)) return "";
  return text;
}

function previewAnswer(text) {
  return String(text || "")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter((line) => line && !/^the materials\b/i.test(line) && !/·\s*成员\s*$/.test(line) && !mostlyEnglish(line))
    .join(" ")
    .replace(/\[S\d+\]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function readableLead(brief, dirs) {
  const text = String(brief || "").trim();
  const latin = (text.match(/[A-Za-z]{4,}/g) || []).length;
  const cjk = (text.match(/[\u4e00-\u9fff]/g) || []).length;
  if (!text || (dirs.length && latin >= 4 && cjk < 24)) {
    return dirs.length ? `组里现在有 ${dirs.length} 个方向。` : "问这个组在做什么、文献讲什么、谁在做。";
  }
  return text;
}

function splitIntro(text) {
  const raw = String(text || "");
  const take = (name) => {
    const matched = raw.match(new RegExp(`【${name}】\\s*([\\s\\S]*?)(?=【(?:称呼|约定|前提)】|$)`));
    return (matched?.[1] || "").trim();
  };
  if (!/【称呼】|【约定】|【前提】/.test(raw)) {
    return { address: "", custom: raw.trim(), premise: "" };
  }
  return { address: take("称呼"), custom: take("约定"), premise: take("前提") };
}

function joinIntro(address, custom, premise) {
  const parts = [];
  if (String(address || "").trim()) parts.push(`【称呼】\n${String(address).trim()}`);
  if (String(custom || "").trim()) parts.push(`【约定】\n${String(custom).trim()}`);
  if (String(premise || "").trim()) parts.push(`【前提】\n${String(premise).trim()}`);
  return parts.join("\n\n");
}

function fillEmpty(value, setValue, sample) {
  if (!String(value || "").trim()) setValue(sample);
}

function AgentVoice({ labId, voice, intro, teachers, onSaved }) {
  const parts = splitIntro(intro);
  const [open, setOpen] = useState(false);
  const [saying, setSaying] = useState(voice || "");
  const [address, setAddress] = useState(parts.address);
  const [custom, setCustom] = useState(parts.custom);
  const [premise, setPremise] = useState(parts.premise);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  useEffect(() => {
    const next = splitIntro(intro);
    setSaying(voice || "");
    setAddress(next.address);
    setCustom(next.custom);
    setPremise(next.premise);
  }, [voice, intro]);
  const names = (teachers || []).map((t) => t.display_name).filter(Boolean);
  const wroteVoice = String(voice || "").trim();
  const wroteIntro = String(intro || "").trim();
  const introLen = joinIntro(address, custom, premise).length;
  if (!open) {
    return (
      <button type="button" className="fs-link-btn fs-ask-voice-line" onClick={() => setOpen(true)}>
        这个助手 · 让助手认识这个组 · {wroteVoice || wroteIntro ? "去修改" : "去填写"}
      </button>
    );
  }
  return (
    <form
      className="fs-ask-voice-card fs-ask-voice-form"
      onSubmit={async (e) => {
        e.preventDefault();
        const memory = joinIntro(address, custom, premise);
        if (memory.length > 4000) {
          setNote("组里要记住的一共最多 4000 字");
          return;
        }
        setBusy(true);
        setNote("");
        try {
          await api.saveAssistantStanding(labId, { voice: saying, intro: memory });
          setNote("已记下。问称呼、约定或前提时会引用对应的一段。");
          onSaved?.();
        } catch {
          setNote("这次没记下");
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="fs-ask-voice-head">
        <p className="fs-ask-voice-title">让助手认识这个组</p>
        <button type="button" className="fs-link-btn" onClick={() => setOpen(false)}>
          收起
        </button>
      </div>
      <p className="fs-muted">写进本组知识库的一篇「组里要记住的」。名册上谁是导师，以成员页为准。</p>
      <label>
        怎么说话
        <span className="fs-ask-voice-hint">只管语气。不改谁是导师，也不改文献里的事实。</span>
        <textarea value={saying} maxLength={400} rows={2} onChange={(e) => setSaying(e.target.value)} />
        <button type="button" className="fs-ask-voice-sample" onClick={() => fillEmpty(saying, setSaying, "对学生直呼名字。先说结论，不要客套。")}>
          用这句例子：对学生直呼名字。先说结论，不要客套。
        </button>
      </label>
      <label>
        对外怎么称呼
        <span className="fs-ask-voice-hint">文献和名册里没有的叫法。和名册不一致时，回答会两句都说。</span>
        <textarea value={address} rows={2} onChange={(e) => setAddress(e.target.value)} />
        <button type="button" className="fs-ask-voice-sample" onClick={() => fillEmpty(address, setAddress, "对外称吴雪为导师。学生之间互称名字。")}>
          用这句例子：对外称吴雪为导师。学生之间互称名字。
        </button>
      </label>
      <label>
        组里约定
        <span className="fs-ask-voice-hint">组会、新人先做什么、组里的习惯。</span>
        <textarea value={custom} rows={3} onChange={(e) => setCustom(e.target.value)} />
        <button type="button" className="fs-ask-voice-sample" onClick={() => fillEmpty(custom, setCustom, "组会每周三下午。新生先读方向简报。")}>
          用这句例子：组会每周三下午。新生先读方向简报。
        </button>
      </label>
      <label>
        回答前要知道的前提
        <span className="fs-ask-voice-hint">文献库里查不到、但回答时必须知道的事。</span>
        <textarea value={premise} rows={3} onChange={(e) => setPremise(e.target.value)} />
        <button type="button" className="fs-ask-voice-sample" onClick={() => fillEmpty(premise, setPremise, "本组做打印后再拍摄的水印。没放进组里的论文要单独说明。")}>
          用这句例子：本组做打印后再拍摄的水印。没放进组里的论文要单独说明。
        </button>
      </label>
      <p className="fs-muted">
        名册里的导师：{names.length ? names.join("、") : "还没有人被标成导师。"}{" "}
        <Link to={`/labs/${labId}/members`}>到成员页改角色</Link>
      </p>
      <p className={introLen > 4000 ? "fs-ask-voice-over" : "fs-muted"}>组里要记住的 {introLen}/4000</p>
      <button type="submit" className="fs-ask-voice-save" disabled={busy || introLen > 4000}>
        {busy ? "正在记下" : "保存"}
      </button>
      {note ? <p className="fs-muted">{note}</p> : null}
    </form>
  );
}

function LabGlanceMini({ labId, overview }) {
  const directions = (overview?.directions || []).slice(0, 4);
  if (!directions.length) return null;
  return (
    <div className="fs-ask-glance">
      <p className="fs-ask-bucket">
        组里一览
        <Link to={`/labs/${labId}/cockpit`}>看全部</Link>
      </p>
      {directions.map((d) => {
        const people = (d.members || []).map((m) => spokenPerson(m.name)).filter(Boolean);
        const reading = (d.reading || []).length;
        const tail = [people.length ? people.slice(0, 3).join("、") : "还没有人", reading ? `在读 ${reading} 篇` : ""]
          .filter(Boolean)
          .join(" · ");
        return (
          <p key={d.id} className="fs-ask-glance-row">
            <Link to={`/labs/${labId}/directions/${d.id}`}>{d.name}</Link>
            <span className="fs-muted">{tail}</span>
          </p>
        );
      })}
    </div>
  );
}

const STARTERS = [
  ["这个组在做什么方向？", "这个组在做什么方向？"],
  ["谁在做什么？", "谁在做什么？"],
  ["组里有哪些文献？", "组里有哪些文献？"],
  ["最近做了什么？", "最近做了什么？"],
  ["某老师在网上的学术情况", "介绍一下导师在网上的学术情况"],
  ["这个方向近一年值得读的", "这个方向近一年有什么值得读的？"],
  ["这个领域的代表学者", "这个方向有哪些代表学者？"],
  ["这篇在外面的影响", "这篇论文在外面的影响怎么样，谁在跟进？"],
  ["全面检索一个主题", "帮我全面检索这个方向近三年的工作，中英文都要"],
  ["组里谁做过", "组里谁做过这个方向的核心方法？"],
];

export default function Assistant() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const directionId = params.get("direction_id") || "";
  const preset = params.get("q") || "";
  const session = params.get("session") || "";
  const chat = useAssistant(id);
  const queryClient = useQueryClient();
  const wasBusy = useRef(false);
  const opened = useRef("");
  const closed = useRef("");
  const [scopeDir, setScopeDir] = useState(directionId);
  const [finder, setFinder] = useState("");
  const [editing, setEditing] = useState("");
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState("");
  const [rowBusy, setRowBusy] = useState("");
  const home = useQuery({
    queryKey: ["assistant-home", id],
    queryFn: () => api.assistantHome(id).then((r) => r.data),
    enabled: !!id,
  });

  useEffect(() => {
    setScopeDir(directionId);
  }, [directionId]);

  useEffect(() => {
    if (!preset || chat.turns.length) return;
    const context = directionId ? { kind: "direction", id: directionId } : undefined;
    chat.ask(preset, { context, direction_id: directionId || undefined, scope: params.get("scope") || "lab" });
    // 只在带着问句进来时问一次。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset, id]);

  useEffect(() => {
    if (!session || preset || chat.turns.length || !home.data) return;
    if (opened.current === "new" && session === closed.current) return;
    if (opened.current === "new") opened.current = "";
    if (opened.current === session) return;
    const found = (home.data.ask_sessions || []).find((s) => s.session_id === session);
    if (!found) return;
    opened.current = session;
    chat.openPast(found);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, home.data, preset, chat.turns.length]);

  useEffect(() => {
    if (wasBusy.current && !chat.busy) {
      queryClient.invalidateQueries({ queryKey: ["assistant-home", id] });
    }
    wasBusy.current = chat.busy;
  }, [chat.busy, id, queryClient]);

  useEffect(() => {
    if (!chat.sessionId || opened.current === "new") return;
    if (params.get("session") === chat.sessionId) return;
    opened.current = chat.sessionId;
    const next = new URLSearchParams(params);
    next.set("session", chat.sessionId);
    next.delete("q");
    setParams(next, { replace: true });
  }, [chat.sessionId]);

  const data = home.data;
  const named = (data?.directions || []).filter((d) => !String(d.name || "").startsWith("↳"));
  const dirs = named.filter((d) => {
    const name = String(d.name || "");
    return !named.some((other) => {
      const o = String(other.name || "");
      return o && o !== name && o.length >= 8 && name.startsWith(o);
    });
  });
  const lead = data ? readableLead(data.lab_brief, dirs) : "";

  const writeParams = (patch) => {
    const next = new URLSearchParams(params);
    Object.entries(patch).forEach(([key, value]) => {
      if (value) next.set(key, value);
      else next.delete(key);
    });
    setParams(next, { replace: true });
  };

  const askHere = (q, extra = {}) => {
    const dir = extra.direction_id || scopeDir;
    opened.current = "";
    chat.ask(q, {
      context: extra.context || (dir ? { kind: "direction", id: dir } : undefined),
      direction_id: dir || undefined,
      replace: extra.replace,
    });
  };

  const startNew = () => {
    closed.current = chat.sessionId || session;
    opened.current = "new";
    chat.reset();
    writeParams({ session: "", q: "" });
  };

  const openSession = (row) => {
    opened.current = row.session_id || "";
    chat.openPast(row);
    writeParams({ session: row.session_id || "", q: "" });
  };

  const saveTitle = async (row) => {
    const title = draft.trim();
    if (!title || !row.session_id) return;
    setRowBusy(row.session_id);
    try {
      await api.renameAskSession(id, row.session_id, title);
      setEditing("");
      await queryClient.invalidateQueries({ queryKey: ["assistant-home", id] });
    } catch (err) {
      message.error(formatError(err, "没改成"));
    } finally {
      setRowBusy("");
    }
  };

  const removeSession = async (row) => {
    if (!row.session_id) return;
    setRowBusy(row.session_id);
    try {
      await api.deleteAskSession(id, row.session_id);
      setConfirming("");
      if ((chat.sessionId || session) === row.session_id) startNew();
      await queryClient.invalidateQueries({ queryKey: ["assistant-home", id] });
    } catch (err) {
      message.error(formatError(err, "没删掉"));
    } finally {
      setRowBusy("");
    }
  };

  const welcome = !chat.turns.length && data ? (
    <div className="fs-ask-welcome">
      <h2 className="fs-ask-hello">问问这个组</h2>
      {lead ? <p className="fs-ask-lead">{lead}</p> : null}
      <div className="fs-ask-starters">
        {STARTERS.map(([label, q]) => (
          <button key={q} type="button" className="fs-ask-suggest" onClick={() => askHere(q)}>
            {label}
          </button>
        ))}
      </div>
      <LabGlanceMini labId={id} overview={data.overview} />
    </div>
  ) : null;

  const past = (data?.ask_sessions || []).filter((s) => s.question || s.title);
  const needle = finder.trim().toLowerCase();
  const matched = needle
    ? past.filter((s) => `${s.title || ""} ${s.question || ""} ${s.answer || ""}`.toLowerCase().includes(needle))
    : past;
  const buckets = ["今天", "昨天", "更早"]
    .map((name) => ({ name, rows: matched.filter((s) => whenBucket(s.created_at) === name) }))
    .filter((b) => b.rows.length);
  if (!buckets.length && matched.length) buckets.push({ name: "更早", rows: matched });
  const currentId = chat.sessionId || session;
  const scopeSlot = (
    <Select
      className="fs-ask-scope"
      size="small"
      value={scopeDir || "all"}
      aria-label="问的范围"
      popupMatchSelectWidth={false}
      onChange={(v) => setScopeDir(!v || v === "all" ? "" : v)}
      options={[{ value: "all", label: "全组" }, ...dirs.map((d) => ({ value: d.id, label: shortTitle(d.name) }))]}
    />
  );

  return (
    <div className="fs-ask-page">
      <PageHeader title="助手" />
      {home.isLoading && !data ? <PageLoading label="正在打开这个组…" /> : null}
      <div className="fs-ask-layout">
        <aside className="fs-ask-side">
          <button type="button" className="fs-ask-new" onClick={startNew}>
            新对话
          </button>
          <input
            className="fs-ask-find"
            value={finder}
            placeholder="搜对话"
            aria-label="搜对话"
            onChange={(e) => setFinder(e.target.value)}
          />
          {data?.my_role === "teacher" ? (
            <AgentVoice
              labId={id}
              voice={data.agent_voice}
              intro={data.agent_intro}
              teachers={data.teachers}
              onSaved={() => queryClient.invalidateQueries({ queryKey: ["assistant-home", id] })}
            />
          ) : null}
          {data && !past.length ? <p className="fs-ask-bucket">还没有问过</p> : null}
          {data && past.length && !matched.length ? <p className="fs-ask-bucket">没有对得上的对话</p> : null}
          {buckets.map((b) => (
            <div key={b.name}>
              <p className="fs-ask-bucket">{b.name}</p>
              {b.rows.map((s) => {
                const label = s.title || s.question;
                const on = s.session_id && s.session_id === currentId;
                const busyRow = rowBusy === s.session_id;
                return (
                  <div
                    key={s.session_id || s.question}
                    className={`fs-ask-past-btn${on ? " is-on" : ""}${editing === s.session_id ? " is-editing" : ""}`}
                  >
                    {editing === s.session_id ? (
                      <form
                        className="fs-ask-rename"
                        onSubmit={(e) => {
                          e.preventDefault();
                          saveTitle(s);
                        }}
                      >
                        <input
                          value={draft}
                          aria-label="对话名称"
                          maxLength={80}
                          autoFocus
                          onChange={(e) => setDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Escape") setEditing("");
                          }}
                        />
                        <button type="submit" disabled={busyRow || !draft.trim()}>
                          保存
                        </button>
                        <button type="button" onClick={() => setEditing("")}>
                          取消
                        </button>
                      </form>
                    ) : (
                      <>
                        <button type="button" className="fs-ask-past-open" title={s.question || label} onClick={() => openSession(s)}>
                          <span className="fs-ask-past-main">
                            <span className="fs-ask-past-q">{label}</span>
                            {previewAnswer(s.answer) ? <span className="fs-ask-past-a">{previewAnswer(s.answer)}</span> : null}
                          </span>
                          {clock(s.created_at) ? <span className="fs-ask-past-time">{clock(s.created_at)}</span> : null}
                        </button>
                        <span className="fs-ask-past-actions">
                          {confirming === s.session_id ? (
                            <>
                              <button type="button" disabled={busyRow} onClick={() => removeSession(s)}>
                                确认删除
                              </button>
                              <button type="button" onClick={() => setConfirming("")}>
                                留下
                              </button>
                            </>
                          ) : (
                            <>
                              <button
                                type="button"
                                onClick={() => {
                                  setConfirming("");
                                  setEditing(s.session_id);
                                  setDraft(label);
                                }}
                              >
                                改名
                              </button>
                              <button
                                type="button"
                                onClick={() => {
                                  setEditing("");
                                  setConfirming(s.session_id);
                                }}
                              >
                                删除
                              </button>
                            </>
                          )}
                        </span>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </aside>
        <AssistantChat
          labId={id}
          turns={chat.turns}
          busy={chat.busy}
          welcome={welcome}
          quiet={!chat.turns.length}
          placeholder="问这个组"
          scopeSlot={scopeSlot}
          onAsk={askHere}
          onStop={chat.stop}
          pendingConfirm={chat.pendingConfirm}
          onConfirm={chat.confirm}
        />
      </div>
    </div>
  );
}
