import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import FirstScreen from "../components/FirstScreen";
import { EmptyState, ErrorState } from "../components/ScreenState";
import {
  addCatalogProduct,
  fetchAssistantContext,
  fetchProduct,
  getStickyArticle,
  sendAssistantChat,
  setStickyArticle,
} from "../api";
import { parseWbArticle } from "../catalog";
import { humanizeText } from "../labels";

const SECONDARY = [
  { id: "unit", label: "Юнит-экономика" },
  { id: "market", label: "Рынок" },
  { id: "dyn", label: "Динамика" },
  { id: "funnel", label: "Воронка" },
];

export default function Assistant() {
  const [article, setArticle] = useState(getStickyArticle() || "");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [log, setLog] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(null);
  const [followups, setFollowups] = useState([]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const ctx = await fetchAssistantContext();
        if (!alive) return;
        if (ctx.article) setArticle(String(ctx.article));
        if (ctx.followups) setFollowups(ctx.followups);
      } catch (e) {
        if (alive) setError(String(e.message || e));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      if (!article) {
        setTitle("");
        return;
      }
      try {
        const p = await fetchProduct(article);
        if (alive) setTitle(p.title || "");
      } catch {
        if (alive) setTitle("");
      }
    })();
    return () => {
      alive = false;
    };
  }, [article]);

  async function send(message, chip) {
    const body = (message || "").trim();
    if (!body && !chip) return;
    setBusy(true);
    setError("");
    if (body) setLog((prev) => [...prev, { role: "user", text: body }]);
    setText("");
    try {
      if (article) await setStickyArticle(article);
      const res = await sendAssistantChat({
        text: body || chip,
        article: article || null,
        chip: chip || null,
      });
      if (res.kind === "public_analyze") {
        setPreview(res);
        if (res.article) setArticle(String(res.article));
      }
      if (res.followups) setFollowups(res.followups);
      setLog((prev) => [...prev, { role: "bot", text: humanizeText(res.text || "Нет ответа") }]);
    } catch (e) {
      setLog((prev) => [
        ...prev,
        { role: "bot", text: "Не удалось получить ответ ARGUS. Повторите попытку." },
      ]);
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="assistant-page">
      <div className="brand-row">
        <div className="brand">
          <strong>ARGUS</strong>
          <span>Твой помощник по магазину</span>
        </div>
      </div>

      {article ? (
        <div className="sticky-chip">
          <span className="sticky-label">📦 {title || `nmID ${article}`}</span>
          <button
            type="button"
            className="chip"
            onClick={() => {
              setArticle("");
              setStickyArticle(null);
            }}
          >
            ×
          </button>
          <Link className="muted" to={`/products/${article}`}>
            открыть
          </Link>
        </div>
      ) : (
        <p className="muted">Вопрос будет про магазин в целом.</p>
      )}

      <div className="filters">
        {SECONDARY.map((c) => (
          <button key={c.id} className="chip" disabled={busy} onClick={() => send(c.label, c.id)}>
            {c.label}
          </button>
        ))}
      </div>

      <div className="chat-log">
        {log.map((m, i) => (
          <div key={`${m.role}-${i}`} className={`chat-bubble ${m.role}`}>
            {m.text}
          </div>
        ))}
        {!log.length && (
          <EmptyState
            title="Напишите что происходит"
            text="Свободный текст или ссылка WB. Кнопки справа — только ускорители."
          />
        )}
      </div>

      {!!followups.length && !!log.length && (
        <div className="filters">
          {followups.map((f) => (
            <button key={f.id} className="chip" disabled={busy} onClick={() => send(f.label, f.id)}>
              {f.label}
            </button>
          ))}
        </div>
      )}

      {preview?.first_screen && (
        <div className="card">
          <FirstScreen cards={preview.first_screen} />
          {preview.can_add && (
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await addCatalogProduct({ article: preview.article });
                  setPreview({ ...preview, can_add: false, owned: true });
                } catch (e) {
                  setError(String(e.message || e));
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

      {error && <ErrorState text={error} onRetry={() => setError("")} />}

      <div className="composer" data-tour="assistant-composer">
        <textarea
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Напишите что происходит..."
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(text);
            }
          }}
        />
        <button className="btn send-arrow" disabled={busy || !text.trim()} onClick={() => send(text)} aria-label="Отправить">
          ➤
        </button>
      </div>
      {parseWbArticle(text) && (
        <p className="muted">В тексте есть ссылка WB — ARGUS разберёт карточку, не добавляя её автоматически.</p>
      )}
    </div>
  );
}
