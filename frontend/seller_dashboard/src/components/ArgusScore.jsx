export default function ArgusScore({ score = null, status = null, empty = false, count = 0 }) {
  if (empty || score == null) {
    return (
      <div className="argus-wrap">
        <div className="argus-ring empty-ring" aria-label="Здоровье карточек неизвестно">
          <div className="inner">
            <div className="score">—</div>
            <div className="label">Здоровье карточек</div>
          </div>
        </div>
        <div>
          <p style={{ margin: 0, fontWeight: 700 }}>Здоровье карточек</p>
          <p className="muted" style={{ margin: "4px 0 0" }}>
            {count ? `${count} товаров` : "нет товаров"}
          </p>
        </div>
      </div>
    );
  }

  const pct = `${Math.max(0, Math.min(100, Number(score) || 0))}%`;
  const st = status || (score >= 75 ? "GREEN" : score >= 50 ? "YELLOW" : "RED");
  const color =
    st === "GREEN" ? "var(--argus)" : st === "RED" ? "var(--danger)" : "var(--warn)";

  return (
    <div className="argus-wrap">
      <div
        className="argus-ring"
        style={{ "--pct": pct, "--ring-color": color }}
        aria-label={`Здоровье карточек ${score}%`}
      >
        <div className="inner">
          <div className="score">{score}%</div>
          <div className="label">Здоровье карточек</div>
        </div>
      </div>
      <div>
        <p style={{ margin: 0, fontWeight: 700 }}>Здоровье карточек</p>
        <p className="muted" style={{ margin: "4px 0 0" }}>
          {count} товар{count === 1 ? "" : count >= 2 && count <= 4 ? "а" : "ов"}
        </p>
      </div>
    </div>
  );
}
