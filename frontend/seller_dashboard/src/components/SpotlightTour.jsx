import { useEffect, useLayoutEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { fetchMissions, skipMissions } from "../api";

const STEPS = [
  {
    id: "home",
    path: "/",
    sel: "[data-tour='health']",
    title: "Главная",
    text: "Здоровье карточек — средний балл по вашим товарам. Без карточек здесь прочерк, не чужие цифры.",
  },
  {
    id: "products",
    path: "/products",
    sel: "[data-tour='nav-products']",
    title: "Товары",
    text: "Каталог только из вашего магазина. Demo-карточки сюда не попадают.",
  },
  {
    id: "add-product",
    path: "/products",
    sel: "[data-tour='add-product']",
    title: "Добавить товар",
    text: "Ссылка WB, nmID или честный отказ, если кабинета нет. После добавления — живые данные карточки.",
  },
  {
    id: "assistant",
    path: "/assistant",
    sel: "[data-tour='assistant-composer']",
    title: "ARGUS",
    text: "Пишите свободно: «Напишите что происходит». Кнопки — только ускорители.",
  },
  {
    id: "finance",
    path: "/more",
    sel: "[data-tour='finance']",
    title: "Деньги",
    text: "Магазин и товар — разные срезы. Прибыль не рисуем, если расходов не хватает.",
  },
  {
    id: "wb",
    path: "/settings",
    sel: "[data-tour='wb-connect']",
    title: "Подключить WB позже",
    text: "Не обязательно. Без ключа можно разбирать публичные карточки. Ключ — чтобы ARGUS сам подтягивал данные магазина.",
  },
  {
    id: "analysis",
    path: "/products",
    sel: "[data-tour='products-list']",
    title: "Первый анализ",
    text: "Добавьте товар, откройте его и посмотрите вывод ARGUS. Шаг закрывается кнопкой «Понятно», а не случайным экраном.",
  },
  {
    id: "action",
    path: "/actions",
    sel: "[data-tour='actions']",
    title: "Действия",
    text: "Идея становится действием только после «Принять». Потом — «Сделал» и проверка.",
  },
];

const KEY_STEP = "selleros_tour_step";
const KEY_DONE = "selleros_tour_done";

function readStep() {
  try {
    return Number(localStorage.getItem(KEY_STEP) || 0) || 0;
  } catch {
    return 0;
  }
}

export default function SpotlightTour({ enabled }) {
  const [missions, setMissions] = useState(null);
  const [idx, setIdx] = useState(readStep);
  const [rect, setRect] = useState(null);
  const [hidden, setHidden] = useState(() => {
    try {
      return localStorage.getItem(KEY_DONE) === "1";
    } catch {
      return false;
    }
  });
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    (async () => {
      try {
        const m = await fetchMissions();
        if (alive) setMissions(m);
      } catch {
        if (alive) setMissions(null);
      }
    })();
    return () => {
      alive = false;
    };
  }, [enabled, location.pathname]);

  const step = STEPS[idx] || STEPS[0];

  useEffect(() => {
    if (!enabled || hidden || !step) return;
    if (location.pathname !== step.path && !(step.path === "/" && location.pathname === "/")) {
      navigate(step.path);
    }
  }, [enabled, hidden, idx, step, location.pathname, navigate]);

  useLayoutEffect(() => {
    if (!enabled || hidden) return;
    const el = document.querySelector(step.sel);
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      const r = el.getBoundingClientRect();
      setRect({
        top: r.top - 6,
        left: r.left - 6,
        width: r.width + 12,
        height: r.height + 12,
      });
    } else {
      setRect(null);
    }
  }, [enabled, hidden, idx, step, location.pathname]);

  if (!enabled || hidden) return null;
  if (missions?.overlay_skipped || missions?.all_done) return null;

  function persist(next) {
    try {
      localStorage.setItem(KEY_STEP, String(next));
    } catch {
      /* ignore */
    }
  }

  function finish() {
    try {
      localStorage.setItem(KEY_DONE, "1");
    } catch {
      /* ignore */
    }
    setHidden(true);
  }

  async function onSkip() {
    try {
      await skipMissions(true);
    } catch {
      /* still close */
    }
    finish();
  }

  function onNext() {
    if (idx >= STEPS.length - 1) {
      finish();
      return;
    }
    const next = idx + 1;
    persist(next);
    setIdx(next);
  }

  const hole = rect
    ? {
        top: Math.max(8, rect.top),
        left: Math.max(8, rect.left),
        width: Math.min(rect.width, window.innerWidth - 16),
        height: rect.height,
      }
    : null;

  return (
    <div className="tour-root" role="dialog" aria-label="Обучение Seller OS">
      <div className="tour-dim" />
      {hole && (
        <div
          className="tour-hole"
          style={{
            top: hole.top,
            left: hole.left,
            width: hole.width,
            height: hole.height,
          }}
        />
      )}
      <div className="card tour-tip">
        <div className="muted" style={{ marginBottom: 6 }}>
          Шаг {idx + 1} из {STEPS.length}
        </div>
        <h3 style={{ margin: "0 0 6px" }}>{step.title}</h3>
        <p className="muted" style={{ margin: "0 0 12px" }}>
          {step.text}
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="btn secondary" onClick={onSkip}>
            Пропустить
          </button>
          <button type="button" className="btn" onClick={onNext}>
            {idx >= STEPS.length - 1 ? "Готово" : "Продолжить"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function resetTour() {
  try {
    localStorage.removeItem(KEY_DONE);
    localStorage.setItem(KEY_STEP, "0");
  } catch {
    /* ignore */
  }
}
