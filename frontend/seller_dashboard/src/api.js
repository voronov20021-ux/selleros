function resolveApiBase() {
  const raw = String(import.meta.env.VITE_API_BASE || "")
    .trim()
    .replace(/\/+$/, "");
  if (import.meta.env.PROD) {
    if (/^(https?:\/\/)?(localhost|127\.0\.0\.1)(:|\/|$)/i.test(raw)) {
      throw new Error(
        "[SellerOS] Production must not use localhost/127.0.0.1 as VITE_API_BASE"
      );
    }
    if (!raw) {
      console.error(
        "[SellerOS] VITE_API_BASE is empty. Set the GitHub Actions variable VITE_API_BASE " +
          "to the public HTTPS API origin. POST ${API}/api/auth/telegram will fail until then."
      );
      return "";
    }
    return raw;
  }
  return raw;
}

const API_BASE = resolveApiBase();

let _sessionToken = null;
let _sellerId = null;
let _displayName = null;

export function getAuthState() {
  return {
    sessionToken: _sessionToken,
    sellerId: _sellerId,
    displayName: _displayName,
    authenticated: Boolean(_sessionToken && _sellerId),
  };
}

/** Clear in-memory + sessionStorage auth (e.g. after 401 / logout). */
export function clearAuth() {
  _sessionToken = null;
  _sellerId = null;
  _displayName = null;
  try {
    sessionStorage.removeItem("selleros_session");
    sessionStorage.removeItem("selleros_seller_id");
    sessionStorage.removeItem("selleros_display_name");
  } catch {
    /* ignore */
  }
}

function authHeaders(extra = {}) {
  const h = { Accept: "application/json", ...extra };
  if (_sessionToken) h.Authorization = `Bearer ${_sessionToken}`;
  return h;
}

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: authHeaders(options.headers || {}),
  });
  if (res.status === 401) {
    clearAuth();
  }
  if (!res.ok) {
    let detail = null;
    try {
      detail = await res.json();
    } catch {
      detail = null;
    }
    const err = new Error((detail && (detail.detail?.message || detail.message)) || `API ${res.status}: ${path}`);
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  return res.json();
}

function persistSession(auth) {
  _sessionToken = auth.session_token;
  _sellerId = auth.seller_id;
  _displayName = auth.display_name || "";
  try {
    sessionStorage.setItem("selleros_session", _sessionToken);
    sessionStorage.setItem("selleros_seller_id", _sellerId);
    if (_displayName) sessionStorage.setItem("selleros_display_name", _displayName);
  } catch {
    /* ignore */
  }
  return auth;
}

export function restoreSession() {
  try {
    const token = sessionStorage.getItem("selleros_session");
    const sellerId = sessionStorage.getItem("selleros_seller_id");
    const name = sessionStorage.getItem("selleros_display_name") || "";
    if (token && sellerId) {
      _sessionToken = token;
      _sellerId = sellerId;
      _displayName = name || _displayName || "seller";
      return { session_token: token, seller_id: sellerId, display_name: _displayName };
    }
  } catch {
    /* ignore */
  }
  return null;
}

function canCallDevAuth() {
  if (!import.meta.env.DEV) return false;
  const flag = String(import.meta.env.VITE_MINIAPP_DEV_AUTH || "1").trim();
  if (flag === "0" || flag.toLowerCase() === "false") return false;
  const base = API_BASE;
  if (!base) return true;
  return /^(https?:\/\/)?(localhost|127\.0\.0\.1)(:|\/|$)/i.test(base);
}

/**
 * Vite DEV only. Production builds replace import.meta.env.DEV with false.
 * Backend still fail-closes unless MINIAPP_DEV_AUTH=1 and the request is loopback.
 */
async function tryDevAuth() {
  if (!import.meta.env.DEV) return null;
  if (!canCallDevAuth()) return null;
  const res = await fetch(`${API_BASE}/api/auth/dev`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
  });
  if (res.status === 404 || res.status === 403) return null;
  if (!res.ok) return null;
  return persistSession(await res.json());
}

