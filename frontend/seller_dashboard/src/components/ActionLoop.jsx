import { humanAction, humanVerify } from "../labels";

export default function ActionLoop({ idea, action, busy, onAccept, onDone, onDefer }) {
  return (
    <div className="card">
      <p style={{ margin: 0, fontWeight: 700 }}>{idea}</p>
      {action ? (
        <p className="muted" style={{ margin: "6px 0 0" }}>
          {humanAction(action.status)}
          {action.verification_status
            ? ` · ${humanVerify(action.verification_status)}`
            : ""}
        </p>
      ) : (
        <p className="muted" style={{ margin: "6px 0 0" }}>
          Это идея ARGUS, ещё не решение. Пока не нажмёте «Принять» — в историю действий не попадёт.
        </p>
      )}
      <div className="action-row">
        <button className="btn" disabled={busy || !!action} onClick={onAccept}>
          Принять
        </button>
        <button className="btn secondary" disabled={busy || !action} onClick={onDone}>
          Сделал
        </button>
        <button className="btn ghost" disabled={busy || !action} onClick={onDefer}>
          Проверить позже
        </button>
      </div>
    </div>
  );
}
