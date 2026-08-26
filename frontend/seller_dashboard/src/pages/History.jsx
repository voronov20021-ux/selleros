import { useCallback, useEffect, useState } from "react";
import BackLink from "../components/BackLink";
import { EmptyState, ErrorState, Skeleton } from "../components/ScreenState";
import { fetchActionHistory } from "../api";
import { formatWhen, humanAction, humanVerify } from "../labels";

export default function History() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const hist = await fetchActionHistory();
      setItems(hist.items || []);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  if (loading) {
    return (
      <div>
        <BackLink to="/more" />
        <Skeleton rows={3} />
      </div>
    );
  }
  if (error) {
    return (
      <div>
        <BackLink to="/more" />
        <ErrorState text={error} onRetry={reload} />
      </div>
    );
  }

  return (
    <div>
      <BackLink to="/more" />
      <div className="brand-row">
        <div className="brand">
          <strong>История</strong>
          <span>Что происходило раньше</span>
        </div>
      </div>
      <div className="stack">
        {items.map((h) => (
          <div className="card history-item" key={h.action_id}>
            <strong>{h.recommendation}</strong>
            <p className="muted" style={{ margin: "6px 0 0" }}>
              {humanAction(h.status)}
              {h.verification_status ? ` · ${humanVerify(h.verification_status)}` : ""}
              {formatWhen(h.executed_at || h.accepted_at || h.created_at)
                ? ` · ${formatWhen(h.executed_at || h.accepted_at || h.created_at)}`
                : ""}
            </p>
            {h.article && <p className="muted">nmID {h.article}</p>}
          </div>
        ))}
        {!items.length && <EmptyState title="Истории ещё нет." />}
      </div>
    </div>
  );
}
