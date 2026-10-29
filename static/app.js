// Минимальный технический фронт для демонстрации API. Токен в sessionStorage:
// каждая вкладка/устройство — отдельная сессия (можно открыть маму и папу в двух вкладках).
const S = { token: sessionStorage.getItem("token"), me: null, accounts: [], family: null, payKey: newKey() };
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rub = (k) => (k / 100).toLocaleString("ru-RU", { minimumFractionDigits: 2 }) + " ₽";
const toKop = (v) => Math.round(parseFloat(String(v).replace(",", ".")) * 100) || 0;
const tag = (s) => `<span class="tag s-${esc(s)}">${esc(s)}</span>`;
const dt = (s) => (s ? new Date(s + "Z").toLocaleString("ru-RU") : "");
function newKey() { return crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now(); }

function toast(msg, ms = 4000) {
  const t = $("toast"); t.textContent = msg; t.style.display = "block";
  clearTimeout(t._h); t._h = setTimeout(() => (t.style.display = "none"), ms);
}

async function api(method, url, body, { idem, form } = {}) {
  const headers = {};
  if (S.token) headers.Authorization = "Bearer " + S.token;
  if (idem) headers["Idempotency-Key"] = idem;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const r = await fetch(url, { method, headers, body: payload });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail;
    const msg = typeof d === "object" && d.message ? `${d.message} [${d.code}]` : JSON.stringify(d ?? data);
    if (r.status === 401 && url !== "/api/auth/login") { S.token = null; sessionStorage.removeItem("token"); }
    toast(`Ошибка ${r.status}: ${msg}`, 6000);
    throw Object.assign(new Error(msg), { status: r.status, data });
  }
  return data;
}
const safe = (fn) => async (...a) => { try { await fn(...a); } catch (e) { console.warn(e); } };

// ---------------- auth ----------------
function setToken(t) { S.token = t; sessionStorage.setItem("token", t); }

window.doRegister = safe(async () => {
  const r = await api("POST", "/api/auth/register", { email: $("rEmail").value, name: $("rName").value, password: $("rPass").value, device: navigator.userAgent.slice(0, 150) });
  setToken(r.token); render();
});
window.doLogin = safe(async () => {
  const r = await api("POST", "/api/auth/login", { email: $("lEmail").value, password: $("lPass").value, device: navigator.userAgent.slice(0, 150) });
  setToken(r.token); render();
});
window.doChildLogin = safe(async () => {
  const r = await api("POST", "/api/auth/child-login", { code: $("cCode").value });
  setToken(r.token); render();
});
$("logoutBtn").onclick = safe(async () => {
  await api("POST", "/api/auth/logout").catch(() => {});
  S.token = null; sessionStorage.removeItem("token"); render();
});

function authView() {
  return `<div class="grid">
  <div class="card"><h2>Регистрация взрослого</h2>
    <div class="row"><input id="rEmail" placeholder="email"><input id="rName" placeholder="Имя"><input id="rPass" type="password" placeholder="пароль"></div>
    <button onclick="doRegister()">Зарегистрироваться</button>
    <p class="muted">На личный счёт начисляется 50 000 ₽ тестовых денег.</p></div>
  <div class="card"><h2>Вход взрослого</h2>
    <div class="row"><input id="lEmail" placeholder="email"><input id="lPass" type="password" placeholder="пароль"></div>
    <button onclick="doLogin()">Войти</button>
    <p class="muted">Тестовая семья (python seed.py): mama@test.ru / papa@test.ru, пароль 1234</p></div>
  <div class="card"><h2>Вход ребёнка (по коду)</h2>
    <div class="row"><input id="cCode" placeholder="код от родителя"></div>
    <button onclick="doChildLogin()">Войти как ребёнок</button></div>
  </div>`;
}

// ---------------- render root ----------------
async function render() {
  const app = $("app");
  if (!S.token) { $("whoami").textContent = ""; $("logoutBtn").classList.add("hidden"); app.innerHTML = authView(); return; }
  try { S.me = await api("GET", "/api/auth/me"); } catch { return render(); }
  $("logoutBtn").classList.remove("hidden");
  if (S.me.kind === "child") { $("whoami").textContent = `Ребёнок: ${S.me.child.name}`; return renderChild(); }
  $("whoami").textContent = `${S.me.user.name} <${S.me.user.email}>` + (S.me.family ? ` · ${S.me.family.name} (${S.me.family.role})` : "");
  if (!S.me.family) return renderNoFamily();
  return renderAdult();
}

