// Нагрузочные сценарии каталога (без LLM). Запуск — scripts/loadtest/run.sh, описание — README.md рядом.
//
// EP   — сценарий (см. ENDPOINTS ниже), по умолчанию mix;
// VUS  — параллельных клиентов (закрытая модель: каждый шлёт запрос сразу после ответа);
// RATE — запросов в секунду (открытая модель: поток не ждёт ответов; VUS — сколько клиентов
//        k6 держит наготове, при нехватке добавит до 4×RATE);
// DUR  — длительность (20s, 1m…);
// BASE — адрес API, по умолчанию стенд lt-api из stand.sh.
//
// Пользователь нагрузки регистрируется в setup() (или входит, если уже есть).
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const BASE = __ENV.BASE || 'http://lt-api:8000/api/v1';
const EP = __ENV.EP || 'mix';
const VUS = Number(__ENV.VUS || 16);
const RATE = Number(__ENV.RATE || 0);
const DUR = __ENV.DUR || '20s';
const EMAIL = __ENV.LT_EMAIL || 'loadtest@example.com';
const PASSWORD = __ENV.LT_PASSWORD || 'LoadTest-Password-123';

export const options = {
  scenarios: {
    load: RATE
      ? {
          executor: 'constant-arrival-rate',
          rate: RATE,
          timeUnit: '1s',
          duration: DUR,
          preAllocatedVUs: VUS,
          maxVUs: Math.max(VUS, RATE * 4),
        }
      : { executor: 'constant-vus', vus: VUS, duration: DUR },
  },
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
  discardResponseBodies: true,
  // Порог-заглушка: без него k6 не считает задержку только успешных ответов.
  thresholds: { 'http_req_duration{expected_response:true}': ['max>=0'] },
  setupTimeout: '60s',
  tags: { ep: EP },
};

// 503 OVERLOADED: API отклонил запрос под перегрузкой (backend/overload.py), а не упал.
const rejected = new Counter('rejected_503');

const json = { headers: { 'Content-Type': 'application/json' }, responseType: 'text' };

export function setup() {
  const body = JSON.stringify({ email: EMAIL, password: PASSWORD });
  let r = http.post(`${BASE}/auth/login`, body, json);
  if (r.status !== 200) r = http.post(`${BASE}/auth/register`, body, json);
  if (r.status !== 200 && r.status !== 201) {
    throw new Error(`вход пользователя нагрузки: ${r.status} ${r.body}`);
  }
  const token = r.json('access_token');
  const page = http.get(`${BASE}/vacancies?page=1&page_size=200`, {
    headers: { Authorization: `Bearer ${token}` },
    responseType: 'text',
  });
  return { token, ids: page.json('items').map((x) => x.id) };
}

const GRADES = ['junior', 'middle', 'senior', 'lead', 'unknown'];
const FORMATS = ['remote', 'office', 'hybrid'];
const WORDS = ['python', 'golang', 'java', 'devops', 'аналитик', 'frontend', 'react', 'тестировщик', 'менеджер', 'kotlin', 'data', 'qa'];
const pick = (a) => a[Math.floor(Math.random() * a.length)];
const randomDate = () => `2026-0${1 + Math.floor(Math.random() * 9)}-${10 + Math.floor(Math.random() * 18)}`;

const ENDPOINTS = {
  health: () => '/health',
  me: () => '/auth/me',
  list: () => '/vacancies?page=1&page_size=50',
  list_filtered: () =>
    `/vacancies?page=1&page_size=50&grade=${pick(GRADES)}&work_format=${pick(FORMATS)}&has_salary=${pick(['true', 'false'])}`,
  list_q: () => `/vacancies?page=1&page_size=50&q=${encodeURIComponent(pick(WORDS))}`,
  detail: (d) => `/vacancies/${pick(d.ids)}`,
  // С кэшем ANALYTICS_CACHE_SECONDS — без фильтров; *_miss — случайные фильтры мимо кэша.
  metrics: () => '/vacancies/metrics',
  metrics_miss: () => `/vacancies/metrics?grade=${pick(GRADES)}&work_format=${pick(FORMATS)}&date_from=${randomDate()}`,
  salaries: () => '/analytics/salaries',
  salaries_miss: () => `/analytics/salaries?grade=${pick(GRADES)}&date_from=${randomDate()}`,
  sources: () => '/analytics/sources',
  filters: () => '/filters',
};

// Смесь запросов UI: в основном списки и карточки, реже поиск и аналитика.
const MIX = [
  [0.35, 'list_filtered'],
  [0.5, 'list'],
  [0.6, 'list_q'],
  [0.85, 'detail'],
  [0.9, 'metrics'],
  [0.95, 'salaries'],
  [1.0, 'me'],
];

function path(ep, d) {
  if (ep === 'mix') {
    const r = Math.random();
    return path(MIX.find(([edge]) => r < edge)[1], d);
  }
  const make = ENDPOINTS[ep];
  if (!make) throw new Error(`неизвестный EP=${ep}; есть: mix, login, ${Object.keys(ENDPOINTS).join(', ')}`);
  return make(d);
}

export default function (d) {
  if (EP === 'login') {
    // argon2 на каждый вход: лимит попыток на стенде выключен (stand.sh).
    const r = http.post(`${BASE}/auth/login`, JSON.stringify({ email: EMAIL, password: PASSWORD }), json);
    check(r, { 'status 200': (x) => x.status === 200 });
    return;
  }
  const r = http.get(`${BASE}${path(EP, d)}`, { headers: { Authorization: `Bearer ${d.token}` } });
  check(r, { 'status 200': (x) => x.status === 200 });
  if (r.status === 503) rejected.add(1);
}

// Итог — одна JSON-строка (удобно собирать серию прогонов) плюс стандартный текст k6.
export function handleSummary(data) {
  const m = data.metrics;
  const t = m.http_req_duration.values;
  const ok = (m['http_req_duration{expected_response:true}'] || m.http_req_duration).values;
  const line = {
    ep: EP,
    ...(RATE ? { rate: RATE } : { vus: VUS }),
    rps: Number(m.http_reqs.values.rate.toFixed(1)),
    p50_ms: Number(t.med.toFixed(1)),
    p95_ms: Number(t['p(95)'].toFixed(1)),
    p99_ms: Number(t['p(99)'].toFixed(1)),
    // Только успешные ответы: быстрые 503 не должны «улучшать» задержку.
    ok_p99_ms: Number(ok['p(99)'].toFixed(1)),
    failed_pct: Number(((1 - m.checks.values.rate) * 100).toFixed(2)),
    rejected_503: m.rejected_503 ? m.rejected_503.values.count : 0,
  };
  // Открытая модель: k6 не успел отправить запрос — свободных клиентов не было, сервис не держит поток.
  if (RATE) {
    line.dropped = m.dropped_iterations ? m.dropped_iterations.values.count : 0;
    line.max_vus = m.vus_max.values.max;
  }
  return { stdout: `${JSON.stringify(line)}\n` };
}
