import { parseWbArticle } from "./catalog";

/**
 * First JS load of the Mini App / browser tab: land on Главная unless this
 * open is a deliberate product deep link (start_param, ?article=, #/products/{nmID}).
 * Do not restore a leftover Telegram hash or a previous in-app page.
 * In-session HashRouter navigations do not re-run this.
 */
export function readLaunchArticle() {
  const search = new URLSearchParams(window.location.search);
  const fromQuery = parseWbArticle(search.get("article") || "");
  const tg = window.Telegram?.WebApp?.initDataUnsafe;
  const fromStart = parseWbArticle(tg?.start_param || "");
  const hash = String(window.location.hash || "");
  const hashNm = hash.match(/\/products\/(\d{4,})/);
  const fromHash = hashNm ? Number(hashNm[1]) : null;
  return fromHash || fromStart || fromQuery || null;
}

export function applyFirstOpenHash() {
  const article = readLaunchArticle();
  const base = `${window.location.pathname}${window.location.search}`;
  if (article) {
    const needle = `/products/${article}`;
    if (!String(window.location.hash || "").includes(needle)) {
      window.history.replaceState(null, "", `${base}#${needle}`);
    }
    return;
  }
  if (String(window.location.hash || "") !== "#/") {
    window.history.replaceState(null, "", `${base}#/`);
  }
}
