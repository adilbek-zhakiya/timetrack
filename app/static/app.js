const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const $ = (sel) => document.querySelector(sel);

// --- запросы ---------------------------------------------------------------

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (res.status >= 500) throw new Error("Не удалось выполнить запрос. Попробуйте ещё раз.");
  if (!res.ok) {
    // Ошибки приложения приходят строкой, ошибки разбора запроса от FastAPI списком.
    const detail = data && typeof data.detail === "string" ? data.detail : "Проверьте введённые данные";
    throw new Error(detail);
  }
  return data;
}

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 4000);
}

// --- форматирование --------------------------------------------------------

function pad(n) {
  return String(n).padStart(2, "0");
}

// toISOString() дал бы дату в UTC: вечером по местному времени это уже завтра.
function localDate(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function formatClock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 3600)}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
}

function formatDuration(seconds) {
  const minutes = Math.floor(seconds / 60);
  if (seconds > 0 && minutes === 0) return "< 1 мин";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h} ч ${pad(m)} мин` : `${m} мин`;
}

const dateFmt = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", weekday: "short" });
const timeFmt = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  node.append(...children.filter((c) => c !== null && c !== undefined));
  return node;
}

// --- период: общий для обеих страниц ---------------------------------------

const periodForms = document.querySelectorAll("form.period");

function setPeriod(from, to) {
  for (const form of periodForms) {
    form.elements.date_from.value = from;
    form.elements.date_to.value = to;
  }
}

function reportParams(extra = {}) {
  const f = periodForms[0].elements;
  return new URLSearchParams({ date_from: f.date_from.value, date_to: f.date_to.value, tz, ...extra });
}

for (const form of periodForms) {
  form.addEventListener("change", () => {
    if (!form.elements.date_from.value || !form.elements.date_to.value) return;
    setPeriod(form.elements.date_from.value, form.elements.date_to.value);
    route();
  });
}

// --- страницы --------------------------------------------------------------

let employees = [];
let current = null; // сотрудник, чья страница открыта
let request = 0;    // при быстрой смене дат показываем только ответ на последний запрос

function route() {
  const match = location.hash.match(/^#\/employee\/(\d+)$/);
  $("#page-list").hidden = Boolean(match);
  $("#page-employee").hidden = !match;
  const load = match ? showEmployee(Number(match[1])) : showList();
  load.catch((err) => {
    toast(err.message);
    if (match) location.hash = "#/"; // например, сотрудника с таким номером нет
  });
}

// Страница 1: список сотрудников с итогом за период.
async function showList() {
  current = null;
  const ticket = ++request;
  const [list, report] = await Promise.all([api("/api/employees"), api(`/api/report?${reportParams()}`)]);
  if (ticket !== request) return;
  employees = list;
  const totals = new Map(report.employees.map((e) => [e.id, e.total_seconds]));

  $("#employees tbody").replaceChildren(...employees.map((emp) =>
    el("tr", { class: "clickable", onclick: () => (location.hash = `#/employee/${emp.id}`) },
      el("td", {}, el("a", { href: `#/employee/${emp.id}` }, emp.name)),
      emp.running
        ? el("td", { class: "busy" }, emp.running.description)
        : el("td", { class: "muted" }, "Свободен"),
      el("td", { class: "num" }, formatDuration(totals.get(emp.id) || 0)),
    ),
  ));
  $("#employees").hidden = employees.length === 0;
  $("#employees-empty").hidden = employees.length > 0;
}

$("#add-employee").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = e.target.elements.name;
  try {
    await api("/api/employees", { method: "POST", body: { name: input.value } });
    input.value = "";
    await showList();
  } catch (err) {
    toast(err.message);
  }
});

// Страница 2: таймер и записи одного сотрудника.
async function showEmployee(id) {
  const ticket = ++request;
  const [list, report] = await Promise.all([
    api("/api/employees"),
    api(`/api/report?${reportParams({ employee_id: id })}`),
  ]);
  if (ticket !== request) return;
  employees = list;
  current = employees.find((e) => e.id === id);
  $("#employee-name").textContent = current.name;
  renderTimer();

  $("#employee-total").textContent = formatDuration(report.total_seconds);
  $("#entries tbody").replaceChildren(...report.entries.map((e) => {
    const start = new Date(e.started_at);
    return el("tr", { class: e.running ? "running" : "" },
      el("td", { class: "nowrap" },
        dateFmt.format(start),
        el("div", { class: "muted" }, `${timeFmt.format(start)} – ${e.running ? "сейчас" : timeFmt.format(new Date(e.stopped_at))}`),
      ),
      el("td", {}, e.description),
      el("td", { class: "num" }, formatDuration(e.seconds)),
    );
  }));
  $("#entries").hidden = report.entries.length === 0;
  $("#entries-empty").hidden = report.entries.length > 0;
}

const timer = $("#timer");

function renderTimer() {
  const button = timer.elements.action;
  const description = timer.elements.description;
  button.disabled = false;
  if (current.running) {
    description.value = current.running.description;
    description.disabled = true;
    button.textContent = "Стоп";
    button.classList.add("stop");
  } else {
    if (description.disabled) description.value = "";
    description.disabled = false;
    button.textContent = "Старт";
    button.classList.remove("stop");
  }
  $("#timer-clock").hidden = !current.running;
  tick();
}

function tick() {
  if (current && current.running) {
    $("#timer-clock").textContent = formatClock((Date.now() - new Date(current.running.started_at)) / 1000);
  }
}
setInterval(tick, 1000);

timer.addEventListener("submit", async (e) => {
  e.preventDefault();
  timer.elements.action.disabled = true;
  try {
    if (current.running) {
      await api(`/api/employees/${current.id}/timer/stop`, { method: "POST" });
    } else {
      await api(`/api/employees/${current.id}/timer/start`, {
        method: "POST",
        body: { description: timer.elements.description.value },
      });
    }
  } catch (err) {
    toast(err.message);
  }
  await showEmployee(current.id);
});

// --- запуск ----------------------------------------------------------------

// По умолчанию текущая неделя: с понедельника по сегодня.
const today = new Date();
const monday = new Date(today);
monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
setPeriod(localDate(monday), localDate(today));

window.addEventListener("hashchange", route);
route();
