---
name: research-workflow
description: 用 Agent 协作做科研的项目工作流——建标准文件结构、抓 arXiv 文献、复现 baseline、记实验、管上下文交接。任何涉及开新课题、搭科研项目、下载/整理论文、复现方法、记录实验版本、写交接文档的请求都用它——包括用户只说「开个新坑」「把这几篇论文弄下来」「实验记一下」「接着上次的做」而没提「科研」两个字的时候。
---

# 科研工作流

把一个科研项目组织成固定的文件结构，让人和每一个新开的 Agent 窗口都知道：
去哪读上下文、往哪写结果。

## 开新项目：先问，再建

先问用户三件事（不要多问）：

1. 项目名 + 一句话在做什么
2. 目标（会议/期刊 + deadline）
3. 这个领域常用的几个标准术语

然后建骨架，并把答案填进 `AGENTS.md` 的 `{{占位符}}`：

```bash
bash <skill>/scripts/init-project.sh <项目目录>
```

已有项目里跑也安全——只补缺的文件，不覆盖。

## 每个文件干什么

| 文件 | 职责 | 什么时候读 / 写 |
|---|---|---|
| `AGENTS.md` | 项目总纲：在做什么、目录说明、硬规矩 | 每个新窗口**第一个读** |
| `handoff.md` | 进度交接：到哪了、下一步、踩过的坑 | 每个新窗口**第二个读**；每个窗口结束前**更新** |
| `related_work/` | 文献，每篇 `paper.pdf` + `source/` | 要引用、要对比方法时 |
| `reproduce/` | baseline 复现，每个方法一份复现报告 | 要确认 baseline 数字时 |
| `brainstorm.md` | 问题和假设是怎么讨论出来的，倒序记 | 提假设阶段写；想不通设计意图时读 |
| `experiment/results.md` | 每一版实验一行：命名、指标、结论 | 每次实验后追加 |
| `experiment/evaluation.md` | 标准化测评：数据、指标、可视化写死 | 建项目时定好；之后每次照着跑 |
| `datasets/` + `dataset.md` | 数据和处理说明 | 动数据时 |

各文件的模板在 `assets/templates/`，里面的 `{{占位符}}` 说明了每一栏该填什么。

## 工作节奏

```
抓文献 → 复现关键 baseline → 提假设 → 跑实验 → （循环）
```

**复现在提假设之前。**论文报的是平均值和成功配置，真正的问题往往在
自己跑一遍才看得见的地方。用户上来就要 idea 时，先问复现过没有。

## 抓文献

```bash
bash <skill>/scripts/fetch-paper.sh <arxiv-id> [目录名]
```

PDF 和 LaTeX 源码都会下，源码自动清洗（隔离旧稿、剥注释）。
泛读读 `paper.pdf`；查表格、公式、引用读 `source/*.tex`；图只在 PDF 里。

## 警示

1. **效果突然变好，先查数据泄漏，再庆祝。**最常见原因：split 按样本分了。
   应该按"希望模型泛化的那个维度"分（来源 / 类别 / 被试）。
2. **verify 中间过程，不只是看代码。**逐块问输入、输出、数据怎么处理的，
   配合代码对一遍——小问题基本都藏在中间过程里，看最终指标看不出来。
3. **每一版实验都命名、都留着。**失败和作废的也留，标明原因。
   禁止 `test2` / `final_v2` / 拿日期当名字。
4. **测评照 `evaluation.md` 走，不临时挑指标。**这份文档存在的意义就是防止
   只报好看的那个数。
5. **上下文快满时主动提醒用户更新 `handoff.md`**，别硬撑到被截断——
   丢的往往正是刚调试出来的结论。
6. **不自造术语。**解释工作时用领域里已有的词。

<!-- BEGIN reading additions; modified 2026-09-25. -->
## 文献与代码导读

先按已有信息填 `research_scope.md`，未知项留空，不另开一轮问卷。
执行前必须读 [READING_WORKFLOW.md](READING_WORKFLOW.md) 与 [READING_STANDARD.md](READING_STANDARD.md)。
项目副本分别是 `related_work/reading_workflow.md` 和 `related_work/reading_standard.md`。
前者是执行顺序、轮次、额度和恢复的唯一规范，后者规定单篇内容与格式；不能只生成骨架就结束任务。

