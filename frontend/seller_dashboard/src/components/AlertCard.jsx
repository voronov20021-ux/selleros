export default function AlertCard({ alert }) {
  const severity = alert.severity || "YELLOW";
  return (
    <div className="card alert-card">
      <span className={`mark bg-${severity}`} />
      <div>
        <strong style={{ fontSize: "0.9rem" }}>{alert.title}</strong>
        <p className="muted" style={{ margin: "4px 0 0" }}>
          арт. {alert.article} — {alert.message}
        </p>
      </div>
    </div>
  );
}
