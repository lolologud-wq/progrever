/* ═══════════════════════════════════════════════════════════
   exwarmer — frontend logic
   ═══════════════════════════════════════════════════════════ */

const API = {
    token: localStorage.getItem("exw_token") || "",
    async call(path, opts = {}) {
        const headers = { "Content-Type": "application/json" };
        if (this.token) headers["Authorization"] = "Bearer " + this.token;
        const res = await fetch(path, { ...opts, headers });
        if (res.status === 401) { showLogin(); throw new Error("Не авторизован"); }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "Ошибка запроса");
        return data;
    },
    get(p) { return this.call(p); },
    post(p, body) { return this.call(p, { method: "POST", body: JSON.stringify(body || {}) }); },
    del(p) { return this.call(p, { method: "DELETE" }); },
};

const STATUS = {
    green:  { label: "Идеально", dot: "green" },
    yellow: { label: "Хорошо",   dot: "yellow" },
    red:    { label: "Слабо",    dot: "red" },
    white:  { label: "Новый",    dot: "white" },
    black:  { label: "Нет сессии", dot: "black" },
    purple: { label: "Трастовый", dot: "purple" },
};

const ACTION_ICONS = {
    hold_start: "🛏", hold_over: "✅", hold_skip: "🛏", profile_change: "📝",
    join_channel: "📢", spambot: "🤖", botfather: "🤖", bot_visit: "🤖",
    dm_sent: "💬", group_msg: "💬", create_group: "🏠", create_group_skip: "🏠",
    send_sticker: "🎭", dm_sticker: "🎭", first_write: "✉️",
    dm_peer: "💬", dm_reply: "💬", group_chat: "💬", group_reply: "💬",
    received_message: "📩", set_avatar: "🖼", create_channel: "📺",
    channel_post: "📝", forward_post: "↗️", add_contact: "👤",
    status_msg: "🌙", error: "❌",
};

let STATE = { accounts: [], summary: null, view: "dashboard" };

/* ── Helpers ──────────────────────────────────────────── */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.remove("hidden");
    clearTimeout(t._t);
    t._t = setTimeout(() => t.classList.add("hidden"), 2600);
}

function fmtEta(sec) {
    if (sec == null) return "";
    if (sec <= 0) return "сейчас / скоро";
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (h) return `через ${h}ч ${m}м`;
    if (m) return `через ${m}м ${s}с`;
    return `через ${s}с`;
}

function initials(acc) {
    if (acc.username) return acc.username.slice(0, 2).toUpperCase();
    return (acc.phone_raw || acc.phone || "??").replace(/\D/g, "").slice(-2);
}

/* ── Auth ─────────────────────────────────────────────── */
function showLogin() {
    $("#login").classList.remove("hidden");
    $("#app").classList.add("hidden");
}
function showApp() {
    $("#login").classList.add("hidden");
    $("#app").classList.remove("hidden");
    loadAll();
}

$("#login-btn").onclick = async () => {
    const pw = $("#login-password").value;
    try {
        const { token } = await fetch("/api/login", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password: pw }),
        }).then(r => r.json().then(d => { if (!r.ok) throw new Error(d.detail); return d; }));
        API.token = token;
        localStorage.setItem("exw_token", token);
        showApp();
    } catch (e) {
        $("#login-error").textContent = e.message || "Ошибка входа";
    }
};
$("#login-password").addEventListener("keydown", e => { if (e.key === "Enter") $("#login-btn").click(); });

$("#logout-btn").onclick = () => {
    localStorage.removeItem("exw_token");
    API.token = "";
    showLogin();
};

/* ── Navigation ───────────────────────────────────────── */
function setView(v) {
    STATE.view = v;
    $$(".view").forEach(el => el.classList.add("hidden"));
    $("#view-" + v).classList.remove("hidden");
    $$(".nav-item[data-view]").forEach(b => b.classList.toggle("active", b.dataset.view === v));
    $$(".tab[data-view]").forEach(b => b.classList.toggle("active", b.dataset.view === v));
    if (v === "add") resetAddFlow();
}
$$("[data-view]").forEach(b => b.addEventListener("click", () => setView(b.dataset.view)));

$("#refresh-btn").onclick = () => { loadAll(); toast("Обновлено"); };
$("#cycle-btn").onclick = async () => {
    toast("Запускаю прогрев всех...");
    try { await API.post("/api/run_cycle"); toast("Цикл выполнен"); loadAll(); }
    catch (e) { toast(e.message); }
};

