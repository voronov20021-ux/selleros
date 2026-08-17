import { useEffect, useState } from "react";
import { completeMission, evaluateFormulaLesson, fetchFormulaLesson } from "../api";
import { EmptyState, ErrorState, Skeleton } from "../components/ScreenState";
import { humanFormula } from "../labels";

export default function Lesson() {
  const [lesson, setLesson] = useState(null);
  const [impressions, setImpressions] = useState("");
  const [clicks, setClicks] = useState("");
  const [orders, setOrders] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await fetchFormulaLesson();
        if (alive) setLesson(data);
        await completeMission("ctr_lesson").catch(() => null);
      } catch (e) {
        if (alive) setError(String(e.message || e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  async function onEval() {
    setBusy(true);
    try {
      const res = await evaluateFormulaLesson({
        impressions: impressions ? Number(impressions) : null,
        clicks: clicks ? Number(clicks) : null,
        orders: orders ? Number(orders) : null,
      });
      setResult(res);
    } catch (e) {
      setResult({ error: String(e.message || e) });
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Skeleton rows={2} />;
  if (error) return <ErrorState text={error} />;
  if (!lesson) return <EmptyState title="Урок недоступен" />;

  return (
    <div data-mission="ctr_lesson">
      <div className="brand-row">
        <div className="brand">
          <strong>{lesson.title}</strong>
          <span>Как ARGUS считает CTR и CVR</span>
        </div>
      </div>

      {(lesson.formulas || []).map((f) => (
        <div className="card" key={f.name || f.formula_id}>
          <h3 style={{ margin: "0 0 6px" }}>{f.name}</h3>
          <pre className="lesson-formula">{f.expression}</pre>
          {(f.limitations || []).map((n) => (
            <p className="muted" key={n} style={{ marginBottom: 0 }}>
              {n}
            </p>
          ))}
        </div>
      ))}

      <div className="card">
        {(lesson.notes || []).map((n) => (
          <p className="muted" key={n}>
            {n}
          </p>
        ))}
      </div>

      <h2 className="section-title">Ваши цифры</h2>
      <div className="card">
        <div className="field">
          <label>Показы</label>
          <input value={impressions} onChange={(e) => setImpressions(e.target.value)} inputMode="numeric" />
        </div>
        <div className="field">
          <label>Клики</label>
          <input value={clicks} onChange={(e) => setClicks(e.target.value)} inputMode="numeric" />
        </div>
        <div className="field">
          <label>Заказы</label>
          <input value={orders} onChange={(e) => setOrders(e.target.value)} inputMode="numeric" />
        </div>
        <button className="btn" disabled={busy} onClick={onEval}>
          Посчитать из Formula Authority
        </button>
      </div>

      {result && !result.error && (
        <div className="card">
          <p>
            <strong>CTR</strong> · {humanFormula(result.ctr?.status)}
            {result.ctr?.value != null ? ` = ${result.ctr.value}` : " — числа нет"}
          </p>
          <p className="muted">{result.explain_ctr}</p>
          <p>
            <strong>CVR</strong> · {humanFormula(result.cvr?.status)}
            {result.cvr?.value != null ? ` = ${result.cvr.value}` : " — числа нет"}
          </p>
          <p className="muted">{result.explain_cvr}</p>
        </div>
      )}
      {result?.error && <div className="card muted">{result.error}</div>}
    </div>
  );
}
