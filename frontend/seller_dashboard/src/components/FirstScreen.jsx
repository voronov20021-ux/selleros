import { COPY, humanFigureLabel, humanFormula, humanFunnel, humanizeText, humanMarket, inferProblemState, isTechLeak, looksLikeNoProblem, presentNumber, problemStateCopy } from "../labels";

export default function FirstScreen({ cards, score = null, status = null }) {
  if (!cards) {
    return (
      <div className="card empty-state">
        <strong>Разбор ещё не собран</strong>
        <p className="muted">Недостаточно данных для проверки.</p>
      </div>
    );
  }
  const problemState = inferProblemState({
    status,
    score,
    figures: cards.figures,
    verdictKind: cards.verdict_kind,
    cardHealthy: cards.card_healthy,
    funnel: cards.funnel_consistency,
  });
  const doItems = (cards.do || []).filter((t) => !isTechLeak(t)).map((t) => humanizeText(t)).filter(Boolean);
  const dontItems = (cards.dont || []).filter((t) => !isTechLeak(t)).map((t) => humanizeText(t)).filter(Boolean);
  const checkItems = (cards.check || []).filter((t) => !isTechLeak(t)).map((t) => humanizeText(t)).filter(Boolean);
  let verdict = humanizeText(cards.verdict, "");
  if (!verdict) {
    verdict = problemState === "confirmed" ? "Короткого диагноза пока нет" : problemStateCopy(problemState);
  } else if (looksLikeNoProblem(verdict) && problemState !== "confirmed") {
    verdict = problemStateCopy(problemState);
  }
  const figures = (cards.figures || []).filter((f) => {
    const label = humanFigureLabel(f.label);
    if (!label) return false;
    if (isTechLeak(f.label) || isTechLeak(f.value)) return false;
    return true;
  });

  return (
    <div className="fs-grid">
      <div className="card fs-card verdict">
        <h3>Главный вывод</h3>
        <p>{verdict}</p>
        {(cards.confidence || cards.priority_tier) && (
          <p className="muted" style={{ marginTop: 8 }}>
            {cards.confidence && !isTechLeak(cards.confidence)
              ? `Уверенность: ${humanizeText(cards.confidence)}. `
              : ""}
            {cards.priority_tier &&
            cards.priority_tier !== "NONE" &&
            !isTechLeak(cards.priority_tier)
              ? `Приоритет ${humanizeText(cards.priority_tier)}`
              : ""}
          </p>
        )}
      </div>

      <div className="card fs-card">
        <h3>Ключевые цифры</h3>
        {figures.length ? (
          <ul className="fs-list">
            {figures.map((f, i) => (
              <li key={`${f.label}-${i}`}>
                {humanFigureLabel(f.label)}: {formatFigureValue(f.value)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">Нет данных продавца по воронке — цифры не подставляем.</p>
        )}
        {humanFunnel(cards.funnel_consistency?.status) && cards.funnel_consistency?.status && (
          <p className="muted" style={{ marginTop: 8 }}>
            {humanFunnel(cards.funnel_consistency.status)}
            {cards.funnel_consistency.check_line
              ? `. ${humanizeText(cards.funnel_consistency.check_line)}`
              : ""}
          </p>
        )}
      </div>

      <div className="card fs-card do">
        <h3>Что делать</h3>
        {doItems.length ? (
          <ul className="fs-list">
            {doItems.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">Пока нет подтверждённого шага</p>
        )}
        {!!cards.idea_only?.length && (
          <p className="muted" style={{ marginTop: 8 }}>
            Сигнал для проверки:{" "}
            {cards.idea_only
              .filter((t) => !isTechLeak(t))
              .slice(0, 2)
              .map((t) => humanizeText(t))
              .join(" · ")}
          </p>
        )}
      </div>

      <div className="card fs-card dont">
        <h3>Что не трогать</h3>
        {dontItems.length ? (
          <ul className="fs-list">
            {dontItems.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">
            {problemState === "insufficient"
              ? COPY.problemInsufficient
              : "Ограничений нет"}
          </p>
        )}
      </div>

      <div className="card fs-card check">
        <h3>Что проверить</h3>
        {checkItems.length ? (
          <ul className="fs-list">
            {checkItems.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">
            {problemState === "insufficient" ? COPY.problemInsufficient : "Отдельной проверки нет"}
          </p>
        )}
      </div>
    </div>
  );
}

export function UnitBlock({ unit }) {
  if (!unit || unit.complete !== true) {
    const partial = Boolean(unit?.partial) && unit?.text;
    return (
      <div>
        <p style={{ marginTop: 0 }}>{COPY.unitMissing}</p>
        <p className="muted">{COPY.unitNeedCost}</p>
        {partial ? (
          <p className="muted">
            {humanizeText(unit.text)} Предварительная оценка, не прибыль.
          </p>
        ) : null}
      </div>
    );
  }
  const formula = humanFormula(unit.status);
  return (
    <div>
      {unit.text && <p style={{ marginTop: 0 }}>{humanizeText(unit.text)}</p>}
      {unit.honesty && <p className="muted">{humanizeText(unit.honesty)}</p>}
      {formula ? <p className="muted">{formula}</p> : null}
    </div>
  );
}

export function MarketBlock({ market, competitors }) {
  const list = Array.isArray(competitors) ? competitors : [];
  if (!market && !list.length) {
    return (
      <p className="muted">Данные рынка не подтверждены. Медиану рынка не показываем.</p>
    );
  }
  const pos = market?.position || market?.band || market?.status;
  const count = presentNumber(market?.count) ?? presentNumber(market?.candidates) ?? (list.length || null);
  const marketText = humanizeText(market?.text, "");
  if (isTechLeak(market?.text) && !marketText && !list.length && count == null) {
    return <p className="muted">Данные рынка не подтверждены. Медиану рынка не показываем.</p>;
  }
  return (
    <div>
      {count != null ? (
        <p style={{ marginTop: 0 }}>
          {count} похож{count === 1 ? "ая карточка" : "их карточек"}
          {market && !market.commercial_confirmed && market.price == null
            ? ", но цены не подтверждены."
            : "."}
        </p>
      ) : null}
      {pos && !isTechLeak(pos) && (
        <p className="muted">
          По цене: {humanMarket(pos)}
        </p>
      )}
      {marketText ? <p className="muted">{marketText}</p> : null}
      {list.slice(0, 5).map((c, i) => (
        <div className="meta-line" key={c.article || i}>
          {c.title && <span>{c.title}</span>}
          {c.article && <span>nmID {c.article}</span>}
          {c.price != null && <span>{c.price} ₽</span>}
          {c.rating != null && presentNumber(c.rating) != null && presentNumber(c.feedback_count) !== 0 && (
            <span>★ {c.rating}</span>
          )}
        </div>
      ))}
    </div>
  );
}

function formatFigureValue(value) {
  if (value == null || value === "") return "Нет данных";
  const n = presentNumber(value);
  if (n != null && (typeof value === "number" || /^-?\d+([.,]\d+)?$/.test(String(value).trim()))) {
    return String(n);
  }
  const text = humanizeText(String(value), "");
  return text || "Нет данных";
}
