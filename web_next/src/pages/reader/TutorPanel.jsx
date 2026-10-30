import { memo, useMemo } from "react";
import { Select } from "antd";
import { AssistantChat } from "../../assistant/AssistantChat.jsx";

const STARTERS = ["这篇在解决什么？", "方法怎么做的？", "结论和关键数字？", "这一页在说什么？"];

function sourceRows(rows) {
  return (rows || []).map((row, index) => ({
    ...row,
    label: row.label || `S${index + 1}`,
  }));
}

function TutorPanelInner({ id, page, scope, onScope, tutor, ctl, onOpenSource }) {
  const turns = useMemo(() => {
    const past = (tutor.items || []).map((row) => ({
      q: row.question || row.q || "",
      answer: row.answer || "",
      sources: sourceRows(row.citations || row.sources),
      steps: row.steps || [],
      followups: row.followups || [],
      ungrounded: Boolean(row.ungrounded),
      error: row.error || "",
    }));
    if (ctl.pending) {
      past.push({
        q: ctl.pending.question || ctl.pending.q || "",
        answer: ctl.pending.stream || "",
        sources: sourceRows(ctl.sources),
        steps: ctl.steps || [],
        followups: [],
        error: ctl.pending.error || "",
      });
    }
    return past;
  }, [tutor.items, ctl.pending, ctl.sources, ctl.steps]);

  return (
    <AssistantChat
      labId={id}
      turns={turns}
      busy={ctl.busy}
      quiet={!turns.length}
      placeholder="问这篇"
      starters={STARTERS}
      prefill={ctl.question || ""}
      scopeSlot={
        <Select
          size="small"
          aria-label="问的范围"
          value={scope || "paper"}
          onChange={onScope}
          options={[
            { value: "paper", label: "这一篇" },
            { value: "lab", label: "全组" },
          ]}
        />
      }
      onAsk={(q) => ctl.send(q)}
      onOpenSource={onOpenSource}
      onStop={ctl.stop}
      actions={(turn) =>
        turn.answer ? (
          <button type="button" className="fs-link-btn" onClick={() => ctl.saveAsNote?.({ answer: turn.answer, page_no: page })}>
            写进笔记
          </button>
        ) : null
      }
    />
  );
}

export const TutorPanel = memo(TutorPanelInner);
