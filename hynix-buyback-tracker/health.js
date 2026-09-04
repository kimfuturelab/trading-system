(() => {
  const DATA_DELAY_AFTER_MS = 3 * 60 * 1000;
  const DATA_ERROR_AFTER_MS = 5 * 60 * 1000;
  const MARKET_START_MIN = 9 * 60;
  const MARKET_FRESHNESS_END_MIN = 15 * 60 + 5;
  const CHECK_MS = 15000;

  let lastPayload = null;
  const baseRender = window.render;
  if (typeof baseRender !== 'function') return;

  const normalizeDate = value => String(value || '')
    .trim()
    .replace(/\./g, '-')
    .replace(/\//g, '-')
    .replace(/-+$/g, '');

  const normalizeTime = value => {
    const match = String(value || '').trim().match(/^(\d{1,2}):(\d{2})/);
    if (!match) return null;
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
    return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
  };

  const timeToMinutes = value => {
    const time = normalizeTime(value);
    if (!time) return null;
    const [hour, minute] = time.split(':').map(Number);
    return hour * 60 + minute;
  };

  const kstNow = () => {
    const shifted = new Date(Date.now() + 9 * 60 * 60 * 1000);
    const year = shifted.getUTCFullYear();
    const month = String(shifted.getUTCMonth() + 1).padStart(2, '0');
    const day = String(shifted.getUTCDate()).padStart(2, '0');
    const hour = shifted.getUTCHours();
    const minute = shifted.getUTCMinutes();
    return {
      date: `${year}-${month}-${day}`,
      minuteOfDay: hour * 60 + minute,
      nowMs: Date.now(),
    };
  };

  const ageFor = (date, time, nowMs) => {
    const cleanDate = normalizeDate(date);
    const cleanTime = normalizeTime(time);
    if (!cleanDate || !cleanTime) return null;
    const timestamp = Date.parse(`${cleanDate}T${cleanTime}:00+09:00`);
    if (!Number.isFinite(timestamp)) return null;
    return {
      time: cleanTime,
      timestamp,
      ageMs: Math.max(0, nowMs - timestamp),
      ageMin: Math.max(0, Math.floor((nowMs - timestamp) / 60000)),
    };
  };

  const latestRawTime = data => {
    const rows = Array.isArray(data && data.timeseries) ? data.timeseries : [];
    for (let i = rows.length - 1; i >= 0; i -= 1) {
      const time = normalizeTime(rows[i] && rows[i].time);
      if (time) return time;
    }
    return null;
  };

  function healthFor(data) {
    const now = kstNow();
    const dataDate = normalizeDate(data && data.date);
    const inMarketWindow = dataDate === now.date
      && now.minuteOfDay >= MARKET_START_MIN
      && now.minuteOfDay <= MARKET_FRESHNESS_END_MIN;

    const dashboard = ageFor(dataDate, data && data.apiTime, now.nowMs);
    const rawTime = latestRawTime(data);
    const raw = ageFor(dataDate, rawTime, now.nowMs);

    if (!inMarketWindow) {
      return { state: 'live', reason: 'outside_market_window', dashboard, raw, dataDate };
    }

    if (raw && raw.ageMs > DATA_ERROR_AFTER_MS) {
      return { state: 'error', label: '수집 오류', reason: 'raw_stale', dashboard, raw, dataDate };
    }
    if (raw && raw.ageMs > DATA_DELAY_AFTER_MS) {
      return { state: 'pending', label: '수집 지연', reason: 'raw_delayed', dashboard, raw, dataDate };
    }

    if (!dashboard) {
      return { state: 'error', label: '데이터 오류', reason: 'dashboard_time_missing', dashboard, raw, dataDate };
    }
    if (dashboard.ageMs > DATA_ERROR_AFTER_MS) {
      return { state: 'error', label: raw ? '계산 오류' : '데이터 오류', reason: raw ? 'dashboard_stale_raw_fresh' : 'dashboard_stale', dashboard, raw, dataDate };
    }
    if (dashboard.ageMs > DATA_DELAY_AFTER_MS) {
      return { state: 'pending', label: raw ? '계산 지연' : '데이터 지연', reason: raw ? 'dashboard_delayed_raw_fresh' : 'dashboard_delayed', dashboard, raw, dataDate };
    }

    return { state: 'live', reason: 'fresh', dashboard, raw, dataDate };
  }

  function applyHealth(data) {
    const health = healthFor(data);
    window.__hynixDataFreshness = {
      checkedAt: new Date().toISOString(),
      ...health,
    };

    if (health.state === 'live') return;

    const liveState = document.getElementById('liveState');
    const liveTime = document.getElementById('liveTime');
    const updatePanel = document.getElementById('updatePanel');
    const updateTime = document.getElementById('updateTime');
    const updateDate = document.getElementById('updateDate');
    if (!liveState || !liveTime || !updatePanel || !updateTime || !updateDate) return;

    const isError = health.state === 'error';
    liveState.textContent = `● ${health.label}`;
    liveState.className = `live ${isError ? 'error' : 'pending'}`;
    updatePanel.className = `update-panel ${isError ? 'error' : 'pending'}`;

    const dashboardTime = health.dashboard ? health.dashboard.time : '-';
    const rawTime = health.raw ? health.raw.time : null;
    const detail = rawTime && rawTime !== dashboardTime
      ? `원본 ${rawTime} · 화면 ${dashboardTime}`
      : `${dashboardTime} 이후 ${health.dashboard ? health.dashboard.ageMin : '?'}분째 갱신 없음`;

    liveTime.textContent = `${health.dataDate || ''} ${detail}`.trim();
    updateTime.textContent = dashboardTime;
    updateDate.textContent = `${health.label} · 자동 재확인 중`;
  }

  window.render = function monitoredRender(data) {
    baseRender(data);
    lastPayload = data;
    applyHealth(data);
  };

  setInterval(() => {
    if (lastPayload) applyHealth(lastPayload);
  }, CHECK_MS);
})();
