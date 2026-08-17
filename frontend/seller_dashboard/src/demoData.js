/* Frozen fixtures — Mini App must NOT import this as live catalog. */
export const DEMO_PRODUCTS = [
  {
    article: 279904819,
    title: "Футболка базовая хлопок премиум",
    image:
      "https://basket-12.wbbasket.ru/vol2799/part279904/279904819/images/c246x328/1.webp",
    price: 994,
    old_price: 1490,
    rating: 4.4,
    feedback_count: 1287,
    position: 18,
    brand: "SellerOS Demo",
    description:
      "Базовая футболка из плотного хлопка. Прямой крой, устойчивость к стирке.",
    photos: [
      "https://basket-12.wbbasket.ru/vol2799/part279904/279904819/images/c246x328/1.webp",
    ],
    argus_score: 62,
    problems: [
      "Слабые SEO-ключи в названии",
      "Мало фото на модели",
      "Описание короче конкурентов",
    ],
    severity: "YELLOW",
    filter_tags: ["attention", "drop"],
    recommendations: [
      "Добавить ключи в title",
      "Дописать УТП в описание",
      "Добавить lifestyle-фото",
    ],
    review_summary: "Хвалят ткань, жалуются на размерную сетку.",
    review_issues: ["маломер", "выцветает"],
    review_texts: ["Ткань приятная, но размер меньше заявленного."],
    competitors: [
      {
        article: 188221001,
        title: "Футболка хлопок unisex",
        price: 890,
        rating: 4.7,
        note: "Выше рейтинг",
      },
    ],
  },
  {
    article: 312445901,
    title: "Кроссовки беговые лёгкие",
    image:
      "https://basket-10.wbbasket.ru/vol3124/part312445/312445901/images/c246x328/1.webp",
    price: 3490,
    rating: 4.7,
    feedback_count: 3421,
    position: 7,
    brand: "RunDemo",
    description: "Лёгкие кроссовки для бега.",
    photos: [],
    argus_score: 81,
    problems: [],
    severity: "GREEN",
    filter_tags: ["growth"],
    recommendations: ["Усилить рекламу на пике спроса"],
    review_summary: "Отзывы позитивные.",
    review_issues: [],
    review_texts: ["Очень лёгкие."],
    competitors: [],
  },
  {
    article: 155667788,
    title: "Плойка для волос керамика",
    image:
      "https://basket-05.wbbasket.ru/vol1556/part155667/155667788/images/c246x328/1.webp",
    price: 1890,
    rating: 3.9,
    feedback_count: 412,
    position: 64,
    brand: "HeatStyle",
    description: "Керамическая плойка 25 мм.",
    photos: [],
    argus_score: 41,
    problems: ["Низкий рейтинг", "Нет инфографики", "SEO: дубли ключей"],
    severity: "RED",
    filter_tags: ["attention", "drop"],
    recommendations: ["Закрыть жалобы по нагреву в описании"],
    review_summary: "Жалобы на перегрев.",
    review_issues: ["перегрев"],
    review_texts: ["Сильно греется."],
    competitors: [],
  },
  {
    article: 401122334,
    title: "Органайзер для косметики",
    image:
      "https://basket-15.wbbasket.ru/vol4011/part401122/401122334/images/c246x328/1.webp",
    price: 690,
    rating: 4.6,
    feedback_count: 890,
    position: 12,
    brand: "HomeDemo",
    description: "Многоярусный органайзер.",
    photos: [],
    argus_score: 74,
    problems: ["Можно усилить title ключами «акрил»"],
    severity: "GREEN",
    filter_tags: ["growth"],
    recommendations: ["Добавить комплектность"],
    review_summary: "Хвалят вместимость.",
    review_issues: [],
    review_texts: [],
    competitors: [],
  },
  {
    article: 288776655,
    title: "Сумка шоппер экокожа",
    image:
      "https://basket-11.wbbasket.ru/vol2887/part288776/288776655/images/c246x328/1.webp",
    price: 1590,
    rating: 4.2,
    feedback_count: 556,
    position: 29,
    brand: "BagDemo",
    description: "Вместительный шоппер.",
    photos: [],
    argus_score: 55,
    problems: ["Падение позиции за 7 дней", "Слабые фото деталей"],
    severity: "YELLOW",
    filter_tags: ["attention", "drop"],
    recommendations: ["Обновить главное фото"],
    review_summary: "Смешанные отзывы по запаху.",
    review_issues: ["запах экокожи"],
    review_texts: ["Красивая, но запах первые дни."],
    competitors: [],
  },
];

export const DEMO_SUMMARY = {
  username: "seller",
  argus_index: 63,
  health: "needs_attention",
  attention_products: DEMO_PRODUCTS.filter((p) =>
    p.filter_tags.includes("attention")
  ),
  growth_points: [
    {
      title: "SEO-заголовок",
      detail: "Усилить ключи в названии у товаров YELLOW/RED",
      impact: "YELLOW",
    },
    {
      title: "Визуал карточки",
      detail: "Добавить lifestyle / инфографику",
      impact: "RED",
    },
  ],
  top_products: [...DEMO_PRODUCTS]
    .sort((a, b) => b.argus_score - a.argus_score)
    .slice(0, 3),
  sales_alerts: DEMO_PRODUCTS.filter((p) => p.filter_tags.includes("drop")).map(
    (p) => ({
      article: p.article,
      title: p.title,
      message: (p.problems || ["Требует внимания"])[0],
      severity: p.severity,
    })
  ),
  demo: true,
};

export const DEMO_FIRST_SCREEN = {
  verdict: "Слабые ключи и визуал тянут карточку вниз — системной поломки воронки не видно.",
  verdict_kind: "problem",
  figures: [
    { label: "Argus", value: 62, source: "fact" },
    { label: "Рейтинг", value: 4.4, source: "fact" },
  ],
  do: ["Добавить ключи в title", "Дописать УТП в описание"],
  dont: ["Не крутить цену без данных воронки"],
  check: ["Сверить CTR/CVR за тот же период"],
  confidence: "средняя",
  priority_tier: "P2",
  idea_only: [],
  details: {
    known: ["Футболка, рейтинг 4.4, 1287 отзывов"],
    assumed: [],
    why: ["Название слабее конкурентов по ключам"],
    verdict_full: "",
  },
};
