/* SHM Chat client — vanilla JS, no build step.
 *
 * Owns: WebSocket protocol, conversation list, chat rendering, presence
 * roster (self-healing via periodic census), unread badges, reconnect.
 * All user-derived text goes through textContent — never innerHTML. */
"use strict";

(() => {
  // ── 小工具 ──────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  /** Create an element; props: class, text, title, attrs, children.
      Null children are skipped, so conditional slots are safe. */
  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    if (props.class) node.className = props.class;
    if (props.text !== undefined) node.textContent = props.text;
    if (props.title !== undefined) node.title = props.title;
    for (const [k, v] of Object.entries(props.attrs || {})) node.setAttribute(k, v);
    for (const child of children) {
      if (child) node.appendChild(child);
    }
    return node;
  }

  let uidSeq = 0;
  const uid = () =>
    window.crypto && crypto.randomUUID
      ? crypto.randomUUID()
      : `m${Date.now().toString(36)}-${uidSeq++}`;

  const hashInt = (s) => {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return Math.abs(h);
  };

  const tileColor = (name) => `hsl(${hashInt(name) % 360} 52% 52%)`;
  const initial = (name) => Array.from(name)[0] || "?";

  /** Deterministic WeChat-style rounded-square avatar for a user. */
  function userAvatar(name, small = false) {
    return el("div", { class: `avatar${small ? " small" : ""}` , attrs: { style: `background:${tileColor(name)}` } }, [
      el("span", { text: initial(name) }),
    ]);
  }

  /** 2×2 grid avatar for a group, seeded from its members. */
  function groupAvatar(names, small = false) {
    const grid = el("div", { class: `avatar grid${small ? " small" : ""}` });
    const seeds = names.slice(0, 4);
    while (grid.children.length < 4) {
      const n = seeds.length ? seeds[grid.children.length % seeds.length] : "?";
      grid.appendChild(
        el("span", { text: initial(n), attrs: { style: `background:${tileColor(n)}` } })
      );
    }
    return grid;
  }

  const pad2 = (n) => String(n).padStart(2, "0");

  const fmtClock = (ts) => {
    const d = new Date(ts * 1000);
    return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  };

  const fmtDivider = (ts) => {
    const d = new Date(ts * 1000);
    const now = new Date();
    const day = (x) => `${x.getFullYear()}-${x.getMonth()}-${x.getDate()}`;
    const clock = fmtClock(ts);
    const yest = new Date(now);
    yest.setDate(now.getDate() - 1);
    if (day(d) === day(now)) return clock;
    if (day(d) === day(yest)) return `昨天 ${clock}`;
    if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日 ${clock}`;
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${clock}`;
  };

  // ── 状态 ────────────────────────────────────────────────
  const state = {
    me: null,          // 我的昵称
    mePid: null,       // 处理本连接的 worker pid(演示用)
    online: new Map(), // 昵称 -> 最近一次确认在线的时刻(census 自愈)
    convs: new Map(),  // key -> conv
    active: null,      // 当前会话 key
    ws: null,
    wsReady: false,
    rejoinNeeded: false, // 重连后需重新认领昵称
  };

  const convKey = (kind, name) => `${kind}:${name}`;

  function ensureConv(kind, name) {
    const key = convKey(kind, name);
    let conv = state.convs.get(key);
    if (!conv) {
      conv = {
        kind, name, key,
        msgs: [],
        roster: kind === "g" ? new Map() : null, // 群成员: 昵称 -> lastSeen
        unread: 0,
        newCount: 0,       // 聊天窗内滚动离开底部时的新消息数
        lastTs: 0,
        left: false,       // 已退出群聊(保留会话,不可发言)
      };
      state.convs.set(key, conv);
    }
    return conv;
  }

  const activeConv = () => (state.active ? state.convs.get(state.active) : null);

  // ── WebSocket ───────────────────────────────────────────
  let retryDelay = 1000;

  function wsSend(obj) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify(obj));
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss://" : "ws://";
    const ws = new WebSocket(`${proto}${location.host}/ws/chat/`);
    state.ws = ws;

    ws.onopen = () => {
      state.wsReady = true;
      retryDelay = 1000;
      updateLoginFoot("服务已连接,输入昵称进入");
      $("login-btn").disabled = !$("login-nick").value.trim();
      setNet("on", state.me ? "已连接" : "已连接");
      if (state.rejoinNeeded && state.me) {
        // 重连:先重新认领昵称,成功后由 welcome 处理器重建会话与群
        updateLoginFoot("正在恢复会话…");
        wsSend({ type: "hello", nickname: state.me });
      }
    };

    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      dispatch(msg);
    };

    ws.onclose = () => {
      state.wsReady = false;
      state.rejoinNeeded = !!state.me;
      setNet(state.me ? "off" : "wait", "重连中…");
      if (state.me) toast("连接已断开,正在重连…");
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 8000);
    };

    ws.onerror = () => ws.close();
  }

  // ── 服务器消息分发 ──────────────────────────────────────
  function dispatch(m) {
    switch (m.type) {
      case "welcome": onWelcome(m); break;
      case "nickname_taken": onNickTaken(m); break;
      case "error": onError(m); break;
      case "pm": onPm(m); break;
      case "gm": onGm(m); break;
      case "pm_delivered": {
        const t = pendingAcks.get(m.msg_id);
        if (t) { clearTimeout(t); pendingAcks.delete(m.msg_id); }
        setMsgStatus(m.msg_id, "ok");
        break;
      }
      case "user_online": markOnline(m.nickname); break;
      case "user_offline": markOffline(m.nickname); break;
      case "joined_group": onJoinedGroup(m.group); break;
      case "left_group": onLeftGroup(m.group); break;
      case "group_member_joined": onGroupMember(m.group, m.nickname, "joined"); break;
      case "group_member_left": onGroupMember(m.group, m.nickname, "left"); break;
      case "group_member_online": onGroupMember(m.group, m.nickname, "online"); break;
      default: break;
    }
  }

  function onWelcome(m) {
    state.rejoinNeeded = false;
    state.mePid = m.worker_pid;
    if (!state.me) {
      // 首次进入
      state.me = m.nickname;
      $("me-name").textContent = state.me;
      replaceAvatar($("me-avatar"), userAvatar(state.me));
      $("login").classList.add("hidden");
      $("main").hidden = false;
      updateLoginFoot(`worker pid ${m.worker_pid} · channels-shm`);
    } else {
      // 重连成功:重建在线名单,重新加入所有群
      state.online.clear();
      for (const conv of state.convs.values()) {
        if (conv.kind === "g" && !conv.left) wsSend({ type: "join_group", group: conv.name });
      }
      toast("已重新连接");
    }
    $("pid-tag").textContent = `pid ${m.worker_pid}`;
    setNet("on", "已连接");
    sendCensus();
  }

  function onNickTaken(m) {
    if (!state.me) {
      showLoginError(`昵称「${m.nickname}」已被占用,换一个吧`);
      setLoginBusy(false);
    } else {
      // 极少见:重连时昵称被别人抢走 —— 回到登录页重新开始
      resetToLogin("连接恢复失败:昵称已被占用,请重新进入");
    }
  }

  function onError(m) {
    if (!state.me) {
      showLoginError(m.message);
      setLoginBusy(false);
    } else {
      toast(m.message);
    }
  }

  // ── 在线名单(带 census 自愈) ─────────────────────────
  function markOnline(nick) {
    if (nick === state.me) return;
    state.online.set(nick, Date.now());
    if (state.active && activeConv()?.kind === "u" && activeConv().name === nick) renderChatHead();
    renderPmList();
  }

  function markOffline(nick) {
    state.online.delete(nick);
    if (state.active && activeConv()?.kind === "u" && activeConv().name === nick) renderChatHead();
    renderPmList();
  }

  function sendCensus() {
    if (!state.me) return;
    const stamp = Date.now();
    wsSend({ type: "census" });
    // census 应答 1.5s 内陆续到达;超时未应答的成员视为已掉线,移除。
    setTimeout(() => {
      let changed = false;
      for (const [nick, seen] of state.online) {
        if (seen < stamp) { state.online.delete(nick); changed = true; }
      }
      if (changed) { renderPmList(); if (activeConv()?.kind === "u") renderChatHead(); }
      for (const conv of state.convs.values()) {
        if (conv.kind !== "g" || !conv.roster) continue;
        let gChanged = false;
        for (const [nick, seen] of conv.roster) {
          if (seen < stamp) { conv.roster.delete(nick); gChanged = true; }
        }
        if (gChanged && conv.key === state.active) { renderChatHead(); renderMembers(); }
      }
    }, 1500);
  }

  // ── 消息接收 ────────────────────────────────────────────
  function onPm(m) {
    const conv = ensureConv("u", m.from);
    appendMessage(conv, {
      id: uid(), from: m.from, text: m.text, ts: m.ts,
      dir: "in", status: "ok", pid: m.handled_by_pid,
    });
  }

  function onGm(m) {
    const conv = ensureConv("g", m.group);
    appendMessage(conv, {
      id: uid(), from: m.from, text: m.text, ts: m.ts,
      dir: "in", status: "ok", pid: m.handled_by_pid,
    });
  }

