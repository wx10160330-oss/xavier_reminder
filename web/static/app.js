// Xavier Reminder — 前端逻辑（原生 JS，无依赖）
(() => {
  const BASE = window.__BASE__ || "";
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const state = {
    curYear: 0,
    curMonth: 0, // 0-based
    items: [],
    umoFilter: "",
    editingId: null,
    umos: [],
    skipDates: [],
  };

  // ---------- 主题 ----------
  const THEME_KEY = "xavier_reminder_theme";
  function initTheme() {
    const saved = localStorage.getItem(THEME_KEY) || "lilac";
    setTheme(saved);
    document.querySelectorAll(".theme-swatch").forEach((el) => {
      el.addEventListener("click", () => setTheme(el.dataset.theme));
    });
  }
  function setTheme(name) {
    document.documentElement.setAttribute("data-theme", name);
    localStorage.setItem(THEME_KEY, name);
    document.querySelectorAll(".theme-swatch").forEach((el) => {
      el.classList.toggle("active", el.dataset.theme === name);
    });
    // 同步 theme-color
    const map = {
      lilac: "#a889e6", pink: "#ff6ea6", mint: "#6dcfa0",
      sky: "#6da9ff", peach: "#ff9a6d", dark: "#2a2740",
    };
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", map[name] || "#a889e6");
  }

  // ---------- API ----------
  async function api(path, opts = {}) {
    const url = BASE + "/api" + path;
    const res = await fetch(url, {
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    const data = await res.json().catch(() => ({ ok: false, msg: "无法解析响应" }));
    if (!res.ok || !data.ok) {
      throw new Error(data.msg || `HTTP ${res.status}`);
    }
    return data;
  }

  async function reload() {
    try {
      const [umosData, listData] = await Promise.all([
        api("/umos"),
        api("/list" + (state.umoFilter ? "?umo=" + encodeURIComponent(state.umoFilter) : "")),
      ]);
      state.umos = umosData.umos || [];
      state.items = listData.items || [];
      renderUmoFilter();
      renderCalendar();
      renderSideList();
    } catch (e) {
      toast("加载失败: " + e.message, true);
    }
  }

  // ---------- Toast ----------
  let toastTimer = null;
  function toast(msg, error = false) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.toggle("error", error);
    el.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), 2400);
  }

  // ---------- 日期辅助 ----------
  function pad2(n) { return String(n).padStart(2, "0"); }
  function ymd(dt) {
    return `${dt.getFullYear()}-${pad2(dt.getMonth() + 1)}-${pad2(dt.getDate())}`;
  }
  function isToday(dt) {
    const t = new Date();
    return dt.getFullYear() === t.getFullYear()
      && dt.getMonth() === t.getMonth()
      && dt.getDate() === t.getDate();
  }

  // 给定日期，判断某个 reminder 是否会在此日出现
  function reminderHitsDate(r, dt) {
    if (r.type === "daily") {
      // 每日提醒：从 created_at 起每天都算
      const created = new Date(r.created_at * 1000);
      created.setHours(0, 0, 0, 0);
      const test = new Date(dt);
      test.setHours(0, 0, 0, 0);
      return test >= created;
    } else {
      // once：日期匹配
      const fire = new Date(r.next_fire_ts * 1000);
      return ymd(fire) === ymd(dt);
    }
  }

  function reminderTimeOfDay(r) {
    if (r.type === "daily") {
      return `${pad2(r.hour ?? 0)}:${pad2(r.minute ?? 0)}`;
    }
    const dt = new Date(r.next_fire_ts * 1000);
    return `${pad2(dt.getHours())}:${pad2(dt.getMinutes())}`;
  }

  // ---------- UMO 筛选 ----------
  function renderUmoFilter() {
    const sel = $("#umo-filter");
    const cur = sel.value;
    sel.innerHTML = '<option value="">全部会话</option>';
    for (const u of state.umos) {
      const opt = document.createElement("option");
      opt.value = u;
      opt.textContent = shortUmo(u);
      opt.title = u;
      sel.appendChild(opt);
    }
    sel.value = cur || state.umoFilter || "";

    // 表单里的 umo 选项也同步
    const fUmo = $("#f-umo");
    const fCur = fUmo.value;
    fUmo.innerHTML = "";
    for (const u of state.umos) {
      const opt = document.createElement("option");
      opt.value = u;
      opt.textContent = shortUmo(u);
      opt.title = u;
      fUmo.appendChild(opt);
    }
    if (fCur) fUmo.value = fCur;
  }

  function shortUmo(u) {
    if (!u) return "(空)";
    return u.length > 40 ? "…" + u.slice(-38) : u;
  }

  // ---------- 日历 ----------
  function renderCalendar() {
    const y = state.curYear, m = state.curMonth;
    $("#cur-month").textContent = `${y} 年 ${m + 1} 月`;

    const cal = $("#calendar");
    cal.innerHTML = "";

    const head = document.createElement("div");
    head.className = "cal-head";
    for (const w of ["日", "一", "二", "三", "四", "五", "六"]) {
      const d = document.createElement("div");
      d.textContent = w;
      head.appendChild(d);
    }
    cal.appendChild(head);

    const body = document.createElement("div");
    body.className = "cal-body";

    const first = new Date(y, m, 1);
    const startWeekday = first.getDay();
    const daysInMonth = new Date(y, m + 1, 0).getDate();
    const cells = [];

    // 前置填充
    for (let i = 0; i < startWeekday; i++) {
      const dt = new Date(y, m, 1 - (startWeekday - i));
      cells.push({ dt, other: true });
    }
    for (let d = 1; d <= daysInMonth; d++) {
      cells.push({ dt: new Date(y, m, d), other: false });
    }
    // 补足 6 行 (42 格)
    while (cells.length < 42) {
      const last = cells[cells.length - 1].dt;
      const next = new Date(last);
      next.setDate(next.getDate() + 1);
      cells.push({ dt: next, other: true });
    }

    for (const { dt, other } of cells) {
      const cell = document.createElement("div");
      cell.className = "cal-cell";
      if (other) cell.classList.add("other-month");
      if (isToday(dt)) cell.classList.add("today");

      const num = document.createElement("div");
      num.className = "date-num";
      num.textContent = dt.getDate();
      cell.appendChild(num);

      const events = document.createElement("div");
      events.className = "events";
      const dstr = ymd(dt);

      const filtered = state.umoFilter
        ? state.items.filter((r) => r.umo === state.umoFilter)
        : state.items;

      // 收集当日事件，按时间排序
      const hits = filtered
        .filter((r) => reminderHitsDate(r, dt))
        .map((r) => ({ r, time: reminderTimeOfDay(r) }))
        .sort((a, b) => a.time.localeCompare(b.time));

      for (const { r, time } of hits) {
        const ev = document.createElement("div");
        ev.className = "evt " + r.type;
        const isSkip = r.skip_dates && r.skip_dates.includes(dstr);
        const isDone = r.completed && r.type === "once";
        if (isSkip) ev.classList.add("skipped");
        if (isDone) ev.classList.add("completed");
        ev.textContent = `${time} ${r.content}`;
        const tips = [];
        if (isSkip) tips.push("此日跳过");
        if (isDone) tips.push("已完成");
        ev.title = r.content + (tips.length ? `（${tips.join("・")}）` : "");
        ev.addEventListener("click", (ev2) => {
          ev2.stopPropagation();
          openEdit(r.id);
        });
        events.appendChild(ev);
      }
      cell.appendChild(events);

      // 手机端：格子高度受限，事件过多时用 "+N" 角标提示
      // 阈值 = 手机端一格能放下的事件数（约 2 条）
      const isMobile = window.matchMedia("(max-width: 600px)").matches;
      const maxVisible = isMobile ? 2 : 999;
      if (hits.length > maxVisible) {
        const evtNodes = events.querySelectorAll(".evt");
        for (let k = maxVisible; k < evtNodes.length; k++) {
          evtNodes[k].style.display = "none";
        }
        const more = document.createElement("div");
        more.className = "more-tag";
        more.textContent = `+${hits.length - maxVisible}`;
        cell.appendChild(more);
      }

      cell.addEventListener("click", () => {
        openNew(dt);
      });

      body.appendChild(cell);
    }
    cal.appendChild(body);
  }

  // ---------- 侧栏列表 ----------
  function renderSideList() {
    const list = $("#list");
    list.innerHTML = "";
    const filtered = state.umoFilter
      ? state.items.filter((r) => r.umo === state.umoFilter)
      : state.items;

    // 有已完成的才显示清理按钮
    const hasDone = filtered.some((r) => r.completed && r.type === "once");
    $("#btn-clear-done").classList.toggle("hidden", !hasDone);

    if (filtered.length === 0) {
      const em = document.createElement("div");
      em.className = "list-empty";
      em.innerHTML = '<span class="icon">🐰</span>还没有提醒<br>点日历新建一个吧';
      list.appendChild(em);
      return;
    }

    // 排序：未完成的按最近触发时间升序；已完成的沉底并按完成时间倒序
    const active = filtered.filter((r) => !r.completed);
    const done = filtered.filter((r) => r.completed);
    active.sort((a, b) => a.next_fire_ts - b.next_fire_ts);
    done.sort((a, b) => (b.completed_at ?? 0) - (a.completed_at ?? 0));
    const sorted = [...active, ...done];

    for (const r of sorted) {
      const item = document.createElement("div");
      item.className = "list-item";
      if (r.completed) item.classList.add("done");
      const typeLabel = r.type === "daily" ? "每日" : "单次";
      const timeStr = r.type === "daily"
        ? `每天 ${pad2(r.hour ?? 0)}:${pad2(r.minute ?? 0)}`
        : r.next_fire_str;
      const skipInfo = (r.skip_dates && r.skip_dates.length > 0)
        ? `已跳过 ${r.skip_dates.length} 天`
        : "";

      item.innerHTML = `
        <div>
          <span class="li-type ${r.type}">${typeLabel}</span>
          ${r.completed ? '<span class="li-done">已完成</span>' : ''}
          <span class="li-content"></span>
        </div>
        <div class="li-meta"></div>
        ${skipInfo ? `<div class="li-skip">${skipInfo}</div>` : ""}
      `;
      item.querySelector(".li-content").textContent = r.content;
      item.querySelector(".li-meta").textContent = `${timeStr} · ${shortUmo(r.umo)}`;
      item.addEventListener("click", () => openEdit(r.id));
      list.appendChild(item);
    }
  }

  // ---------- Modal ----------
  function openModal() {
    $("#modal").classList.remove("hidden");
  }
  function closeModal() {
    $("#modal").classList.add("hidden");
    state.editingId = null;
    state.skipDates = [];
  }

  function openNew(dt) {
    state.editingId = null;
    state.skipDates = [];
    $("#modal-title").textContent = "🐰 新建提醒";
    $("#f-type").value = "once";
    $("#f-content").value = "";
    // 默认时间设为当天 20:00 或明天 09:00
    const hh = 20, mm = 0;
    const default_dt = new Date(dt);
    default_dt.setHours(hh, mm, 0, 0);
    if (default_dt.getTime() < Date.now()) {
      default_dt.setDate(default_dt.getDate() + 1);
      default_dt.setHours(9, 0, 0, 0);
    }
    $("#f-once-time").value = toDatetimeLocal(default_dt);
    $("#f-daily-time").value = "08:00";
    $("#btn-delete").classList.add("hidden");
    $("#skip-editor").classList.add("hidden");
    // 单会话时自动选中，隐藏选择器
    const fUmo = $("#f-umo");
    if (state.umos.length === 1) {
      fUmo.value = state.umos[0];
      $("#f-umo-wrap").classList.add("hidden");
    } else if (state.umos.length === 0) {
      // 没有任何会话记录 → 提示
      $("#f-umo-wrap").classList.remove("hidden");
    } else {
      $("#f-umo-wrap").classList.remove("hidden");
      // 默认选中当前筛选
      if (state.umoFilter) fUmo.value = state.umoFilter;
    }
    toggleTimeFields();
    openModal();
  }

  function openEdit(id) {
    const r = state.items.find((x) => x.id === id);
    if (!r) return;
    state.editingId = id;
    state.skipDates = [...(r.skip_dates || [])];
    $("#modal-title").textContent = "🐰 编辑提醒";
    $("#f-type").value = r.type;
    $("#f-content").value = r.content;
    if (r.type === "daily") {
      $("#f-daily-time").value = `${pad2(r.hour ?? 0)}:${pad2(r.minute ?? 0)}`;
    } else {
      const dt = new Date(r.next_fire_ts * 1000);
      $("#f-once-time").value = toDatetimeLocal(dt);
    }
    const fUmo = $("#f-umo");
    if (![...fUmo.options].some((o) => o.value === r.umo)) {
      const opt = document.createElement("option");
      opt.value = r.umo;
      opt.textContent = shortUmo(r.umo);
      fUmo.appendChild(opt);
    }
    fUmo.value = r.umo;
    // 编辑时始终显示会话字段（可能用户想改）
    $("#f-umo-wrap").classList.remove("hidden");
    $("#btn-delete").classList.remove("hidden");
    $("#skip-editor").classList.remove("hidden");
    renderSkipList();
    toggleTimeFields();
    openModal();
  }

  function toDatetimeLocal(dt) {
    return `${dt.getFullYear()}-${pad2(dt.getMonth() + 1)}-${pad2(dt.getDate())}T${pad2(dt.getHours())}:${pad2(dt.getMinutes())}`;
  }

  function toggleTimeFields() {
    const t = $("#f-type").value;
    const isDaily = t === "daily";
    $("#f-time-label").textContent = isDaily ? "时间（每天固定时间）" : "时间";
    $("#f-daily-time").classList.toggle("hidden", !isDaily);
    $("#f-once-time").classList.toggle("hidden", isDaily);
  }

  function renderSkipList() {
    const box = $("#skip-list");
    box.innerHTML = "";
    if (state.skipDates.length === 0) {
      const em = document.createElement("span");
      em.className = "skip-empty";
      em.textContent = "（无）";
      box.appendChild(em);
      return;
    }
    for (const d of [...state.skipDates].sort()) {
      const pill = document.createElement("span");
      pill.className = "skip-pill";
      pill.textContent = d;
      const x = document.createElement("span");
      x.className = "x";
      x.textContent = "×";
      x.addEventListener("click", async () => {
        if (!state.editingId) return;
        try {
          await api(`/unskip/${state.editingId}`, {
            method: "POST",
            body: JSON.stringify({ date: d }),
          });
          state.skipDates = state.skipDates.filter((x) => x !== d);
          renderSkipList();
          await reload();
          toast("已取消跳过");
        } catch (e) {
          toast("取消失败: " + e.message, true);
        }
      });
      pill.appendChild(x);
      box.appendChild(pill);
    }
  }

  // ---------- 保存 ----------
  async function save() {
    const type = $("#f-type").value;
    const content = $("#f-content").value.trim();
    const umo = $("#f-umo").value;
    if (!content) { toast("请填写内容", true); return; }
    if (!umo) { toast("请选择会话", true); return; }
    let fire_time = "";
    if (type === "daily") {
      fire_time = $("#f-daily-time").value;
    } else {
      const v = $("#f-once-time").value;
      if (!v) { toast("请选择时间", true); return; }
      const dt = new Date(v);
      fire_time = `${dt.getFullYear()}-${pad2(dt.getMonth()+1)}-${pad2(dt.getDate())} ${pad2(dt.getHours())}:${pad2(dt.getMinutes())}`;
    }

    try {
      if (state.editingId) {
        await api(`/update/${state.editingId}`, {
          method: "POST",
          body: JSON.stringify({ type, content, fire_time }),
        });
        toast("已保存");
      } else {
        await api("/add", {
          method: "POST",
          body: JSON.stringify({ umo, type, content, fire_time }),
        });
        toast("已新建");
      }
      closeModal();
      await reload();
    } catch (e) {
      toast("保存失败: " + e.message, true);
    }
  }

  async function del() {
    if (!state.editingId) return;
    if (!confirm("确定删除这条提醒吗？")) return;
    try {
      await api(`/delete/${state.editingId}`, { method: "DELETE" });
      toast("已删除");
      closeModal();
      await reload();
    } catch (e) {
      toast("删除失败: " + e.message, true);
    }
  }

  async function clearCompleted() {
    const doneCount = state.items.filter(
      (r) => r.completed && r.type === "once" &&
             (!state.umoFilter || r.umo === state.umoFilter)
    ).length;
    if (doneCount === 0) { toast("没有已完成的提醒"); return; }
    if (!confirm(`确定清理 ${doneCount} 条已完成的提醒吗？`)) return;
    try {
      const body = state.umoFilter ? { umo: state.umoFilter } : {};
      const res = await api("/clear_completed", {
        method: "POST",
        body: JSON.stringify(body),
      });
      toast(`已清理 ${res.removed || 0} 条`);
      await reload();
    } catch (e) {
      toast("清理失败: " + e.message, true);
    }
  }

  async function addSkip() {
    if (!state.editingId) return;
    const date = $("#skip-date").value;
    if (!date) { toast("请选择日期", true); return; }
    try {
      await api(`/skip/${state.editingId}`, {
        method: "POST",
        body: JSON.stringify({ date }),
      });
      if (!state.skipDates.includes(date)) state.skipDates.push(date);
      renderSkipList();
      await reload();
      toast("已添加跳过");
    } catch (e) {
      toast("失败: " + e.message, true);
    }
  }

  // ---------- 事件绑定 ----------
  function init() {
    initTheme();
    const now = new Date();
    state.curYear = now.getFullYear();
    state.curMonth = now.getMonth();

    $("#btn-prev").addEventListener("click", () => {
      state.curMonth--;
      if (state.curMonth < 0) { state.curMonth = 11; state.curYear--; }
      renderCalendar();
    });
    $("#btn-next").addEventListener("click", () => {
      state.curMonth++;
      if (state.curMonth > 11) { state.curMonth = 0; state.curYear++; }
      renderCalendar();
    });
    $("#btn-today").addEventListener("click", () => {
      const t = new Date();
      state.curYear = t.getFullYear();
      state.curMonth = t.getMonth();
      renderCalendar();
    });
    $("#umo-filter").addEventListener("change", (e) => {
      state.umoFilter = e.target.value;
      renderCalendar();
      renderSideList();
    });
    $("#btn-new").addEventListener("click", () => openNew(new Date()));

    $("#f-type").addEventListener("change", toggleTimeFields);
    $("#btn-cancel").addEventListener("click", closeModal);
    $("#btn-save").addEventListener("click", save);
    $("#btn-delete").addEventListener("click", del);
    $("#btn-add-skip").addEventListener("click", addSkip);
    $("#btn-clear-done").addEventListener("click", clearCompleted);

    // 点遮罩关闭
    $("#modal").addEventListener("click", (e) => {
      if (e.target.id === "modal") closeModal();
    });

    reload();
    // 每 30 秒自动刷新
    setInterval(reload, 30000);

    // 窗口尺寸变化时重绘日历（防止手机/桌面模式切换后事件数量显示不对）
    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => renderCalendar(), 200);
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