/* ── Load data ────────────────────────────────────────── */
async function loadAll() {
    try {
        const [summary, accounts] = await Promise.all([
            API.get("/api/summary"),
            API.get("/api/accounts"),
        ]);
        STATE.summary = summary;
        STATE.accounts = accounts;
        renderDashboard();
        renderAccounts();
    } catch (e) {
        console.error(e);
    }
}

/* ── Dashboard ────────────────────────────────────────── */
function renderDashboard() {
    const s = STATE.summary, accs = STATE.accounts;
    $("#hello-date").textContent = new Date().toLocaleDateString("ru-RU", {
        weekday: "long", day: "numeric", month: "long",
    });

    // progress rows — top 4 warming accounts
    const warming = accs.filter(a => a.has_session && !a.is_trusted).slice(0, 4);
    $("#progress-rows").innerHTML = warming.length ? warming.map(a => `
        <div class="p-row">
            <div class="p-row-top">
                <span class="lbl">${a.username ? "@" + a.username : a.phone}</span>
                <span class="val">${a.progress}%</span>
            </div>
            <div class="p-bar"><span style="width:${a.progress}%"></span></div>
        </div>`).join("") : `<div class="muted" style="color:#aaa">Нет прогреваемых аккаунтов</div>`;

    // stat cards
    $("#stat-cards").innerHTML = `
        <div class="stat-card"><div class="num">${s.total}</div><div class="cap">Всего аккаунтов</div></div>
        <div class="stat-card"><div class="num">${s.auto_active}</div><div class="cap">Активно прогревается</div></div>
        <div class="stat-card dark"><div class="num">${s.avg_score}%</div><div class="cap">Средний прогрев</div></div>
        <div class="stat-card"><div class="num">${s.complete}</div><div class="cap">Завершили прогрев</div></div>`;

    // dash table (top 5)
    renderTable("#dash-table", accs.slice(0, 5));

    // statuses dark panel
    $("#att-active").textContent = s.auto_active;
    const legend = [
        ["green", "Идеально"], ["yellow", "Хорошо"], ["red", "Слабо"],
        ["white", "Новые"], ["purple", "Трастовые"], ["black", "Без сессии"],
    ];
    $("#status-legend").innerHTML = legend.map(([k, lbl]) => `
        <div class="sl-item"><span class="sl-dot sd ${k}"></span>${lbl}<b>${s.status_counts[k] || 0}</b></div>
    `).join("");

    // score bars — buckets
    const buckets = [0, 0, 0, 0, 0];
    accs.forEach(a => {
        const i = Math.min(4, Math.floor(a.score / 20));
        buckets[i]++;
    });
    const maxB = Math.max(1, ...buckets);
    const labels = ["0-20", "20-40", "40-60", "60-80", "80-100"];
    $("#score-bars").innerHTML = buckets.map((b, i) => `
        <div class="bar-col">
            <div class="bar ${i === 4 ? "hi" : ""}" style="height:${(b / maxB) * 100}%"></div>
            <div class="bar-lbl">${labels[i]}</div>
        </div>`).join("");

    // donut
    renderDonut(s.status_counts, s.total);
}

function renderDonut(counts, total) {
    const order = [
        ["green", "#0d0d0f"], ["yellow", "#3a3a40"], ["red", "#6a6a72"],
        ["white", "#a0a0a8"], ["purple", "#4a4a50"], ["black", "#c4c4cc"],
    ];
    let acc = 0;
    const segs = [];
    order.forEach(([k, col]) => {
        const c = counts[k] || 0;
        if (!c || !total) return;
        const start = acc / total * 360;
        acc += c;
        const end = acc / total * 360;
        segs.push(`${col} ${start}deg ${end}deg`);
    });
    const donut = $("#donut");
    donut.style.background = total
        ? `conic-gradient(${segs.join(", ")})`
        : "#e4e4e7";
    donut.style.webkitMask = "radial-gradient(circle, transparent 54%, #000 55%)";
    donut.style.mask = "radial-gradient(circle, transparent 54%, #000 55%)";
    $("#donut-total").textContent = total;

    $("#comp-legend").innerHTML = order.map(([k, col]) => `
        <div class="cl-item"><span class="cl-dot" style="background:${col}"></span>
        ${STATUS[k].label}<b>${counts[k] || 0}</b></div>`).join("");
}

