import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import BackLink from "../components/BackLink";
import { EmptyState, ErrorState, Skeleton } from "../components/ScreenState";
import { MarketBlock } from "../components/FirstScreen";
import { analyzePublic, fetchProduct, getStickyArticle } from "../api";
import { loadSellerStore, parseWbArticle } from "../catalog";

export default function Market() {
  const [params] = useSearchParams();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [market, setMarket] = useState(null);
  const [article, setArticle] = useState(params.get("article") || getStickyArticle() || "");
  const [title, setTitle] = useState("");
  const [query, setQuery] = useState("");
  const [owned, setOwned] = useState(true);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        let art = article;
        if (!art) {
          const store = await loadSellerStore();
          art = store.products?.[0]?.article ? String(store.products[0].article) : "";
          if (alive) setArticle(art);
        }
        if (!art) {
          if (alive) setMarket(null);
          return;
        }
        const p = await fetchProduct(art);
        if (!alive) return;
        setTitle(p.title || "");
        setOwned(Boolean(p.owned));
        setMarket(p.first_screen?.details?.market_compare || null);
      } catch (e) {
        if (alive) setError(String(e.message || e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [article]);

  async function onQuery() {
    const nm = parseWbArticle(query) || null;
    setLoading(true);
    setError("");
    try {
      if (nm) {
        const p = await analyzePublic({ article: nm, text: query });
        setArticle(String(p.article));
        setTitle(p.title || "");
        setOwned(Boolean(p.owned));
        setMarket(p.first_screen?.details?.market_compare || null);
      } else {
        setError("Для рынка нужна ссылка WB или nmID. Запрос «посмотри рынок очков» без карточки кандидатов не ищет.");
        setMarket(null);
      }
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div>
        <BackLink to="/more" />
        <Skeleton rows={3} />
      </div>
    );
  }

  return (
    <div>
      <BackLink to="/more" />
      <div className="brand-row">
        <div className="brand">
          <strong>Рынок</strong>
          <span>Что происходит вокруг моего товара</span>
        </div>
      </div>
      <div className="card">
        <div className="field">
          <label>Ссылка или nmID — без добавления в каталог</label>
          <div className="diag-row">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="https://www.wildberries.ru/catalog/…"
            />
            <button type="button" className="btn secondary" onClick={onQuery}>
              Смотреть
            </button>
          </div>
        </div>
      </div>
      {error && <ErrorState text={error} />}
      {article && (
        <p className="muted">
          {title || "Товар"} · nmID {article}
          {owned ? "" : " · не в ваших товарах"}
        </p>
      )}
      <div className="card">
        {article ? (
          <MarketBlock market={market} />
        ) : (
          <EmptyState
            title="Нет товара для сравнения"
            text="Добавьте nmID или вставьте ссылку WB. Без карточки кандидатов не ищем и медиану не рисуем."
            to="/products"
            action="Добавить товар"
          />
        )}
      </div>
    </div>
  );
}
