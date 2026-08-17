import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { EmptyState, ErrorState, Skeleton } from "../components/ScreenState";
import { UnitBlock } from "../components/FirstScreen";
import { fetchFinance, getStickyArticle } from "../api";
import { loadSellerStore } from "../catalog";
import { humanFormula } from "../labels";

export default function Finance() {
  const [params, setParams] = useSearchParams();
  const [scope, setScope] = useState(params.get("article") ? "product" : "shop");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [data, setData] = useState(null);
  const [products, setProducts] = useState([]);
  const [article, setArticle] = useState(params.get("article") || getStickyArticle() || "");

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      setError("");
      try {
        const store = await loadSellerStore();
        const list = (store.products || []).filter((p) => !p.demo);
        if (alive) setProducts(list);
        let art = article;
        if (scope === "product") {
          if (art && !list.some((p) => String(p.article) === String(art))) {
            art = list[0] ? String(list[0].article) : "";
            if (alive) setArticle(art);
          } else if (!art && list[0]) {
            art = String(list[0].article);
            if (alive) setArticle(art);
          }
        }
        const snap = await fetchFinance({
          scope,
          article: scope === "product" && art ? art : undefined,
        });
        if (alive) setData(snap);
      } catch (e) {
        if (alive) setError(String(e.message || e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [scope, article]);

  const selected = useMemo(
    () => products.find((p) => String(p.article) === String(article)),
    [products, article]
  );

  if (loading) return <Skeleton rows={3} />;
  if (error) return <ErrorState text={error} />;

  const lines = data?.lines || [];
  const tariffs = data?.tariffs || {};

  return (
    <div>
      <div className="brand-row">
        <div className="brand">
          <strong>Деньги</strong>
          <span>Сколько я реально зарабатываю</span>
        </div>
      </div>

      <div className="filters" data-tour="finance">
        <button type="button" className={`chip ${scope === "shop" ? "active" : ""}`} onClick={() => setScope("shop")}>
          Магазин
        </button>
        <button type="button" className={`chip ${scope === "product" ? "active" : ""}`} onClick={() => setScope("product")}>
          Товар
        </button>
      </div>

      {scope === "shop" && (
        <p className="muted">Сводка по магазину. Без выручки и расходов кабинета прибыль не считаем.</p>
      )}

      {scope === "product" && !products.length && (
        <EmptyState
          title="Нет товара для экономики"
          text="Добавьте карточку в каталог. Demo SKU сюда не попадают."
          to="/products"
          action="Добавить товар"
        />
      )}

      {scope === "product" && !!products.length && (
        <div className="card">
          <div className="field">
            <label>Товар</label>
            <select
              value={article}
              onChange={(e) => {
                const next = e.target.value;
                setArticle(next);
                const nextParams = new URLSearchParams(params);
                nextParams.set("article", next);
                setParams(nextParams, { replace: true });
              }}
            >
              {products.map((p) => (
                <option key={p.article} value={String(p.article)}>
                  {p.title} · nmID {p.article}
                </option>
              ))}
            </select>
          </div>
          {selected && (
            <p className="muted" style={{ marginBottom: 0 }}>
              Выбран: {selected.title} · nmID {selected.article}
            </p>
          )}
        </div>
      )}

      {scope === "product" && data?.unit_economics && !!products.length && (
        <div className="card">
          <UnitBlock unit={data.unit_economics} />
        </div>
      )}

      {!(scope === "product" && !products.length) && (
        <>
          <h2 className="section-title">Статьи</h2>
          <div className="stack">
            {lines.map((line) => (
              <div className="card" key={line.id || line.label}>
                <strong>{line.label}</strong>
                <p className="muted" style={{ margin: "4px 0 0" }}>
                  {line.value != null
                    ? `${line.value}${line.id === "margin" ? "%" : " ₽"}`
                    : line.text || "Нет данных"}
                  {line.status ? ` · ${humanFormula(line.status)}` : ""}
                </p>
              </div>
            ))}
          </div>

          <h2 className="section-title">Тарифы WB</h2>
          <div className="card">
            {tariffs.confirmed ? (
              <p style={{ margin: 0 }}>
                Комиссия {tariffs.commission ?? "—"} · логистика {tariffs.logistics ?? "—"}
              </p>
            ) : (
              <p className="muted" style={{ margin: 0 }}>
                {tariffs.note || "Тариф не подтверждён"}
              </p>
            )}
          </div>
        </>
      )}

      {data?.note && <p className="muted">{data.note}</p>}
    </div>
  );
}