/* ── Table renderer ───────────────────────────────────── */
function renderTable(sel, list) {
    const el = $(sel);
    if (!list.length) {
        el.innerHTML = `<div class="muted" style="padding:20px;text-align:center">Нет аккаунтов</div>`;
        return;
    }
    const head = `<div class="table-row head">
        <div>Аккаунт</div><div>Прогрев</div><div>День</div><div>Статус</div></div>`;
    el.innerHTML = head + list.map(a => `
        <div class="table-row" data-id="${a.id}">
            <div class="cell-name">
                <div class="ava">${initials(a)}</div>
                <div class="meta">
                    <div class="nm">${a.username ? "@" + a.username : a.phone}</div>
                    <div class="sub">${a.is_trusted ? "Трастовый донор" : a.status_label}</div>
                </div>
            </div>
            <div class="cell">
                <div class="mini-progress"><span style="width:${a.progress}%"></span></div>
                <div class="v" style="margin-top:5px">${a.progress}%</div>
            </div>
            <div class="cell"><div class="v">${a.day}/${a.warmup_days}</div></div>
            <div class="cell">
                <span class="pill ${a.status}"><span class="sd ${a.status}"></span>${STATUS[a.status].label}</span>
            </div>
        </div>`).join("");
    el.querySelectorAll(".table-row[data-id]").forEach(r =>
        r.addEventListener("click", () => openAccount(+r.dataset.id)));
}

function renderAccounts() {
    const q = ($("#acc-search").value || "").toLowerCase();
    const list = STATE.accounts.filter(a =>
        !q || (a.phone_raw || "").toLowerCase().includes(q) ||
        (a.username || "").toLowerCase().includes(q));
    renderTable("#accounts-table", list);
}
$("#acc-search").addEventListener("input", renderAccounts);
$("#dash-search").addEventListener("input", e => {
    const q = e.target.value.toLowerCase();
    renderTable("#dash-table", STATE.accounts.filter(a =>
        !q || (a.phone_raw || "").toLowerCase().includes(q) ||
        (a.username || "").toLowerCase().includes(q)).slice(0, 8));
});

