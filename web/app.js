(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const icon = (name, style = "") => `<svg class="ico" ${style ? `style="${style}"` : ""}><use href="#i-${name}"/></svg>`;
  const embed = new URLSearchParams(location.search).has("embed");

  // Нестабильная сеть: незагрузившуюся картинку перезапрашиваем (до 4 раз, с паузой).
  document.addEventListener("error", (e) => {
    const img = e.target;
    if (!(img instanceof HTMLImageElement)) return;
    const n = +(img.dataset.retry || 0);
    if (n >= 4) return;
    img.dataset.retry = n + 1;
    const base = img.src.split("#")[0].replace(/[?&]r=\d+$/, "");
    setTimeout(() => { img.src = base + (base.includes("?") ? "&" : "?") + "r=" + (n + 1); }, 800 * (n + 1));
  }, true);
  if (embed) document.body.classList.add("embed");

  // localStorage может быть недоступен (приватный режим) — оборачиваем.
  const store = {
    get(k, d = null) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
    del(k) { try { localStorage.removeItem(k); } catch {} },
  };
  let token = store.get("dou_token2"), user = null;

  async function api(path, opts = {}) {
    opts.headers = Object.assign({ Authorization: "Bearer " + token }, opts.headers || {});
    const r = await fetch(path, opts);
    if (r.status === 401) { showLogin(); throw new Error("auth"); }
    return r;
  }
  const getJSON = async (path) => (await api(path)).json();
  const fmtDate = (iso) => { if (!iso) return ""; const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleDateString("ru-RU"); };
  const fmtTime = (ts) => new Date(ts).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

  // ======================================================== вход
  async function showLogin() {
    const list = await (await fetch("/api/users")).json();
    const box = $("#userList"); box.innerHTML = ""; $("#loginErr").textContent = "";
    for (const u of list) {
      const b = document.createElement("button");
      b.className = "user-opt";
      const ini = u.name.split(/\s+/).map((p) => p[0]).slice(0, 2).join("");
      b.innerHTML = `<span class="avatar">${esc(ini)}</span><span><b>${esc(u.name)}</b><small>${esc(u.role_name)} · ${esc(u.unit)}${u.needs_password ? " · 🔒 по паролю" : ""}</small></span>`;
      const doLogin = async (password) => {
        const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user_id: u.id, password: password || "" }) });
        const d = await r.json();
        if (!r.ok) { $("#loginErr").textContent = d.detail || "Ошибка входа"; return; }
        token = d.token; store.set("dou_token2", token); $("#login").classList.remove("open"); init();
      };
      b.onclick = () => {
        if (!u.needs_password) return doLogin();
        const f = document.createElement("form");
        f.className = "pw-form";
        f.innerHTML = '<input type="password" placeholder="Пароль администратора" autocomplete="current-password" required><button>Войти</button>';
        f.onsubmit = (e) => { e.preventDefault(); doLogin(f.querySelector("input").value); };
        b.replaceWith(f); f.querySelector("input").focus();
      };
      box.appendChild(b);
    }
    $("#login").classList.add("open");
  }
  $("#logout").onclick = () => { store.del("dou_token2"); token = null; user = null; showLogin(); };

  async function init() {
    if (!token) return showLogin();
    try { user = await getJSON("/api/me"); } catch { return; }
    $("#uName").textContent = user.name;
    $("#uRole").textContent = user.role_name;
    $("#uAva").textContent = user.name.split(/\s+/).map((p) => p[0]).slice(0, 2).join("");
    const staff = user.role === "admin" || user.role === "dou";
    $("#adminLink").hidden = $("#adminSep").hidden = !staff;
    chats = store.get(chatKey(), []);
    loadBell();
    route();
  }

  // ======================================================== роутер
  const loaders = {};
  function route() {
    if (!user) return;
    let [path, query] = (location.hash.replace(/^#\/?/, "") || (embed ? "chat" : "home")).split("?");
    if (embed) path = "chat";
    if (!$(`.view[data-view="${path}"]`)) path = "home";
    $$(".view").forEach((v) => v.classList.toggle("active", v.dataset.view === path));
    $$("#nav a[data-view]").forEach((a) => a.classList.toggle("active", a.dataset.view === path));
    document.body.classList.remove("nav-open");
    const params = new URLSearchParams(query || "");
    if (loaders[path]) loaders[path](params);
    window.scrollTo(0, 0);
  }
  window.addEventListener("hashchange", route);
  const go = (path) => { if (location.hash === "#/" + path) route(); else location.hash = "#/" + path; };
  $("#burger").onclick = () => document.body.classList.toggle("nav-open");

  // Любой элемент с data-ask открывает новый чат с этим вопросом.
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-ask]");
    if (!a || !a.dataset.ask) return;
    e.preventDefault();
    askInChat(a.dataset.ask);
  });

  // ======================================================== главная
  const POPULAR = [
    "Какие сроки рассмотрения проекта приказа?",
    "Подготовить проект приказа о вводе в эксплуатацию системы",
    "С кем согласовать распоряжение о создании рабочей группы?",
    "Что нужно удалить из файла модуля перед подписанием?",
  ];
  const rowAsk = (q, ico = "file") => `<button class="row" data-ask="${esc(q)}"><span class="tile-sm">${icon(ico)}</span><span class="txt">${esc(q)}</span>${icon("chev")}</button>`;
  loaders.home = () => { $("#popular").innerHTML = POPULAR.map((q) => rowAsk(q)).join(""); };

  // ======================================================== чат
  let chats = [], current = null, busy = false;
  const chatKey = () => "dou_chats_" + (user ? user.id : "anon");
  const saveChats = () => store.set(chatKey(), chats.slice(0, 30));
  const newId = () => Math.random().toString(36).slice(2, 10);

  function renderChatList() {
    const box = $("#chatList");
    if (!chats.length) { box.innerHTML = '<div class="empty">Здесь появятся ваши диалоги.</div>'; return; }
    box.innerHTML = "";
    for (const c of chats) {
      const b = document.createElement("button");
      b.className = "chat-item" + (current && c.id === current.id ? " active" : "");
      const d = new Date(c.ts), today = new Date().toDateString() === d.toDateString();
      b.innerHTML = `<span class="tile-sm">${icon("chat")}</span><span><span class="t">${esc(c.title)}</span><span class="d">${today ? "Сегодня, " + fmtTime(c.ts) : fmtDate(c.ts)}</span></span><span class="del" title="Удалить">${icon("trash", "width:16px;height:16px")}</span>`;
      b.onclick = (e) => {
        if (e.target.closest(".del")) { chats = chats.filter((x) => x.id !== c.id); if (current && current.id === c.id) current = null; saveChats(); openChat(current); return; }
        openChat(c);
      };
      box.appendChild(b);
    }
  }

  function openChat(c) {
    current = c || null;
    const box = $("#messages"); box.innerHTML = "";
    if (!current || !current.msgs.length) renderWelcome();
    else current.msgs.forEach((m, i) => renderStored(m, i === current.msgs.length - 1));
    renderChatList(); scrollMsgs();
  }
  $("#newChat").onclick = () => { openChat(null); $("#input").focus(); };
  loaders.chat = () => { if (!$("#messages").children.length || !current) openChat(current); renderChatList(); setTimeout(() => $("#input").focus(), 50); };

  function askInChat(text) {
    current = null;
    if (!location.hash.startsWith("#/chat")) location.hash = "#/chat";
    setTimeout(() => { openChat(null); send(text); }, 30);
  }

  function renderWelcome() {
    const m = addBot();
    m.body.innerHTML = `<div class="welcome"><h3>Здравствуйте! 👋<br>Я ваш интеллектуальный помощник по документообороту.</h3>
      <p>Помогу найти требования Инструкции, подготовить проект по модульному шаблону, подсказать маршрут визирования и проверить документ. Отвечаю только по документам базы знаний и со ссылками на пункты.</p>
      <p><b>С чего начнем?</b></p></div>`;
    const grid = document.createElement("div");
    grid.className = "qa-grid";
    const cards = [
      ["search", "Найти информацию", "Найти пункт, срок, требование", () => { $("#input").placeholder = "Например: какой срок рассмотрения распоряжения?"; $("#input").focus(); }, ""],
      ["file", "Создать документ", "Подготовить проект приказа, распоряжения", () => send("Какие шаблоны есть?"), "gray"],
      ["shield", "Проверить документ", "Найти ошибки и соответствие требованиям", () => $("#file").click(), ""],
      ["book", "Инструкции", "Пошаговые руководства и регламенты", () => go("guides"), "gray"],
    ];
    for (const [ic, t, s, fn, cls] of cards) {
      const b = document.createElement("button");
      b.className = "qa-card"; b.type = "button";
      b.innerHTML = `<span class="tile-sm ${cls}">${icon(ic, "width:28px;height:28px")}</span><span><b>${t}</b><small>${s}</small></span><span class="go">${icon("chev")}</span>`;
      b.onclick = fn; grid.appendChild(b);
    }
    m.bubble.appendChild(grid);
  }

  function addUser(text, ts = Date.now()) {
    const d = document.createElement("div");
    d.className = "msg user";
    d.innerHTML = `<div class="bubble">${esc(text)}<div class="time">${fmtTime(ts)}</div></div>`;
    $("#messages").appendChild(d); scrollMsgs();
  }
  function addBot() {
    const d = document.createElement("div");
    d.className = "msg bot";
    d.innerHTML = `<div class="ava"><svg><use href="#logo"/></svg></div><div class="bubble"><div class="body"></div></div>`;
    $("#messages").appendChild(d);
    return { el: d, bubble: d.querySelector(".bubble"), body: d.querySelector(".body"), md: "", sources: [], used: [], invalid: [], buttons: [], file: null, progress: null };
  }
  const scrollMsgs = () => { const b = $("#messages"); b.scrollTop = b.scrollHeight; };

  function renderBody(m, final) {
    m.body.innerHTML = renderMarkdown(m.md) || '<span class="thinking">Формирую ответ…</span>';
    if (!final && m.body.lastElementChild) m.body.lastElementChild.classList.add("typing");
    $$(".cite", m.body).forEach((c) => {
      const s = m.sources.find((x) => x.n === +c.dataset.n);
      if (!s) { if (final && m.sources.length) c.classList.add("bad"); return; }
      c.title = `${s.doc_short}, ${s.ref}`;
      c.onclick = () => openSource(m, s.n);
    });
  }
  function renderSources(m) {
    let box = $(".sources", m.bubble);
    if (!m.sources.length) return;
    if (!box) { box = document.createElement("details"); box.className = "sources"; m.bubble.appendChild(box); }
    box.open = !embed && m.sources.length <= 6;
    box.innerHTML = `<summary>Источники (${m.sources.length})</summary>`;
    for (const s of m.sources) {
      const d = document.createElement("div");
      d.className = "src" + (m.used.includes(s.n) ? " used" : "");
      d.dataset.n = s.n;
      const ver = `ред. ${s.version}${s.approved ? ", " + s.approved : ""}`;
      d.innerHTML = `<div class="src-head"><span class="n">[${s.n}]</span><span class="ref"><b>${esc(s.doc_short)}</b>, ${esc(s.ref)}</span>
        <span class="rel" title="Релевантность запросу"><span class="bar"><i style="width:${s.relevance}%"></i></span>${s.relevance}%</span></div>
        <div class="src-body">${esc(s.text)}<div class="src-meta">${esc(s.doc)}${s.section ? " · " + esc(s.section) : ""} · стр. ${s.page} · ${esc(ver)}</div></div>`;
      $(".src-head", d).onclick = () => d.classList.toggle("open");
      box.appendChild(d);
    }
  }
  function openSource(m, n) {
    const box = $(".sources", m.bubble); if (!box) return;
    box.open = true;
    const s = $(`.src[data-n="${n}"]`, box);
    if (s) { s.classList.add("open", "flash"); s.scrollIntoView({ block: "nearest", behavior: "smooth" }); setTimeout(() => s.classList.remove("flash"), 1300); }
  }
  function addButtons(m, items) {
    $$(".quick", $("#messages")).forEach((q) => q.classList.add("used"));
    const box = document.createElement("div");
    box.className = "quick";
    for (const t of items) {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = t;
      b.onclick = () => { box.classList.add("used"); send(t); };
      box.appendChild(b);
    }
    m.bubble.appendChild(box);
  }
  function addFile(m, f) {
    const d = document.createElement("div");
    d.className = "file-card";
    d.innerHTML = `<span class="tile-sm">${icon("file")}</span><span><a href="${esc(f.url)}" download>${esc(f.name.replace(/_[0-9a-f]{32}/, ""))}</a><div class="src-meta">${esc(f.title)}</div></span>`;
    m.bubble.appendChild(d);
  }
  function addProgress(m, p) {
    const d = document.createElement("div");
    d.className = "progress";
    d.innerHTML = `<i style="width:${Math.round((100 * p.done) / p.total)}%"></i>`;
    m.bubble.appendChild(d);
  }
  function addTime(m, ts) {
    const t = document.createElement("div"); t.className = "time"; t.textContent = fmtTime(ts); m.bubble.appendChild(t);
  }
  function addFeedback(m) {
    const d = document.createElement("div");
    d.className = "fb";
    for (const [r, ico] of [[1, "👍"], [-1, "👎"]]) {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = ico; b.title = r > 0 ? "Полезно" : "Неточно";
      b.onclick = () => {
        $$("button", d).forEach((x) => x.classList.remove("on")); b.classList.add("on");
        api("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: current && current.sid || "", rating: r }) });
      };
      d.appendChild(b);
    }
    m.bubble.appendChild(d);
  }
  function renderStored(s, isLast) {
    if (s.role === "user") return addUser(s.md, s.ts);
    const m = addBot();
    Object.assign(m, { md: s.md, sources: s.sources || [], used: s.used || [], invalid: s.invalid || [] });
    renderBody(m, true); renderSources(m);
    if (s.file) addFile(m, s.file);
    if (isLast && s.buttons && s.buttons.length) addButtons(m, s.buttons);
    addTime(m, s.ts);
  }

  async function send(text) {
    text = (text ?? $("#input").value).trim();
    if (!text || busy) return;
    if (!current) {
      current = { id: newId(), sid: null, title: text.slice(0, 60), ts: Date.now(), msgs: [] };
      chats.unshift(current); $("#messages").innerHTML = "";
    }
    busy = true; $("#sendBtn").disabled = true;
    $("#input").value = ""; autosize();
    const ts = Date.now();
    addUser(text, ts);
    current.msgs.push({ role: "user", md: text, ts });
    current.ts = ts; saveChats(); renderChatList();
    const m = addBot();
    m.body.innerHTML = '<span class="thinking">Ищу в базе знаний…</span>';
    let hasQA = false;
    try {
      const r = await api("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: current.sid }) });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, i); buf = buf.slice(i + 1);
          if (!line.trim()) continue;
          const ev = JSON.parse(line);
          if (ev.type === "session") current.sid = ev.session_id;
          else if (ev.type === "text") { m.md += ev.text; renderBody(m, false); scrollMsgs(); }
          else if (ev.type === "status") { m.body.insertAdjacentHTML("beforeend", `<div class="thinking">⏳ ${esc(ev.text)}</div>`); scrollMsgs(); }
          else if (ev.type === "sources") { m.sources = ev.items; hasQA = true; renderSources(m); }
          else if (ev.type === "citations") { m.used = ev.used; m.invalid = ev.invalid; renderSources(m); }
          else if (ev.type === "buttons") { m.buttons = ev.items; addButtons(m, ev.items); }
          else if (ev.type === "file") { m.file = ev; addFile(m, ev); }
          else if (ev.type === "progress") addProgress(m, ev);
        }
      }
      renderBody(m, true);
      if (hasQA) addFeedback(m);
    } catch (e) {
      if (e.message !== "auth") { m.md += `\n\n⚠️ ${e.message}`; renderBody(m, true); }
    } finally {
      const bts = Date.now();
      addTime(m, bts);
      current.msgs.push({ role: "bot", md: m.md, sources: m.sources, used: m.used, invalid: m.invalid, buttons: m.buttons, file: m.file, ts: bts });
      saveChats();
      busy = false; $("#sendBtn").disabled = false; scrollMsgs();
    }
  }
  const autosize = () => { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 160) + "px"; };
  $("#input").addEventListener("input", autosize);
  $("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  $("#composer").onsubmit = (e) => { e.preventDefault(); send(); };
  $("#attach").onclick = () => $("#file").click();

  // ======================================================== документы
  let docFilter = "all";
  loaders.docs = async (params) => {
    docFilter = params.get("f") || docFilter;
    const chips = [["all", "Все", "file"], ["norm", "Нормативные", "book"], ["module", "Шаблоны модулей", "folder"], ["mine", "Мои проекты", "edit"]];
    $("#docChips").innerHTML = chips.map(([k, t, ic]) => `<button class="chip ${k === docFilter ? "active" : ""}" data-f="${k}">${icon(ic)}${t}</button>`).join("");
    $$("#docChips .chip").forEach((c) => c.onclick = () => { docFilter = c.dataset.f; loaders.docs(new URLSearchParams()); });
    const box = $("#docList"); box.innerHTML = '<div class="empty">Загрузка…</div>';
    const [d, mine, mods0] = await Promise.all([getJSON("/api/documents"), getJSON("/api/my/files"), getModules()]);
    const modTitle = Object.fromEntries(mods0.map((m) => [m.id, m.title]));
    const html = [];
    const norm = d.documents.filter((x) => x.kind !== "module"), mods = d.documents.filter((x) => x.kind === "module");
    if (docFilter === "all" || docFilter === "norm") {
      html.push("<h4>Нормативные документы</h4>");
      for (const x of norm) html.push(`<div class="doc-row"><span class="tile-sm">${icon("book")}</span>
        <span><b>${esc(x.title)}<span class="tag">ред. ${esc(x.version)}</span></b>
        <small>${x.approved ? esc(x.approved) + " · " : ""}действует с ${fmtDate(x.effective_from)} · фрагментов в индексе: ${x.chunks}</small></span>
        <span class="acts"><button class="btn-sm" data-ask="Кратко: что регулирует ${esc(x.short)} и из каких разделов состоит?">${icon("chat", "width:16px;height:16px")}Спросить</button></span></div>`);
    }
    if (docFilter === "all" || docFilter === "module") {
      html.push(`<h4 style="margin-top:18px">Шаблоны модулей</h4>`);
      for (const x of mods) {
        const t = modTitle[x.module_id] || x.title;
        html.push(`<div class="doc-row"><span class="tile-sm">${icon("folder")}</span>
        <span><b>${esc(t)}<span class="tag">ред. ${esc(x.version)}</span></b><small>Модульный документ · DOCX</small></span>
        <span class="acts">${x.module_id ? `<a class="btn-sm" href="/api/modules/${x.module_id}/file">${icon("download", "width:16px;height:16px")}Скачать</a>
        <button class="btn-sm" data-ask="Подготовь ${esc(t.toLowerCase())}">${icon("edit", "width:16px;height:16px")}Заполнить</button>` : ""}</span></div>`);
      }
    }
    if (docFilter === "all" || docFilter === "mine") {
      html.push(`<h4 style="margin-top:18px">Мои проекты</h4>`);
      if (!mine.length) html.push('<div class="empty">Пока нет подготовленных проектов. Создайте первый на вкладке «Создать документ».</div>');
      for (const f of mine) html.push(`<div class="doc-row"><span class="tile-sm">${icon("file")}</span>
        <span><b>${esc(f.title)}</b><small>${esc(f.module)} · ${fmtDate(f.created)}</small></span>
        <span class="acts"><a class="btn-sm" href="/api/files/${encodeURIComponent(f.name)}" download>${icon("download", "width:16px;height:16px")}DOCX</a></span></div>`);
    }
    box.innerHTML = html.join("");
  };

  // ======================================================== создать документ
  let modulesCache = null;
  const getModules = async () => modulesCache || (modulesCache = await getJSON("/api/modules"));
  loaders.create = async () => {
    const mods = await getModules();
    $("#types").innerHTML = mods.map((m, i) => `<button class="type ${i === 0 ? "hl" : ""}" data-ask="Подготовь ${esc(m.title.toLowerCase())}">
      <span class="tile-sm">${icon(m.doc_type === "приказ" ? "file" : "folder")}</span>
      <span><b>${esc(m.title)}</b><small>${m.fields} вопросов · Матрица, строка ${m.matrix_row}</small></span>
      <span class="go">${icon("chev")}</span></button>`).join("") +
      `<button class="type" id="otherType"><span class="tile-sm">${icon("edit")}</span><span><b>Другой документ</b><small>Опишите задачу — подскажу требования Инструкции</small></span><span class="go">${icon("chev")}</span></button>`;
    $("#otherType").onclick = () => $("#createInput").focus();
    const mine = await getJSON("/api/my/files");
    $("#recentCreated").innerHTML = mine.length ? mine.slice(0, 4).map((f) => `<a class="row" href="/api/files/${encodeURIComponent(f.name)}" download>
      <span class="tile-sm">${icon("file")}</span><span class="txt"><b>${esc(f.title)}</b><small>${esc(f.doc_type)}</small></span><span class="meta">${fmtDate(f.created)}</span></a>`).join("")
      : '<div class="empty">Здесь появятся сформированные проекты.</div>';
  };
  $("#createForm").onsubmit = (e) => {
    e.preventDefault();
    const v = $("#createInput").value.trim(); if (!v) return;
    $("#createInput").value = "";
    askInChat(/^(подготов|созда|составь|сделай|оформи)/i.test(v) ? v : "Подготовь " + v);
  };

  // ======================================================== проверка
  loaders.check = async () => {
    const mods = await getModules();
    $("#checkTemplates").innerHTML = mods.map((m) => `<a class="row" href="/api/modules/${m.id}/file"><span class="tile-sm">${icon(m.doc_type === "приказ" ? "file" : "folder")}</span>
      <span class="txt" title="${esc(m.title)}">${esc(m.title)}</span>${icon("download", "width:18px;height:18px")}</a>`).join("");
  };
  $("#pickFile").onclick = () => $("#file").click();
  const drop = $("#drop");
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) checkFile(f); });
  $("#file").onchange = (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) checkFile(f); };

  let lastReport = null, recFilter = "all";
  async function checkFile(f) {
    if (!location.hash.startsWith("#/check")) location.hash = "#/check";
    const res = $("#checkResult"); res.hidden = false;
    $("#crName").textContent = f.name; $("#crMeta").textContent = "Проверяю…"; $("#crBadge").innerHTML = "";
    $("#recList").innerHTML = '<div class="empty">Проверяю документ…</div>';
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await api("/api/check", { method: "POST", body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Ошибка проверки");
      lastReport = d.report; recFilter = "all";
      const s = d.report.summary;
      $("#crMeta").textContent = `Проверен сегодня, ${fmtTime(Date.now())}${d.report.module ? " · модуль: " + d.report.module : ""}`;
      $("#crBadge").innerHTML = s.errors ? `<span class="badge bad">${icon("x", "width:16px;height:16px")}Есть ошибки</span>` : `<span class="badge">${icon("ok", "width:16px;height:16px")}Проверен</span>`;
      renderRecs();
    } catch (e) {
      if (e.message === "auth") return;
      $("#crMeta").textContent = "Не удалось проверить"; $("#recList").innerHTML = `<div class="empty">⚠️ ${esc(e.message)}</div>`;
    }
    res.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function renderRecs() {
    const r = lastReport; if (!r) return;
    const groups = {};
    for (const i of r.issues) (groups[i.message] = groups[i.message] || { ...i, n: 0, frags: [] }).n++, i.fragment && groups[i.message].frags.push(i.fragment);
    const items = Object.values(groups).map((g) => ({ level: g.level, text: g.message, ref: g.ref, n: g.n, frag: g.frags[0] }))
      .concat(r.passed.map((p) => ({ level: "ok", text: p })))
      .concat(r.recommendations.map((t) => ({ level: "info", text: t })));
    const count = (l) => items.filter((x) => x.level === l).length;
    // Счетчики — по видам нарушений; число мест указано в самой рекомендации.
    $("#cErr").textContent = count("error"); $("#cWarn").textContent = count("warning"); $("#cOk").textContent = count("ok");
    const tabs = [["all", "Все", items.length], ["error", "Ошибки", count("error")], ["warning", "Замечания", count("warning")], ["ok", "Соответствует", count("ok")]];
    $("#recTabs").innerHTML = tabs.map(([k, t, n]) => `<button class="tab ${k === recFilter ? "active" : ""}" data-f="${k}">${t}<i>${n}</i></button>`).join("");
    $$("#recTabs .tab").forEach((b) => b.onclick = () => { recFilter = b.dataset.f; renderRecs(); });
    const ic = { error: ["x", "var(--red)"], warning: ["alert", "var(--amber)"], ok: ["ok", "var(--green)"], info: ["bulb", "var(--violet-ink)"] };
    const shown = items.filter((x) => recFilter === "all" || x.level === recFilter);
    $("#recList").innerHTML = shown.length ? shown.map((x) => `<div class="rec"><svg class="ico lvl" style="color:${ic[x.level][1]}"><use href="#i-${ic[x.level][0]}"/></svg>
      <span>${esc(x.text)}${x.n > 1 ? ` <b>— ${x.n} мест(а)</b>` : ""}${x.ref ? `<small>${esc(x.ref)}</small>` : ""}${x.frag ? `<q>${esc(x.frag)}</q>` : ""}</span></div>`).join("")
      : '<div class="empty">Нет пунктов в этой категории.</div>';
  }
  $$(".counter").forEach((c) => c.onclick = () => { recFilter = c.dataset.f; renderRecs(); $("#recList").scrollIntoView({ behavior: "smooth", block: "nearest" }); });

  // ======================================================== инструкции
  const GUIDES = [
    { t: "Как подготовить проект приказа", s: "Этапы подготовки, визирование и сроки рассмотрения", tag: "orders", ic: "file", c: "violet", q: "Какие этапы подготовки проекта приказа и какие сроки его рассмотрения?" },
    { t: "Создание и согласование распоряжения", s: "Этапы подготовки, обязательные визы", tag: "disp", ic: "folder", c: "plain", q: "Как подготовить и согласовать распоряжение: этапы, визы и сроки?" },
    { t: "Проверка документа на соответствие", s: "Как проверить проект перед отправкой по маршруту", tag: "modules", ic: "shield", c: "plain", go: "check" },
    { t: "Работа с модульными документами", s: "Регистрационная карточка, файл, визирование", tag: "modules", ic: "users", c: "lime", q: "Как подготовить проект на основе модульного документа: карточка, файл, визирование?" },
    { t: "Регистрация входящих документов", s: "Прием, первичная обработка, сроки", tag: "in", ic: "inbox", c: "plain", q: "Как регистрируются входящие документы и в какие сроки?" },
    { t: "Контроль исполнения поручений", s: "Постановка на контроль, сроки, перенос", tag: "control", ic: "clock", c: "plain", q: "Как ставится на контроль поручение и какие сроки исполнения применяются?" },
    { t: "Исходящие письма", s: "Оформление, регистрация и отправка", tag: "letters", ic: "mail", c: "plain", q: "Как подготовить, зарегистрировать и отправить исходящее письмо?" },
    { t: "Формирование дел и архив", s: "Хранение, передача в архив, уничтожение", tag: "archive", ic: "archive", c: "plain", q: "Как формируются дела и передаются на архивное хранение?" },
  ];
  const GUIDE_TAGS = [["all", "Все инструкции", "book"], ["orders", "Приказы", "file"], ["disp", "Распоряжения", "folder"], ["modules", "Модули", "users"],
    ["in", "Входящие", "inbox"], ["control", "Контроль", "clock"], ["letters", "Письма", "mail"], ["archive", "Архив", "archive"]];
  const POPULAR_Q = ["Как подготовить проект приказа?", "Что удалить из файла модуля перед подписанием?", "Кто визирует письмо в Государственную Думу?",
    "Можно ли исправить документ после подписания?", "Когда нужна пояснительная записка?"];
  const FAQ = ["Какие сроки рассмотрения проекта распоряжения?", "Какие документы проходят лингвистическую экспертизу?", "Что делать, если ИИС ДО не работает?"];
  let guideTag = "all";
  loaders.guides = () => {
    $("#guideChips").innerHTML = GUIDE_TAGS.map(([k, t, ic]) => `<button class="chip ${k === guideTag ? "active" : ""}" data-t="${k}">${icon(ic)}${t}</button>`).join("");
    $$("#guideChips .chip").forEach((c) => c.onclick = () => { guideTag = c.dataset.t; loaders.guides(); });
    $("#guides").innerHTML = GUIDES.filter((g) => guideTag === "all" || g.tag === guideTag).map((g) =>
      `<a class="guide ${g.c}" ${g.go ? `href="#/${g.go}"` : `href="#/chat" data-ask="${esc(g.q)}"`}><span class="tile">${icon(g.ic, "width:30px;height:30px")}</span>
       <span><h3>${esc(g.t)}</h3><p>${esc(g.s)}</p></span><span class="go">${icon("chev")}</span></a>`).join("");
    $("#popularQ").innerHTML = POPULAR_Q.map((q) => `<li><button data-ask="${esc(q)}">${esc(q)}</button></li>`).join("");
    $("#faq").innerHTML = FAQ.map((q) => `<details><summary>${esc(q)}${icon("chev-down", "width:18px;height:18px")}</summary>
      <div class="ans">Ответ сформирует помощник по актуальной редакции Инструкции — со ссылками на пункты.<br><button class="btn-sm" style="margin-top:8px" data-ask="${esc(q)}">Получить ответ</button></div></details>`).join("");
  };

  // ======================================================== поиск Ctrl+K
  let qTimer = null, lastResults = [];
  const qInput = $("#q"), qBox = $("#qResults");
  qInput.addEventListener("input", () => {
    clearTimeout(qTimer);
    const v = qInput.value.trim();
    if (v.length < 3) { qBox.classList.remove("open"); return; }
    qTimer = setTimeout(async () => {
      qBox.innerHTML = '<div class="dd-empty">Ищу…</div>'; qBox.classList.add("open");
      try { lastResults = await getJSON("/api/search?q=" + encodeURIComponent(v)); } catch { return; }
      const good = lastResults.filter((r) => r.relevance >= 30).slice(0, 7);
      qBox.innerHTML = good.length ? good.map((r, i) => `<button class="dd-item" data-i="${lastResults.indexOf(r)}"><span class="tile-sm">${icon("file")}</span>
        <span><span class="t">${esc(r.doc_short)}, ${esc(r.ref_full)} · ${r.relevance}%</span><span class="s">${esc(r.text)}</span></span></button>`).join("")
        + `<button class="dd-item" data-ask="${esc(v)}"><span class="tile-sm">${icon("chat")}</span><span><span class="t">Спросить помощника: «${esc(v)}»</span></span></button>`
        : `<div class="dd-empty">Ничего не найдено в базе знаний.</div><button class="dd-item" data-ask="${esc(v)}"><span class="tile-sm">${icon("chat")}</span><span class="t">Спросить помощника</span></button>`;
      $$(".dd-item[data-i]", qBox).forEach((b) => b.onclick = () => showPoint(lastResults[+b.dataset.i]));
    }, 300);
  });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); qInput.focus(); qInput.select(); }
    if (e.key === "Escape") { qBox.classList.remove("open"); $("#bellList").classList.remove("open"); $("#pointModal").classList.remove("open"); }
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#search")) qBox.classList.remove("open");
    if (!e.target.closest(".bell")) $("#bellList").classList.remove("open");
  });
  function showPoint(r) {
    qBox.classList.remove("open");
    $("#pmDoc").textContent = r.doc_title;
    $("#pmRef").textContent = r.ref_full;
    $("#pmMeta").textContent = [r.section, r.page ? "стр. " + r.page : "", "ред. " + r.version, r.approved].filter(Boolean).join(" · ");
    $("#pmText").textContent = r.text;
    $("#pmAsk").onclick = () => { $("#pointModal").classList.remove("open"); askInChat(`Объясни простыми словами ${r.ref_full} документа «${r.doc_short}»`); };
    $("#pointModal").classList.add("open");
  }
  $("#pmClose").onclick = () => $("#pointModal").classList.remove("open");
  $("#pointModal").onclick = (e) => { if (e.target.id === "pointModal") $("#pointModal").classList.remove("open"); };

  // ======================================================== колокольчик
  async function loadBell() {
    let d; try { d = await getJSON("/api/documents"); } catch { return; }
    const seen = store.get("dou_seen_updates", "");
    const newest = d.updates[0] ? d.updates[0].added_at : "";
    $("#bellDot").hidden = !newest || newest <= seen;
    $("#bellList").innerHTML = `<div class="dd-empty" style="font-weight:700;color:var(--ink)">Обновления базы знаний</div>` +
      d.updates.map((u) => `<div class="dd-item"><span class="tile-sm">${icon("db")}</span><span><span class="t">${esc(u.title)} — ред. ${esc(u.version)}</span>
        <span class="s">Действует с ${fmtDate(u.effective_from)} · загружено ${fmtDate(u.added_at)}</span></span></div>`).join("");
    $("#bellBtn").onclick = () => { $("#bellList").classList.toggle("open"); store.set("dou_seen_updates", newest); $("#bellDot").hidden = true; };
  }

  init();
})();