// ---------------- без семьи ----------------
async function incomingHtml() {
  const inv = await api("GET", "/api/invitations/incoming");
  const urlTok = new URLSearchParams(location.search).get("invite");
  return `<div class="card"><h2>Входящие приглашения</h2>
    ${urlTok ? `<p>Открыта ссылка-приглашение. <button onclick="acceptInv('${esc(urlTok)}')">Принять</button> <button class="sec" onclick="declineInv('${esc(urlTok)}')">Отклонить</button></p>` : ""}
    <table><tr><th>Семья</th><th>От</th><th>Статус</th><th>Истекает</th><th></th></tr>
    ${inv.map((i) => `<tr><td>${esc(i.family_name)}</td><td>${esc(i.inviter)}</td><td>${tag(i.status)}</td><td>${dt(i.expires_at)}</td>
      <td>${i.status === "sent" ? `<button onclick="acceptInv('${esc(i.token)}')">Принять</button>
        <button class="sec" onclick="acceptInv('${esc(i.token)}');acceptInv('${esc(i.token)}')">Тест: принять дважды</button>
        <button class="sec" onclick="declineInv('${esc(i.token)}')">Отклонить</button>` : ""}</td></tr>`).join("") || "<tr><td colspan=5 class=muted>нет</td></tr>"}
    </table><p class="muted">Повторное принятие не создаёт второго участника.</p></div>`;
}
window.acceptInv = safe(async (t) => {
  const r = await api("POST", `/api/invitations/${t}/accept`);
  toast(r.already ? "Приглашение уже было принято (повтор)" : "Вы в семье!");
  history.replaceState(null, "", "/"); render();
});
window.declineInv = safe(async (t) => { await api("POST", `/api/invitations/${t}/decline`); history.replaceState(null, "", "/"); render(); });

async function renderNoFamily() {
  $("app").innerHTML = `<div class="grid">
    <div class="card"><h2>Создать семью</h2><div class="row"><input id="famName" placeholder="Название семьи" value="Наша семья"><button onclick="createFamily()">Создать</button></div>
    <p class="muted">Создатель становится владельцем. Общему счёту начисляется 30 000 ₽ тестовых денег.</p></div>
    ${await incomingHtml()}
    ${await accountsHtml()}
    ${bugsFormHtml()}</div>`;
}
window.createFamily = safe(async () => { await api("POST", "/api/family", { name: $("famName").value }); render(); });

// ---------------- взрослый в семье ----------------
async function renderAdult() {
  const [fam, accounts] = await Promise.all([api("GET", "/api/family"), api("GET", "/api/accounts")]);
  S.family = fam; S.accounts = accounts;
  const isOwner = fam.my_role === "owner";
  const sections = [familyHtml(fam, isOwner)];
  if (isOwner) sections.push(await invitationsHtml());
  sections.push(await incomingHtml(), accountsBlock(accounts), payFormHtml(), await familyOpsHtml(),
    childrenHtml(fam, isOwner), await budgetHtml(), await remindersHtml(), await paymentsHtml(), await eventsHtml(),
    bugsFormHtml(), await bugsListHtml());
  $("app").innerHTML = `<div class="row"><button class="sec" onclick="render()">⟳ Обновить</button>
    <span class="muted">Данные с сервера; во второй вкладке/устройстве войдите вторым взрослым.</span></div>
    <div class="grid">${sections.join("")}</div>`;
  if (S.selChild) { const s = $("planChild"); if (s) s.value = S.selChild; }
  if (S.selMonth) { const m = $("planMonth"); if (m) m.value = S.selMonth; }
  loadPlan();
}

