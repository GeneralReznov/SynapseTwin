const API = (() => {
  const BASE = '/api';

  function token() { return localStorage.getItem('st_token') || ''; }
  function headers(extra = {}) {
    return {
      'Content-Type': 'application/json',
      ...(token() ? { 'Authorization': `Bearer ${token()}` } : {}),
      ...extra,
    };
  }

  async function request(method, path, body = null, multipart = false) {
    const opts = { method, headers: multipart ? { 'Authorization': `Bearer ${token()}` } : headers() };
    if (body && !multipart)  opts.body = JSON.stringify(body);
    if (body && multipart)   opts.body = body;   // FormData
    try {
      const res  = await fetch(`${BASE}${path}`, opts);
      const data = await res.json().catch(() => ({}));
      if (res.status === 401 && path !== '/users/login' && path !== '/users/create') {
        // Token expired or invalid — clear session and redirect to login
        localStorage.removeItem('st_token');
        localStorage.removeItem('st_user');
        window.location.href = '/onboarding.html';
        return;
      }
      if (!res.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      return data;
    } catch (err) {
      console.error(`API ${method} ${path}:`, err);
      throw err;
    }
  }

  return {
    // Auth
    register:  (name, email, password) => request('POST', '/users/create', { name, email, password }),
    login:     (email, password)       => request('POST', '/users/login',  { email, password }),
    setLang:   (language)              => request('PATCH', '/users/language', { language }),
    assignTeam:(userId, teamId, role)  => request('PATCH', '/users/team',   { userId, teamId, role }),

    // Agent
    process:   (text, preferredLanguage = 'en-IN') => request('POST', '/agent/process',  { text, preferredLanguage }),
    dispatch:  (text, preferredLanguage = 'en-IN') => request('POST', '/agent/dispatch', { text, preferredLanguage }),
    jobStatus: (jobId)  => request('GET', `/agent/job/${jobId}`),
    jobs:      ()       => request('GET', '/agent/jobs'),

    // Voice
    stt: (blob, languageCode = 'hi-IN') => {
      const fd = new FormData();
      fd.append('audio', blob, 'recording.webm');
      fd.append('languageCode', languageCode);
      return request('POST', '/voice/stt', fd, true);
    },
    tts:       (text, languageCode = 'hi-IN')               => request('POST', '/voice/tts',      { text, languageCode }),
    translate: (text, sourceLanguage, targetLanguage)        => request('POST', '/voice/translate', { text, sourceLanguage, targetLanguage }),
    detect:    (text)                                        => request('POST', '/voice/detect',   { text }),

    // Voice+Pipeline
    voiceProcess: (blob, preferredLanguage = 'en-IN') => {
      const fd = new FormData();
      fd.append('audio', blob, 'recording.webm');
      fd.append('preferredLanguage', preferredLanguage);
      return request('POST', '/agent/voice', fd, true);
    },

    // Memory
    history:  (limit = 10)              => request('GET', `/memory/history?limit=${limit}`),
    saveGoal: (title, category, targetDate) => request('POST', '/memory/goal', { title, category, targetDate }),
    weeklyInsights: ()                  => request('GET', '/memory/insights'),

    // Insights
    insights:  ()         => request('GET', '/insights/'),
    timeline:  (days = 30) => request('GET', `/insights/timeline?days=${days}`),

    // Graph
    graphData: ()         => request('GET', '/graph/data'),

    // Goals
    createGoal:   (data)              => request('POST',  '/goals/',         data),
    listGoals:    ()                  => request('GET',   '/goals/'),
    updateProgress: (title, progress) => request('PATCH', '/goals/progress', { title, progress }),
    causalChain:  (title)             => request('GET',   `/goals/causal-chain?title=${encodeURIComponent(title)}`),

    // Notifications
    notifications: () => request('GET', '/notifications/'),

    // Enterprise
    team:    (teamId, days = 14) => request('GET', `/enterprise/team/${teamId}?days=${days}`),
    summary: (teamId, days = 7)  => request('GET', `/enterprise/summary/${teamId}?days=${days}`),

    // Health
    health: () => request('GET', '/health'),

    // ── Learning & Growth ──────────────────────────────────────────────────────
    learningRecommendations: (platform = 'both') =>
      request('GET', `/learning/recommendations?platform=${platform}`),
    learningLogProgress: (platform, course_title, course_url, progress, skills = []) =>
      request('POST', '/learning/progress', { platform, course_title, course_url, progress, skills }),
    learningHistory:   (limit = 10) => request('GET', `/learning/history?limit=${limit}`),
    learningAdvice:    (topic, platform = 'both', level = 'intermediate') =>
      request('POST', '/learning/ai-advice', { topic, platform, level }),
    learningStats:     () => request('GET', '/learning/stats'),

    // ── Environment Intelligence ───────────────────────────────────────────────
    envWeather:    (qs) => request('GET', `/environment/weather?${qs}`),
    envForecast:   (qs) => request('GET', `/environment/forecast?${qs}`),
    envLogLocation:(lat, lon, location_name = null, location_type = 'home') =>
      request('POST', '/environment/location', { latitude: lat, longitude: lon, location_name, location_type }),
    envHistory:    (limit = 7) => request('GET', `/environment/history?limit=${limit}`),
    envImpact:     ()          => request('GET', '/environment/impact-analysis'),
  };
})();

// ── Session helpers ────────────────────────────────────────────────────────────
const Session = {
  save(user, token) {
    localStorage.setItem('st_token', token);
    localStorage.setItem('st_user',  JSON.stringify(user));
  },
  clear() {
    localStorage.removeItem('st_token');
    localStorage.removeItem('st_user');
  },
  user()  { try { return JSON.parse(localStorage.getItem('st_user') || 'null'); } catch { return null; } },
  token() { return localStorage.getItem('st_token') || ''; },
  valid() { return !!this.token() && !!this.user(); },
  requireAuth() {
    if (!this.valid()) { window.location.href = '/onboarding.html'; return false; }
    return true;
  },
};

// ── Toast notification system ─────────────────────────────────────────────────
const Toast = {
  _container: null,
  init() {
    if (!this._container) {
      this._container = document.createElement('div');
      this._container.id = 'toast-container';
      document.body.appendChild(this._container);
    }
  },
  show(title, message = '', type = 'info', duration = 4000) {
    this.init();
    const icons = { info: '💡', success: '✅', warning: '⚠️', error: '❌' };
    const t = document.createElement('div');
    t.className = 'toast';
    t.innerHTML = `
      <span class="toast-icon">${icons[type] || '💡'}</span>
      <div><div class="toast-title">${title}</div>${message ? `<div class="toast-msg">${message}</div>` : ''}</div>
    `;
    this._container.appendChild(t);
    setTimeout(() => t.remove(), duration);
  },
  success(title, msg)  { this.show(title, msg, 'success'); },
  error(title, msg)    { this.show(title, msg, 'error'); },
  warning(title, msg)  { this.show(title, msg, 'warning'); },
  info(title, msg)     { this.show(title, msg, 'info'); },
};

// ── Utility helpers ────────────────────────────────────────────────────────────
function el(id)  { return document.getElementById(id); }
function qs(sel) { return document.querySelector(sel); }
function qsa(sel){ return document.querySelectorAll(sel); }

function scoreColor(score) {
  if (score >= 75) return '#10b981';
  if (score >= 50) return '#a78bfa';
  if (score >= 30) return '#f59e0b';
  return '#ef4444';
}

function burnoutColor(score) {
  if (score >= 70) return '#ef4444';
  if (score >= 45) return '#f59e0b';
  return '#10b981';
}

function renderGauge(svgId, score) {
  const svg = el(svgId);
  if (!svg) return;
  const r = 70, cx = 90, cy = 90;
  const circumference = 2 * Math.PI * r;
  const arc = (score / 100) * circumference;
  svg.innerHTML = `
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="rgba(124,58,237,0.15)" stroke-width="12"/>
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
      stroke="${scoreColor(score)}" stroke-width="12"
      stroke-dasharray="${arc} ${circumference}"
      stroke-dashoffset="${circumference * 0.25}"
      stroke-linecap="round"
      style="transition:stroke-dasharray 1s ease;filter:drop-shadow(0 0 8px ${scoreColor(score)}60)"/>
  `;
}

function renderDimensions(breakdown) {
  const dims = [
    { key: 'physical',     label: 'Physical',    cls: 'dim-physical' },
    { key: 'mental',       label: 'Mental',      cls: 'dim-mental' },
    { key: 'productivity', label: 'Productivity',cls: 'dim-productivity' },
    { key: 'learning',     label: 'Learning',    cls: 'dim-learning' },
    { key: 'social',       label: 'Social',      cls: 'dim-social' },
  ];
  return dims.map(d => `
    <div class="dimension-row">
      <div class="dimension-label">${d.label}</div>
      <div class="dimension-track">
        <div class="dimension-fill ${d.cls}" style="width:${breakdown[d.key] || 0}%"></div>
      </div>
      <div class="dimension-val">${breakdown[d.key] || 0}</div>
    </div>
  `).join('');
}

function setNavActive(page) {
  qsa('.nav-item').forEach(n => {
    n.classList.toggle('active', n.dataset.page === page);
  });
}

function updateScoreBadge(score) {
  const badge = qs('.twin-score-badge');
  if (badge) badge.textContent = `Twin Score: ${score}`;
}
