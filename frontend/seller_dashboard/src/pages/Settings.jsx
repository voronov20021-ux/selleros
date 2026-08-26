import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import BackLink from "../components/BackLink";
import { ErrorState } from "../components/ScreenState";
import { resetTour } from "../components/SpotlightTour";
import {
  checkWb,
  completeMission,
  connectWb,
  disconnectWb,
  fetchDueActions,
  fetchProfile,
  fetchTimeSettings,
  fetchTimeZones,
  fetchWbStatus,
  logout,
  saveProfile,
  saveTimeSettings,
  skipMissions,
} from "../api";

const DEFAULT_SCHEDULE = {
  morning_enabled: false,
  morning_time: "09:00",
  evening_enabled: false,
  evening_time: "20:00",
  action_check_enabled: false,
  action_check_time: "10:00",
  critical_enabled: true,
  notify_event: true,
  notify_action_check: false,
  notify_reengagement: false,
};

export default function Settings() {
  const [profile, setProfile] = useState({
    entity: "",
    display_name: "",
    category: "",
  });
  const [wb, setWb] = useState(null);
  const [wbKey, setWbKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [time, setTime] = useState({ tz: "Europe/Moscow", schedule: { ...DEFAULT_SCHEDULE } });
  const [zones, setZones] = useState([]);
  const [due, setDue] = useState([]);

  async function refresh() {
    setError("");
    try {
      const [p, w, t, d, z] = await Promise.all([
        fetchProfile(),
        fetchWbStatus(),
        fetchTimeSettings(),
        fetchDueActions().catch(() => ({ items: [] })),
        fetchTimeZones().catch(() => ({ zones: [] })),
      ]);
      setProfile({
        entity: p.entity || "",
        display_name: p.display_name || "",
        category: p.category || "",
      });
      setWb(w);
      setTime({
        tz: t.tz || "Europe/Moscow",
        schedule: { ...DEFAULT_SCHEDULE, ...(t.schedule || {}) },
      });
      setZones(z.zones || t.zones || []);
      setDue(d.items || []);
    } catch (e) {
      setError(String(e.message || e));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onSaveProfile() {
    setBusy(true);
    setMsg("");
    try {
      await saveProfile({
        entity: profile.entity,
        display_name: profile.display_name,
        category: profile.category,
        marketplaces: ["wildberries"],
      });
      if (profile.entity && profile.category) {
        await completeMission("profile").catch(() => null);
      }
      setMsg("Профиль сохранён");
      await refresh();
    } catch (e) {
      setMsg(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onConnect() {
    setBusy(true);
    setMsg("");
    try {
      const res = await connectWb(wbKey);
      setWbKey("");
      if (res.error === "invalid_credentials" || !res.connected) {
        setMsg("Ключ отклонён. Подключение не подтверждаем.");
      } else {
        setMsg("Проверка ключа прошла. Доступен ping API. Карточки, CTR и рекламу отдельно не подтверждаем.");
      }
      await refresh();
    } catch (e) {
      setWbKey("");
      setMsg(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  const cap = wb?.capabilities || {};
  const pingOk = Boolean(cap.ping && wb?.connected);
  const schedule = time.schedule || DEFAULT_SCHEDULE;

  function patchSchedule(patch) {
    setTime({ ...time, schedule: { ...schedule, ...patch } });
  }

  return (
    <div>
      <BackLink to="/more" />
      <div className="brand-row">
        <div className="brand">
          <strong>Настройки</strong>
          <span>Что подключено и чем ARGUS может пользоваться</span>
        </div>
      </div>

      {error && <ErrorState text={error} onRetry={refresh} />}

      <h2 className="section-title">Профиль</h2>
      <div className="card" data-mission="profile">
        <div className="field">
          <label>Тип бизнеса</label>
          <select
            value={profile.entity}
            onChange={(e) => setProfile({ ...profile, entity: e.target.value })}
          >
            <option value="">Выберите</option>
            <option value="ip">ИП</option>
            <option value="ooo">ООО</option>
            <option value="self">Самозанятый</option>
          </select>
        </div>
        <div className="field">
          <label>Имя</label>
          <input
            value={profile.display_name}
            onChange={(e) => setProfile({ ...profile, display_name: e.target.value })}
            placeholder="Как к вам обращаться"
          />
        </div>
        <div className="field">
          <label>Категория</label>
          <input
            value={profile.category}
            onChange={(e) => setProfile({ ...profile, category: e.target.value })}
            placeholder="Одежда"
          />
        </div>
        <button className="btn" disabled={busy} onClick={onSaveProfile}>
          Сохранить профиль
        </button>
      </div>

      <h2 className="section-title">Wildberries</h2>
      <div className="card trust-box" data-mission="wb_connect" data-tour="wb-connect">
        <p style={{ marginTop: 0, fontWeight: 700 }}>
          {pingOk ? "✓ Подключено" : "Не подключено"}
        </p>
        <p className="muted">
          API Wildberries нужен, чтобы ARGUS мог автоматически получать данные вашего магазина и отслеживать изменения. Без подключения можно пользоваться анализом отдельных публичных карточек.
        </p>
        <p className="muted">
          Ключ передаётся на сервер по защищённому соединению, не показывается в интерфейсе и используется только для запрошенного доступа к данным.
        </p>
        <div className="field">
          <label>API-ключ</label>
          <input
            type="password"
            autoComplete="off"
            value={wbKey}
            onChange={(e) => setWbKey(e.target.value)}
            placeholder="Вставьте ключ"
          />
        </div>
        <div className="actions">
          <button className="btn" disabled={busy || !wbKey.trim()} onClick={onConnect}>
            Проверить и подключить
          </button>
          <button
            className="btn secondary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await checkWb();
                const status = await fetchWbStatus();
                setWb(status);
                setMsg(status.connected ? status.capabilities?.note : "Проверка: доступ не подтверждён.");
              } catch (e) {
                setMsg(String(e.message || e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Повторить проверку
          </button>
          <button
            className="btn ghost"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              await disconnectWb();
              setMsg("Ключ отозван");
              await refresh();
              setBusy(false);
            }}
          >
            Отключить
          </button>
        </div>
        <div className="cap-row">
          <span>{pingOk ? "✓" : "○"} Проверка ключа (ping)</span>
        </div>
        <div className="cap-row">
          <span>○ Нет доступа · заказы, CTR и реклама кабинета</span>
        </div>
        <div className="cap-row">
          <span>○ Нет доступа · цена и рейтинг из кабинета продавца</span>
        </div>
        {wb?.error && <p className="muted">Ошибка проверки: {wb.error}</p>}
      </div>

      <h2 className="section-title">Уведомления</h2>
      <div className="card">
        <div className="field">
          <label>Часовой пояс</label>
          <select value={time.tz} onChange={(e) => setTime({ ...time, tz: e.target.value })}>
            {(zones.length ? zones : [{ id: "Europe/Moscow", label: "UTC+3 Москва" }]).map((z) => (
              <option key={z.id} value={z.id}>
                {z.label}
              </option>
            ))}
          </select>
        </div>
        <label className="schedule-row">
          <input
            type="checkbox"
            checked={!!schedule.notify_event}
            onChange={(e) =>
              patchSchedule({ notify_event: e.target.checked, critical_enabled: e.target.checked })
            }
          />
          События
          <span className="muted">важное изменение — сразу</span>
        </label>
        <label className="schedule-row">
          <input
            type="checkbox"
            checked={!!schedule.notify_action_check}
            onChange={(e) =>
              patchSchedule({
                notify_action_check: e.target.checked,
                action_check_enabled: e.target.checked,
              })
            }
          />
          Проверка действия
          <input
            type="time"
            value={schedule.action_check_time}
            onChange={(e) => patchSchedule({ action_check_time: e.target.value })}
          />
        </label>
        <label className="schedule-row">
          <input
            type="checkbox"
            checked={!!schedule.notify_reengagement}
            onChange={(e) => patchSchedule({ notify_reengagement: e.target.checked })}
          />
          Вернуться в ARGUS
          <span className="muted">~24 / 48 / 72 ч, не каждый день</span>
        </label>
        <p className="muted">
          Три независимых типа. Нет ежедневной рассылки. «Проверка действия» напоминает
          только про уже созданное действие. «Вернуться» — одно сообщение на окно, пока вы не зашли.
        </p>
        <button
          className="btn"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await saveTimeSettings({
                tz: time.tz,
                schedule,
                push_enabled:
                  !!schedule.notify_event ||
                  !!schedule.notify_action_check ||
                  !!schedule.notify_reengagement,
              });
              setMsg("Настройки уведомлений сохранены");
            } catch (e) {
              setMsg(String(e.message || e));
            } finally {
              setBusy(false);
            }
          }}
        >
          Сохранить уведомления
        </button>
        <p className="muted" style={{ marginBottom: 0 }}>
          Сейчас к проверке действия: {due.length}
        </p>
      </div>

      <h2 className="section-title">Безопасность</h2>
      <div className="card">
        <p className="muted" style={{ marginTop: 0 }}>
          Сессия привязана к вашему Telegram. Ключ WB не хранится в открытом виде. Чужой кабинет по id недоступен.
        </p>
        <div className="actions">
          <Link className="btn ghost" to="/lesson">
            Урок CTR / CVR
          </Link>
          <button
            className="btn secondary"
            onClick={async () => {
              await logout();
              window.location.reload();
            }}
          >
            Выйти
          </button>
        </div>
      </div>

      <h2 className="section-title">Интерфейс</h2>
      <div className="card">
        <p className="muted" style={{ marginTop: 0 }}>
          Тёмная тема Seller OS. Обучение можно пройти снова — оно не блокирует кабинет.
        </p>
        <button
          className="btn secondary"
          onClick={async () => {
            resetTour();
            await skipMissions(false);
            window.location.href = "/";
          }}
        >
          Показать обучение снова
        </button>
      </div>

      {msg && <p className="muted">{msg}</p>}
    </div>
  );
}