function familyHtml(fam, isOwner) {
  return `<div class="card"><h2>Семья «${esc(fam.name)}»</h2>
  <table><tr><th>Участник</th><th>Роль</th><th>С</th><th></th></tr>
  ${fam.members.map((m) => `<tr><td>${esc(m.name)}<div class="muted">${esc(m.email)}</div></td><td>${esc(m.role)}</td><td>${dt(m.joined_at)}</td>
    <td>${isOwner && m.role !== "owner" ? `<button class="danger" onclick="removeMember(${m.user_id})">Удалить</button>` : ""}</td></tr>`).join("")}
  </table>
  ${isOwner ? `<div class="row" style="margin-top:8px"><b>Добавить ребёнка:</b><input id="chName" placeholder="Имя"><input id="chBirth" type="date">
     лимит/мес ₽ <input id="chLimit" type="number" value="1000"><button onclick="addChild()">Добавить</button></div>`
    : `<button class="danger" onclick="leaveFamily()">Выйти из семьи</button>`}
  </div>`;
}
window.removeMember = safe(async (id) => { if (confirm("Удалить участника? Доступ пропадёт сразу.")) { await api("DELETE", `/api/family/members/${id}`); render(); } });
window.leaveFamily = safe(async () => { if (confirm("Выйти из семьи?")) { await api("POST", "/api/family/leave"); render(); } });
window.addChild = safe(async () => {
  await api("POST", "/api/family/children", { name: $("chName").value, birth_date: $("chBirth").value || null, monthly_limit: toKop($("chLimit").value) });
  render();
});

async function invitationsHtml() {
  const inv = await api("GET", "/api/family/invitations");
  return `<div class="card"><h2>Приглашения (владелец)</h2>
  <div class="row"><input id="invEmail" placeholder="email второго взрослого">
    срок, сек <input id="invTtl" type="number" placeholder="по умолч. 72ч"><button onclick="sendInvite()">Пригласить</button></div>
  <table><tr><th>Кому</th><th>Статус</th><th>Истекает</th><th>Ссылка</th><th></th></tr>
  ${inv.map((i) => `<tr><td>${esc(i.invitee_email)}</td><td>${tag(i.status)}</td><td>${dt(i.expires_at)}</td>
    <td>${i.token ? `<input readonly style="width:150px" value="${esc(location.origin + "/?invite=" + i.token)}" onclick="this.select()">` : ""}</td>
    <td>${i.status === "sent" ? `<button class="sec" onclick="cancelInvite(${i.id})">Отменить</button>` : ""}</td></tr>`).join("") || "<tr><td colspan=5 class=muted>нет</td></tr>"}
  </table><p class="muted">Ссылку передайте второму взрослому: он входит своим аккаунтом и принимает.</p></div>`;
}
window.sendInvite = safe(async () => {
  const ttl = parseInt($("invTtl").value) || null;
  await api("POST", "/api/family/invitations", { email: $("invEmail").value, ttl_seconds: ttl });
  toast("Приглашение отправлено"); render();
});
window.cancelInvite = safe(async (id) => { await api("POST", `/api/family/invitations/${id}/cancel`); render(); });

async function accountsHtml() { return accountsBlock(await api("GET", "/api/accounts")); }
function accountsBlock(accounts) {
  S.accounts = accounts;
  const shared = accounts.find((a) => a.kind === "shared"), personal = accounts.find((a) => a.kind === "personal");
  return `<div class="card"><h2>Мои счета</h2>
  <table><tr><th>Счёт</th><th>Остаток</th><th>Карты (привязка к счёту)</th><th></th></tr>
  ${accounts.map((a) => `<tr><td>${esc(a.name)} <span class="muted">#${a.id} ${a.kind}</span></td><td><b>${rub(a.balance)}</b></td>
    <td>${a.cards.map((c) => `${esc(c.pan_masked)} · ${esc(c.holder)} ${tag(c.status)}`).join("<br>")}</td>
    <td><button class="sec" onclick="showAccOps(${a.id})">История</button></td></tr>`).join("")}
  </table>
  ${shared && personal ? `<div class="row">Перевести с личного на общий ₽ <input id="trAmt" type="number" value="1000"><button onclick="transfer(${personal.id},${shared.id})">Перевести</button></div>` : ""}
  <div id="accOps"></div>
  <p class="muted">Личный счёт партнёра API не отдаёт.</p></div>`;
}
window.transfer = safe(async (from, to) => {
  const r = await api("POST", "/api/transfers", { from_account_id: from, to_account_id: to, amount: toKop($("trAmt").value) }, { idem: newKey() });
  toast(`Перевод: ${r.status}${r.failure_reason ? " — " + r.failure_reason : ""}`); render();
});
window.showAccOps = safe(async (id) => { $("accOps").innerHTML = `<h2 style="margin-top:8px">История счёта #${id}</h2>` + opsTable(await api("GET", `/api/accounts/${id}/operations`)); });

