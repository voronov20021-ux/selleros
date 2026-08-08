-- ============================================================
-- Intelligence Layer — схема базы данных
-- ============================================================
--
-- Все таблицы используют TEXT PRIMARY KEY (UUID) — совместимо
-- с PostgreSQL без изменений.
--
-- Timestamp-поля — REAL (unix time в секундах с плавающей точкой).
-- JSON-поля — TEXT, сериализованные через json.dumps.
-- Булевы поля — INTEGER (0/1), как принято в SQLite.
--
-- Файл загружается один раз при connect() через executescript().
-- Повторный запуск безопасен (IF NOT EXISTS / IF NOT EXISTS INDEX).
-- ============================================================


-- ──────────────────────────────── data_sources ─────────────
-- Реестр зарегистрированных источников данных.
-- Заполняется при старте через SourceRegistry.register().

CREATE TABLE IF NOT EXISTS data_sources (
    id              TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL,
    source_type     TEXT    NOT NULL,
    authority       REAL    NOT NULL DEFAULT 0.5
                            CHECK (authority BETWEEN 0.0 AND 1.0),
    freshness_hours INTEGER NOT NULL DEFAULT 24,
    capabilities    TEXT    NOT NULL DEFAULT '[]',  -- JSON array
    base_url        TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    last_fetched_at REAL,
    metadata        TEXT    NOT NULL DEFAULT '{}'   -- JSON object
);


-- ──────────────────────────────── knowledge_items ──────────
-- Сырые записи, полученные от внешних источников.
-- Один вызов DataSourceAdapter.fetch() → N строк.

