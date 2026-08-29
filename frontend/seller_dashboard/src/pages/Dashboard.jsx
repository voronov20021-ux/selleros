import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import ArgusScore from "../components/ArgusScore";
import ProductCard from "../components/ProductCard";
import MissionBar from "../components/MissionBar";
import { EmptyState, ErrorState, Skeleton } from "../components/ScreenState";
import { attentionList, improveList, loadSellerStore } from "../catalog";
import { fetchActionHistory, fetchMissions, getAuthState } from "../api";
import { COPY, formatWhen, humanAction, inferProblemState } from "../labels";

export default function Dashboard({ displayName }) {
  const [store, setStore] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [missions, setMissions] = useState(null);
  const [changes, setChanges] = useState([]);

  const reload = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const s = await loadSellerStore();
      setStore(s);
      if (!s.ok && s.reason === "api") setError(s.error || "api");
      if (getAuthState().authenticated) {
        try {
          setMissions(await fetchMissions());
        } catch {
          setMissions(null);
        }
        try {
          const hist = await fetchActionHistory();
          setChanges((hist.items || []).slice(0, 5));
        } catch {
          setChanges([]);
        }
      }
    } catch (e) {
      setError(String(e.message || e));
      setStore({ ok: false, products: [], metrics: null });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const name =
    displayName && displayName !== "Гость" && displayName !== "seller"
      ? displayName
      : "продавец";

  if (loading) return <Skeleton rows={4} />;
  if (error && !store?.ok) return <ErrorState text={error} onRetry={reload} />;

  const products = store?.products || [];
  const attention = attentionList(products);
  const improve = improveList(products);
  const healthScore = store?.metrics?.argus_index;
  const emptyHealth = !products.length || healthScore == null;
  const scoredCount = store?.metrics?.scored_count ?? 0;
  const preliminary = !emptyHealth && scoredCount > 0 && scoredCount < products.length;
  const status =
    healthScore >= 75
      ? "GREEN"
      : healthScore >= 50
        ? "YELLOW"
        : healthScore != null
          ? "RED"
          : null;
  const firstArticle = products[0]?.article;
  const attentionState = products.length
    ? inferProblemState({
        status: attention.length ? "RED" : products.some((p) => p.argus_status) ? "GREEN" : null,
        score: healthScore,
        figures: products.some((p) => (p.first_screen?.figures || []).length) ? [{}] : [],
      })
    : "insufficient";

  return (
    <div>
      <div className="brand-row">
        <div className="brand">
          <strong>ARGUS</strong>
          <span>Seller OS · что сейчас происходит</span>
        </div>
      </div>

      <h1 className="greeting">Привет, {name}</h1>

      {missions && !missions.all_done && (
        <MissionBar missions={missions} article={firstArticle} />
      )}

      <div className="card" data-tour="health" data-mission="dashboard_ready">
        <ArgusScore
          score={emptyHealth ? null : healthScore}
          status={status}
          empty={emptyHealth}
          count={products.length}
          preliminary={preliminary}
        />
      </div>

      <h2 className="section-title">Требует внимания</h2>
      <div className="stack">
        {attention.map((p) => (
          <ProductCard key={p.article} product={p} />
        ))}
        {!attention.length && (
          <EmptyState
            title={
              !products.length
                ? "Товаров пока нет"
                : attentionState === "insufficient"
                  ? COPY.problemInsufficient
                  : COPY.problemNotConfirmed
            }
            text={
              products.length
                ? attentionState === "insufficient"
                  ? "По статусам карточек пока нельзя судить — нет оценок ARGUS."
                  : "Нет карточек со статусом «критично» или «требует внимания»."
                : "Товаров пока нет — нечего подсветить."
            }
            to="/products"
            action={products.length ? "К товарам" : "Добавить товар"}
            to2={products.length ? undefined : "/settings"}
            action2={products.length ? undefined : "Подключить WB позже"}
          />
        )}
      </div>

      <h2 className="section-title">Что можно улучшить</h2>
      <div className="stack">
        {improve.map((g) => (
          <Link className="card" key={g.article} to={`/products/${g.article}`}>
            <div className="badge status-GREEN">
              <span className="status-dot bg-GREEN" />
              {g.title}
            </div>
            <p className="muted" style={{ margin: "8px 0 0" }}>
              {(g.recommendations || [])[0]}
            </p>
          </Link>
        ))}
        {!improve.length && (
          <EmptyState
            title="Пока нет подтверждённых улучшений"
            text="ARGUS не выдумывает советы. Когда по зелёным карточкам появятся реальные рекомендации — они будут здесь."
            to="/products"
            action="К товарам"
          />
        )}
      </div>

      <h2 className="section-title">Что изменилось</h2>
      <div className="stack">
        {changes.map((h) => (
          <div className="card history-item" key={h.action_id}>
            <strong>{humanAction(h.status)}</strong>
            <p className="muted" style={{ margin: "4px 0 0" }}>
              {h.recommendation}
              {formatWhen(h.executed_at || h.accepted_at || h.created_at)
                ? ` · ${formatWhen(h.executed_at || h.accepted_at || h.created_at)}`
                : ""}
            </p>
          </div>
        ))}
        {!changes.length && (
          <EmptyState
            title="Пока нет подтверждённых изменений"
            text="Цена, рейтинг, CTR/CVR и действия появятся здесь после фактов, а не из демо."
          />
        )}
      </div>

      <div className="ask-argus">
        <Link className="btn" to="/assistant" data-tour="ask-argus">
          Спросить ARGUS
        </Link>
      </div>
    </div>
  );
}