function opsTable(ops) {
  return `<table><tr><th>Дата</th><th>Сумма</th><th>Плательщик</th><th>Назначение</th><th>Ребёнок</th><th>Карта</th></tr>
  ${ops.map((o) => `<tr><td>${dt(o.date)}</td><td style="color:${o.amount < 0 ? "#b3261e" : "#1b7a2e"}">${rub(o.amount)}</td>
    <td>${esc(o.payer)}</td><td>${esc(o.purpose)} <span class="muted">${esc(o.type)}</span></td><td>${esc(o.child || "")}</td><td class="muted">${esc(o.card || "")}</td></tr>`).join("") || "<tr><td colspan=6 class=muted>нет операций</td></tr>"}</table>`;
}

async function familyOpsHtml() {
  return `<div class="card"><h2>Общая история (общий счёт и кошельки детей)</h2>${opsTable(await api("GET", "/api/family/operations"))}</div>`;
}

function accountOptions() { return S.accounts.map((a) => `<option value="${a.id}">${esc(a.name)} — ${rub(a.balance)}</option>`).join(""); }
function childOptions(empty = true) { return (empty ? `<option value="">— без ребёнка —</option>` : "") + (S.family?.children || []).map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join(""); }

function payFormHtml() {
  return `<div class="card"><h2>Тестовая оплата</h2>
  <div class="row">Счёт <select id="payAcc">${accountOptions()}</select></div>
  <div class="row">Сумма ₽ <input id="payAmt" type="number" value="500"> Назначение <input id="payPurpose" value="Тестовая покупка"></div>
  <div class="row">Расход относится к <select id="payChild">${childOptions()}</select>
    <label><input type="checkbox" id="paySim"> имитировать ошибку провайдера</label></div>
  <div class="row"><button onclick="pay(false)">Оплатить</button>
    <button class="sec" onclick="pay(true)">Тест: 3 нажатия подряд</button>
    <span class="muted">Idempotency-Key: ${S.payKey.slice(0, 8)}…</span></div>
  <div id="payResult" class="muted"></div></div>`;
}
window.pay = safe(async (burst) => {
  const body = { account_id: +$("payAcc").value, amount: toKop($("payAmt").value), purpose: $("payPurpose").value,
    child_id: $("payChild").value ? +$("payChild").value : null, simulate_failure: $("paySim").checked };
  const key = S.payKey;
  const n = burst ? 3 : 1;
  const res = await Promise.all(Array.from({ length: n }, () => api("POST", "/api/payments", body, { idem: key })));
  S.payKey = newKey(); // новый ключ — только для новой оплаты
  toast(res.map((r) => `#${r.id}: ${r.status}${r.failure_reason ? " (" + r.failure_reason + ")" : ""}${r.replayed ? " [повтор, не списано]" : ""}`).join("\n"));
  render();
});

