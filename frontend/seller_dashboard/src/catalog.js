import { fetchCatalog, fetchOnboardingState, fetchProduct, getAuthState } from "./api";

export function parseWbArticle(raw) {
  const s = String(raw || "").trim();
  if (/^\d{4,}$/.test(s)) return Number(s);
  const m = s.match(/(?:wildberries\.ru|wb\.ru)\/catalog\/(\d{4,})/i);
  if (m) return Number(m[1]);
  const q = s.match(/[?&](?:nm|nmId|nm_id)=(\d{4,})/i);
  if (q) return Number(q[1]);
  return null;
}

export const DEV_PREVIEW_SELLER_ID = "dev-preview";

/** Production builds: always false. Vite DEV may show a labeled Local Preview banner. */
export function isViteDev() {
  return Boolean(import.meta.env.DEV);
}

export function isDevPreviewSeller(sellerId) {
  return String(sellerId || "") === DEV_PREVIEW_SELLER_ID;
}

export function isTelegramWebApp() {
  return Boolean(window.Telegram?.WebApp);
}

/**
 * Real seller catalog only. demo=true from DashboardService is treated as empty.
 * If onboarding has first_article, that one SKU is loaded via product adapter.
 */
export async function loadSellerStore() {
  if (!getAuthState().authenticated) {
    return {
      ok: false,
      reason: "auth",
      products: [],
      metrics: null,
      demoIgnored: false,
      onboarding: null,
      sellerName: "",
    };
  }

  let catalog = null;
  let onboarding = null;
  let catalogError = "";
  try {
    catalog = await fetchCatalog();
  } catch (e) {
    catalogError = String(e.message || e);
  }
  try {
    onboarding = await fetchOnboardingState();
  } catch {
    onboarding = null;
  }

  if (catalogError && !catalog) {
    return {
      ok: false,
      reason: "api",
      error: catalogError,
      products: [],
      metrics: null,
      demoIgnored: false,
      onboarding,
      sellerName: "",
    };
  }

  const demoIgnored = Boolean(catalog?.demo);
  let products = [];
  if (catalog && !catalog.demo) {
    products = list(catalog.items).map(normalizeProduct).filter((p) => !p.demo);
  }

  const first = onboarding?.first_article;
  if (products.length === 0 && first) {
    try {
      const one = await fetchProduct(first);
      if (one && one.article && one.owned && !one.demo) {
        products = [normalizeProduct(one)];
      }
    } catch {
      /* keep empty — do not invent */
    }
  }

  const scores = products.map((p) => Number(p.argus_score)).filter((n) => Number.isFinite(n));
  const metrics =
    products.length === 0
      ? null
      : {
          argus_index:
            catalog && !catalog.demo && catalog.argus_index != null
              ? catalog.argus_index
              : scores.length
                ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length)
                : null,
          products_count: products.length,
          updated_at: catalog?.updated_at || null,
        };

  return {
    ok: true,
    reason: null,
    error: "",
    products,
    metrics,
    demoIgnored,
    onboarding,
    sellerName: "",
    alerts: [],
  };
}

function list(v) {
  return Array.isArray(v) ? v : [];
}

export function normalizeProduct(p) {
  return {
    article: p.article,
    title: p.title || String(p.article),
    image: p.image || null,
    price: p.price ?? null,
    rating: p.rating ?? null,
    feedback_count: p.feedback_count ?? p.reviews_count ?? null,
    reviews_count: p.reviews_count ?? p.feedback_count ?? null,
    position: p.position ?? null,
    argus_score: p.argus_score ?? null,
    argus_status: p.argus_status || p.severity || null,
    problems: p.problems || [],
    recommendations: p.recommendations || [],
    first_screen: p.first_screen || null,
    demo: Boolean(p.demo),
  };
}

export function attentionList(products) {
  return products
    .filter((p) => p.argus_status === "RED" || p.argus_status === "YELLOW")
    .sort((a, b) => (a.argus_status === "RED" ? 0 : 1) - (b.argus_status === "RED" ? 0 : 1))
    .slice(0, 5);
}

export function improveList(products) {
  return products
    .filter((p) => p.argus_status === "GREEN" && (p.recommendations || []).length)
    .slice(0, 5);
}
