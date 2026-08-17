import { Link } from "react-router-dom";

export function EmptyState({ title, text, to, action, onClick, to2, action2, onClick2 }) {
  return (
    <div className="card empty-state">
      <strong>{title}</strong>
      {text && <p className="muted">{text}</p>}
      <div className="empty-actions">
        {to && (
          <Link className="btn" to={to}>
            {action}
          </Link>
        )}
        {!to && onClick && action && (
          <button type="button" className="btn" onClick={onClick}>
            {action}
          </button>
        )}
        {to2 && (
          <Link className="btn secondary" to={to2}>
            {action2}
          </Link>
        )}
        {!to2 && onClick2 && action2 && (
          <button type="button" className="btn secondary" onClick={onClick2}>
            {action2}
          </button>
        )}
      </div>
    </div>
  );
}

export function ErrorState({ text, onRetry }) {
  return (
    <div className="card empty-state">
      <strong>Не удалось получить данные WB.</strong>
      <p className="muted">{text || "Проверьте сеть и повторите попытку."}</p>
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          Повторить
        </button>
      )}
    </div>
  );
}

export function Skeleton({ rows = 3 }) {
  return (
    <div className="stack" aria-busy="true" aria-label="Загрузка">
      {Array.from({ length: rows }).map((_, i) => (
        <div className="card skeleton-card" key={i}>
          <div className="skeleton-line w60" />
          <div className="skeleton-line w90" />
          <div className="skeleton-line w40" />
        </div>
      ))}
    </div>
  );
}

export function AuthWall({ telegram, localDev }) {
  return (
    <div className="card empty-state">
      <strong>Seller OS</strong>
      {telegram ? (
        <p className="muted">
          Не удалось войти. Откройте Mini App заново из Telegram — без сессии магазин не показываем.
        </p>
      ) : (
        <p className="muted">
          Откройте Seller OS в Telegram. Без WebApp-сессии товары и цифры не подставляем.
        </p>
      )}
      {localDev && (
        <p className="muted">
          DEV preview (только localhost): на бэкенде задайте MINIAPP_DEV_AUTH=1, затем
          uvicorn на :8000 и npm run dev на :5175. Production HMAC не ослабляется.
        </p>
      )}
    </div>
  );
}