function childrenHtml(fam, isOwner) {
  return `<div class="card"><h2>Дети и виртуальные кошельки</h2>
  ${fam.children.map((c) => `<div style="border-top:1px solid #eee;padding:6px 0">
    <b>${esc(c.name)}</b> · баланс <b>${rub(c.balance)}</b> · лимит ${rub(c.monthly_limit)}/мес · потрачено ${rub(c.spent_this_month)} · осталось ${rub(c.limit_left)} ${c.frozen ? tag("заморожен") : ""}
    ${isOwner ? `<div class="muted">Код входа ребёнка: <b>${esc(c.access_code)}</b> <button class="sec" onclick="newCode(${c.id})">Новый код</button></div>` : ""}
    <div class="row">Лимит ₽ <input id="lim${c.id}" type="number" value="${c.monthly_limit / 100}"><button class="sec" onclick="setLimit(${c.id})">Сохранить</button>
      <button class="${c.frozen ? "" : "danger"}" onclick="setFrozen(${c.id}, ${!c.frozen})">${c.frozen ? "Разморозить" : "Заморозить"}</button></div>
    <div class="row">Пополнить ₽ <input id="top${c.id}" type="number" value="500"> с <select id="topAcc${c.id}">${accountOptions()}</select><button onclick="topup(${c.id})">Пополнить</button>
      <button class="sec" onclick="childHist(${c.id})">История</button></div>
    <div class="row"><b>Тестовая покупка с кошелька ребёнка</b> ₽ <input id="kb${c.id}" type="number" value="100">
      <input id="kbM${c.id}" value="Школьная столовая"><button onclick="walletBuy(${c.id},1)">Купить</button>
      <button class="sec" onclick="walletBuy(${c.id},3)">Тест: 3 нажатия</button></div>
    <div class="muted">Списывается с баланса ${esc(c.name)}, проверяются заморозка, лимит и остаток. То же самое ребёнок делает со своего устройства (вход по коду).</div>
    <div id="chHist${c.id}"></div></div>`).join("") || `<p class="muted">Детей нет. Добавляет владелец.</p>`}
  </div>`;
}
window.setLimit = safe(async (id) => { await api("PATCH", `/api/children/${id}/settings`, { monthly_limit: toKop($("lim" + id).value) }); render(); });
window.setFrozen = safe(async (id, f) => { await api("PATCH", `/api/children/${id}/settings`, { frozen: f }); render(); });
window.newCode = safe(async (id) => { await api("POST", `/api/children/${id}/access-code`); render(); });
window.topup = safe(async (id) => {
  const r = await api("POST", `/api/children/${id}/topup`, { account_id: +$("topAcc" + id).value, amount: toKop($("top" + id).value) }, { idem: newKey() });
  toast(`Пополнение: ${r.status}${r.failure_reason ? " — " + r.failure_reason : ""}`); render();
});
window.walletBuy = safe(async (id, n) => {
  const key = newKey(); // один ключ на все n нажатий — проверка защиты от повторного списания
  const body = { amount: toKop($("kb" + id).value), merchant: $("kbM" + id).value };
  const res = await Promise.all(Array.from({ length: n }, () => api("POST", `/api/children/${id}/test-purchase`, body, { idem: key })));
  toast(res.map((r) => `#${r.id}: ${r.status}${r.failure_reason ? " (" + r.failure_reason + ")" : ""}${r.replayed ? " [повтор, не списано]" : ""}`).join("\n"));
  render();
});
window.childHist = safe(async (id) => {
  const h = await api("GET", `/api/children/${id}/history`);
  $("chHist" + id).innerHTML = purchasesTable(h.purchases) + opsTable(h.wallet_operations);
});
function purchasesTable(ps) {
  return `<table><tr><th>Покупка</th><th>Сумма</th><th>Статус</th><th>Когда</th></tr>${ps.map((p) => `<tr><td>${esc(p.merchant)}</td><td>${rub(p.amount)}</td>
  <td>${tag(p.status)} ${esc(p.failure_reason || "")}</td><td>${dt(p.created_at)}</td></tr>`).join("") || "<tr><td colspan=4 class=muted>нет покупок</td></tr>"}</table>`;
}

