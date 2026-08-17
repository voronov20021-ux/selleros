/** Human labels for Mini App. Internal codes stay in API payloads. */

export const ARGUS_STATUS = {
  RED: "Критично",
  YELLOW: "Требует внимания",
  GREEN: "Без критичных сигналов",
};

export const ACTION_STATUS = {
  PROPOSED: "Предложено",
  ACCEPTED: "Принято",
  EXECUTED: "Сделано",
  CHECK_PENDING: "Проверяется",
  CHECKED: "Подтверждено",
  CANCELLED: "Отменено",
};

export const VERIFY_STATUS = {
  APPLIED: "Подтверждено",
  APPLIED_PARTIAL: "Частично применено",
  NOT_APPLIED: "Не применено",
  NOT_VERIFIABLE: "Недостаточно данных",
  INCONCLUSIVE: "Пока недостаточно данных",
  SELLER_CONFIRMED: "Подтверждено продавцом",
  NEEDS_REVIEW: "Требует проверки",
  UNKNOWN: "Неизвестно",
  PENDING: "Ожидает проверки",
};

export const FORMULA_STATUS = {
  KNOWN: "Известно",
  MISSING: "Не хватает данных",
  NOT_INCLUDED: "Не учтено",
  PARTIAL: "Частично",
  INCOMPLETE: "Не хватает данных",
};

export const MARKET_POS = {
  ABOVE: "Выше рынка",
  BELOW: "Ниже рынка",
  MARKET: "На уровне рынка",
  UNKNOWN: "Неизвестно",
  strong: "Сильная позиция",
  mid: "Средняя позиция",
  weak: "Слабая позиция",
};

export const FUNNEL_STATUS = {
  CONSISTENT: "Данные воронки согласованы",
  INCONSISTENT: "Данные противоречат друг другу",
  INVALID: "Данные воронки некорректны",
  MISSING: "Данных недостаточно",
  UNKNOWN: "Данных недостаточно",
};

export const SOURCE_LABEL = {
  PUBLIC_BROWSER: "Источник: карточка WB",
  public_browser: "Источник: карточка WB",
  browser: "Источник: карточка WB",
  cdn: "Источник: карточка WB",
  card: "Источник: карточка WB",
  seller: "Источник: данные продавца",
  funnel_consistency: "Воронка",
  fact: "Факт карточки",
};

const TECH_TOKEN = /\b(funnel_status|CONSISTENT|INCONSISTENT|PUBLIC_BROWSER|verified_nm_id|candidate|locus|kind|F_[A-Z0-9_]+|evd_[a-z0-9_]+|src_[a-z0-9_]+)\b/gi;

export function humanFunnel(code) {
  return human(FUNNEL_STATUS, code, "Данных недостаточно");
}

export function humanSource(code) {
  return human(SOURCE_LABEL, code, "Источник: карточка WB");
}

/** Strip leftover internal codes from seller-facing text. */
export function humanizeText(raw, fallback = "") {
  if (raw == null || raw === "") return fallback;
  let text = String(raw);
  text = text.replace(/\bCONSISTENT\b/gi, "Данные воронки согласованы");
  text = text.replace(/\bINCONSISTENT\b/gi, "Данные противоречат друг другу");
  text = text.replace(/\bPUBLIC_BROWSER\b/gi, "карточка WB");
  text = text.replace(/\bverified_nm_id\b/gi, "артикул карточки");
  text = text.replace(/\bcandidate(s)?\b/gi, "похожая карточка");
  text = text.replace(/\bfunnel_status\b/gi, "состояние воронки");
  text = text.replace(TECH_TOKEN, "").replace(/\s{2,}/g, " ").trim();
  return text || fallback;
}

export function human(map, code, fallback = "Неизвестно") {
  if (code == null || code === "") return fallback;
  const key = String(code);
  if (map[key]) return map[key];
  const upper = key.toUpperCase();
  if (map[upper]) return map[upper];
  return fallback;
}

export function humanArgus(code) {
  return human(ARGUS_STATUS, code, "Неизвестно");
}

export function humanAction(code) {
  return human(ACTION_STATUS, code, "Неизвестно");
}

export function humanVerify(code) {
  return human(VERIFY_STATUS, code, "Неизвестно");
}

export function humanFormula(code) {
  return human(FORMULA_STATUS, code, "Неизвестно");
}

export function humanMarket(code) {
  return human(MARKET_POS, code, "Неизвестно");
}

export function formatWhen(ts) {
  if (ts == null || ts === "") return "";
  const n = Number(ts);
  const d = Number.isFinite(n) ? (n > 1e12 ? new Date(n) : new Date(n * 1000)) : new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
