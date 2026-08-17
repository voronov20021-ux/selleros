import { humanFormula, humanFunnel, humanizeText, humanMarket } from "../labels";

export default function FirstScreen({ cards }) {
  if (!cards) {
    return (
      <div className="card empty-state">
        <strong>Разбор ещё не собран</strong>
        <p className="muted">Нет данных продавца для первого экрана ARGUS.</p>
      </div>
    );
  }
  const details = cards.details || {};
  return (
    <div className="fs-grid">
      <div className="card fs-card verdict">
        <h3>Главный вывод</h3>
        <p>{humanizeText(cards.verdict, "Данных недостаточно")}</p>
        {(cards.confidence || cards.priority_tier) && (
          <p className="muted" style={{ marginTop: 8 }}>
            {cards.confidence ? `Уверенность: ${cards.confidence}. ` : ""}
            {cards.priority_tier && cards.priority_tier !== "NONE"
              ? `Приоритет ${cards.priority_tier}`
              : ""}
          </p>
        )}
      </div>

      <div className="card fs-card">
        <h3>Ключевые цифры</h3>
        {cards.figures?.length ? (
          <ul className="fs-list">
            {cards.figures.map((f, i) => (
              <li key={`${f.label}-${i}`}>
                {f.label}: {humanizeText(String(f.value))}
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
        {cards.do?.length ? (
          <ul className="fs-list">
            {cards.do.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">Пока нет подтверждённого шага</p>
        )}
        {!!cards.idea_only?.length && (
          <p className="muted" style={{ marginTop: 8 }}>
            Сигнал для проверки: {cards.idea_only.slice(0, 2).join(" · ")}
          </p>
        )}
      </div>

      <div className="card fs-card dont">
        <h3>Что не трогать</h3>
        {cards.dont?.length ? (
          <ul className="fs-list">
            {cards.dont.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">Ограничений нет</p>
        )}
      </div>

      <div className="card fs-card check">
        <h3>Что проверить</h3>
        {cards.check?.length ? (
          <ul className="fs-list">
            {cards.check.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">Отдельной проверки нет</p>
        )}
      </div>
    </div>
  );
}

export function UnitBlock({ unit }) {
  if (!unit) {
    return (
      <p className="muted">
        Нет данных продавца. Прибыль не определена: не хватает комиссии и логистики.
      </p>
    );
  }
  const missing = unit.missing || [];
  return (
    <div>
      {unit.text && <p style={{ marginTop: 0 }}>{unit.text}</p>}
      {unit.honesty && <p className="muted">{unit.honesty}</p>}
      <p className="muted">
        Статус: {humanFormula(unit.status || (unit.complete ? "KNOWN" : "MISSING"))}
      </p>
      {!!missing.length && (
        <p className="muted">Не хватает: {missing.join(", ")}</p>
      )}
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
  const count = market?.count || market?.candidates || list.length;
  return (
    <div>
      {count ? (
        <p style={{ marginTop: 0 }}>
          {count} похож{count === 1 ? "ая карточка" : "их карточек"}
          {market && !market.commercial_confirmed && !market.price
            ? ", но цены не подтверждены."
            : "."}
        </p>
      ) : null}
      {pos && (
        <p className="muted">
          По цене: {humanMarket(pos)}
        </p>
      )}
      {market?.text && <p className="muted">{market.text}</p>}
      {market?.quality && (
        <p className="muted">Качество свидетельств: {String(market.quality)}</p>
      )}
      {list.slice(0, 5).map((c, i) => (
        <div className="meta-line" key={c.article || i}>
          {c.title && <span>{c.title}</span>}
          {c.article && <span>nmID {c.article}</span>}
          {c.price != null && <span>{c.price} ₽</span>}
          {c.rating != null && <span>★ {c.rating}</span>}
        </div>
      ))}
    </div>
  );
}