// ---------------- бюджет ----------------
const CATS = { section: "Секция", club: "Кружок", pocket_money: "Карманные", other: "Другое" };
async function budgetHtml() {
  const month = S.selMonth || new Date().toISOString().slice(0, 7);
  return `<div class="card" style="grid-column: 1 / -1"><h2>Бюджет ребёнка по месяцам</h2>
  <div class="row">Ребёнок <select id="planChild" onchange="S.selChild=this.value;loadPlan()">${childOptions(false)}</select>
    Месяц <input id="planMonth" type="month" value="${month}" onchange="S.selMonth=this.value;loadPlan()"></div>
  <div id="planBox"></div><p class="muted">План деньги не резервирует. Изменение сумм в согласованном плане возвращает его в черновик. Сохранение с устаревшей версией отклоняется (409).</p></div>`;
}
window.loadPlan = safe(async () => {
  const box = $("planBox"); if (!box) return;
  const cid = $("planChild")?.value, month = $("planMonth").value;
  if (!cid) { box.innerHTML = `<p class="muted">Сначала добавьте ребёнка.</p>`; return; }
  const plans = await api("GET", `/api/children/${cid}/plans`);
  const p = plans.find((x) => x.month === month);
  if (!p) { box.innerHTML = `<button onclick="createPlan()">Создать план на ${esc(month)}</button>`; return; }
  S.plan = p;
  // согласует не тот, кто отправил (если взрослых двое) — то же правило проверяет сервер
  const waitingOther = p.status === "pending_approval" && p.submitted_by_id === S.me.user.id && S.family.members.length > 1;
  box.innerHTML = `<div class="row">Статус ${tag(p.status)} · <span class="muted" title="Растёт при каждом изменении плана (правка, отправка, согласование, возврат). Нужна, чтобы правка по устаревшей копии не затёрла чужую.">версия ${p.version}</span> · итого <b>${rub(p.total)}</b> · изменил ${esc(p.updated_by)}
    ${p.submitted_by ? ` · отправил ${esc(p.submitted_by)}` : ""}${p.approved_by ? ` · согласовал ${esc(p.approved_by)}` : ""}
    ${p.review_comment ? ` · комментарий: «${esc(p.review_comment)}»` : ""}</div>
  <table id="itemsTbl"><tr><th>Категория</th><th>Название</th><th>Сумма ₽</th><th>Срок</th><th>Сайт оплаты</th><th></th></tr>
  ${p.items.map((i) => itemRow(i)).join("")}</table>
  <div class="row"><button class="sec" onclick="addItemRow()">+ статья</button><button onclick="savePlan()">Сохранить изменения</button>
    ${["draft", "needs_revision"].includes(p.status) ? `<button onclick="planAction('submit')">Отправить на согласование</button>` : ""}
    ${p.status === "pending_approval" && !waitingOther ? `<button onclick="planAction('approve')">Согласовать</button>
      <input id="retComment" placeholder="комментарий"><button class="danger" onclick="planAction('return')">Вернуть на доработку</button>` : ""}</div>
  ${waitingOther ? `<p>⏳ Вы отправили план — его согласовывает второй взрослый (${esc(S.family.members.filter((m) => m.user_id !== S.me.user.id).map((m) => m.name).join(", "))}). Войдите им на другом устройстве / в другой вкладке.</p>` : ""}`;
});
function itemRow(i = {}) {
  return `<tr data-id="${i.id ?? ""}"><td><select class="iCat">${Object.entries(CATS).map(([k, v]) => `<option value="${k}" ${i.category === k ? "selected" : ""}>${v}</option>`).join("")}</select></td>
  <td><input class="iTitle" value="${esc(i.title ?? "")}"></td><td><input class="iAmt" type="number" value="${i.amount != null ? i.amount / 100 : ""}"></td>
  <td><input class="iDue" type="date" value="${esc(i.due_date ?? "")}"></td><td><input class="iUrl" value="${esc(i.payee_url ?? "")}" placeholder="https://"></td>
  <td><button class="sec" onclick="this.closest('tr').remove()">✕</button></td></tr>`;
}
window.addItemRow = () => $("itemsTbl").insertAdjacentHTML("beforeend", itemRow({ category: "section" }));
function readItems() {
  return [...document.querySelectorAll("#itemsTbl tr[data-id]")].map((tr) => ({
    id: tr.dataset.id ? +tr.dataset.id : null, category: tr.querySelector(".iCat").value, title: tr.querySelector(".iTitle").value,
    amount: toKop(tr.querySelector(".iAmt").value), due_date: tr.querySelector(".iDue").value || null, payee_url: tr.querySelector(".iUrl").value || null,
  }));
}
window.createPlan = safe(async () => {
  await api("POST", `/api/children/${$("planChild").value}/plans`, { month: $("planMonth").value, items: [] });
  loadPlan();
});
window.savePlan = safe(async () => {
  try {
    const items = readItems();
    const norm = (arr) => JSON.stringify(arr.map((i) => [i.id ?? null, i.category, i.title, i.amount, i.due_date || null, i.payee_url || null]));
    if (norm(items) === norm(S.plan.items)) { toast("Изменений нет — сохранять нечего"); return; }
    const p = await api("PUT", `/api/plans/${S.plan.id}`, { version: S.plan.version, items });
    toast(!p.changed ? "Изменений нет — сохранять нечего"
      : p.status === "draft" && S.plan.status !== "draft" ? "Суммы изменены — план вернулся в черновик, нужно согласовать заново" : "Сохранено");
  } catch (e) { if (e.status === 409) toast("Конфликт версий: план изменил другой участник. Загружена актуальная версия.", 6000); }
  render();
});
window.planAction = safe(async (action) => {
  await api("POST", `/api/plans/${S.plan.id}/${action}`, { version: S.plan.version, comment: $("retComment")?.value || null });
  render();
});

