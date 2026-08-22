# DCS-RFC: The agent-driven harness — dcs-rfcs, layered instructions, skills, and documentation gates

Status: proposed

[English](2026-08-22-agent-driven-harness.md) | 中文

## Problem

本仓库主要由 AI coding agent 开发 —— 四套工具 harness（`.zcode/`、`.claude/`、`.codex/`、`.opencode/`）已把每一项 format 与 lint 决策委托给 prek —— 然而承载这一工作模式的约定却以纯 prose 存在，背后没有任何机械手段。决策理由没有持久的家：艰辛选择背后的 why（弃 broker 改用无锁 ring + slab、让 prek 成为唯一门控源、分层测试架构）只活在 PRINCIPLES 的叙述与 git 历史里，代码与文档都无法链接到它，于是每次重提都得从零重新论证。根 `AGENTS.md` 背着每个子树的指令 —— Rust 构建与风格规则、chat demo 的验收流程、发布步骤 —— 1,242 个词，无论任务为何每次会话都全量加载，且没有预算来阻止继续膨胀。README 双语对已无声漂移（英文 1,128 词对中文 736 词），因为没有任何东西检查配对。`.agents/skills/` 与 `.claude/skills/` 下的 skills 镜像靠手工保持一致，AGENTS.md ↔ CLAUDE.md 闸门也只会报告漂移 —— agent 实际编辑过的那一侧仍需人工和解。deepseek-harness 参考项目陈述了本记录据以行动的奠基观察：agent 遵守被强制的闸门远比遵守 prose 约定可靠，而且当劳动由 agent 承担时，“工作量大”不构成成本论据。

## Proposal

**文档分层，一个事实一个家。** 文档标准活在 [`docs/AGENTS.md`](../../../docs/AGENTS.md)：一张为每类事实指派恰好一个家的层级图，其余一切以链接指过去。[`README.md`](../../../README.zh.md) + 孪生 —— 面向用户的产品文档；[`PRINCIPLES.md`](../../../PRINCIPLES.zh.md) + 孪生 —— 行为原则，且是活的家（本项目的原则是新写的、被 `iterating` skill 直接引用；与 markpost 不同，PRINCIPLES 不冻结进根文件的 Conventions 节）；`docs/` —— 操作指南（[`docs/development.md`](../../../docs/development.zh.md)、[`docs/releasing.md`](../../../docs/releasing.zh.md)、[`docs/benchmarking.md`](../../../docs/benchmarking.zh.md)），从今天的根 AGENTS.md 抽取；[`.agents/dcs-rfcs/`](../README.zh.md) —— 决策记录；[`CHANGELOG.md`](../../../CHANGELOG.md) —— 台账，按设计叙述历史，豁免于 prose 闸门；agent 指令文件（`AGENTS.md`、`CLAUDE.md`、`SKILL.md`）—— 常设指令，仅英文；`examples/chat/` —— 独立 uv 项目，经清单豁免。不设 `specs/` 层：设计事实留在 README 概览与代码里，直到触发信号到来。

**双语语料，平权。** 每个在范围内的文档文件成对发布，`foo.md` 与 `foo.zh.md` 同目录，两侧平权：任一侧都可先写或先改，被编辑的一侧是该次变更的源，孪生侧在同一变更中以最小补丁跟随 —— 绝不整篇重译；实质分歧时修正错的一侧，没有哪一侧默认获胜。每侧头部携带链接孪生的语言切换；进入语料的相对链接跟随读者语言；标题序列与围栏代码块字节一致；机器 token 与节标题保持英文；`.md` 侧 prose 不含 CJK；中文在拉丁词周围用半角空格、用全角标点。`README.zh-CN.md` 改名为 `README.zh.md` —— 一词一形 —— 漂移的对子重新校齐；补写 `PRINCIPLES.zh.md`。豁免活在只含豁免的清单 [`scripts/doc_languages.manifest.json`](../../../scripts/doc_languages.manifest.json)：agent 指令、CHANGELOG 台账、`examples/chat/`、工具本地状态。

