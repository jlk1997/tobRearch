import { useCallback, useRef, useState } from "react";
import { message } from "antd";
import { api } from "../../api.js";
import { formatError } from "../../err.js";
import { clipPreview, surroundingContext } from "../../pdfText.js";

/** 辅导提问。走 Agent 运行时（后端可多轮调工具），事件流仍是同一个 SSE。 */
export function useTutor({ id, page, session, setSession, tutor, setTutor, pageText, reload, scope }) {
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(null);
  const [ungrounded, setUngrounded] = useState(false);
  const [nextStep, setNextStep] = useState(null);
  const [emptyNext, setEmptyNext] = useState(null);
  const [steps, setSteps] = useState([]);
  const [trace, setTrace] = useState(null);
  const [sources, setSources] = useState([]);
  const [writeNote, setWriteNote] = useState(false);
  const [busy, setBusy] = useState(false);
  const [degraded, setDegraded] = useState("");
  const [pendingConfirm, setPendingConfirm] = useState([]);
  const [followups, setFollowups] = useState([]);
  const [availableTools, setAvailableTools] = useState([]);
  const lastAsk = useRef({ q: "", excerpt: "", context: "" });
  const abortRef = useRef(null);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const send = useCallback(
    async (qIn, excerpt, context, opts) => {
      if (busy) return;
      const q = String(qIn || question).trim();
      if (!q) return;
      const pageNo = Number(opts?.pageNo) || page;
      lastAsk.current = { q, excerpt: excerpt || "", context: context || "" };
      setPending({ question: q, page_no: pageNo, stream: "", excerpt: excerpt || "" });
      if (!qIn) setQuestion("");
      setBusy(true);
      setSteps([]);
      setPendingConfirm([]);
      setEmptyNext(null);
      setFollowups([]);
      const ac = new AbortController();
      abortRef.current = ac;
      try {
        const r = await api.tutorLiterature(
          id,
          {
            question: q,
            page_no: pageNo,
            write_note: writeNote,
            session_id: session || undefined,
            scope: scope || undefined,
            stream: true,
            excerpt: excerpt || undefined,
            context: context || undefined,
            confirm: Boolean(opts?.confirm) || undefined,
            confirm_tools: opts?.confirm
              ? (opts.confirmTools || []).map((p) => ({ tool: p.tool, args: p.args || {} }))
              : undefined,
          },
          {
            signal: ac.signal,
            onDelta: (_delta, acc, meta) => {
              setPending((cur) => (cur ? { ...cur, stream: acc } : cur));
              if (meta?.tool) setSteps((prev) => [...prev, meta]);
            },
          }
        );
        setPending(null);
        setUngrounded(Boolean(r.data.ungrounded));
        setNextStep(r.data.next_step || null);
        setEmptyNext(r.data.empty_next || null);
        setTrace(r.data.trace_id || r.data.session_id || null);
        setSources(r.data.sources || []);
        setDegraded(r.data.degraded || r.data.degraded_to || "");
        setAvailableTools(r.data.available_tools || []);
        setPendingConfirm(r.data.pending_confirm || []);
        setFollowups(r.data.followups || []);
        if (r.data.next_question) setQuestion(r.data.next_question);
        if (r.data.session_id) setSession(r.data.session_id);
        setTutor((prev) => ({
          ...prev,
          total: prev.total + 1,
          sessions: r.data.session_id && !prev.sessions.some((s) => s.session_id === r.data.session_id)
            ? [{ session_id: r.data.session_id, last_question: q, turns: 1 }, ...prev.sessions]
            : prev.sessions,
          items: [
            ...prev.items,
            {
              id: r.data.id,
              question: q,
              answer: r.data.answer,
              page_no: pageNo,
              session_id: r.data.session_id,
              ungrounded: r.data.ungrounded,
              next_question: r.data.next_question,
              excerpt: r.data.excerpt || excerpt || "",
              excerpt_preview: r.data.excerpt_preview || clipPreview(excerpt || r.data.excerpt || ""),
              followup_prompt: r.data.followup_prompt || "",
              followups: r.data.followups || [],
              sources: r.data.sources || [],
              citations: r.data.citations || [],
              steps: r.data.steps || [],
            },
          ],
        }));
        if (r.data.quota_warning) message.warning(r.data.quota_warning);
        if (writeNote) reload({ session: r.data.session_id || session, tutorPage: 1 });
      } catch (e) {
        if (e?.code === "ERR_CANCELED" || e?.name === "CanceledError" || e?.name === "AbortError") {
          setPending(null);
          return;
        }
        setPending((cur) => (cur ? { ...cur, error: formatError(e, "辅导未完成") } : cur));
      } finally {
        setBusy(false);
        abortRef.current = null;
      }
    },
    [busy, id, page, question, reload, scope, session, setSession, setTutor, writeNote]
  );

  const followExcerpt = useCallback(
    (excerpt, prompt) => {
      if (!excerpt) return;
      send(prompt || "这几句再讲细一点？", excerpt, surroundingContext(pageText, excerpt));
    },
    [pageText, send]
  );

  const saveAsNote = useCallback(
    async (turn) => {
      if (!turn?.answer) return;
      try {
        await api.addNote(id, turn.answer, turn.page_no || page);
        message.success("已写入本页笔记");
        reload({ session, tutorPage: 1 });
      } catch (e) {
        message.error(formatError(e, "无法保存笔记"));
      }
    },
    [id, page, reload, session]
  );

  const reset = useCallback(() => {
    setPending(null);
    setUngrounded(false);
    setSteps([]);
    setTrace(null);
    setSources([]);
    setDegraded("");
    setPendingConfirm([]);
    setEmptyNext(null);
    setFollowups([]);
    setAvailableTools([]);
  }, []);

  const confirmPending = useCallback(() => {
    const last = lastAsk.current;
    if (!last.q) return;
    return send(last.q, last.excerpt, last.context, { confirm: true, confirmTools: pendingConfirm });
  }, [pendingConfirm, send]);

  return {
    question,
    setQuestion,
    pending,
    ungrounded,
    nextStep,
    emptyNext,
    steps,
    trace,
    sources,
    writeNote,
    setWriteNote,
    busy,
    send,
    stop,
    reset,
    followExcerpt,
    saveAsNote,
    degraded,
    availableTools,
    pendingConfirm,
    confirmPending,
    followups,
  };
}