// ---------------- напоминания ----------------
async function remindersHtml() {
  const rs = await api("GET", "/api/reminders");
  return `<div class="card"><h2>Напоминания об оплате</h2>
  <div class="row">Оплачивать со счёта <select id="remAcc">${accountOptions()}</select></div>
  <table><tr><th>Ребёнок</th><th>Статья</th><th>Сумма</th><th>Срок</th><th>Статус</th><th></th></tr>
  ${rs.map((r) => `<tr><td>${esc(r.child)}</td><td>${esc(r.title)}</td><td>${rub(r.amount)}</td><td>${esc(r.due_date || "")}</td>
    <td>${tag(r.status)}${r.payment_id ? `<div class="muted">платёж #${r.payment_id}, операция #${r.operation_id}</div>` : ""}${r.closed_by ? `<div class="muted">${esc(r.closed_by)}</div>` : ""}</td>
    <td>${r.status === "open" ? `<button onclick="payReminder(${r.id})">Оплатить</button>
      ${r.payee_url ? `<button class="sec" onclick="openExt(${r.id})">Сайт</button>` : ""}
      <button class="sec" onclick="markDone(${r.id})">Отметить вручную</button>` : ""}</td></tr>`).join("") || "<tr><td colspan=6 class=muted>Появятся после согласования плана</td></tr>"}
  </table><p class="muted">Переход на сайт и ручная отметка остатки не меняют.</p></div>`;
}
window.payReminder = safe(async (id) => {
  S.remKeys = S.remKeys || {};
  const key = (S.remKeys[id] = S.remKeys[id] || newKey()); // один ключ на попытку оплаты — защита от двойного клика
  let r;
  try { r = await api("POST", "/api/payments", { account_id: +$("remAcc").value, reminder_id: id }, { idem: key }); }
  finally { delete S.remKeys[id]; }
  toast(`Оплата: ${r.status}${r.failure_reason ? " — " + r.failure_reason : ""}${r.replayed ? " [повтор]" : ""}`); render();
});
window.openExt = safe(async (id) => { const r = await api("POST", `/api/reminders/${id}/open-external`); if (r.url) window.open(r.url, "_blank"); });
window.markDone = safe(async (id) => { await api("POST", `/api/reminders/${id}/mark-done`); render(); });

async function paymentsHtml() {
  const ps = await api("GET", "/api/payments");
  return `<div class="card"><h2>Платежи (статусы)</h2><table><tr><th>#</th><th>Тип</th><th>Сумма</th><th>Плательщик</th><th>Ребёнок</th><th>Статус</th></tr>
  ${ps.map((p) => `<tr><td>${p.id}</td><td>${esc(p.kind)}<div class="muted">${esc(p.purpose)}</div></td><td>${rub(p.amount)}</td><td>${esc(p.payer)}</td><td>${esc(p.child || "")}</td>
    <td>${tag(p.status)} ${esc(p.failure_reason || "")}</td></tr>`).join("") || "<tr><td colspan=6 class=muted>нет</td></tr>"}</table></div>`;
}

async function eventsHtml() {
  const evs = await api("GET", "/api/family/events");
  return `<div class="card"><h2>Журнал событий (для метрик)</h2><div style="max-height:300px;overflow:auto"><table>
  ${evs.map((e) => `<tr><td class="muted">${dt(e.at)}</td><td>${esc(e.type)}</td><td>${esc(e.actor || "")}</td><td><pre>${esc(JSON.stringify(e.payload))}</pre></td></tr>`).join("")}
  </table></div><a href="/api/metrics" target="_blank">/api/metrics</a></div>`;
}