主代理必须实际使用 `collaboration` 工具自动派发每篇作者子代理，按并发容量分批；
自检后再派发未参与该篇写作的独立核验子代理，问题交回作者修复后复核。
同轮的多个并发批次不是多轮。脚本只记录状态与证据，不会自动调用 agent，也不能代替语义核验。
阶段、额度、运行与条目内部引用、作者/核验者身份及完整审阅报告，只保留在单个 `.workflow/execution_state.json`。
候选和证据输入用内存标准输入或项目外临时文件，不能另存带执行标识的报告、元数据、路径或备份。
正式目录由论文标题或方法名命名，元数据用 title/name，关联用真实路径；官方 DOI/arXiv/commit 保留。
从生成时就隔离内部标识，收尾再复查。本次迁移仅涉及阅读产物与新 workflow，保留其他任务上下文和历史备份。

按流程先检索初筛和暂定阅读顺序，再由单篇作者获取材料、全文快读确认等级、提取作者贡献原文，
精确匹配 Paper-Notes 页面，依单篇标准复用经核验的详细内容；不可得时按本 workflow 的写作指引生成。
完成必译内容、详细 note 和 qa 六问初稿，读懂源码后直接注释关键块并双向标注，据此修订 note/qa。
结论整合在 note，六问完整问题、答案与依据只放 qa，不另写重复总结。两次分级不单独强制记录调整理由。
主代理每轮合并新发现与积压候选、更新阅读推荐，在本次任务的轮数和总额度内继续。

| 文件 | 用途 |
|---|---|
| `related_work/README.md` | 论文类型与等级；survey 类型独立，inbox 仅筛选 |
| 每篇 `note.md` | 材料、英中配对 Highlights、一句话总结、背景、方法详解、实验与消融、局限与启发 |
| 每篇 `translation_zh.md` / `qa.md` | 必译主要部分；六问完整问题、答案与依据，空白个人区 |
| `related_work/reading_guide.md` | 最新方向关系、阅读顺序与同期对比 |
| reproduce 中的 `note.md` / `code_map.md` | 代码主链路与论文对应 |
| `related_work/code_annotation_guide.md` | PDF 批注及现有源码关键块中文注释 |
| 每篇 `reading_manifest.json` / `reading.html` | 机器证据记录；无过程横幅的三线表阅读版 |

正式 ultra/max/mid 的内容底线相同；survey 依 priority 处理，survey+inbox 只做筛选。
英中配对 Highlights、主要部分译文及独立总结、详细 note、qa 六问答案、每行可见文字 ≤160 字符、Markdown 与三线表 HTML
仍为正式阅读要求。公开代码需核查适用五类流程；先读懂再注释，不逐行注释、不改行为、不要求专用分支。
只用选定同版 PDF 与代码，保留原始源码，不记录 SHA256，不自动覆盖笔记。
PDF、代码与映射通过论文标题、章节/公式、代码位置互相定位，不创建模块流水号或机器边界标记。
源码只加普通中文“论文对应”解释；manifest 用 paper_title、覆盖项用 locations，核验者身份不写到其中。

阅读完成与实现一致性分开。经独立核验、有证据的真实实现差异可以作为阅读结论收尾；
未确认缺口仍是 partial / blocked。静态阅读不要求跑实验，未运行不得写成已验证。
最终正文只呈现最新结论、来源、真实差异与局限，个人原始笔记及交互区不覆盖。
note 的代码差异嵌入对应方法，Highlights 原句后紧随中文译文，与 translation_zh 的完整译文一致。
不设文末来源章；实际复用时在材料区用一条简短来源保留作者、原文链接、许可链接和“已改编”。
详细搜索与纠错记录只入唯一内部状态，不新建公开 provenance；不导入星级、泛赞或自动推荐列表。
Paper-Notes 改编内容遵守其 CC BY-NC-SA 4.0，不把许可扩展到整个 workflow；本项目写作指引不称为作者 prompt。
独立核验须核对详细方法解释、实验/消融证据和 qa 完整答案，不能只检查章节齐全。

```bash
python -m pip install -r <skill>/requirements-reading.txt
python <skill>/scripts/reading_artifacts.py render <paper-dir> --repo-entry <reproduce-entry>
python <skill>/scripts/reading_artifacts.py check <paper-dir> --repo-entry <reproduce-entry>
```

无公开代码时省略 `--repo-entry` 并记录检索依据。检查报告的 `workflow_ready` 不替代独立核验；
主代理负责确认作者与核验者分离、证据对应当前修订，并检查唯一状态文件以外无执行标识残留。流程命令与阶段见执行规范。
<!-- END reading additions -->
