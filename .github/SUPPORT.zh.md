# 支持指南

[English](SUPPORT.md) | 中文

issue 跟踪器只受理可复现的 bug 与明确的功能请求。其余问题在下面各有更快的去处 —— 把问题发到正确渠道，你会更快得到回应。

## 去哪获得帮助

1. **用法、配置与调优问题** → GitHub Discussions 的 Q&A 分类。提问前先搜索已有讨论。
2. **可复现 bug 与明确功能请求** → GitHub Issues。issue 模板会引导你填写必要字段。
3. **安全漏洞** → 按[安全策略](SECURITY.zh.md)走私密报告渠道，绝不要开公开 issue。
4. **`examples/chat` 使用问题** → 同样走 Discussions，按 demo 级社区支持处理。
5. **与本信道层无关的 Django / channels 问题** → 它们自己的社区（[Django Forum](https://forum.djangoproject.com/)、[Django Discord](https://chat.djangoproject.com/)、[channels 官方支持文档](https://channels.readthedocs.io/en/latest/support.html)）。在此类 issue 区提交的问题会被引导过去。

## 什么样的 bug 报告算「具体」

- 精确的复现步骤，最好基于干净项目或 `examples/chat`。
- 环境信息：Python 版本、发行版/内核、`/dev/shm` 可用空间、worker 进程数，以及你的 django-channels-shm 配置（`prefix`、`capacity`、`shm_size` 等）。
- 预期行为与实际行为对比，并附上日志或错误输出。

bug issue 表单会引导你逐项填写这些字段。

## 我们帮不上的

模糊的问题描述（「很慢！」「消息随机丢失！」）会被关闭并指向本页。抱歉这显得严厉 —— 维护时间有限，把 issue 区留给可复现的问题，对所有人更公平。

对于仅在生产环境出现、无法本地复现的问题：先做环境消元（绕过反向代理直连 ASGI 服务器、比对两端的包版本、尝试把场景缩小到 `examples/chat`）。仍无法定位时，带上环境细节来 Discussions，我们一起排查。

本仓库不提供商业支持或 SLA —— 这里的支持是社区性的尽力而为。
