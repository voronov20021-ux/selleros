import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import ActionLoop from "../components/ActionLoop";
import { EmptyState, ErrorState, Skeleton } from "../components/ScreenState";
import {
  acceptIdea,
  deferAction,
  fetchActionHistory,
  fetchProduct,
  markActionDone,
} from "../api";
import { formatWhen, humanAction, humanVerify } from "../labels";

export default function Actions() {
  const [params] = useSearchParams();
  const article = params.get("article") || "";
  const [items, setItems] = useState([]);
  const [ideas, setIdeas] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const hist = await fetchActionHistory(article || undefined);
      setItems(hist.items || []);
      if (article) {
        try {
          const p = await fetchProduct(article);
          setIdeas(p.first_screen?.do || []);
        } catch {
          setIdeas([]);
        }
      } else {
        setIdeas([]);
      }
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }, [article]);

  useEffect(() => {
    reload();
  }, [reload]);

  if (loading) return <Skeleton rows={3} />;
  if (error) return <ErrorState text={error} onRetry={reload} />;

  return (
    <div data-tour="actions">
      <div className="brand-row">
        <div className="brand">
          <strong>Действия</strong>
          <span>Что я уже решил и что проверяется</span>
        </div>
      </div>

      {!!ideas.length && (
        <>
          <h2 className="section-title">Ещё не принято</h2>
          <div className="stack">
            {ideas.map((idea) => {
              const action = items.find((h) => h.recommendation === idea);
              if (action) return null;
              return (
                <ActionLoop
                  key={idea}
                  idea={idea}
                  action={null}
                  busy={busy}
                  onAccept={async () => {
                    setBusy(true);
                    try {
                      await acceptIdea(article, idea);
                      await reload();
                    } finally {
                      setBusy(false);
                    }
                  }}
                />
              );
            })}
          </div>
        </>
      )}

      <h2 className="section-title">Зафиксированные действия</h2>
      <div className="stack">
        {items.map((h) => (
          <div className="card" key={h.action_id}>
            <strong>{h.recommendation}</strong>
            <p className="muted" style={{ margin: "6px 0 0" }}>
              {humanAction(h.status)}
              {h.verification_status ? ` · ${humanVerify(h.verification_status)}` : ""}
            </p>
            {h.expected_effect && <p className="muted">Ожидание: {h.expected_effect}</p>}
            {h.check_after && (
              <p className="muted">Проверить после: {formatWhen(h.check_after)}</p>
            )}
            <div className="action-row">
              <button className="btn" disabled>
                {humanAction(h.status)}
              </button>
              <button
                className="btn secondary"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await markActionDone(h.action_id);
                    await reload();
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Сделал
              </button>
              <button
                className="btn ghost"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await deferAction(h.action_id, 3);
                    await reload();
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Проверить позже
              </button>
            </div>
          </div>
        ))}
        {!items.length && (
          <EmptyState
            title="Действий пока нет"
            text="Идеи разбора не считаются решениями, пока не нажмёте «Принять»."
          />
        )}
      </div>
    </div>
  );
}
