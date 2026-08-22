# DCS-RFCs — django-channels-shm 的 RFC

[English](README.md) | 中文

DCS-RFC 是持久留存的提案与决策记录 —— 记录 _为什么_、_放弃了什么_、以及代码与规格承载不了的部分。文档描述当前状态；DCS-RFC 解释该状态为何如此。

## Layout and naming

每条 DCS-RFC 活在 `.agents/dcs-rfcs/{lifecycle}/yyyy-mm-dd-topic-title.md`。日期是主题首次提出之日（以 git 历史为准）。生命周期树即清单 —— 浏览它或 grep 仓库即可；没有需要维护的索引文件。

- **`proposed/`** —— 实现前接受评审的提案。尚未构建，或只部分构建。
- **`implemented/`** —— 决策已发布。文件以现在时记录决定了什么、拒绝了什么。当代码后来改名文件或改默认值时，在同一变更中更新 DCS-RFC 的事实（路径、名称、结构）—— 但绝不把它改写成另一个决策；用新 DCS-RFC 取代它并互相链接。
- **`rejected/`** —— 提案被考虑后否决。只在其理由能阻止一个诱人错误时保留；否则删除。

DCS-RFC 之间的交叉引用使用相对 Markdown 链接，绝不用裸文本，这样 [`verify_md_links`](../../scripts/verify_md_links.py) 才能检查它们，它们也经得起目录间移动。

## When to write one

每个非平凡变更在同一变更集中新增或更新至少一条 DCS-RFC。当变更改动行为、架构、跨文件契约、工具链、测试策略、磁盘或线上格式、或维护者可能合理重审的任何东西时，即为非平凡。纯机械或局部编辑豁免。更新已拥有该决策的 DCS-RFC 即满足规则 —— 不要造重复；先 grep `.agents/dcs-rfcs/` 查重。

## The file format

头部块严格为：

```markdown
# DCS-RFC: <title>

Status: <status>
```

`Status:` 的值必须与目录一致，取三种形式之一：`proposed`、`implemented`、或 `rejected — <一句话原因>`（拒绝原因正是读者要找的事实；机器 token 保持 ASCII）。正文以 `## Problem` 开篇，须脱离解决方案仍能成立。

`implemented/` 续以 `## Decision`（现在时，发布的是什么）…… `## Alternatives considered` …… `## Consequences`。提案期标题 —— `## Proposal`、`## Plan`、`## Migration plan`、`## Acceptance criteria` —— 在此被格式闸门拒绝。

`proposed/` 续以 `## Proposal` …… `## Alternatives considered` …… `## Acceptance criteria` …… `## Risks`。工作未建时提案可用将来时。

`rejected/` 冻结其提案期的全部章节；裁决活在 `Status:` 行上。

每条记录都是双语对：英文原版旁带 `.zh.md` 孪生 —— 骨架相同、机器 token 与节标题保持英文 —— 且双侧同变更更新（[文档标准规则 7](../../docs/AGENTS.md)）。

**`## Alternatives considered` 在每条 DCS-RFC 中强制** —— 每个真实的败选方一段加粗引导的段落及其败因。没有记录败者的决策在邀请重新论证 —— 那正是 DCS-RFC 要防止的失效。备选方案按当时论证的原样记录，绝不在事后编造。

在生命周期目录之间移动文件意味着同一变更内更新其 `Status:` 行并重新满足目标目录的骨架：`proposed/` → `implemented/` 将 `## Proposal` 重写为现在时的 `## Decision`，并把 `## Acceptance criteria`/`## Risks` 折入 `## Consequences`；`proposed/` → `rejected/` 只在 `Status:` 上加原因并冻结文件。

[`verify_dcs_rfc_format.py`](../../scripts/verify_dcs_rfc_format.py) 强制上述一切；它作为 [`doc_sync.py`](../../scripts/doc_sync.py) 的一部分运行。
