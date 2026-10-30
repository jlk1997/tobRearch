import { Link } from "react-router-dom";

export function EmptyState({ title, children, action }) {
  return (
    <div className="fs-empty">
      <strong>{title}</strong>
      {children ? <p>{children}</p> : null}
      {action || null}
    </div>
  );
}

export function SkeletonRows({ n = 4 }) {
  return (
    <div className="fs-skeleton" aria-hidden>
      {Array.from({ length: n }, (_, i) => (
        <span key={i} />
      ))}
    </div>
  );
}

export function StatStrip({ items }) {
  const rows = (items || []).filter((it) => it && (it.value || it.label));
  if (!rows.length) return null;
  return (
    <div className="fs-statstrip">
      {rows.map((it) => {
        const inner = (
          <>
            {it.value ? <b>{it.value}</b> : null} {it.label}
          </>
        );
        return it.href ? (
          <Link key={it.label} to={it.href}>
            {inner}
          </Link>
        ) : (
          <span key={it.label}>{inner}</span>
        );
      })}
    </div>
  );
}

export function FilterBar({ children, chips }) {
  return (
    <div className="fs-filterbar">
      {children}
      {(chips || []).map((c) => (
        <button key={c.key || c.label} type="button" className="fs-filter-chip" onClick={c.onClear}>
          {c.label}
          <span aria-hidden>×</span>
        </button>
      ))}
    </div>
  );
}

export function ListRow({ title, meta, aside, to, onClick, menu }) {
  const body = (
    <>
      <span className="fs-listrow-main">
        <span className="fs-listrow-title">{title}</span>
        {meta ? <span className="fs-listrow-meta">{meta}</span> : null}
      </span>
      {aside ? <span className="fs-listrow-aside">{aside}</span> : null}
      {menu || null}
    </>
  );
  if (to) {
    return (
      <Link className="fs-listrow" to={to} onClick={onClick}>
        {body}
      </Link>
    );
  }
  return (
    <div className="fs-listrow" onClick={onClick} role={onClick ? "button" : undefined}>
      {body}
    </div>
  );
}

export function FloatingBar({ children }) {
  return <div className="fs-float-bar">{children}</div>;
}

export function SidePanel({ open, title, onClose, children }) {
  if (!open) return null;
  return (
    <aside className="fs-source-panel">
      <div className="fs-source-panel-head">
        <strong>{title}</strong>
        {onClose ? (
          <button type="button" className="fs-link-btn" onClick={onClose}>
            收起
          </button>
        ) : null}
      </div>
      {children}
    </aside>
  );
}
