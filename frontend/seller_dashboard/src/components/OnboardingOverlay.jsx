import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { fetchMissions, skipMissions } from "../api";

export default function OnboardingOverlay() {
    const [data, setData] = useState(null);
  const [hidden, setHidden] = useState(() => {
    try {
      return sessionStorage.getItem("selleros_overlay_dismissed") === "1";
    } catch {
      return false;
    }
  });
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const m = await fetchMissions();
        if (alive) setData(m);
      } catch {
        if (alive) setData(null);
      }
    })();
    return () => {
      alive = false;
    };
  }, [location.pathname]);

  if (hidden || !data || data.overlay_skipped || data.all_done || !data.next) {
    return null;
  }

  const next = data.next;

  function dismiss() {
    try {
      sessionStorage.setItem("selleros_overlay_dismissed", "1");
    } catch {
      /* ignore */
    }
    setHidden(true);
  }

  async function onSkip() {
    try {
      await skipMissions(true);
    } catch {
      /* local hide still */
    }
    dismiss();
  }

  return (
    <div className="ob-backdrop">
      <div className="card ob-card">
        <div className="badge status-YELLOW" style={{ marginBottom: 8 }}>
          Онбординг · {data.status}
        </div>
        <h3 style={{ margin: "0 0 6px" }}>{next.title}</h3>
        <p className="muted" style={{ margin: "0 0 12px" }}>
          {next.hint}. Можно пропустить и вернуться из Настроек.
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn secondary" onClick={onSkip}>
            Пропустить
          </button>
          <button
            className="btn"
            onClick={() => {
              dismiss();
              navigate(next.to || "/settings");
            }}
          >
            Продолжить
          </button>
        </div>
      </div>
    </div>
  );
}
