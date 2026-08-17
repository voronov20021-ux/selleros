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


-- ──────────────────────────────── api_calls ─────────────────
-- Учёт реальных HTTP-запросов к внешним API (Yandex Search).
-- Используется YandexCostGuard для ограничения расхода лимита.
-- called_at — unix timestamp вызова.

CREATE TABLE IF NOT EXISTS api_calls (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL,
    query       TEXT,
    category    TEXT,
    region      TEXT,
    called_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ac_source_ts
    ON api_calls (source_id, called_at DESC);


-- ──────────────────────────────── action_outcomes ──────────
-- Результаты действий продавца / рекомендаций Argus.
-- Learning Loop v1: память «сделали X → получили Y».

CREATE TABLE IF NOT EXISTS action_outcomes (
    id                   TEXT  PRIMARY KEY,
    user_hash            TEXT  NOT NULL,
    category             TEXT  NOT NULL,
    article              TEXT,
    recommendation_type  TEXT  NOT NULL,
    action               TEXT  NOT NULL,
    period_start         REAL  NOT NULL,
    period_end           REAL  NOT NULL,
    created_at           REAL  NOT NULL,
    metrics_before       TEXT  NOT NULL DEFAULT '{}',
    metrics_after        TEXT  NOT NULL DEFAULT '{}',
    outcome_direction    TEXT  NOT NULL DEFAULT 'unknown'
                               CHECK (outcome_direction IN (
                                   'positive', 'negative', 'neutral', 'unknown'
                               )),
    outcome_score        REAL  NOT NULL DEFAULT 0.0
                               CHECK (outcome_score BETWEEN -1.0 AND 1.0),
    confidence           REAL  NOT NULL DEFAULT 0.5
                               CHECK (confidence BETWEEN 0.0 AND 1.0),
    evidence_ids         TEXT  NOT NULL DEFAULT '[]',
    metadata             TEXT  NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_ao_category
    ON action_outcomes (category, period_end DESC);

CREATE INDEX IF NOT EXISTS idx_ao_action
    ON action_outcomes (action, category, period_end DESC);

CREATE INDEX IF NOT EXISTS idx_ao_user
    ON action_outcomes (user_hash, period_end DESC);


-- ──────────────────────────────── learning_signals ─────────
-- Сигналы, извлечённые LearningBrain из ActionOutcome.

CREATE TABLE IF NOT EXISTS learning_signals (
    id           TEXT  PRIMARY KEY,
    outcome_id   TEXT  REFERENCES action_outcomes(id),
    signal_type  TEXT  NOT NULL,
    claim        TEXT  NOT NULL,
    confidence   REAL  NOT NULL DEFAULT 0.5
                       CHECK (confidence BETWEEN 0.0 AND 1.0),
    evidence_ids TEXT  NOT NULL DEFAULT '[]',
    metadata     TEXT  NOT NULL DEFAULT '{}',
    created_at   REAL  NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ls_outcome
    ON learning_signals (outcome_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ls_type
    ON learning_signals (signal_type, created_at DESC);


-- ──────────────────────────────── recommendation_outcomes ──
-- Жизненный цикл рекомендаций Argus → действие → результат.
-- OutcomeTracker v1. UNKNOWN — нормальное состояние до record_result().

CREATE TABLE IF NOT EXISTS recommendation_outcomes (
    id                        TEXT  PRIMARY KEY,
    user_hash                 TEXT  NOT NULL,
    category                  TEXT  NOT NULL,
    article                   TEXT,
    recommendation_type       TEXT  NOT NULL,
    recommendation_action     TEXT  NOT NULL,
    recommendation_confidence REAL  NOT NULL DEFAULT 0.0
                                    CHECK (recommendation_confidence BETWEEN 0.0 AND 1.0),
    recommended_at            REAL  NOT NULL,
    action_taken              TEXT,
    action_taken_at           REAL,
    period_start              REAL,
    period_end                REAL,
    metrics_before            TEXT  NOT NULL DEFAULT '{}',
    metrics_after             TEXT  NOT NULL DEFAULT '{}',
    outcome_direction         TEXT  NOT NULL DEFAULT 'unknown'
                                    CHECK (outcome_direction IN (
                                        'positive', 'negative', 'mixed', 'unknown'
                                    )),
    outcome_score             REAL  CHECK (
                                    outcome_score IS NULL
                                    OR (outcome_score BETWEEN -1.0 AND 1.0)
                                ),
    confidence                REAL  NOT NULL DEFAULT 0.0
                                    CHECK (confidence BETWEEN 0.0 AND 1.0),
    evidence_ids              TEXT  NOT NULL DEFAULT '[]',
    metadata                  TEXT  NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_ro_category
    ON recommendation_outcomes (category, recommended_at DESC);

CREATE INDEX IF NOT EXISTS idx_ro_article
    ON recommendation_outcomes (article, recommended_at DESC);

CREATE INDEX IF NOT EXISTS idx_ro_type
    ON recommendation_outcomes (recommendation_type, recommended_at DESC);

CREATE INDEX IF NOT EXISTS idx_ro_recommended_at
    ON recommendation_outcomes (recommended_at DESC);

CREATE INDEX IF NOT EXISTS idx_ro_direction
    ON recommendation_outcomes (outcome_direction, recommended_at DESC);

CREATE INDEX IF NOT EXISTS idx_ro_user
    ON recommendation_outcomes (user_hash, recommended_at DESC);


-- ──────────────────────────────── review intelligence ──
-- Сигналы и recurring issues из отзывов товара.
-- seller isolation через user_hash (без raw user_id).

CREATE TABLE IF NOT EXISTS review_signals (
    id           TEXT  PRIMARY KEY,
    user_hash    TEXT  NOT NULL,
    article      TEXT,
    category     TEXT,
    signal_type  TEXT  NOT NULL,
    sentiment    TEXT  NOT NULL,
    claim        TEXT  NOT NULL,
    confidence   REAL  NOT NULL DEFAULT 0.5
                       CHECK (confidence BETWEEN 0.0 AND 1.0),
    source_ids   TEXT  NOT NULL DEFAULT '[]',
    source_url   TEXT,
    review_id    TEXT,
    created_at   REAL  NOT NULL,
    metadata     TEXT  NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_rs_user_article
    ON review_signals (user_hash, article, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_rs_category
    ON review_signals (category, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_rs_type
    ON review_signals (signal_type, sentiment, created_at DESC);

CREATE TABLE IF NOT EXISTS review_issues (
    id           TEXT  PRIMARY KEY,
    user_hash    TEXT  NOT NULL,
    article      TEXT,
    category     TEXT,
    signal_type  TEXT  NOT NULL,
    sentiment    TEXT  NOT NULL,
    claim        TEXT  NOT NULL,
    count        INTEGER NOT NULL DEFAULT 1,
    ratio        REAL  NOT NULL DEFAULT 0.0
                       CHECK (ratio BETWEEN 0.0 AND 1.0),
    confidence   REAL  NOT NULL DEFAULT 0.5
                       CHECK (confidence BETWEEN 0.0 AND 1.0),
    source_ids   TEXT  NOT NULL DEFAULT '[]',
    created_at   REAL  NOT NULL,
    metadata     TEXT  NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_ri_user_article
    ON review_issues (user_hash, article, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ri_category
    ON review_issues (category, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ri_type
    ON review_issues (signal_type, sentiment, count DESC);


-- ──────────────────────────────── competitor_evidence_cache ──────────
-- Ranked competitor evidence для ARGUS (отдельно от сырых knowledge_items).
-- TTL задаётся expires_at. Identity = competitor_id (wb:{nm_id} или url:...).

CREATE TABLE IF NOT EXISTS competitor_evidence_cache (
    id             TEXT PRIMARY KEY,
    query          TEXT NOT NULL,
    product_id     TEXT NOT NULL,
    competitor_id  TEXT NOT NULL,
    source         TEXT NOT NULL,
    data           TEXT NOT NULL,
    retrieved_at   REAL NOT NULL,
    expires_at     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cec_product_query
    ON competitor_evidence_cache (product_id, query, expires_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cec_unique
    ON competitor_evidence_cache (query, product_id, competitor_id);


-- ──────────────────────────────── competitor_snapshots ──────────
-- История коммерческих полей конкурента (не live-мониторинг).
-- Identity = competitor_id (wb:{nm_id} или url:{normalized_url}).
-- Не смешивает IMT / seller / чужие nm.

CREATE TABLE IF NOT EXISTS competitor_snapshots (
    id             TEXT PRIMARY KEY,
    competitor_id  TEXT NOT NULL,
    query          TEXT,
    product_id     TEXT,
    price          INTEGER,
    rating         REAL,
    feedbacks      INTEGER,
    captured_at    REAL NOT NULL,
    source         TEXT
);

CREATE INDEX IF NOT EXISTS idx_csnap_competitor
    ON competitor_snapshots (competitor_id, captured_at DESC);