**dcs-rfcs —— 本项目的 RFC。** 持久留存的提案与决策记录，活在 `.agents/dcs-rfcs/{proposed,implemented,rejected}/yyyy-mm-dd-slug.md`（日期 = 主题首次提出之日，以 git 历史为准），每条记录都是双语对。树承载两份职责正交的文档：[`README.md`](../README.zh.md) + 孪生是规范契约 —— 布局、生命周期、格式骨架；[`AGENTS.md`](../AGENTS.md) 承载常设指令 —— 写前查重、只可取代不可改写、保持双语对同步。头部严格为 `# DCS-RFC: <title>` 加一行与目录一致的 `Status:`；正文以 `## Problem` 开篇；`implemented/` 续以 `## Decision` → `## Alternatives considered` → `## Consequences`（现在时，提案期标题在此被拒）；`proposed/` 续以 `## Proposal` → `## Alternatives considered` → `## Acceptance criteria` → `## Risks`；`rejected/` 冻结提案，裁决写在 `Status:` 行。`## Alternatives considered` 在每条记录中强制 —— 没有记录败者的决策在邀请重新论证。每个非平凡变更在同一变更集中新增或更新至少一条记录；纯机械或局部编辑豁免。开局语料是本记录加三条回填，依据 PRINCIPLES 叙述与 git 历史落地：无锁 ring + slab 弃 broker、prek 作为一切质量闸门的唯一来源、分层测试架构。

**分层 agent 指令与词预算。** 根 `AGENTS.md` 瘦身为 800 词上限内的常设指令 —— 身份、布局图、命令索引、git 工作流、边界、以及陈述镜像与预算契约的 “Editing these instructions” 节 —— 同时 Rust 指令迁往 [`crates/_channels_shm_native/AGENTS.md`](../../../crates/_channels_shm_native/AGENTS.md)（≤ 400），demo 的指令迁往 [`examples/chat/AGENTS.md`](../../../examples/chat/AGENTS.md)（≤ 300），文档标准迁往 [`docs/AGENTS.md`](../../../docs/AGENTS.md)（≤ 600），过程性内容迁往各自的 `docs/` 指南。子树文件补充根文件、绝不重复它；`tests/` 与 `bench/` 不设文件（没有自己的工具链）。[`scripts/doc_budgets.manifest.json`](../../../scripts/doc_budgets.manifest.json) + `verify_doc_budgets.py` 强制上限 —— 预算是整文件 `wc -w`，被预算的文件缺失即闸门失败；变红时先搬迁、再压缩，最后才以带论证的清单 diff 提升上限。

**无方向镜像。** [`scripts/agentlib.py`](../../../scripts/agentlib.py) 逐对基于 git HEAD 检测方向 —— 绝不用 mtime，clone 与 checkout 会重置它：恰好一侧不同于 HEAD 意味着该侧被复制到另一侧之上；内容相等即通过；两侧都变且不一致是工具拒绝猜测的冲突；HEAD 中不存在的路径视为该侧已变更，这正好引导新对子。`check_agent_instructions.py`（闸门）与 `sync_agent_instructions.py`（修复器）共享该模块；覆盖范围是根与子树的 `AGENTS.md` ↔ `CLAUDE.md` 拷贝对以及 skills 树 —— `.agents/skills/` 与 `.claude/skills/` 是无主从的相等拷贝镜像。符号链接被拒：Windows checkout 会把它们物化为持有目标路径的普通文件。prek hook 在无歧义的单侧变更上自动修复并 stage 过时的孪生；真冲突则以点名两侧、修复命令与和解步骤的方式失败。

**Skills。** `.agents/skills/` 是工具中立的家（ZCode 与 Codex 侧工具原生加载）；`.claude/skills/` 是它的镜像。两个 skill 加入 `iterating` 与 `commit`：`writing-rfcs`（记录工作流 —— 查重、生命周期、骨架、校验）与 `doc-standards`（放置、写作纪律、闸门响应）。`iterating` 增加落地规则：承载决策的产出入棚为 dcs-rfcs；`commit` 承载记录对的捆绑与 `README.zh.md` 命名。

