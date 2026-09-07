# 安全策略

[English](SECURITY.md) | 中文

> 中英文本如有出入，以 [英文版](SECURITY.md) 为准。

## 如何报告漏洞

请通过私密渠道报告漏洞 —— 绝不要在公开 issue、PR 或 Discussion 中报告。

1. **首选：** 使用 GitHub 私密漏洞报告（打开仓库的 **Security** 标签页，点击 **Report a vulnerability**）。表单会直接送达维护者，并支持通过 GitHub Security Advisory 进行协调披露（含申请 CVE）。
2. **备用：** 发送邮件到 jukanntenn@outlook.com。

## 报告应包含

足以完成分诊的信息即可 —— 无需长篇大论：

- 问题简述与所涉组件（Python 层、Rust 原生模块、唤醒机制或 `examples/chat`）。
- 最小概念验证或复现步骤。
- 你的环境：Python 版本、发行版/内核、所用 django-channels-shm 的 commit。
- 一两句影响评估：攻击者能获得什么、需要什么前提。

## 响应预期

- 目标 3 天内确认收到（单人维护，尽力而为 —— 不作硬性 SLA 承诺）。
- 报告确认后，通过 GitHub Security Advisory 协调披露；修复发布前细节保持私密。
- 报告者会收到进展同步，并在公告中获得致谢（除非要求匿名）。

## 范围与信任边界

本仓库内的所有代码均在范围内，包括 `examples/chat`（按 demo 级严重性分诊）。

django-channels-shm 是面向单机、同一信任域内协作进程的 IPC 层。共享内存区域以 `0o600` 权限创建，仅同 UID 进程（及 root）可访问 —— 以同一用户身份运行的其他进程可以读取或篡改消息。这是设计使然，与 POSIX IPC 语义一致，不构成漏洞。共享主机上的跨用户/多租户隔离属于部署层职责，超出本层范围。

## 支持版本

| 版本 | 支持状态 |
| ---- | -------- |
| `main`（未发布 0.x） | ✅ |

没有回溯分支。本策略随正式版本发布而演进。