/* ── Account modal ────────────────────────────────────── */
async function openAccount(id) {
    let acc;
    try { acc = await API.get("/api/accounts/" + id); }
    catch (e) { toast(e.message); return; }

    const nx = acc.next || {};
    const holdBtn = acc.hold_enabled
        ? `<button class="chip" data-hold-restart>🔄 Перезапуск холда</button>
           <button class="chip" data-toggle="toggle_hold">🛏 Холд ВЫКЛ</button>`
        : `<button class="chip" data-toggle="toggle_hold">🛏 Холд ВКЛ</button>`;

    const runNow = (acc.auto_warming && acc.has_session && !acc.warmup_complete)
        ? `<button class="btn btn-primary" data-run-now>⚡ Выполнить сейчас</button>` : "";

    $("#modal-card").innerHTML = `
        <div class="md-head">
            <div class="ava">${initials(acc)}</div>
            <div>
                <div class="md-title">${acc.username ? "@" + acc.username : acc.phone}</div>
                <div class="md-sub">${acc.is_trusted ? "🟣 Трастовый донор" : acc.status_label} · ${acc.phone}</div>
            </div>
            <button class="md-close" data-close>✕</button>
        </div>

        <div class="md-stats">
            <div class="md-stat"><div class="n">${acc.progress}%</div><div class="c">прогрев</div></div>
            <div class="md-stat"><div class="n">${acc.day}/${acc.warmup_days}</div><div class="c">дней</div></div>
            <div class="md-stat"><div class="n">${acc.score}</div><div class="c">score</div></div>
        </div>

        ${acc.has_session && !acc.warmup_complete ? `
        <div class="md-next">
            <div class="lbl">🔮 Следующее действие</div>
            <div class="act">${nx.label || "—"}</div>
            <div class="eta">${acc.hold_remaining ? "⏳ Холд ещё " + fmtEta(acc.hold_remaining) : (nx.eta_seconds != null ? "⏰ " + fmtEta(nx.eta_seconds) + (nx.clock ? " (в " + nx.clock + ")" : "") : "")}</div>
        </div>` : (acc.warmup_complete ? `<div class="md-next"><div class="act">✅ Прогрев завершён</div></div>` : "")}

        ${runNow ? `<div style="margin-bottom:16px">${runNow}</div>` : ""}

        <div class="md-settings">
            <div><label>Холд (часов)</label><input id="set-hold" type="number" min="0" value="${acc.hold_hours}"></div>
            <div><label>Прогрев (дней)</label><input id="set-warmup" type="number" min="1" value="${acc.warmup_days}"></div>
            <button class="btn btn-ghost" data-save-settings>Сохранить</button>
        </div>

        <div class="md-toggles">
            <button class="chip ${acc.auto_warming ? "chip-dark" : ""}" data-toggle="toggle_auto">
                ${acc.auto_warming ? "⏸ Авто ВЫКЛ" : "▶️ Авто ВКЛ"}</button>
            <button class="chip ${acc.is_trusted ? "chip-dark" : ""}" data-toggle="toggle_trust">
                🟣 ${acc.is_trusted ? "Trusted ВЫКЛ" : "Trusted ВКЛ"}</button>
            ${holdBtn}
        </div>

        <div class="md-actions">
            <button class="btn btn-ghost" data-act="join">📢 Вступить в канал</button>
            <button class="btn btn-ghost" data-act="spambot">🤖 SpamBot</button>
            <button class="btn btn-ghost" data-act="profile">📝 Обновить профиль</button>
            <button class="btn btn-ghost" data-act="dm">💬 Написать в ЛС</button>
            <button class="btn btn-ghost" data-act="create_group">🏠 Создать группу</button>
            <button class="btn btn-ghost" data-act="grpmsg">💬 Сообщ. в группу</button>
            <button class="btn btn-ghost" data-act="channel">📺 Создать канал</button>
            <button class="btn btn-ghost" data-act="chpost">📬 Пост в канал</button>
        </div>

        <div class="md-logs">
            <h4>История действий</h4>
            ${(acc.logs || []).length ? acc.logs.map(l => `
                <div class="log-item">
                    <span class="li-ic">${ACTION_ICONS[l.action] || "•"}</span>
                    <span class="li-tx">${l.detail}${l.score_delta > 0 ? " (+" + l.score_delta + ")" : ""}</span>
                    <span class="li-ts">${l.ts}</span>
                </div>`).join("") : `<div class="muted">Пока нет действий</div>`}
        </div>

        <div style="margin-top:20px;display:flex;gap:10px">
            <button class="btn btn-danger" data-delete style="flex:1">🗑 Удалить аккаунт</button>
        </div>
    `;
    $("#modal").classList.remove("hidden");

    const card = $("#modal-card");
    card.querySelector("[data-close]").onclick = closeModal;
    card.querySelectorAll("[data-toggle]").forEach(b => b.onclick = async () => {
        try { await API.post(`/api/accounts/${id}/${b.dataset.toggle}`); await refreshAndReopen(id); loadAll(); }
        catch (e) { toast(e.message); }
    });
    const runBtn = card.querySelector("[data-run-now]");
    if (runBtn) runBtn.onclick = async () => {
        runBtn.textContent = "⏳ Выполняю...";
        try { const r = await API.post(`/api/accounts/${id}/run_now`); toast(strip(r.result)); await refreshAndReopen(id); loadAll(); }
        catch (e) { toast(e.message); runBtn.textContent = "⚡ Выполнить сейчас"; }
    };
    card.querySelectorAll("[data-act]").forEach(b => b.onclick = async () => {
        const orig = b.textContent; b.textContent = "⏳...";
        try { const r = await API.post(`/api/accounts/${id}/action/${b.dataset.act}`); toast(r.msg || "Готово"); await refreshAndReopen(id); loadAll(); }
        catch (e) { toast(e.message); b.textContent = orig; }
    });
    const hr = card.querySelector("[data-hold-restart]");
    if (hr) hr.onclick = async () => {
        try { await API.post(`/api/accounts/${id}/hold_restart`); toast("Холд перезапущен"); await refreshAndReopen(id); loadAll(); }
        catch (e) { toast(e.message); }
    };
    card.querySelector("[data-save-settings]").onclick = async () => {
        const hold = +$("#set-hold").value, warmup = +$("#set-warmup").value;
        try { await API.post(`/api/accounts/${id}/settings`, { hold_hours: hold, warmup_days: warmup }); toast("Сохранено"); await refreshAndReopen(id); loadAll(); }
        catch (e) { toast(e.message); }
    };
    card.querySelector("[data-delete]").onclick = async () => {
        if (!confirm("Удалить аккаунт? Все данные будут стёрты.")) return;
        try { await API.del("/api/accounts/" + id); toast("Аккаунт удалён"); closeModal(); loadAll(); }
        catch (e) { toast(e.message); }
    };
}