/**
 * Telegram Mini App bootstrap: ready/expand + POST /api/auth/telegram.
 * Outside Telegram returns null unless a stored session exists.
 */
export async function bootstrapTelegramAuth() {
  const tg = window.Telegram?.WebApp;
  if (tg) {
    try {
      tg.ready?.();
      tg.expand?.();
    } catch {
      /* ignore */
    }
  }
  const initData = tg?.initData || "";
  if (initData) {
    const res = await fetch(`${API_BASE}/api/auth/telegram`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ initData }),
    });
    if (!res.ok) {
      throw new Error(`Auth failed: ${res.status}`);
    }
    return persistSession(await res.json());
  }
  const restored = restoreSession();
  if (restored) return restored;
  if (import.meta.env.DEV) {
    try {
      const devAuth = await tryDevAuth();
      if (devAuth) return devAuth;
    } catch {
      /* backend down or DEV auth disabled — AuthWall handles it */
    }
  }
  return null;
}

export function fetchDashboard(sellerId = _sellerId) {
  if (!sellerId) throw new Error("sellerId required");
  return request(`/dashboard/${encodeURIComponent(sellerId)}`);
}

/** Onboarding progress — seller from session only (no client seller_id). */
export function fetchOnboardingState() {
  return request("/api/onboarding/state");
}

export function connectWb(apiKey) {
  return request("/api/onboarding/wb/connect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export function checkWb() {
  return request("/api/onboarding/wb/check", { method: "POST" });
}

export function disconnectWb() {
  return request("/api/onboarding/wb/disconnect", { method: "POST" });
}

export function addOnboardingProduct(article) {
  return request("/api/onboarding/product", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ article: Number(article) }),
  });
}

export function runOnboardingAnalyze(article) {
  const body = article ? { article: Number(article) } : {};
  return request("/api/onboarding/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function fetchProducts(filter = "all", sellerId = _sellerId) {
  if (!sellerId) throw new Error("sellerId required");
  return request(
    `/dashboard/${encodeURIComponent(sellerId)}/products?filter=${encodeURIComponent(filter)}`
  );
}

export function fetchProduct(article, sellerId = _sellerId) {
  if (_sessionToken) {
    return request(`/api/products/${encodeURIComponent(article)}`);
  }
  if (!sellerId) throw new Error("sellerId required");
  return fetchDashboard(sellerId).then((d) => {
    const p = (d.products || []).find((x) => String(x.article) === String(article));
    if (!p) throw new Error(`Product ${article} not found`);
    return p;
  });
}

export async function postAction(article, action, sellerId = _sellerId) {
  if (!sellerId) throw new Error("sellerId required");
  const res = await fetch(
    `${API_BASE}/dashboard/${encodeURIComponent(sellerId)}/products/${article}/actions/${action}`,
    { method: "POST", headers: authHeaders() }
  );
  if (res.status === 401) clearAuth();
  if (!res.ok) throw new Error(`Action failed: ${action}`);
  return res.json();
}

/** Revoke current server session and clear local auth state. */
export async function logout() {
  if (!_sessionToken) {
    clearAuth();
    return { ok: true };
  }
  try {
    const res = await fetch(`${API_BASE}/api/auth/logout`, {
      method: "POST",
      headers: authHeaders(),
    });
    if (!res.ok && res.status !== 401) {
      throw new Error(`Logout failed: ${res.status}`);
    }
  } finally {
    clearAuth();
  }
  return { ok: true };
}

export function fetchProfile() {
  return request("/api/profile");
}

export function saveProfile(body) {
  return request("/api/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function fetchWbStatus() {
  return request("/api/wb/status");
}

export function fetchMissions() {
  return request("/api/missions");
}

export function completeMission(id) {
  return request(`/api/missions/${encodeURIComponent(id)}/complete`, { method: "POST" });
}

export function skipMissions(skipped = true) {
  return request("/api/missions/skip", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skipped }),
  });
}