**闸门套件与接线。** 所有闸门都是 `scripts/` 里 stdlib-only 的 Python（合乎本仓库的 scripts 规则），共享 [`doclib.py`](../../../scripts/doclib.py) 的助手，由 [`doc_sync.py`](../../../scripts/doc_sync.py) 聚合 —— 按序运行、保持各自可独立运行、并把范围限定于给定的文件参数：`verify_md_links.py`（相对链接与 `#fragment` 锚点可解析 —— 覆盖整个被闸门约束的语料，含 agent 指令）、`verify_md_wrap.py`（每段一个物理行）、`verify_md_current.py`（仅 README 与 `docs/` 的当前状态 prose；PRINCIPLES 与记录按设计叙述理由）、`verify_dcs_rfc_format.py`（双语言的骨架、机器 token、头部与目录一致）、`verify_doc_pairs.py`（配对完整、切换头、链接语言、结构对齐、CJK 纯度）、`verify_doc_budgets.py`。接线遵循既有 prek 教义：`doc-check` hook 加入 `lint` 组、对已暂存的 Markdown 运行 `doc_sync`（prek 在 hook 运行期间贮藏未暂存变更，更宽的扫描会看到过时的树）；`agents-md-sync` 升级为 `format` 组里自愈的 `agent-instructions-sync` 并新增 `skills-sync` 兄弟。CI 零改动 —— lint job 本就运行 `prek run --all-files`，对全量语料执行每个 hook；四套 AI 工具 hook 树同样零改动，原因相同：它们委托 prek 组，新闸门自动流入。

**迁移。** 各批次以分层提交构成一个变更集落地，CI 校验终态：先将在途工作（PRINCIPLES.md、`iterating` skill、AGENTS/CLAUDE 修改）原样提交获得干净基线；然后落地闸门脚本与清单并规整车料 —— 段落重排为每段一个物理行、`README.zh-CN.md` 改名并校齐其对、补写 `PRINCIPLES.zh.md`；然后是 dcs-rfcs 树、其契约对、格式闸门、`writing-rfcs` skill 与回填；然后是带预算的 AGENTS.md 拆分、镜像升级、`docs/` 抽取；最终全量 `prek run --all-files` 跑绿，本记录以重写后的骨架从 `proposed/` 迁往 `implemented/`。

**凭信号生长，不凭想象。** 未移植项及唤醒它的触发信号：`.i18n.yaml` blob-hash 边车与 merge driver（单侧配对编辑反复逃过评审）；带封存清单的 archived 冻结树（记录多到难以浏览）；生命周期内的 `{class}/` 子目录（类别数超出可浏览性）；闸门调度器 DAG（闸门数量或运行时间超出顺序执行）；带索引闸门的 `specs/` 层（设计事实超出 README 概览）；postmortem、PR/issue 模板与策略自动化（外部贡献者到来）；`grill-me`、`shipping`、`release` skills（工作流频繁到值得固化）。参考项目的完整机制是这条路通往何处的记录上限，而非起点。

## Alternatives considered

**整包移植 deepseek-harness 机制。** 它输在：本语料比 markpost 小一个数量级，而 markpost 又比参考项目小一个数量级；blob-hash i18n 三元组、冻结档案、依赖图闸门调度器、postmortem、issue 策略自动化，每一件都会是这棵树里最复杂的脚本，却服务不了这里的任何需求。markpost 的触发信号裁决被有意继承 —— 部件在信号出现时到来，绝不整包。

**只接线不分层 —— 保留单根 AGENTS.md。** 它输在：1,242 个词已经让每次会话为每个子树的细节买单，而没有机械预算，膨胀就会继续；加载路径支持渐进披露，分层把上下文税局部化到支持它的工具。评审中提出并被否决，改为完整拆分。

**把 PRINCIPLES.md 冻结进根文件的 Conventions 节 —— markpost 模式。** 它输在：markpost 冻结的是规则正在迁走的前身；这里的 PRINCIPLES.md 是新写的、是 `iterating` skill 赖以运作的活的家，没有任何漂移问题构成迁移动机。保持其活性并配对双语，是更小且有依据的改动。

**符号链接镜像 —— 参考项目的机制。** 它输在：git 把符号链接存为 mode-120000 blob，Windows checkout 在没有 `core.symlinks` 加特权的情况下会把 `CLAUDE.md` 物化为含九个字节的普通文件；没有任何东西把贡献者约束在类 Unix checkout 上。拷贝镜像加基于 HEAD 的方向检测在任何地方都能工作。