function onJoinedGroup(name) {
  const conv = ensureConv("g", name);
  const firstJoin = !conv.everJoined;
  conv.everJoined = true;
  conv.left = false;
  if (firstJoin) {
    appendMessage(conv, { id: uid(), kind: "sys", text: "你已加入群聊", ts: Date.now() / 1000 });
    openConv(conv.key);
  } else if (conv.key === state.active) {
    // 重连后重新加入:刷新头部与成员面板即可,不追加系统消息
    renderChatHead();
    renderMembers();
    renderComposerState();
  }
}

  function onLeftGroup(name) {
    const conv = state.convs.get(convKey("g", name));
    if (!conv) return;
    conv.left = true;
    conv.roster.clear();
    appendMessage(conv, { id: uid(), kind: "sys", text: "你已退出群聊", ts: Date.now() / 1000 });
    if (conv.key === state.active) { renderChatHead(); renderComposerState(); }
  }

  function onGroupMember(group, nick, action) {
    const conv = state.convs.get(convKey("g", group));
    if (!conv || nick === state.me) return;
    if (action === "left") {
      conv.roster.delete(nick);
      appendMessage(conv, { id: uid(), kind: "sys", text: `${nick} 退出了群聊`, ts: Date.now() / 1000 });
    } else {
      conv.roster.set(nick, Date.now());
      if (action === "joined") {
        appendMessage(conv, { id: uid(), kind: "sys", text: `${nick} 加入了群聊`, ts: Date.now() / 1000 });
      }
    }
    if (conv.key === state.active) { renderChatHead(); renderMembers(); }
  }

  // ── 发送 ────────────────────────────────────────────────
  const pendingAcks = new Map(); // msg_id -> 超时句柄

  function sendCurrent() {
    const conv = activeConv();
    if (!conv || conv.left || !state.wsReady) return;
    const input = $("input");
    const text = input.value.replace(/\s+$/, "");
    if (!text.trim()) return;
    const msg = {
      id: uid(), from: state.me, text, ts: Date.now() / 1000,
      dir: "out", status: conv.kind === "g" ? "ok" : "pending",
    };
    appendMessage(conv, msg);
    input.value = "";
    autoresize();
    if (conv.kind === "u") {
      wsSend({ type: "pm", to: conv.name, text, msg_id: msg.id });
      pendingAcks.set(
        msg.id,
        setTimeout(() => setMsgStatus(msg.id, "fail"), 4000)
      );
    } else {
      wsSend({ type: "gm", group: conv.name, text });
    }
  }

  function setMsgStatus(msgId, status) {
    const host = document.querySelector(`[data-mid="${msgId}"]`);
    if (!host) return;
    const box = host.querySelector(".msg-status");
    if (!box) return;
    box.textContent = "";
    if (status === "pending") box.appendChild(el("span", { class: "spinner" }));
    else if (status === "fail") {
      box.appendChild(el("span", { class: "fail-mark", text: "!", title: "未送达:对方可能不在线" }));
      host.classList.add("fail");
    }
  }

  // ── 消息渲染 ────────────────────────────────────────────
  function appendMessage(conv, msg) {
    conv.msgs.push(msg);
    conv.lastTs = Math.max(conv.lastTs, msg.ts);
    if (conv.key !== state.active) {
      if (msg.kind !== "sys") conv.unread++;
      renderConvList();
      updateTitle();
      return;
    }
    renderOneMessage(conv, msg);
    renderConvList();
  }

  function nearBottom() {
    const box = $("msgs");
    return box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  }

  function renderOneMessage(conv, msg) {
    const box = $("msgs");
    const stick = nearBottom();
    const prev = conv.msgs[conv.msgs.length - 2];
    if (!prev || msg.ts - (prev.ts || 0) > 300) {
      box.appendChild(el("div", { class: "divider", text: fmtDivider(msg.ts) }));
    }
    box.appendChild(buildMsgNode(conv, msg));
    if (stick) box.scrollTop = box.scrollHeight;
    else if (msg.dir === "in" || msg.kind === "sys") {
      conv.newCount++;
      $("btn-jump").hidden = false;
      $("btn-jump").textContent = `↓ ${conv.newCount} 条新消息`;
    }
  }

  function buildMsgNode(conv, msg) {
    if (msg.kind === "sys") return el("div", { class: "sysline", text: msg.text });

    const row = el("div", { class: "msg-row" });
    row.appendChild(el("div", { class: "bubble", text: msg.text, title: msgTitle(msg) }));
    if (msg.dir === "out") {
      const status = el("div", { class: "msg-status" });
      if (msg.status === "pending") status.appendChild(el("span", { class: "spinner" }));
      if (msg.status === "fail") {
        status.appendChild(el("span", { class: "fail-mark", text: "!", title: "未送达:对方可能不在线" }));
      }
      row.appendChild(status);
    }

    const body = el("div", { class: "msg-body" }, [
      el("div", { class: "msg-name", text: msg.from }),
      row,
    ]);
    return el("div", {
      class: `msg ${msg.dir === "out" ? "out" : ""}${msg.status === "fail" ? " fail" : ""}`,
      attrs: { "data-mid": msg.id },
    }, [
      msg.dir === "out" ? userAvatar(state.me) : userAvatar(msg.from),
      body,
    ]);
  }

  const msgTitle = (msg) =>
    msg.pid ? `${fmtDivider(msg.ts)} · 由 worker pid ${msg.pid} 转发` : fmtDivider(msg.ts);

  function renderConvMessages(conv) {
    const box = $("msgs");
    box.textContent = "";
    let prev = null;
    for (const msg of conv.msgs) {
      if (!prev || msg.ts - (prev.ts || 0) > 300) {
        box.appendChild(el("div", { class: "divider", text: fmtDivider(msg.ts) }));
      }
      box.appendChild(buildMsgNode(conv, msg));
      prev = msg;
    }
    box.scrollTop = box.scrollHeight;
  }

  // ── 会话列表 ────────────────────────────────────────────
  function renderConvList() {
    const list = $("conv-list");
    const sel = $("search").value.trim().toLowerCase();
    const convs = [...state.convs.values()]
      .filter((c) => !sel || c.name.toLowerCase().includes(sel))
      .sort((a, b) => b.lastTs - a.lastTs);
    list.textContent = "";
    for (const conv of convs) {
      const last = conv.msgs[conv.msgs.length - 1];
      const preview = conv.left
        ? "[已退出]"
        : last
          ? last.kind === "sys" ? last.text : `${last.dir === "out" ? "我: " : conv.kind === "g" ? `${last.from}: ` : ""}${last.text}`
          : "暂无消息";
      const item = el("div", {
        class: `conv${conv.key === state.active ? " active" : ""}${conv.unread ? " unread" : ""}`,
      }, [
        conv.kind === "u"
          ? userAvatar(conv.name)
          : groupAvatar([state.me, ...conv.roster.keys()]),
        el("div", { class: "conv-main" }, [
          el("div", { class: "conv-row" }, [
            el("div", { class: "conv-name", text: conv.name }),
            el("div", { class: "conv-time", text: conv.lastTs ? fmtClock(conv.lastTs) : "" }),
          ]),
          el("div", { class: "conv-row2" }, [
            el("div", { class: "conv-last", text: preview }),
            conv.unread ? el("span", { class: "conv-badge", text: conv.unread > 99 ? "99+" : String(conv.unread) }) : null,
          ]),
        ]),
      ]);
      item.addEventListener("click", () => openConv(conv.key));
      list.appendChild(item);
    }
  }

  function openConv(key) {
    const conv = state.convs.get(key);
    if (!conv) return;
    state.active = key;
    conv.unread = 0;
    conv.newCount = 0;
    $("btn-jump").hidden = true;
    $("chat-empty").hidden = true;
    $("chat-pane").hidden = false;
    $("members").dataset.open = "0";
    $("members").hidden = true;
    document.querySelector(".main").classList.add("chat-open");
    renderChatHead();
    renderConvMessages(conv);
    renderComposerState();
    renderMembers();
    renderConvList();
    updateTitle();
    $("input").focus();
  }

  function closeConvMobile() {
    document.querySelector(".main").classList.remove("chat-open");
  }

  // ── 聊天头部 / 群成员 ──────────────────────────────────
  function renderChatHead() {
    const conv = activeConv();
    if (!conv) return;
    const membersBtn = $("btn-members");
    if (conv.kind === "u") {
      $("chat-title").textContent = conv.name;
      const on = state.online.has(conv.name);
      $("chat-sub").textContent = "";
      $("chat-sub").appendChild(
        el("span", {}, [
          el("span", { class: `status-dot${on ? " on" : ""}` }),
          el("span", { text: on ? "在线" : "离线" }),
        ])
      );
      membersBtn.hidden = true;
      $("members").hidden = true;
    } else {
      const count = conv.roster.size + 1;
      $("chat-title").textContent = conv.name;
      $("chat-sub").textContent = conv.left ? "已退出" : `群聊 · ${count} 人`;
      membersBtn.hidden = false;
      $("members").hidden = !( $("members").dataset.open === "1" ) || conv.left;
    }
  }

  function replaceAvatar(host, node) {
    host.textContent = "";
    host.appendChild(node);
  }

  function renderMembers() {
    const conv = activeConv();
    const list = $("members-list");
    if (!conv || conv.kind !== "g") { list.textContent = ""; return; }
    const names = [state.me, ...[...conv.roster.keys()].sort((a, b) => a.localeCompare(b, "zh"))];
    $("members-count").textContent = `(${names.length})`;
    list.textContent = "";
    for (const name of names) {
      list.appendChild(
        el("li", { class: name === state.me ? "me-row" : "" }, [
          userAvatar(name, true),
          el("span", { class: "nick", text: name }),
          ...(name === state.me ? [el("span", { class: "me-flag", text: "(我)" })] : []),
        ])
      );
    }
  }

  function renderComposerState() {
    const conv = activeConv();
    const input = $("input");
    const disabled = !conv || conv.left;
    input.disabled = disabled;
    $("btn-send").disabled = disabled;
    input.placeholder = conv && conv.left ? "已退出群聊" : "输入消息…";
  }

  // ── 弹窗:发起私聊 / 加入群聊 ──────────────────────────
  function openModal(tab) {
    $("modal").hidden = false;
    $("modal").classList.remove("hidden");
    switchTab(tab);
    if (tab === "pm") { renderPmList(); $("pm-filter").value = ""; setTimeout(() => $("pm-filter").focus(), 50); }
    else setTimeout(() => $("group-name").focus(), 50);
  }

  function closeModal() {
    $("modal").classList.add("hidden");
    $("modal").hidden = true;
  }

  function switchTab(tab) {
    $("tab-pm").classList.toggle("active", tab === "pm");
    $("tab-group").classList.toggle("active", tab === "group");
    $("pane-pm").hidden = tab !== "pm";
    $("pane-group").hidden = tab !== "group";
  }

  function renderPmList() {
    if ($("modal").hidden || !$("pane-pm") || $("pane-pm").hidden) return;
    const filter = $("pm-filter").value.trim().toLowerCase();
    const users = [...state.online.keys()]
      .filter((n) => n.toLowerCase().includes(filter))
      .sort((a, b) => a.localeCompare(b, "zh"));
    const list = $("pm-list");
    list.textContent = "";
    $("pm-empty").hidden = users.length > 0;
    for (const name of users) {
      const item = el("li", {}, [userAvatar(name, true), el("span", { text: name })]);
      item.addEventListener("click", () => {
        closeModal();
        openConv(ensureConv("u", name).key);
      });
      list.appendChild(item);
    }
  }

  function joinGroupAction() {
    const name = $("group-name").value.trim();
    if (!name) return;
    if (!state.wsReady) { toast("尚未连接服务器"); return; }
    wsSend({ type: "join_group", group: name });
    $("group-name").value = "";
    closeModal();
  }

  /** 直接按昵称发起私聊(对方可以暂时不在线)。 */
  function pmDirectAction() {
    const name = $("pm-direct").value.trim();
    if (!name || name === state.me) { $("pm-direct").focus(); return; }
    $("pm-direct").value = "";
    closeModal();
    openConv(ensureConv("u", name).key);
  }

  // ── 杂项 UI ────────────────────────────────────────────
  let toastTimer = null;
  function toast(text) {
    const node = $("toast");
    node.textContent = text;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.hidden = true; }, 2600);
  }

  function setNet(cls, text) {
    $("net-dot").className = `dot dot-${cls}`;
    $("net-text").textContent = text;
  }

  function updateLoginFoot(text) { $("login-foot").textContent = text; }

  function showLoginError(text) {
    const node = $("login-error");
    node.textContent = text;
    node.hidden = false;
    node.style.animation = "none";
    void node.offsetWidth; // 重新触发 shake 动画
    node.style.animation = "";
    $("login-nick").focus();
  }

  function setLoginBusy(busy) {
    $("login-btn").disabled = busy || !$("login-nick").value.trim();
    $("login-btn").textContent = busy ? "正在进入…" : "进入聊天";
  }

  function resetToLogin(message) {
    state.me = null;
    state.online.clear();
    state.convs.clear();
    state.active = null;
    $("main").hidden = true;
    document.querySelector(".main").classList.remove("chat-open");
    const login = $("login");
    login.classList.remove("hidden");
    setLoginBusy(false);
    if (message) showLoginError(message);
    updateLoginFoot("服务已连接,输入昵称进入");
    updateTitle();
  }

  function updateTitle() {
    let unread = 0;
    for (const conv of state.convs.values()) unread += conv.unread;
    document.title = unread ? `(${unread}) SHM Chat` : "SHM Chat";
  }

  function autoresize() {
    const input = $("input");
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
  }

  // ── 事件绑定 ────────────────────────────────────────────
  function bind() {
    $("login-form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const nick = $("login-nick").value.trim();
      if (!nick || !state.wsReady) return;
      $("login-error").hidden = true;
      setLoginBusy(true);
      wsSend({ type: "hello", nickname: nick });
    });

    $("login-nick").addEventListener("input", () => {
      $("login-btn").disabled = !$("login-nick").value.trim();
    });

    $("btn-new").addEventListener("click", () => openModal("pm"));
    $("modal-close").addEventListener("click", closeModal);
    $("modal-mask").addEventListener("click", closeModal);
    $("tab-pm").addEventListener("click", () => { switchTab("pm"); renderPmList(); $("pm-filter").focus(); });
    $("tab-group").addEventListener("click", () => switchTab("group"));
    $("pm-filter").addEventListener("input", renderPmList);
    $("btn-pm-direct").addEventListener("click", pmDirectAction);
    $("pm-direct").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); pmDirectAction(); }
    });
    $("btn-join").addEventListener("click", joinGroupAction);
    $("group-name").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); joinGroupAction(); }
    });

    $("btn-send").addEventListener("click", sendCurrent);
    const input = $("input");
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
        ev.preventDefault();
        sendCurrent();
      }
    });
    input.addEventListener("input", autoresize);

    $("btn-back").addEventListener("click", () => { closeConvMobile(); });
    $("btn-members").addEventListener("click", () => {
      const panel = $("members");
      const willOpen = panel.hidden && activeConv()?.kind === "g";
      panel.dataset.open = willOpen ? "1" : "0";
      panel.hidden = !willOpen;
    });
    $("btn-jump").addEventListener("click", () => {
      const conv = activeConv();
      if (conv) conv.newCount = 0;
      $("btn-jump").hidden = true;
      const box = $("msgs");
      box.scrollTop = box.scrollHeight;
    });
    $("msgs").addEventListener("scroll", () => {
      if (nearBottom()) {
        const conv = activeConv();
        if (conv) conv.newCount = 0;
        $("btn-jump").hidden = true;
      }
    });

    $("search").addEventListener("input", renderConvList);

    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && !$("modal").hidden) closeModal();
    });
  }

  // ── 启动 ────────────────────────────────────────────────
  bind();
  updateLoginFoot("正在连接服务…");
  connect();
  setInterval(() => {
    if (state.wsReady && state.me) sendCensus();
  }, 45000 + Math.floor(Math.random() * 8000));
})();