export function fetchFormulaLesson() {
  return request("/api/formula/lesson");
}

export function evaluateFormulaLesson(body) {
  return request("/api/formula/lesson", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function proposeAction(article, recommendation, actionType = "OTHER") {
  return request("/api/actions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      article: Number(article),
      recommendation,
      action_type: actionType,
    }),
  });
}

export async function acceptIdea(article, recommendation) {
  const proposed = await proposeAction(article, recommendation);
  const accepted = await request(`/api/actions/${encodeURIComponent(proposed.action_id)}/accept`, {
    method: "POST",
  });
  return accepted;
}

export function markActionDone(actionId) {
  return request(`/api/actions/${encodeURIComponent(actionId)}/done`, { method: "POST" });
}

export function deferAction(actionId, days = 3) {
  return request(`/api/actions/${encodeURIComponent(actionId)}/defer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ days }),
  });
}

export function fetchActionHistory(article) {
  const q = article ? `?article=${encodeURIComponent(article)}` : "";
  return request(`/api/actions/history${q}`);
}

export function fetchDueActions() {
  return request("/api/actions/due");
}

export function fetchTimeSettings() {
  return request("/api/time/settings");
}

export function saveTimeSettings(body) {
  return request("/api/time/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function fetchAssistantContext() {
  return request("/api/assistant/context");
}

export function setStickyArticle(article) {
  try {
    if (article) sessionStorage.setItem("selleros_sticky_article", String(article));
    else sessionStorage.removeItem("selleros_sticky_article");
  } catch {
    /* ignore */
  }
  if (!_sessionToken) return Promise.resolve({ article: article || null });
  return request("/api/assistant/context", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ article: article ? Number(article) : null }),
  });
}

export function getStickyArticle() {
  try {
    return sessionStorage.getItem("selleros_sticky_article");
  } catch {
    return null;
  }
}

export function fetchCatalog() {
  return request("/api/catalog");
}

export function addCatalogProduct({ article, url, text }) {
  return request("/api/catalog/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      article: article ? Number(article) : null,
      url: url || null,
      text: text || null,
    }),
  });
}

export function refreshCatalog() {
  return request("/api/catalog/refresh", { method: "POST" });
}

export function analyzePublic({ article, url, text }) {
  return request("/api/analyze/public", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      article: article ? Number(article) : null,
      url: url || null,
      text: text || null,
    }),
  });
}

export function saveSellerData(article, body) {
  return request(`/api/products/${encodeURIComponent(article)}/seller-data`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function fetchFinance({ scope = "shop", article } = {}) {
  const q = new URLSearchParams({ scope });
  if (article) q.set("article", String(article));
  return request(`/api/finance?${q.toString()}`);
}

export function fetchTimeZones() {
  return request("/api/time/zones");
}

export function sendAssistantChat({ text, article, chip }) {
  return request("/api/assistant/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      article: article ? Number(article) : null,
      chip: chip || null,
    }),
  });
}

/** @deprecated use fetchDashboard after auth — kept for older pages with local demo fallback */
export function fetchSummary(username = "seller") {
  if (_sellerId && _sessionToken) {
    return fetchDashboard(_sellerId).then((d) => ({
      username: _displayName || username,
      argus_index: d.metrics?.argus_index,
      health: d.competitors?.market_position,
      demo: d.demo,
      attention_products: (d.products || []).filter(
        (p) => p.argus_status === "RED" || p.argus_status === "YELLOW"
      ),
      sales_alerts: (d.alerts || []).map((a) => ({
        article: a.article || 0,
        message: a.message,
        priority: a.priority,
      })),
      growth_points: [],
    }));
  }
  return request(`/dashboard/summary?username=${encodeURIComponent(username)}`);
}
