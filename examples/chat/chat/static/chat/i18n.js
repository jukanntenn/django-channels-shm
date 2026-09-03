/* SHM Chat i18n + theme runtime — vanilla JS, no build step.
 *
 * Owns: the zh/en dictionary, data-i18n* attribute application, the theme
 * (auto/light/dark) and language (中文/English) toolbar buttons and their
 * localStorage persistence. Theme is pure CSS: the button only flips
 * [data-theme], which selects color-scheme; every color is light-dark().
 * app.js consumes window.SHMI18N (t/has/lang) and listens for the
 * "shm:langchange" event to re-render its dynamic strings. */
"use strict";

(() => {
  const LANG_KEY = "shmchat.lang";
  const THEME_KEY = "shmchat.theme";
  const THEMES = ["auto", "light", "dark"];

  const DICT = {
    zh: {
      // ── 登录页 ──
      tagline: "共享内存通道层上的即时聊天 · 无需注册",
      nick_placeholder: "输入昵称",
      enter_chat: "进入聊天",
      entering: "正在进入…",
      connecting: "正在连接服务…",
      foot_ready: "服务已连接,输入昵称进入",
      foot_recovering: "正在恢复会话…",
      foot_worker: "worker pid {pid} · channels-shm",
      // ── 侧栏 / 主界面 ──
      btn_new: "发起聊天",
      search_placeholder: "搜索",
      conv_list_aria: "会话列表",
      net_connected: "已连接",
      net_reconnecting: "重连中…",
      chat_empty: "选择左侧会话,或点击 <b>+</b> 发起私聊 / 加入群聊",
      btn_back: "返回",
      members_title: "群成员",
      jump_new: "↓ 有新消息",
      jump_new_count: "↓ {n} 条新消息",
      input_placeholder: "输入消息…",
      composer_left: "已退出群聊",
      composer_hint: "Enter 发送 · Shift+Enter 换行",
      send: "发送",
      online: "在线",
      offline: "离线",
      left_status: "已退出",
      group_sub: "群聊 · {count} 人",
      me_flag: "(我)",
      // ── 弹窗 ──
      tab_pm: "发起私聊",
      tab_group: "加入群聊",
      close: "关闭",
      pm_filter_placeholder: "搜索在线用户",
      pm_direct_placeholder: "或输入昵称直接发起",
      pm_empty: "当前没有其他在线用户",
      go: "发起",
      group_name_placeholder: "群名称(不存在则自动创建)",
      join: "加入",
      modal_note: "第一个加入的人创建该群;群聊最多 500 人。",
      // ── 会话列表预览 ──
      preview_left: "[已退出]",
      preview_empty: "暂无消息",
      preview_me: "我: ",
      // ── 动态提示 ──
      toast_reconnecting: "连接已断开,正在重连…",
      toast_rejoined: "已重新连接",
      toast_not_connected: "尚未连接服务器",
      undelivered: "未送达:对方可能不在线",
      relayed_by: "由 worker pid {pid} 转发",
      nickname_taken: "昵称「{nick}」已被占用,换一个吧",
      reset_nick_taken: "连接恢复失败:昵称已被占用,请重新进入",
      // ── 系统消息 ──
      sys_you_joined: "你已加入群聊",
      sys_you_left: "你已退出群聊",
      sys_member_joined: "{nick} 加入了群聊",
      sys_member_left: "{nick} 退出了群聊",
      // ── 时间 ──
      yesterday: "昨天",
      // ── 工具栏 ──
      "theme.auto": "主题:跟随系统",
      "theme.light": "主题:浅色",
      "theme.dark": "主题:深色",
      "lang.to_en": "切换到 English",
      "lang.to_zh": "切换到中文",
      // ── 服务器错误(code → 文案;中文原文是未知 code 的兜底) ──
      "err.name.empty": "{what}不能为空",
      "err.name.too_long": "{what}不能超过 {max} 个字符",
      "err.name.control_chars": "{what}不能包含控制字符",
      "err.text.empty": "消息不能为空",
      "err.text.too_long": "消息不能超过 {max} 个字符",
      "err.bad_type": "未知消息类型: {kind}",
      "err.already_identified": "本连接已使用昵称,请刷新页面",
      "err.not_identified": "请先设置昵称",
      "err.not_in_group": "尚未加入群聊 {group},请先加入",
      "err.group_full": "群聊人数已满({max})",
      "err.join_failed": "暂时无法加入,请稍后再试",
      "what.nick": "昵称",
      "what.peer": "对方昵称",
      "what.group": "群名称",
    },
    en: {
      // ── Login ──
      tagline: "Instant chat over a shared-memory channel layer · no sign-up",
      nick_placeholder: "Pick a nickname",
      enter_chat: "Join chat",
      entering: "Joining…",
      connecting: "Connecting…",
      foot_ready: "Connected — pick a nickname to join",
      foot_recovering: "Restoring session…",
      foot_worker: "worker pid {pid} · channels-shm",
      // ── Sidebar / main ──
      btn_new: "New chat",
      search_placeholder: "Search",
      conv_list_aria: "Conversation list",
      net_connected: "Connected",
      net_reconnecting: "Reconnecting…",
      chat_empty: "Pick a conversation on the left, or tap <b>+</b> to start a private chat or join a group",
      btn_back: "Back",
      members_title: "Group members",
      jump_new: "↓ new messages",
      jump_new_count: "↓ {n} new message{n|s}",
      input_placeholder: "Type a message…",
      composer_left: "You left this group",
      composer_hint: "Enter to send · Shift+Enter for a new line",
      send: "Send",
      online: "online",
      offline: "offline",
      left_status: "left",
      group_sub: "Group · {count} member{count|s}",
      me_flag: "(me)",
      // ── Modal ──
      tab_pm: "New private chat",
      tab_group: "Join group",
      close: "Close",
      pm_filter_placeholder: "Search online users",
      pm_direct_placeholder: "Or type a nickname to chat directly",
      pm_empty: "No other users online right now",
      go: "Start",
      group_name_placeholder: "Group name (created if new)",
      join: "Join",
      modal_note: "The first member creates the group; groups hold up to 500 people.",
      // ── Conversation preview ──
      preview_left: "[left]",
      preview_empty: "No messages yet",
      preview_me: "You: ",
      // ── Dynamic notices ──
      toast_reconnecting: "Connection lost — reconnecting…",
      toast_rejoined: "Reconnected",
      toast_not_connected: "Not connected to the server yet",
      undelivered: "Not delivered: the recipient may be offline",
      relayed_by: "relayed by worker pid {pid}",
      nickname_taken: "The nickname “{nick}” is taken — try another",
      reset_nick_taken: "Reconnect failed: your nickname was taken — please rejoin",
      // ── System messages ──
      sys_you_joined: "You joined the group",
      sys_you_left: "You left the group",
      sys_member_joined: "{nick} joined the group",
      sys_member_left: "{nick} left the group",
      // ── Time ──
      yesterday: "Yesterday",
      // ── Toolbar ──
      "theme.auto": "Theme: follow system",
      "theme.light": "Theme: light",
      "theme.dark": "Theme: dark",
      "lang.to_en": "Switch to English",
      "lang.to_zh": "Switch to 中文",
      // ── Server errors (code → copy; the Chinese original is the
      //    fallback for unknown codes) ──
      "err.name.empty": "{what} cannot be empty",
      "err.name.too_long": "{what} must be at most {max} characters",
      "err.name.control_chars": "{what} must not contain control characters",
      "err.text.empty": "Message must not be empty",
      "err.text.too_long": "Message must be at most {max} characters",
      "err.bad_type": "Unknown message type: {kind}",
      "err.already_identified": "This connection already has a nickname — refresh the page",
      "err.not_identified": "Pick a nickname first",
      "err.not_in_group": "You have not joined {group} yet — join first",
      "err.group_full": "The group is full ({max} members)",
      "err.join_failed": "Could not join right now — try again shortly",
      "what.nick": "Nickname",
      "what.peer": "Recipient name",
      "what.group": "Group name",
    },
  };

  const root = document.documentElement;

  // Language: persisted choice → browser preference → Chinese.
  let lang = "zh";
  const browserLang = () =>
    (navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";
  try {
    const saved = localStorage.getItem(LANG_KEY);
    lang = saved === "zh" || saved === "en" ? saved : browserLang();
  } catch {
    lang = browserLang(); // storage unavailable (private mode etc.)
  }

  // Theme is applied pre-paint by the inline <head> script; read it back.
  let theme = THEMES.includes(root.dataset.theme) ? root.dataset.theme : "auto";

  const THEME_ICONS = {
    auto: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 2v16a8 8 0 0 1 0-16z" fill="currentColor"/></svg>',
    light: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><circle cx="12" cy="12" r="4.4" fill="currentColor"/><path d="M12 2.2v2.6M12 19.2v2.6M2.2 12h2.6M19.2 12h2.6M4.8 4.8l1.9 1.9M17.3 17.3l1.9 1.9M19.2 4.8l-1.9 1.9M6.7 17.3l-1.9 1.9" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" fill="none"/></svg>',
    dark: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M20.6 14.6A9 9 0 0 1 9.4 3.4a9 9 0 1 0 11.2 11.2z" fill="currentColor"/></svg>',
  };

  // "{key}" → vars[key]. "{key|s}" → "" when vars[key] is "1", else the
  // suffix — the value itself is printed by its own "{key}" token nearby
  // (English plural marker; Chinese copy simply never uses it).
  const fill = (s, vars) =>
    vars
      ? s
          .replace(/\{(\w+)\|(\w*)\}/g, (m, k, suffix) =>
            !(k in vars) ? m : String(vars[k]) === "1" ? "" : suffix
          )
          .replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m))
      : s;

  const t = (key, vars) => fill(DICT[lang][key] ?? DICT.zh[key] ?? key, vars);
  const has = (key) => key in DICT[lang] || key in DICT.zh;

  function applyStatic() {
    root.lang = lang === "zh" ? "zh-CN" : "en";
    // data-i18n-html values come from this dictionary only, never user input.
    for (const node of document.querySelectorAll("[data-i18n-html]")) {
      node.innerHTML = t(node.dataset.i18nHtml);
    }
    for (const node of document.querySelectorAll("[data-i18n]")) {
      node.textContent = t(node.dataset.i18n);
    }
    for (const node of document.querySelectorAll("[data-i18n-ph]")) {
      node.placeholder = t(node.dataset.i18nPh);
    }
    for (const node of document.querySelectorAll("[data-i18n-title]")) {
      node.title = t(node.dataset.i18nTitle);
    }
    for (const node of document.querySelectorAll("[data-i18n-aria]")) {
      node.setAttribute("aria-label", t(node.dataset.i18nAria));
    }
  }

  function renderThemeButtons() {
    for (const btn of document.querySelectorAll(".btn-theme")) {
      btn.innerHTML = THEME_ICONS[theme];
      const label = t(`theme.${theme}`);
      btn.title = label;
      btn.setAttribute("aria-label", label);
    }
  }

  function renderLangButtons() {
    for (const btn of document.querySelectorAll(".btn-lang")) {
      btn.textContent = lang === "zh" ? "EN" : "中";
      const label = t(lang === "zh" ? "lang.to_en" : "lang.to_zh");
      btn.title = label;
      btn.setAttribute("aria-label", label);
    }
  }

  function setTheme(next) {
    theme = next;
    root.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* storage unavailable — the choice just won't persist */
    }
    renderThemeButtons();
  }

  function setLang(next) {
    lang = next;
    root.dataset.lang = lang;
    try {
      localStorage.setItem(LANG_KEY, lang);
    } catch {
      /* storage unavailable — the choice just won't persist */
    }
    applyStatic();
    renderLangButtons();
    renderThemeButtons(); // button titles are translated
    document.dispatchEvent(new CustomEvent("shm:langchange"));
  }

  for (const btn of document.querySelectorAll(".btn-theme")) {
    btn.addEventListener("click", () =>
      setTheme(THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length])
    );
  }
  for (const btn of document.querySelectorAll(".btn-lang")) {
    btn.addEventListener("click", () => setLang(lang === "zh" ? "en" : "zh"));
  }

  root.dataset.lang = lang;
  applyStatic();
  renderThemeButtons();
  renderLangButtons();

  window.SHMI18N = {
    t,
    has,
    get lang() {
      return lang;
    },
  };
})();
