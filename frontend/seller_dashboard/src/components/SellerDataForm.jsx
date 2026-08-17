import { useState } from "react";
import { saveSellerData } from "../api";

const FIELDS = [
  { key: "ctr", label: "CTR, %" },
  { key: "cvr", label: "CVR, %" },
  { key: "impressions", label: "Показы" },
  { key: "views", label: "Просмотры" },
  { key: "clicks", label: "Клики" },
  { key: "sales", label: "Продажи" },
  { key: "orders", label: "Заказы" },
  { key: "returns", label: "Возвраты" },
  { key: "ads", label: "Реклама, ₽", from: "ad_spend" },
  { key: "cogs", label: "Себестоимость, ₽", from: "cost" },
  { key: "commission", label: "Комиссия, ₽" },
  { key: "logistics", label: "Логистика, ₽" },
  { key: "storage", label: "Хранение, ₽" },
  { key: "period", label: "Период" },
];

function knownValue(seller, field) {
  const src = field.from || field.key;
  const v = seller?.[src];
  return v == null || v === "" ? null : v;
}

export default function SellerDataForm({ article, sellerData, onSaved }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [values, setValues] = useState({});

  const known = FIELDS.filter((f) => knownValue(sellerData, f) != null);
  const missing = FIELDS.filter((f) => knownValue(sellerData, f) == null);

  async function onSave() {
    setBusy(true);
    setMsg("");
    try {
      const body = {};
      for (const f of missing) {
        const raw = values[f.key];
        body[f.key] = raw == null || String(raw).trim() === "" ? "-" : String(raw).trim();
      }
      const res = await saveSellerData(article, body);
      setMsg("Сохранено. ARGUS пересчитал вывод. Действие само не создаётся.");
      setOpen(false);
      setValues({});
      onSaved?.(res);
    } catch (e) {
      setMsg(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" data-tour="seller-data">
      <button type="button" className="btn" onClick={() => setOpen((v) => !v)}>
        + Добавить данные продавца
      </button>
      <p className="muted">
        Это ваши цифры кабинета, не публичная карточка. Прочерк «-» — пропустить поле.
      </p>
      {open && (
        <div className="seller-form">
          {!!known.length && (
            <>
              <p className="muted">Уже известно — не спрашиваем снова:</p>
              <ul className="fs-list">
                {known.map((f) => (
                  <li key={f.key}>
                    {f.label}: {knownValue(sellerData, f)}
                  </li>
                ))}
              </ul>
            </>
          )}
          {missing.map((f) => (
            <div className="field" key={f.key}>
              <label>{f.label}</label>
              <input
                value={values[f.key] || ""}
                onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                placeholder="число или -"
              />
            </div>
          ))}
          {!missing.length && <p className="muted">Все поля уже заполнены.</p>}
          <button type="button" className="btn" disabled={busy || !missing.length} onClick={onSave}>
            Сохранить данные
          </button>
        </div>
      )}
      {msg && <p className="muted">{msg}</p>}
    </div>
  );
}
