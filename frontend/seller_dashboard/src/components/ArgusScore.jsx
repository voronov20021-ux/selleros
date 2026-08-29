import { presentNumber } from "../labels";

export default function ArgusScore({
  score = null,
  status = null,
  empty = false,
  count = 0,
  preliminary = false,
}) {
  const n = presentNumber(score);
  if (empty || n == null) {
    return (
      <div className="argus-wrap">
        <div className="argus-ring empty-ring" aria-label="Здоровье карточки неизвестно">
          <div className="inner">
            <div className="score">—</div>
            <div className="label">Здоровье карточки</div>
          </div>
        </div>
        <div>
          <p style={{ margin: 0, fontWeight: 700 }}>Здоровье карточки</p>
          <p className="muted" style={{ margin: "4px 0 0" }}>
            Недостаточно данных
          </p>
          <p className="muted" style={{ margin: "4px 0 0" }}>
            {count ? `${count} товаров` : "нет товаров"}
          </p>
        </div>
      </div>
    );
  }

  const clamped = Math.max(0, Math.min(100, n));
  const pct = `${clamped}%`;
  const st = status || (clamped >= 75 ? "GREEN" : clamped >= 50 ? "YELLOW" : "RED");
  const color =
    st === "GREEN" ? "var(--argus)" : st === "RED" ? "var(--danger)" : "var(--warn)";

  return (
    <div className="argus-wrap">
      <div
        className="argus-ring"
        style={{ "--pct": pct, "--ring-color": color }}
        aria-label={`Здоровье карточки ${pct}`}
      >
        <div className="inner">
          <div className="score">{pct}</div>
          <div className="label">Здоровье карточки</div>
        </div>
      </div>
      <div>
        <p style={{ margin: 0, fontWeight: 700 }}>Здоровье карточки</p>
        <p className="muted" style={{ margin: "4px 0 0" }}>
          {preliminary ? "Предварительная оценка" : null}
          {preliminary ? " · " : ""}
          {count} товар{count === 1 ? "" : count >= 2 && count <= 4 ? "а" : "ов"}
        </p>
      </div>
    </div>
  );
}
