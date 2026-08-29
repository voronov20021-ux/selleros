import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import BackLink from "../components/BackLink";
import FirstScreen, { MarketBlock, UnitBlock } from "../components/FirstScreen";
import ActionLoop from "../components/ActionLoop";
import ProductImage from "../components/ProductImage";
import SellerDataForm from "../components/SellerDataForm";
import { EmptyState, ErrorState, Skeleton } from "../components/ScreenState";
import {
  acceptIdea,
  addCatalogProduct,
  analyzePublic,
  completeMission,
  deferAction,
  fetchActionHistory,
  fetchMissions,
  fetchProduct,
  markActionDone,
  setStickyArticle,
} from "../api";
import { COPY, formatHealth, formatRatingParts, humanArgus, humanFunnel, humanizeText, isTechLeak, presentNumber } from "../labels";

export default function ProductDetail() {
  const { article } = useParams();
  const [params] = useSearchParams();
  const preview = params.get("preview") === "1";
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [analysisMission, setAnalysisMission] = useState(false);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetchActionHistory(article);
      setHistory(res.items || []);
    } catch {
      setHistory([]);
    }
  }, [article]);

  const reload = useCallback(async () => {
    setLoading(true);
    setError("");
    setStickyArticle(article);
    try {
      let detail;
      if (preview) {
        detail = await analyzePublic({ article });
      } else {
        detail = await fetchProduct(article);
        if (detail.demo && !detail.owned) {
          detail = await analyzePublic({ article });
        }
      }
      setData(detail);
    } catch (e) {
      setError(String(e.message || e));
      setData(null);
    } finally {
      setLoading(false);
    }
    await loadHistory();
  }, [article, preview, loadHistory]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const m = await fetchMissions();
        const next = m?.next;
        if (alive) setAnalysisMission(next?.id === "first_analysis" && !m?.all_done);
      } catch {
        if (alive) setAnalysisMission(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [article]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function onAccept(idea) {
    setBusy(true);
    setMsg("");
    try {
      await acceptIdea(article, idea);
      setMsg("Принято как действие.");
      await loadHistory();
    } catch (e) {
      setMsg(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onFinishAnalysis() {
    setBusy(true);
    try {
      await completeMission("first_analysis");
      setAnalysisMission(false);
    } catch (e) {
      setMsg(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div>
        <BackLink to="/products" />
        <Skeleton rows={4} />
      </div>
    );
  }
  if (error) {
    return (
      <div>
        <BackLink to="/products" />
        <ErrorState text={error} onRetry={reload} />
      </div>
    );
  }
  if (!data || (data.demo && !data.owned && !data.first_screen)) {
    return (
      <div>
        <BackLink to="/products" />
        <EmptyState
          title="Этого товара нет в вашем магазине"
          text="Не показываем demo-каталог как ваш SKU."
          to="/products"
          action="Добавить товар"
        />
      </div>
    );
  }

  const cards = data.first_screen;
  const details = cards?.details || {};
  const ideas = cards?.do || [];
  const owned = Boolean(data.owned);
  const rating = formatRatingParts(data.rating, data.feedback_count ?? data.reviews_count);
  const health = formatHealth(data.argus_score);
  const reviewCount = presentNumber(data.feedback_count ?? data.reviews_count);
  const dynRaw = details.dynamic_analytics?.summary || details.dynamic_analytics?.text;
  const dynText = humanizeText(dynRaw, "");

  return (
    <div>
      <BackLink to="/products" />
      <div className="brand-row">
        <div className="brand">
          <strong>{owned ? "Что с этим товаром" : "Быстрый разбор"}</strong>
        </div>
      </div>

      {!owned && (
        <p className="muted">Карточка не в ваших товарах. Разбор публичный, ассортимент не меняли.</p>
      )}

      <div className="card detail-hero">
        <ProductImage src={data.image} alt="" />
        <div className="product-card-body">
          <h1 className="meta-title" style={{ fontSize: "1.05rem" }}>
            {data.title}
          </h1>
          <div className="meta-line">
            <span>nmID {data.article}</span>
            {data.brand && <span>{data.brand}</span>}
          </div>
          <div className="meta-line">
            {data.price != null && <span>{data.price} ₽</span>}
            <span>{rating.rating}</span>
            <span>{rating.reviews}</span>
          </div>
          {data.argus_status ? (
            <div className={`badge status-${data.argus_status}`}>
              <span className={`status-dot bg-${data.argus_status}`} />
              {humanArgus(data.argus_status)}
              {health.missing ? "" : ` · ${health.value}`}
            </div>
          ) : (
            <div className="badge status-YELLOW">Пока недостаточно данных</div>
          )}
          <p className="muted" style={{ margin: "6px 0 0" }}>
            {health.text}
          </p>
        </div>
      </div>

      {data.can_add && (
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await addCatalogProduct({ article: data.article });
              setMsg("Добавлен в мои товары.");
              await reload();
            } catch (e) {
              setMsg(String(e.message || e));
            } finally {
              setBusy(false);
            }
          }}
        >
          Добавить в мои товары
        </button>
      )}

      {owned && (
        <SellerDataForm
          article={article}
          sellerData={data.seller_data || {}}
          onSaved={(res) => {
            setData((prev) => ({
              ...prev,
              first_screen: res.first_screen || prev.first_screen,
              seller_data: res.seller_data || prev.seller_data,
              argus_score: res.argus_score ?? prev.argus_score,
            }));
          }}
        />
      )}

      {owned && (
        <div className="hub-row">
          <Link className="hub-link" to={`/finance?article=${article}`}>
            Деньги
          </Link>
          <Link className="hub-link" to={`/market?article=${article}`}>
            Рынок
          </Link>
          <Link className="hub-link" to={`/actions?article=${article}`}>
            Действия
          </Link>
          <Link className="hub-link" to="/assistant">
            Спросить ARGUS
          </Link>
        </div>
      )}

      <h2 className="section-title">Главный экран ARGUS</h2>
      <FirstScreen cards={cards} score={data.argus_score} status={data.argus_status} />

      {analysisMission && cards && (
        <div className="card" data-mission="first_analysis">
          <p style={{ marginTop: 0 }}>
            Это первый разбор ARGUS. Когда просмотрите вывод и цифры — закройте шаг.
          </p>
          <button type="button" className="btn" disabled={busy} onClick={onFinishAnalysis}>
            {COPY.missionDone}
          </button>
        </div>
      )}

      <details className="card details-block">
        <summary>Почему ARGUS так решил</summary>
        {(details.why || []).filter((w) => humanizeText(w) && !isTechLeak(w)).length ? (
          <ul className="fs-list">
            {(details.why || []).filter((w) => humanizeText(w) && !isTechLeak(w)).map((w) => (
              <li key={w}>{humanizeText(w)}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">Короткого объяснения пока нет.</p>
        )}
      </details>

      <details className="card details-block" open>
        <summary>Карточка</summary>
        <p className="muted">
          {data.title}. Цена {data.price != null ? `${data.price} ₽` : "неизвестна"}. {rating.rating}.{" "}
          {health.text}.
        </p>
      </details>

      <details className="card details-block">
        <summary>Отзывы</summary>
        <p className="muted">
          {reviewCount != null
            ? `${rating.reviews} на карточке.`
            : "Нет данных по отзывам."}{" "}
          {details.photos_analyzed ? "" : "Детальный разбор фото не делали."}
        </p>
      </details>

      <details className="card details-block">
        <summary>Воронка</summary>
        {cards?.funnel_consistency ? (
          <p className="muted">
            {humanFunnel(cards.funnel_consistency.status) ||
              humanizeText(cards.funnel_consistency.human_message || cards.funnel_consistency.check_line) ||
              "Есть проверка согласованности воронки."}
          </p>
        ) : (
          <p className="muted">Нет данных продавца по показам, кликам и заказам.</p>
        )}
      </details>

      <details className="card details-block">
        <summary>Экономика</summary>
        <UnitBlock unit={details.unit_economics} />
      </details>

      <details className="card details-block">
        <summary>Рынок</summary>
        <MarketBlock market={details.market_compare} />
      </details>

      <details className="card details-block">
        <summary>Динамика</summary>
        {dynText ? (
          <p className="muted">{dynText}</p>
        ) : (
          <p className="muted">{COPY.dynamicsMissing}</p>
        )}
      </details>

      {owned && (
        <>
          <h2 className="section-title">Действия</h2>
          <div className="stack" data-mission="first_action" data-tour="product-actions">
            {ideas.slice(0, 3).map((idea) => {
              const action = history.find((h) => h.recommendation === idea);
              return (
                <ActionLoop
                  key={idea}
                  idea={idea}
                  action={action}
                  busy={busy}
                  onAccept={() => onAccept(idea)}
                  onDone={async () => {
                    if (!action) return;
                    setBusy(true);
                    try {
                      await markActionDone(action.action_id);
                      await loadHistory();
                    } finally {
                      setBusy(false);
                    }
                  }}
                  onDefer={async () => {
                    if (!action) return;
                    setBusy(true);
                    try {
                      await deferAction(action.action_id, 3);
                      await loadHistory();
                    } finally {
                      setBusy(false);
                    }
                  }}
                />
              );
            })}
            {!ideas.length && (
              <EmptyState title="Пока нечего принимать" text="Нет шага «что делать» — идея не рисуется как действие." />
            )}
          </div>
          {msg && <p className="muted">{msg}</p>}

          <h2 className="section-title">История</h2>
          <div className="stack">
            {history.map((h) => (
              <div className="card history-item" key={h.action_id}>
                <strong>{h.recommendation}</strong>
                <p className="muted" style={{ margin: "4px 0 0" }}>
                  Решение зафиксировано. Статус смотрите в разделе «Действия».
                </p>
              </div>
            ))}
            {!history.length && <EmptyState title="Истории ещё нет." />}
          </div>
        </>
      )}
      {!owned && msg && <p className="muted">{msg}</p>}
    </div>
  );
}