CREATE TABLE IF NOT EXISTS knowledge_items (
    id           TEXT  PRIMARY KEY,
    source_id    TEXT  NOT NULL REFERENCES data_sources(id),
    source_url   TEXT,
    collected_at REAL  NOT NULL,
    published_at REAL,
    item_type    TEXT  NOT NULL
                       CHECK (item_type IN (
                           'fact', 'observation', 'inference', 'recommendation'
                       )),
    category     TEXT,
    region       TEXT,
    period       TEXT,
    confidence   REAL  NOT NULL DEFAULT 1.0
                       CHECK (confidence BETWEEN 0.0 AND 1.0),
    content      TEXT  NOT NULL,
    metadata     TEXT  NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_ki_source
    ON knowledge_items (source_id, collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_ki_category
    ON knowledge_items (category, collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_ki_region
    ON knowledge_items (region, collected_at DESC);


-- ──────────────────────────────── evidence ─────────────────
-- Обработанные, типизированные единицы знания.
-- EvidenceEngine.ingest() превращает KnowledgeItem → Evidence.
--
-- claim — нормализованное утверждение, готовое к включению в промпт.
-- supporting_data — JSON со ссылками на источники и числами.

CREATE TABLE IF NOT EXISTS evidence (
    id                TEXT  PRIMARY KEY,
    knowledge_item_id TEXT  NOT NULL REFERENCES knowledge_items(id),
    evidence_type     TEXT  NOT NULL
                            CHECK (evidence_type IN (
                                'fact', 'observation', 'inference'
                            )),
    claim             TEXT  NOT NULL,
    supporting_data   TEXT  NOT NULL DEFAULT '{}',
    confidence        REAL  NOT NULL DEFAULT 1.0
                            CHECK (confidence BETWEEN 0.0 AND 1.0),
    created_at        REAL  NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ev_type_conf
    ON evidence (evidence_type, confidence DESC);

CREATE INDEX IF NOT EXISTS idx_ev_item
    ON evidence (knowledge_item_id);


-- ──────────────────────────────── seller_observations ──────
-- Обезличенные наблюдения продавцов: действие → измеренный результат.
--
-- user_hash = sha256(str(user_id)) — raw user_id НЕ хранится.
-- article — публичный артикул WB, не персональные данные.

CREATE TABLE IF NOT EXISTS seller_observations (
    id                   TEXT    PRIMARY KEY,
    user_hash            TEXT    NOT NULL,
    article              INTEGER NOT NULL,
    created_at           REAL    NOT NULL,
    change_type          TEXT    NOT NULL
                                 CHECK (change_type IN (
                                     'price', 'content', 'ad', 'ranking', 'other'
                                 )),
    category             TEXT,
    before_value         TEXT,
    after_value          TEXT,
    period_start         REAL,
    period_end           REAL,
    outcome_sales_delta  INTEGER,
    outcome_orders_delta INTEGER,
    outcome_rating_delta REAL,
    notes                TEXT
);

CREATE INDEX IF NOT EXISTS idx_so_category
    ON seller_observations (category, change_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_so_article
    ON seller_observations (article, created_at DESC);


-- ──────────────────────────────── seasonality_records ──────
-- Сезонный индекс спроса по категории / региону / месяцу.
--
-- demand_index: 1.0 = среднегодовой уровень,
--               1.4 = на 40% выше среднего.
--
-- Может содержать несколько записей за один (category, region, month)
-- из разных источников или годов — агрегация на стороне caller.

CREATE TABLE IF NOT EXISTS seasonality_records (
    id            TEXT    PRIMARY KEY,
    category      TEXT    NOT NULL,
    region        TEXT    NOT NULL DEFAULT 'RU',
    month         INTEGER NOT NULL
                          CHECK (month BETWEEN 1 AND 12),
    week          INTEGER CHECK (week BETWEEN 1 AND 53),
    demand_index  REAL    NOT NULL,
    source_id     TEXT    NOT NULL,
    period_year   INTEGER NOT NULL,
    confidence    REAL    NOT NULL DEFAULT 1.0
                          CHECK (confidence BETWEEN 0.0 AND 1.0),
    created_at    REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sr_lookup
    ON seasonality_records (category, region, month, period_year DESC);


-- ──────────────────────────────── trend_records ────────────
-- Направление динамики поискового запроса или категории.
--
-- query = конкретный запрос из Wordstat (NULL если запись — для категории).
-- change_pct = % изменения (NULL если нет точных данных).

CREATE TABLE IF NOT EXISTS trend_records (
    id           TEXT  PRIMARY KEY,
    source_id    TEXT  NOT NULL,
    period_start REAL  NOT NULL,
    period_end   REAL  NOT NULL,
    direction    TEXT  NOT NULL
                       CHECK (direction IN ('up', 'down', 'stable')),
    created_at   REAL  NOT NULL,
    category     TEXT,
    query        TEXT,
    region       TEXT,
    change_pct   REAL,
    confidence   REAL  NOT NULL DEFAULT 1.0
                       CHECK (confidence BETWEEN 0.0 AND 1.0),
    metadata     TEXT  NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_tr_lookup
    ON trend_records (category, query, period_start DESC);

CREATE INDEX IF NOT EXISTS idx_tr_source
    ON trend_records (source_id, period_start DESC);


-- ──────────────────────────────── market_events ────────────
-- Внешние события, влияющие на рыночную динамику.
-- Примеры: акция WB, изменение алгоритма, новые требования к сертификации.

CREATE TABLE IF NOT EXISTS market_events (
    id               TEXT  PRIMARY KEY,
    event_type       TEXT  NOT NULL
                           CHECK (event_type IN (
                               'sale', 'holiday', 'regulation',
                               'competitor', 'platform'
                           )),
    title            TEXT  NOT NULL,
    source_id        TEXT  NOT NULL,
    event_date       REAL  NOT NULL,
    created_at       REAL  NOT NULL,
    description      TEXT,
    category         TEXT,
    region           TEXT,
    impact_direction TEXT  CHECK (impact_direction IN (
                               'positive', 'negative', 'neutral'
                           )),
    confidence       REAL  NOT NULL DEFAULT 1.0
                           CHECK (confidence BETWEEN 0.0 AND 1.0),
    metadata         TEXT  NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_me_date
    ON market_events (event_date DESC, category);

CREATE INDEX IF NOT EXISTS idx_me_type
    ON market_events (event_type, event_date DESC);
