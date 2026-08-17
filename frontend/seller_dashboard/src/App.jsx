import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Products from "./pages/Products";
import ProductDetail from "./pages/ProductDetail";
import Assistant from "./pages/Assistant";
import Settings from "./pages/Settings";
import Lesson from "./pages/Lesson";
import Finance from "./pages/Finance";
import Market from "./pages/Market";
import Actions from "./pages/Actions";
import History from "./pages/History";
import More from "./pages/More";
import SpotlightTour from "./components/SpotlightTour";
import { AuthWall, Skeleton } from "./components/ScreenState";
import { bootstrapTelegramAuth, fetchAssistantContext, getAuthState, setStickyArticle } from "./api";
import { isDevPreviewSeller, isTelegramWebApp, isViteDev, parseWbArticle } from "./catalog";

const MORE_PREFIXES = ["/more", "/finance", "/market", "/actions", "/history", "/settings", "/lesson"];

function readLaunchArticle() {
  const search = new URLSearchParams(window.location.search);
  const fromQuery = parseWbArticle(search.get("article") || "");
  const tg = window.Telegram?.WebApp?.initDataUnsafe;
  const fromStart = parseWbArticle(tg?.start_param || "");
  const hash = String(window.location.hash || "");
  const hashNm = hash.match(/\/products\/(\d{4,})/);
  const fromHash = hashNm ? Number(hashNm[1]) : null;
  return fromHash || fromStart || fromQuery || null;
}

export default function App() {
  const [authReady, setAuthReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [sellerId, setSellerId] = useState("");
  const location = useLocation();
  const navigate = useNavigate();
  const moreActive = MORE_PREFIXES.some(
    (p) => location.pathname === p || location.pathname.startsWith(`${p}/`)
  );

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const auth = await bootstrapTelegramAuth();
        if (!alive) return;
        if (auth && getAuthState().authenticated) {
          setAuthed(true);
          setDisplayName(auth.display_name || getAuthState().displayName || "");
          setSellerId(auth.seller_id || getAuthState().sellerId || "");
          const launched = readLaunchArticle();
          if (launched) {
            await setStickyArticle(launched);
            if (!String(window.location.hash || "").includes(`/products/${launched}`)) {
              navigate(`/products/${launched}`, { replace: true });
            }
          } else {
            try {
              const ctx = await fetchAssistantContext();
              if (ctx?.article) await setStickyArticle(ctx.article);
            } catch {
              /* sticky restore is best-effort */
            }
          }
        } else {
          setAuthed(false);
          setSellerId("");
        }
      } catch {
        if (alive) setAuthed(false);
      } finally {
        if (alive) setAuthReady(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [navigate]);

  if (!authReady) {
    return (
      <div className="app-shell">
        <Skeleton rows={2} />
      </div>
    );
  }

  const canUse = authed;
  const devSeller = authed && isDevPreviewSeller(sellerId);

  return (
    <div className="app-shell">
      {!authed && <AuthWall telegram={isTelegramWebApp()} localDev={isViteDev()} />}
      {devSeller && (
        <div className="guest-banner">DEV MODE / Seller: Local Preview</div>
      )}

      {canUse && (
        <Routes>
          <Route path="/" element={<Dashboard displayName={displayName} />} />
          <Route path="/products" element={<Products />} />
          <Route path="/products/:article" element={<ProductDetail />} />
          <Route path="/assistant" element={<Assistant />} />
          <Route path="/more" element={<More />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/lesson" element={<Lesson />} />
          <Route path="/finance" element={<Finance />} />
          <Route path="/market" element={<Market />} />
          <Route path="/actions" element={<Actions />} />
          <Route path="/history" element={<History />} />
        </Routes>
      )}

      {authed && <SpotlightTour enabled />}

      {canUse && (
        <nav className="tabbar">
          <NavLink to="/" end className={({ isActive }) => `tab ${isActive ? "active" : ""}`}>
            <span className="tab-ico" aria-hidden="true">⌂</span>
            Главная
          </NavLink>
          <NavLink
            to="/products"
            data-tour="nav-products"
            className={({ isActive }) => `tab ${isActive ? "active" : ""}`}
          >
            <span className="tab-ico" aria-hidden="true">▣</span>
            Товары
          </NavLink>
          <NavLink to="/assistant" className={({ isActive }) => `tab ${isActive ? "active" : ""}`}>
            <span className="tab-ico tab-argus" aria-hidden="true">A</span>
            ARGUS
          </NavLink>
          <NavLink to="/more" className={`tab ${moreActive ? "active" : ""}`} data-tour="nav-more">
            <span className="tab-ico" aria-hidden="true">▦</span>
            Кабинет
          </NavLink>
        </nav>
      )}
    </div>
  );
}
