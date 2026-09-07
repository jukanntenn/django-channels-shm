# DCS-RFC: 社区友好基础设施

Status: implemented

[English](2026-09-07-community-friendly-infrastructure.md) | 中文

## Problem

仓库交付了一个能用的库,却没有任何 GitHub Community Standards 清单所要求的社区管道:没有行为准则、贡献指南、安全策略或支持分流,外部报告者没有 issue 表单,PR 没有核对清单,没有审查路由,也没有讨论阵地。外部贡献者评估本项目时无从了解规则(本仓库门槛很高:原生构建、prek 门禁、RFC 要求),看不到谁在维护、去哪里提问,README 也已与它所描述的代码脱节。项目要成为社区友好的开源项目 —— 这个目标与仓库对外表面之间的落差就是问题。

## Decision

社区表面是一组各有唯一归属、受门禁约束的双语对:根目录的 [`CONTRIBUTING.zh.md`](../../../CONTRIBUTING.zh.md)(环境搭建、基本规则、接受范围、AI 辅助贡献);[`.github/CODE_OF_CONDUCT.zh.md`](../../../.github/CODE_OF_CONDUCT.zh.md) 逐字采用 Contributor Covenant 2.1,执行联系方式为维护者邮箱;[`.github/SECURITY.zh.md`](../../../.github/SECURITY.zh.md)(私密漏洞报告为主渠道、明确的 IPC 信任边界、支持版本表)与 [`.github/SUPPORT.zh.md`](../../../.github/SUPPORT.zh.md)(渠道分流与「可执行报告」判据)位于 `.github/`;[`.github/CODEOWNERS`](../../../.github/CODEOWNERS) 将所有路径路由到 `@jukanntenn`,内嵌子树地图与 `layout.rs` 的 ABI 注记。

任何变更都先开 issue,包括错字修正;非平凡变更另附 DCS-RFC。GitHub Discussions 是公开对话阵地:提问进 Q&A,想法进 Ideas,有价值的讨论由促成共识的人蒸馏为 DCS-RFC,RFC 回链来源讨论 —— Discussions 承载未成型的对话,RFC 承载已定型的决策,互不重叠。

Bug 与 feature 报告采用 YAML issue 表单([`bug.yml`](../../../.github/ISSUE_TEMPLATE/bug.yml)、[`feature.yml`](../../../.github/ISSUE_TEMPLATE/feature.yml)),带必填结构化字段与行为准则勾选框;`idea.md`、`research.md`、`task.md` 保持极简 Markdown 模板,服务维护者侧的工作类型(无效的 `type:` frontmatter 键已移除)。agent 保留零摩擦路径:issue 表单只在网页 UI 生效,API/CLI 的程序化创建完全绕过它们,YAML 本身机器可读,根 `AGENTS.md` 指示程序化 bug 报告遵循 `bug.yml` 的结构。PR 携带五项核对清单(测试、`prek run --all-files`、Conventional Commits、非平凡附 RFC、双语文档),每项带 N/A 出口。

README 回答 GitHub 官方定义的五个 README 问题 —— 项目做什么、为什么有用、如何上手、去哪获得帮助、谁在维护 —— 分别由 Features/Quickstart、Benchmarks、Community & support 节与点名维护者的 Contributing 节承担;基准表跟随 `bench/docker/results/` 下最新提交的运行结果。`examples/chat` 交付双语 README 对,截图在各自语言的界面下实拍。

项目名在一切书写处均为 `django-channels-shm`:仓库、发行名与行文统一用全称,导入名保持 `channels_shm` 以保证 `CHANNEL_LAYERS` 的 `BACKEND` 字符串工效 —— 这是标准的发行名/导入名分工(`beautifulsoup4` → `bs4`)。

门禁跟随语料:`verify_doc_pairs.py` 与 `verify_md_links.py` 现覆盖四个社区双语对,`verify_md_wrap.py` 仅覆盖 `CONTRIBUTING.md`,`docs/AGENTS.md` 载有新的分层表行。启用 Discussions 及其分类文案、置顶欢迎帖、私密漏洞报告开关与仓库 topics(`python rust django django-channels asgi channel-layer shared-memory ipc pyo3 linux`)是 git 之外的仓库元数据操作。

## Alternatives considered

**指针式社区文件,仿 django/channels。** django 与 channels 用一行 `SECURITY.md`/`CODE_OF_CONDUCT.md` 指向 djangoproject.com 的政策,背后是 DSF 的行为委员会与安全团队。否决:本仓库不在其管辖内,而 GitHub 官方指南要求采纳行为准则的前提是「愿意并且能够执行」—— 指向一个无法在此行动的团队恰恰过不了这一关。

**社区文件仅英文。** 生态默认做法,维护更省。否决:本项目的文档标准是权威对等的双语对,且 Contributor Covenant 官方 zh-cn 译本消除了「措辞敏感文件不敢翻译」的反对理由。

**Bug/feature 保持极简 Markdown 模板。** 既有的简练模板对 agent harness 运转良好。对这两个面向社区的报表否决:带必填字段的表单是 GitHub 保证人类输入结构化信息的机制,SUPPORT.md 承诺表单会引导报告者填写环境字段;而 agent 路径保持零摩擦 —— API/CLI 创建根本不渲染表单,引导只是移进了 agent 可读的 YAML。

**删除 Idea 模板。** Discussions 定位为未成型想法的家之后,`idea.md` 显得冗余。否决:Discussions 没有 REST API,`gh` 也无一等命令(仅 GraphQL),以程序化方式建档的 harness 工作流要付真实代价;模板保留,由 `about` 行限定为维护者侧用途。

**琐碎 PR 豁免 issue 要求。** 更软的门禁 —— 错字修正可直接 PR —— 符合常见做法。评审中否决:维护者选择了更严格的统一规则(每个 PR 关联一个 issue),以少量贡献者摩擦换取单一规则与完整的公开记录。

**全子树枚举的 CODEOWNERS。** 把每个子树映射到 owner,类似 cpython 的结构。否决:单一 owner 下每行内容都相同;地图放注释里,作为未来 owner 加入时填写的槽位。

**新双语对置于文档门禁之外。** 门禁范围早于这些文件存在;扩展只需改三个脚本。否决:不受门禁约束的双语对会以门禁存在所要抓住的方式腐坏 —— 一侧被改而另一侧没跟上。

**保留 `channels-shm` 作为发行名。** 短名与导入名一致,且改名要动 pyproject、两份锁文件与所有文档行文。否决:仓库本就命名为 `django-channels-shm`,仓库名与包名已经分歧且无生态先例支持这种分歧(`django/channels_redis` 两者同名);改名只在首次发布到 PyPI 之前是零成本的,之后会让用户困在旧名上。导入名保持 `channels_shm`,`BACKEND` 字符串得以简短。

## Consequences

Community Standards 清单达到推荐全集,贡献者仅凭仓库表面就能回答「如何参与」。代价是真实的:四个双语对从此必须同步演进(现已强制),行为准则以单人 best-effort 的规模约束维护者履行其执行阶梯,「每个 PR 都要 issue」给顺手小修加了一步,Discussions、私密报告与 topics 仍是可能悄悄偏离文档承诺的手工仓库设置。验证:`uv run python scripts/doc_sync.py`(pairs、links、wrap、budgets)与 `prek run --all-files` 覆盖仓库内表面;`bench/docker/results/` 每次新增运行结果后,README 的基准表须随之刷新。
