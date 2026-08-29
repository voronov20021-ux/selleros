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
  KNOWN: "",
  MISSING: "Не хватает данных",
  NOT_INCLUDED: "Не учтено",
  PARTIAL: "Предварительная оценка",
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

/** Never shown in UI — kept so API codes can be mapped if needed. */
export const SOURCE_LABEL = {
  PUBLIC_BROWSER: "",
  public_browser: "",
  browser: "",
  cdn: "",
  card: "",
  seller: "",
  funnel_consistency: "",
  fact: "",
};

export const ND = "Н/Д";
export const NO_DATA = "Нет данных";
export const INSUFFICIENT = "Недостаточно данных для оценки";

export const COPY = {
  unitMissing:
    "Пока недостаточно данных для расчёта unit economics.",
  unitNeedCost:
    "Чтобы посчитать прибыль с продажи, нужны себестоимость и расходы.",
  dynamicsMissing:
    "Недостаточно исторических данных для оценки динамики.",
  problemNotConfirmed:
    "Проблема не подтверждена по доступным данным.",
  problemInsufficient:
    "Недостаточно данных для проверки.",
  healthMissing: "Недостаточно данных",
  healthLabel: "Здоровье карточки",
  ratingNone: "Рейтинг: нет оценок",
  reviewsNone: "Отзывы: нет данных",
  missionAnalysisTitle: "Первый разбор ARGUS",
  missionAnalysisHint: "Откройте товар, посмотрите вывод и нажмите «Понятно».",
  missionAnalysisCta: "Открыть разбор",
  missionDone: "Понятно",
};

const TECH_TOKEN =
  /\b(funnel_status|CONSISTENT|INCONSISTENT|PUBLIC_BROWSER|verified_nm_id|candidate|locus|kind|F_[A-Z0-9_]+|evd_[a-z0-9_]+|src_[a-z0-9_]+|provenance|pipeline|provider|Knowledge(\s+Base)?|KNOWN|Known)\b/gi;

const TECH_PHRASE = [
  /в knowledge\s*base нет точного термина\.?/gi,
  /нет точного термина\.?/gi,
  /не выдумываю определение\.?/gi,
  /knowledge\s*база\.?/gi,
  /card feedbacks/gi,
  /источник карт(очки|а)?\.?/gi,
  /источник:\s*[^\n.]+/gi,
  /\bsource\s*:\s*[^\n.]+/gi,
  /\bprovenance\s*:\s*[^\n.]+/gi,
  /\bpipeline\s*:\s*[^\n.]+/gi,
  /\bprovider\s*:\s*[^\n.]+/gi,
  /уточни \(ctr, маржа, unit-экономика, лид, оферта\)\.?/gi,
  /считай только known inputs[^.]*\.?/gi,
  /остальное — missing\.?/gi,
  /formula authority/gi,
];

const HIDE_FIGURE_LABEL = /^(source|provenance|pipeline|provider|knowledge|locus|kind|card feedbacks)$/i;

const FIGURE_LABEL = {
  "Card Feedbacks": "Отзывы",
  Feedbacks: "Отзывы",
  feedbacks: "Отзывы",
  Source: "",
  source: "",
  Known: "",
  Knowledge: "",
};

function hasOwn(map, key) {
  return Object.prototype.hasOwnProperty.call(map, key);
}

/** Finite number or null. Real 0 is kept. null/undefined/"" /NaN → null. */
export function presentNumber(v) {
  if (v == null || v === "") return null;
  if (typeof v === "boolean") return null;
  const n = typeof v === "number" ? v : Number(String(v).replace(",", "."));
  return Number.isFinite(n) ? n : null;
}

export function formatNd(v, missing = ND) {
  const n = presentNumber(v);
  return n == null ? missing : String(n);
}

/** 0 reviews + no rating → нет оценок + Отзывы: 0. Real rating shown as-is. */
export function formatRatingParts(rating, reviews) {
  const rev = presentNumber(reviews);
  const rate = presentNumber(rating);
  const noRating = rate == null || (rev === 0 && rate === 0);
  return {
    rating: noRating ? COPY.ratingNone : `Рейтинг: ${rate}`,
    reviews: rev == null ? COPY.reviewsNone : `Отзывы: ${rev}`,
    noRating,
  };
}

export function formatHealth(score, { preliminary = false } = {}) {
  const n = presentNumber(score);
  if (n == null) {
    return { label: COPY.healthLabel, value: COPY.healthMissing, missing: true, text: `${COPY.healthLabel}: ${COPY.healthMissing}` };
  }
  const pct = `${Math.max(0, Math.min(100, n))}%`;
  const value = preliminary ? `${pct} · предварительная оценка` : pct;
  return { label: COPY.healthLabel, value, missing: false, text: `${COPY.healthLabel}: ${value}` };
}