**单独的 CI 文档工作流 —— markpost 需要一个。** 它输在：markpost 的 lint CI 按 path 忽略 `*.md`，文档因此需要自己的工作流；本项目的 CI 对一切运行 `prek run --all-files`，第二个工作流会在没有 path-ignore 理由的情况下重复那个 job。

**现在就设 `specs/` 层。** 它输在：今天的设计事实装得进 README 概览与代码；一层加索引闸门在任何信号提出要求之前，就是需要维护的面、需要翻译的对。

**保留 `README.zh-CN.md` 命名。** 它输在：`.zh.md` 是新语料与生俱来的约定；一词两形本身就是漂移，配对闸门将永远背着两个正则。改名是一次 `git mv`。

**把本次引入拆成五条记录，每个子系统一条。** 它输在：markpost 的细粒度记录反映的是历经数周展开的采纳过程；本次引入是一个决策事件，作为整体评审与落地，各子系统共享同一次迁移。细粒度记录从下一个触发信号开始。

## Acceptance criteria

1. `.agents/dcs-rfcs/` 存在，含 `README.md` + `README.zh.md` 契约、`AGENTS.md` 常设指令、`proposed/`、`implemented/`、`rejected/` 目录；`verify_dcs_rfc_format.py` 通过每一条记录对，包括本记录的双语两侧。
2. 语料已规整且成对：`README.zh.md`（已改名，与英文侧内容对齐）、`PRINCIPLES.zh.md`、天生成对的 `docs/` 指南；`verify_doc_pairs.py`、`verify_md_links.py`、`verify_md_wrap.py` 通过全量语料。
3. 分层生效：根 `AGENTS.md` ≤ 800 词、`crates/_channels_shm_native/AGENTS.md` ≤ 400、`examples/chat/AGENTS.md` ≤ 300、`docs/AGENTS.md` ≤ 600；`verify_doc_budgets.py` 绿，且删掉任一被预算文件会使它失败。
4. 镜像无方向，经 scratch 状态演练验证：任一 AGENTS ↔ CLAUDE 对或 skills 树的单侧编辑被自动修复并 stage；双侧相同编辑通过；双侧分叉失败并点名两侧与和解步骤；新对子从单侧引导生成。
5. 闸门已接线：`doc-check` 经 `lint` 组对已暂存 Markdown 运行 `doc_sync`；`agent-instructions-sync` 与 `skills-sync` 经 `format` 组自愈；`prek run --all-files` 端到端绿；`.github/workflows/` 字节不变。
6. 开局语料完整：本记录加三条 implemented 回填（ring + slab 弃 broker；prek 作为唯一闸门源；分层测试架构），每条都落根于其引用的 PRINCIPLES 叙述与 git 历史；落地时本记录迁往 `implemented/`，`## Proposal` 重写为现在时的 `## Decision`，`## Acceptance criteria`/`## Risks` 折入 `## Consequences`。
7. 产品代码与行为零改动：`uv run pytest -m "not slow and not e2e"` 绿、crate 内 `cargo test` 绿；新脚本在本仓库配置下通过 ruff。
8. Skills 就位且相等：`writing-rfcs` 与 `doc-standards` 存在于 `.agents/skills/` 且与 `.claude/skills/` 一致；`iterating` 与 `commit` 按规格更新。

## Risks

结构对齐证明的是形状而非实质 —— 敷衍的孪生侧照样绿，语义由评审承载，这是 markpost 为同一设计记录在案的代价。双语义务给今后每次文档变更上税；在本语料规模上相称，且信号门控的升级（新鲜度记录、PR 级共变闸门）保持延迟并带命名触发信号。词预算上限招致提升拉锯；余量加上 “以清单 diff 论证” 的文档化路径吸收它。pre-commit 只查已暂存文件，意味着全量语料的真相活在 CI 与 AI Stop hook 里，而非每次提交。方向检测读 HEAD，因此合并与 rebase 状态下分叉的对子会以带指引的冲突浮出水面，绝不会被静默拷贝。移植的脚本可能携带移植 bug；验收标准中的镜像演练与闸门演练正是为抓住这一点而存在。规整批次一次性触碰每个文档文件 —— 重排是机械的，但任何语义编辑都单独骑行、作为内容评审。
