import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import ProductCard from "../components/ProductCard";
import FirstScreen from "../components/FirstScreen";
import ProductImage from "../components/ProductImage";
import { EmptyState, ErrorState, Skeleton } from "../components/ScreenState";
import { loadSellerStore, parseWbArticle } from "../catalog";
import { addCatalogProduct, analyzePublic, fetchWbStatus, refreshCatalog } from "../api";
import { formatWhen } from "../labels";

const FILTERS = [
  { id: "all", label: "Все" },
  { id: "attention", label: "Требует внимания" },
  { id: "improve", label: "Можно улучшить" },
  { id: "ok", label: "Без критичных сигналов" },
];

export default function Products() {
  const navigate = useNavigate();
  const [filter, setFilter] = useState("all");
  const [store, setStore] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [addMode, setAddMode] = useState("link");
  const [addValue, setAddValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [refreshState, setRefreshState] = useState(null);
  const [wb, setWb] = useState(null);
  const [quick, setQuick] = useState("");
  const [quickResult, setQuickResult] = useState(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const s = await loadSellerStore();
      setStore(s);
      if (!s.ok && s.reason === "api") setError(s.error || "");
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
    fetchWbStatus()
      .then(setWb)
      .catch(() => setWb(null));
  }, [reload]);

  const products = useMemo(() => {
    const all = store?.products || [];
    if (filter === "attention") {
      return all.filter((p) => p.argus_status === "RED" || p.argus_status === "YELLOW");
    }
    if (filter === "improve") {
      return all.filter((p) => p.argus_status === "GREEN" && (p.recommendations || []).length);
    }
    if (filter === "ok") {
      return all.filter((p) => p.argus_status === "GREEN");
    }
    return all;
  }, [store, filter]);

  async function onAdd() {
    const article = parseWbArticle(addValue);
    if (!article && addMode !== "cabinet") {
      setMsg("Нужна ссылка Wildberries или nmID.");
      return;
    }
    setBusy(true);
    setMsg("");
    try {
      const res = await addCatalogProduct({
        article,
        url: addMode === "link" ? addValue : null,
        text: addValue,
      });
      setMsg(`Товар ${res.title || res.article} добавлен из карточки WB.`);
      setAddValue("");
      setShowAdd(false);
      await reload();
      if (res.article) navigate(`/products/${res.article}`);
    } catch (e) {
      setMsg(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onRefresh() {
    setBusy(true);
    setRefreshState({ phase: "loading" });
    setMsg("");
    try {
      const res = await refreshCatalog();
      setRefreshState({
        phase: "done",
        count: res.found ?? res.count ?? 0,
        updated_at: res.updated_at,
      });
      await reload();
    } catch (e) {
      setRefreshState({ phase: "error", text: String(e.message || e) });
    } finally {
      setBusy(false);
    }
  }

  async function onQuick() {
    const article = parseWbArticle(quick);
    if (!article) {
      setMsg("Вставьте ссылку на карточку WB.");
      return;
    }
    setBusy(true);
    setMsg("");
    try {
      const res = await analyzePublic({ url: quick, article, text: quick });
      setQuickResult(res);
    } catch (e) {
      setMsg(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  const pingOk = Boolean(wb?.connected && wb?.capabilities?.ping);

  return (
    <div>
      <div className="brand-row">
        <div className="brand">
          <strong>Товары</strong>
          <span>Что происходит с моими карточками</span>
        </div>
      </div>

      <div className="toolbar-row">
        <button type="button" className="btn" data-tour="add-product" onClick={() => setShowAdd((v) => !v)}>
          + Добавить товар
        </button>
        <button type="button" className="btn secondary" disabled={busy} onClick={onRefresh}>
          ↻ Обновить ассортимент
        </button>
      </div>

      {refreshState?.phase === "loading" && <p className="muted">Обновляем ассортимент...</p>}
      {refreshState?.phase === "done" && (
        <p className="muted">
          Найдено {refreshState.count} товар
          {refreshState.count === 1 ? "" : "ов"}
          {refreshState.updated_at ? ` · обновлено ${formatWhen(refreshState.updated_at)}` : ""}
        </p>
      )}
      {refreshState?.phase === "error" && <ErrorState text={refreshState.text} onRetry={onRefresh} />}

      {showAdd && (
        <div className="card" data-tour="add-product-form">
          <div className="filters">
            <button type="button" className={`chip ${addMode === "link" ? "active" : ""}`} onClick={() => setAddMode("link")}>
              Ссылка WB
            </button>
            <button type="button" className={`chip ${addMode === "nm" ? "active" : ""}`} onClick={() => setAddMode("nm")}>
              Артикул / nmID
            </button>
            <button type="button" className={`chip ${addMode === "cabinet" ? "active" : ""}`} onClick={() => setAddMode("cabinet")}>
              Из кабинета
            </button>
          </div>
          {addMode === "cabinet" ? (
            <EmptyState
              title="Нет доступа к кабинету"
              text={
                pingOk
                  ? "Ключ проверен (ping), но список товаров кабинета этим доступом не подтверждаем — импорт не рисуем."
                  : "Подключите Wildberries в настройках. Даже после ping ассортимент кабинета может быть недоступен."
              }
              to="/settings"
              action="Подключить WB"
            />
          ) : (
            <>
              <div className="field">
                <label>{addMode === "link" ? "Ссылка на карточку" : "nmID"}</label>
                <input
                  value={addValue}
                  onChange={(e) => setAddValue(e.target.value)}
                  placeholder={addMode === "link" ? "https://www.wildberries.ru/catalog/…" : "211246754"}
                />
              </div>
              <button type="button" className="btn" disabled={busy || !addValue.trim()} onClick={onAdd}>
                Добавить
              </button>
            </>
          )}
        </div>
      )}

      <div className="card">
        <strong>Быстрый анализ</strong>
        <p className="muted">Чужая карточка: разбор без добавления в ваши товары.</p>
        <div className="diag-row">
          <input
            value={quick}
            onChange={(e) => setQuick(e.target.value)}
            placeholder="Ссылка WB для быстрого разбора"
          />
          <button type="button" className="btn secondary" disabled={busy || !quick.trim()} onClick={onQuick}>
            Разобрать
          </button>
        </div>
        {quickResult && (
          <div className="quick-result">
            <div className="detail-hero" style={{ marginTop: 12 }}>
              <ProductImage src={quickResult.image} alt="" />
              <div>
                <strong>{quickResult.title}</strong>
                <p className="muted">nmID {quickResult.article}</p>
              </div>
            </div>
            <FirstScreen cards={quickResult.first_screen} />
            {quickResult.can_add && (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await addCatalogProduct({ article: quickResult.article });
                    await reload();
                    navigate(`/products/${quickResult.article}`);
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
          </div>
        )}
      </div>

      <div className="filters">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            className={`chip ${filter === f.id ? "active" : ""}`}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {loading && <Skeleton rows={3} />}
      {error && !loading && <ErrorState text={error} onRetry={reload} />}

      {!loading && !error && (
        <div className="stack" data-tour="products-list">
          {products.map((p) => (
            <ProductCard key={p.article} product={p} />
          ))}
          {!products.length && (
            <EmptyState
              title="Товаров пока нет"
              text="Подключите Wildberries или добавьте товар вручную."
              to="/settings"
              action="Подключить WB"
              onClick2={() => setShowAdd(true)}
              action2="Добавить товар"
            />
          )}
        </div>
      )}

      {msg && <p className="muted">{msg}</p>}
      {!loading && store?.onboarding && !store.onboarding.has_product && (
        <p className="muted" style={{ marginTop: 12 }}>
          <Link to="/settings">Подключить WB</Link>
        </p>
      )}
    </div>
  );
}