export function looksLikeNoProblem(text) {
  const t = String(text || "").toLowerCase();
  if (!t) return false;
  return (
    /проблем(ы|а)?\s+нет/.test(t) ||
    /нет\s+проблем/.test(t) ||
    /замечаний нет/.test(t) ||
    /карточка в норме/.test(t)
  );
}

/**
 * confirmed | not_confirmed | insufficient
 * Missing score/figures is never “проблем нет”.
 */
export function inferProblemState({
  status = null,
  score = null,
  figures = null,
  verdictKind = "",
  cardHealthy = false,
  funnel = null,
} = {}) {
  const hasScore = presentNumber(score) != null;
  const hasFigures = Array.isArray(figures) && figures.length > 0;
  const funnelStatus = funnel && (funnel.status || funnel.funnel_status);
  const funnelKnown =
    funnelStatus &&
    !["MISSING", "UNKNOWN", ""].includes(String(funnelStatus).toUpperCase());
  const sufficient = hasScore || hasFigures || funnelKnown;
  if (!sufficient) return "insufficient";
  const kind = String(verdictKind || "").toLowerCase();
  if (status === "RED" || status === "YELLOW" || kind === "problem") return "confirmed";
  if (cardHealthy || status === "GREEN" || kind === "no_systemic") return "not_confirmed";
  return "not_confirmed";
}

export function problemStateCopy(state) {
  if (state === "not_confirmed") return COPY.problemNotConfirmed;
  if (state === "insufficient") return COPY.problemInsufficient;
  return "";
}

export function humanFunnel(code) {
  return human(FUNNEL_STATUS, code, "Данных недостаточно");
}

export function humanSource(_code) {
  return "";
}

export function humanFigureLabel(raw) {
  if (raw == null || raw === "") return "";
  const key = String(raw).trim();
  if (hasOwn(FIGURE_LABEL, key)) return FIGURE_LABEL[key];
  if (HIDE_FIGURE_LABEL.test(key)) return "";
  return humanizeText(key, key);
}

export function isTechLeak(raw) {
  if (raw == null || raw === "") return false;
  const t = String(raw);
  TECH_TOKEN.lastIndex = 0;
  const tokenHit = TECH_TOKEN.test(t);
  TECH_TOKEN.lastIndex = 0;
  const low = t.toLowerCase();
  return (
    tokenHit ||
    /knowledge/.test(low) ||
    /нет точного термина/.test(low) ||
    /не выдумываю определение/.test(low) ||
    /provenance|pipeline/.test(low) ||
    /\bprovider\b/.test(low) ||
    /card feedbacks/.test(low) ||
    /источник карт/.test(low)
  );
}

/** Strip leftover internal codes from seller-facing text. */
export function humanizeText(raw, fallback = "") {
  if (raw == null || raw === "") return fallback;
  let text = String(raw);
  text = text.replace(/\bCONSISTENT\b/gi, "Данные воронки согласованы");
  text = text.replace(/\bINCONSISTENT\b/gi, "Данные противоречат друг другу");
  text = text.replace(/\bPUBLIC_BROWSER\b/gi, "");
  text = text.replace(/\bverified_nm_id\b/gi, "артикул карточки");
  text = text.replace(/\bcandidate(s)?\b/gi, "похожая карточка");
  text = text.replace(/\bfunnel_status\b/gi, "состояние воронки");
  for (const re of TECH_PHRASE) {
    re.lastIndex = 0;
    text = text.replace(re, " ");
    re.lastIndex = 0;
  }
  TECH_TOKEN.lastIndex = 0;
  text = text.replace(TECH_TOKEN, "").replace(/\s{2,}/g, " ").trim();
  TECH_TOKEN.lastIndex = 0;
  text = text.replace(/^[:·\-–]\s*/, "").replace(/\s{2,}/g, " ").trim();
  return text || fallback;
}

export function human(map, code, fallback = "Неизвестно") {
  if (code == null || code === "") return fallback;
  const key = String(code);
  if (hasOwn(map, key)) return map[key];
  const upper = key.toUpperCase();
  if (hasOwn(map, upper)) return map[upper];
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
  return human(FORMULA_STATUS, code, "");
}

export function humanMarket(code) {
  return human(MARKET_POS, code, "Неизвестно");
}

export function presentMission(item, { article } = {}) {
  if (!item) return item;
  if (item.id !== "first_analysis") {
    return item;
  }
  const to =
    article != null && String(article) !== ""
      ? `/products/${article}`
      : item.to || "/products";
  return {
    ...item,
    title: COPY.missionAnalysisTitle,
    hint: COPY.missionAnalysisHint,
    to,
    cta: COPY.missionAnalysisCta,
  };
}

export function formatWhen(ts) {
  if (ts == null || ts === "") return "";
  const n = Number(ts);
  const d = Number.isFinite(n) ? (n > 1e12 ? new Date(n) : new Date(n * 1000)) : new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