// ---------------- баг-репорты ----------------
function bugsFormHtml() {
  return `<div class="card"><h2>Сообщить об ошибке</h2>
  <input id="bTitle" placeholder="Кратко" style="width:100%;box-sizing:border-box">
  <textarea id="bSteps" rows="3" placeholder="Шаги воспроизведения" style="width:100%;box-sizing:border-box"></textarea>
  <textarea id="bExp" rows="2" placeholder="Ожидаемый результат" style="width:100%;box-sizing:border-box"></textarea>
  <textarea id="bAct" rows="2" placeholder="Фактический результат" style="width:100%;box-sizing:border-box"></textarea>
  <div class="row">Скриншот <input id="bShot" type="file" accept="image/*"><button onclick="sendBug()">Отправить</button></div></div>`;
}
window.sendBug = safe(async () => {
  const f = new FormData();
  f.append("title", $("bTitle").value); f.append("steps", $("bSteps").value); f.append("expected", $("bExp").value); f.append("actual", $("bAct").value);
  f.append("reporter", S.me?.user?.email || S.me?.child?.name || "");
  if ($("bShot").files[0]) f.append("screenshot", $("bShot").files[0]);
  await api("POST", "/api/bugs", undefined, { form: f });
  toast("Баг записан"); render();
});
async function bugsListHtml() {
  const bs = await api("GET", "/api/bugs");
  return `<div class="card"><h2>Найденные ошибки</h2><table><tr><th>#</th><th>Ошибка</th><th>Шаги / ожидание / факт</th><th></th></tr>
  ${bs.map((b) => `<tr><td>${b.id}</td><td>${esc(b.title)}<div class="muted">${esc(b.reporter || "")} ${dt(b.created_at)}</div></td>
    <td><pre>${esc(b.steps)}</pre><b>Ожид.:</b> ${esc(b.expected)}<br><b>Факт:</b> ${esc(b.actual)}</td>
    <td>${b.screenshot ? `<a href="${esc(b.screenshot)}" target="_blank">скрин</a>` : ""}</td></tr>`).join("") || "<tr><td colspan=4 class=muted>нет</td></tr>"}</table></div>`;
}

// ---------------- режим ребёнка ----------------
async function renderChild() {
  const me = await api("GET", "/api/child/me");
  S.childKey = S.childKey || newKey();
  $("app").innerHTML = `<div class="grid"><div class="card"><h2>Привет, ${esc(me.name)}!</h2>
    <p>Баланс: <b>${rub(me.balance)}</b></p>
    <p>Лимит на месяц: ${rub(me.monthly_limit)} · потрачено ${rub(me.spent_this_month)} · можно ещё <b>${rub(me.limit_left)}</b></p>
    ${me.frozen ? `<p>${tag("blocked")} Кошелёк заморожен родителем</p>` : ""}
    <div class="row">Покупка ₽ <input id="kAmt" type="number" value="100"> <input id="kMerch" value="Школьная столовая"></div>
    <div class="row"><button onclick="kidBuy(1)">Купить</button><button class="sec" onclick="kidBuy(3)">Тест: 3 нажатия</button>
      <button class="sec" onclick="render()">⟳</button></div></div>
    <div class="card"><h2>Мои покупки</h2>${purchasesTable(me.purchases)}<h2 style="margin-top:8px">Кошелёк</h2>${opsTable(me.wallet_operations)}</div>
    ${bugsFormHtml()}</div>`;
}
window.kidBuy = safe(async (n) => {
  const key = S.childKey;
  const res = await Promise.all(Array.from({ length: n }, () => api("POST", "/api/child/purchases", { amount: toKop($("kAmt").value), merchant: $("kMerch").value }, { idem: key })));
  S.childKey = newKey();
  toast(res.map((r) => `#${r.id}: ${r.status}${r.failure_reason ? " (" + r.failure_reason + ")" : ""}${r.replayed ? " [повтор]" : ""}`).join("\n"));
  render();
});

render();