function strip(html) { return (html || "").replace(/<[^>]+>/g, "").replace(/\n/g, " "); }
async function refreshAndReopen(id) { await openAccount(id); }
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal").querySelector(".modal-backdrop").onclick = closeModal;

/* ── Add account flow ─────────────────────────────────── */
let ADD = { stage: "phone", login_id: null, phone: null };

function resetAddFlow() {
    ADD = { stage: "phone", login_id: null, phone: null };
    ["phone", "code", "2fa", "options"].forEach(s =>
        $("#step-" + s).classList.toggle("hidden", s !== "phone"));
    $("#add-error").textContent = "";
    $("#add-success").classList.add("hidden");
    $("#add-phone").value = ""; $("#add-code").value = "";
    $("#add-2fa").value = "";
    updateAddSteps();
}

function updateAddSteps() {
    const order = ["phone", "code", "2fa", "options"];
    const idx = order.indexOf(ADD.stage);
    $("#add-steps").innerHTML = order.map((_, i) =>
        `<div class="st ${i <= idx ? "on" : ""}"></div>`).join("");
}

function showAddStep(stage) {
    ADD.stage = stage;
    ["phone", "code", "2fa", "options"].forEach(s =>
        $("#step-" + s).classList.toggle("hidden", s !== stage));
    updateAddSteps();
}

$("#add-phone-btn").onclick = async () => {
    const phone = $("#add-phone").value.trim();
    $("#add-error").textContent = "";
    $("#add-phone-btn").textContent = "⏳ Отправка...";
    try {
        const r = await API.post("/api/add/start", { phone });
        ADD.phone = r.phone;
        if (r.stage === "exists") { showAddStep("options"); toast("Сессия уже есть"); }
        else { ADD.login_id = r.login_id; showAddStep("code"); toast("Код отправлен"); }
    } catch (e) { $("#add-error").textContent = e.message; }
    finally { $("#add-phone-btn").textContent = "Отправить код"; }
};

$("#add-code-btn").onclick = async () => {
    $("#add-error").textContent = "";
    $("#add-code-btn").textContent = "⏳...";
    try {
        const r = await API.post("/api/add/code", { login_id: ADD.login_id, code: $("#add-code").value });
        if (r.stage === "2fa") showAddStep("2fa");
        else { ADD.phone = r.phone; showAddStep("options"); toast("Авторизация успешна"); }
    } catch (e) { $("#add-error").textContent = e.message; }
    finally { $("#add-code-btn").textContent = "Подтвердить код"; }
};

$("#add-2fa-btn").onclick = async () => {
    $("#add-error").textContent = "";
    $("#add-2fa-btn").textContent = "⏳...";
    try {
        const r = await API.post("/api/add/password", { login_id: ADD.login_id, password: $("#add-2fa").value });
        ADD.phone = r.phone; showAddStep("options"); toast("Вход выполнен");
    } catch (e) { $("#add-error").textContent = e.message; }
    finally { $("#add-2fa-btn").textContent = "Войти"; }
};

$("#add-finalize-btn").onclick = async () => {
    $("#add-error").textContent = "";
    $("#add-finalize-btn").textContent = "⏳ Создаю...";
    try {
        const r = await API.post("/api/add/finalize", {
            login_id: ADD.login_id,
            phone: ADD.phone,
            is_trusted: $("#add-trusted").checked,
            hold_hours: +$("#add-hold").value,
            warmup_days: +$("#add-warmup").value,
        });
        $("#add-success").classList.remove("hidden");
        $("#add-success").innerHTML = `✅ Аккаунт добавлен!<br>${ADD.phone} · ${r.has_session ? "сессия активна, прогрев запущен" : "нужна сессия"}`;
        ["phone", "code", "2fa", "options"].forEach(s => $("#step-" + s).classList.add("hidden"));
        loadAll();
        setTimeout(() => setView("accounts"), 1600);
    } catch (e) { $("#add-error").textContent = e.message; }
    finally { $("#add-finalize-btn").textContent = "Создать аккаунт"; }
};

/* ── Boot ─────────────────────────────────────────────── */
if (API.token) showApp(); else showLogin();

// Live ETA refresh every 20s
setInterval(() => { if (!$("#app").classList.contains("hidden") && $("#modal").classList.contains("hidden")) loadAll(); }, 20000);
