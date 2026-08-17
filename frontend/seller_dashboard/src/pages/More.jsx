import { Link } from "react-router-dom";

const CONTACT_URL = "https://t.me/pinkod953";

const LINKS = [
  { to: "/finance", title: "Деньги", text: "Выручка, расходы и прибыль — только из известных данных" },
  { to: "/market", title: "Рынок", text: "Кандидаты и подтверждённые поля вокруг товара" },
  { to: "/actions", title: "Действия", text: "Что принято, сделано и проверяется" },
  { to: "/history", title: "История", text: "Что происходило раньше" },
  { to: "/settings", title: "⚙ Настройки", text: "Профиль, Wildberries, уведомления" },
];

const TELEGRAM_LINKS = [
  { title: "💬 Обратная связь", text: "Вопросы, замечания и проблемы" },
  { title: "💡 Новое предложение", text: "Идеи и новые функции" },
];

function openTelegramLink(url) {
  const tg = window.Telegram?.WebApp;
  try {
    if (tg?.openTelegramLink) {
      tg.openTelegramLink(url);
      return;
    }
    if (tg?.openLink) {
      tg.openLink(url);
      return;
    }
  } catch {
    /* fall through to browser tab */
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

export default function More() {
  return (
    <div>
      <div className="brand-row">
        <div className="brand">
          <strong>Кабинет</strong>
          <span>Деньги, рынок, действия и настройки</span>
        </div>
      </div>
      <div className="stack">
        {LINKS.map((item) => (
          <Link
            className="card more-link"
            key={item.to}
            to={item.to}
            data-tour={item.to === "/finance" ? "finance" : item.to === "/settings" ? "nav-settings" : undefined}
          >
            <strong>{item.title}</strong>
            <p className="muted" style={{ margin: "6px 0 0" }}>
              {item.text}
            </p>
          </Link>
        ))}
        {TELEGRAM_LINKS.map((item) => (
          <a
            className="card more-link"
            key={item.title}
            href={CONTACT_URL}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => {
              e.preventDefault();
              openTelegramLink(CONTACT_URL);
            }}
          >
            <strong>{item.title}</strong>
            <p className="muted" style={{ margin: "6px 0 0" }}>
              {item.text}
            </p>
          </a>
        ))}
      </div>
    </div>
  );
}
