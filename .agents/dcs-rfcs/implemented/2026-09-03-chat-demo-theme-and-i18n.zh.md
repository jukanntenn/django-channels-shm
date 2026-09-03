# DCS-RFC: Chat demo theming and i18n — one palette, client-side dictionary

Status: implemented

[English](2026-09-03-chat-demo-theme-and-i18n.md) | 中文

## Problem

聊天 demo 是本项目的门面 —— 所有 README 都指向它,它也是发布前的验收项目 —— 但它只有中文一种语言、浅色一种主题。读英文文档的用户面对一个看不懂的界面,而仅浅色的配色与系统的深色模式(以及把一切都保持深色的终端重度用户)格格不入。服务端错误文案让问题更糟:线上传输的是拼好的中文句子,任何客户端都无法本地化。无论怎么解决,都必须保持 demo 的 vanilla JS 无构建形态,并且不触碰库本身。

## Decision

主题是一份调色板,由 CSS `light-dark()` 逐元素解析,没有任何重复。`<html>` 上的 `data-theme` 属性把 `color-scheme` 钉在 `light` 或 `dark`,或保持 `light dark` 让操作系统偏好决定(跟随系统)。工具栏按钮按 auto → light → dark 循环,持久化到 `localStorage["shmchat.theme"]`;`<head>` 里的内联脚本在首次绘制前应用持久化的值,因此没有闪烁。原先硬编码浅色值的每个颜色(输入框填充、边框、我的高亮行、滚动条、toast、阴影)都改成了变量;亮绿色气泡在两种主题下都保持深色文字。

语言切换是 `chat/static/chat/i18n.js` 里的客户端字典(zh/en,约 90 个 key)。模板静态字符串通过 `data-i18n*` 属性承载;`app.js` 里每个动态字符串都走 `t()`;系统消息以字典 key + 参数存储,历史消息会以当前语言重渲染,而用户输入的消息文本永不翻译。切换按钮翻转并持久化 `localStorage["shmchat.lang"]`;首次访问跟随浏览器语言。`shm:langchange` 事件触发有状态的动态字符串重渲染(连接栏、登录页脚、会话列表、聊天头部、群成员、输入区、跳转按钮)。

为了让错误可本地化,demo 协议的 `error` 载荷增加了机器可读字段 —— `code` 加 `params`(例如 `name.too_long` 带 `{what: "nick", max: 24}`)—— 同时保留中文 `message` 作为未知 code 的兜底。客户端认识该 code 时渲染 `t("err.<code>", params)`,否则显示原始 message。

## Alternatives considered

**服务端本地化错误消息(每连接记录 locale)。** 它输了:demo 服务端刻意无状态 —— 没有会话、没有存储 —— 把 locale 穿进每条连接会增加状态并把展示逻辑渗进 consumer;语言是客户端偏好,结构化 code 让线上保持语言中立,中文文本作为人类可读的兜底。

**重复的暗色调色板(`[data-theme="dark"]` 块再加一个 `@media (prefers-color-scheme: dark)` 块处理 auto)。** 它输了:每个颜色要存在两到三份并悄然漂移;`light-dark()` 保住了唯一事实来源,代价是要求支持它的浏览器 —— 面向 evergreen、无构建的 demo 本来就这么假设。

**真正的 i18n 框架(Django i18n、gettext、Intl.MessageFormat)。** 它输了:两种语言、一个静态模板、没有构建步骤 —— 约 100 行的字典加属性应用就覆盖了,不需要新工具链或服务端往返,字典还兼作 UI 文案的唯一审计点。

**一个固定工具栏同时覆盖登录页与主界面。** 它输了:固定在右上角的工具栏会撞上聊天头部的群成员按钮;两组小的同步按钮(登录卡片角落、侧栏头部)不需要任何布局扭曲,并让每个屏幕的控件留在本地。

## Consequences

从构造上就仅限 demo:库的线上格式与 `src/channels_shm` 未被触碰;协议记录在 `examples/chat/chat/consumers.py`(其 docstring 记载了 error 的形状)。深色模式与英文覆盖了每个 UI 字符串,UA 界面(滚动条、表单控件、默认焦点)也随 `color-scheme` 免费跟上。语言切换会重渲染打开中的会话 —— 系统消息按 key,用户输入的文本原样 —— 而 toast 保持渲染时的语言。英文文案在字典里带 `{key|s}` 单复数标记,`Group · 1 member` 与 `Group · 2 members` 都读得自然。`light-dark()` 为 demo 设定了浏览器下限(Baseline 2024),对 evergreen 展示项目可以接受,但这也是它不能进入任何发布产物的原因。
