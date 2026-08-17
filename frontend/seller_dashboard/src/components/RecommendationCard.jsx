export default function RecommendationCard({ title, problems = [], recommendations = [], status }) {
  return (
    <div className="card rec-card">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <h4>{title}</h4>
        {status && (
          <span className={`badge status-${status}`}>
            <span className={`status-dot bg-${status}`} />
            {status}
          </span>
        )}
      </div>
      {!!problems.length && (
        <>
          <p className="muted" style={{ margin: "0 0 4px" }}>
            Проблемы
          </p>
          <ul>
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </>
      )}
      {!!recommendations.length && (
        <>
          <p className="muted" style={{ margin: "10px 0 4px" }}>
            Рекомендации
          </p>
          <ul>
            {recommendations.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </>
      )}
      {!problems.length && !recommendations.length && (
        <p className="muted" style={{ margin: 0 }}>
          Замечаний нет
        </p>
      )}
    </div>
  );
}
