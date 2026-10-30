import { useRef, useState } from "react";
import { api } from "../api.js";

export function useAssistant(labId) {
  const [turns, setTurns] = useState([]);
  const [sessionId, setSessionId] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingConfirm, setPendingConfirm] = useState([]);
  const busyRef = useRef(false);
  const abortRef = useRef(null);
  const ticketRef = useRef(0);

  const ask = async (q, extra = {}) => {
    const text = String(q || "").trim();
    if (!text || !labId || busyRef.current) return;
    busyRef.current = true;
    const ticket = ticketRef.current;
    const replace = !!extra.replace;
    const prior = replace ? turns.slice(0, -1) : turns;
    const history = prior.slice(-2).map((t) => ({ q: t.q, a: t.answer || "" }));
    const blank = { q: text, answer: "", citations: [], steps: [], followups: [], sources: [], stopped: false };
    setTurns((prev) => (replace && prev.length ? [...prev.slice(0, -1), blank] : [...prev, blank]));
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let frame = 0;
    let pending = null;
    const paint = () => {
      frame = 0;
      const snap = pending;
      pending = null;
      if (!snap || snap.ticket !== ticketRef.current) return;
      setTurns((prev) => {
        const next = prev.slice();
        const last = { ...(next[next.length - 1] || { q: snap.text }) };
        if (snap.acc) last.answer = snap.acc;
        if (snap.ev?.tool && !snap.ev.done) last.steps = [...(last.steps || []), snap.ev];
        next[next.length - 1] = last;
        return next;
      });
    };
    try {
      const r = await api.askLabStream(
        labId,
        text,
        {
          signal: ctrl.signal,
          onDelta: (_d, acc, ev) => {
            pending = { acc, ev, ticket, text };
            if (!frame) frame = requestAnimationFrame(paint);
          },
        },
        {
          scope: extra.scope || "lab",
          session_id: sessionId || extra.session_id || undefined,
          history,
          context: extra.context,
          direction_id: extra.direction_id,
          confirm: extra.confirm || undefined,
          confirm_tools: extra.confirm_tools,
        }
      );
      const data = r.data || {};
      if (ticket !== ticketRef.current) return;
      if (data.session_id) setSessionId(data.session_id);
      setTurns((prev) => {
        const next = prev.slice();
        const last = { ...(next[next.length - 1] || { q: text }) };
        last.answer = data.answer || data.text || last.answer || "";
        last.citations = data.citations || [];
        last.steps = data.steps || last.steps || [];
        last.followups = data.followups || [];
        last.sources = (data.sources || []).length ? data.sources : data.hits || [];
        last.trace_id = data.trace_id || data.session_id || "";
        last.ungrounded = data.ungrounded;
        last.askBack = data.ask_back || null;
        last.pending = data.pending_confirm || [];
        setPendingConfirm(data.pending_confirm || []);
        last.groups = data.relation_groups || [];
        next[next.length - 1] = last;
        return next;
      });
    } catch (err) {
      if (ticket !== ticketRef.current) return;
      if (ctrl.signal.aborted) {
        setTurns((prev) => {
          const next = prev.slice();
          const last = { ...(next[next.length - 1] || { q: text }) };
          last.stopped = true;
          next[next.length - 1] = last;
          return next;
        });
        return;
      }
      const raw = String(err?.response?.data?.detail || err?.message || "").trim();
      const msg =
        raw && !/^failed to fetch$/i.test(raw) && raw.length < 200
          ? raw
          : "这次没问成。过一会儿再问一次。";
      setTurns((prev) => {
        const next = prev.slice();
        const last = { ...(next[next.length - 1] || { q: text }) };
        last.answer = last.answer || msg;
        last.error = String(err?.message || msg);
        next[next.length - 1] = last;
        return next;
      });
    } finally {
      if (ticket === ticketRef.current) {
        busyRef.current = false;
        setBusy(false);
        abortRef.current = null;
      }
    }
  };

  const confirm = () => {
    const last = turns[turns.length - 1];
    if (!last?.q) return;
    return ask(last.q, {
      replace: true,
      confirm: true,
      confirm_tools: (pendingConfirm || []).map((p) => ({ tool: p.tool, args: p.args || {} })),
    });
  };
  const stop = () => abortRef.current?.abort();
  const reset = () => {
    ticketRef.current += 1;
    abortRef.current?.abort();
    busyRef.current = false;
    setBusy(false);
    setTurns([]);
    setSessionId("");
  };
  const openPast = (session) => {
    const q = String(session?.question || "").trim();
    const prior = Array.isArray(session?.turns) ? session.turns : [];
    const restored = prior
      .map((t) => ({
        q: String(t.q || t.question || "").trim(),
        answer: t.a || t.answer || "",
        citations: [],
        steps: [],
        followups: [],
        sources: t.sources || [],
      }))
      .filter((t) => t.q);
    if (!restored.length && !q) return;
    setSessionId(session.session_id || "");
    setTurns(restored.length ? restored : [{ q, answer: session.answer || "", citations: [], steps: [], followups: [], sources: [] }]);
  };

  return { turns, setTurns, sessionId, setSessionId, busy, ask, stop, reset, openPast, pendingConfirm, confirm };
}
