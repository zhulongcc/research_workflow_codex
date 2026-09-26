# {{项目名}}

{{一句话说清这个项目在做什么}}

**核心主张：{{你的主张——这是整个项目的锚，Agent 靠它判断什么该做什么不该做}}**

目标：{{会议/期刊 + deadline}}

---

## 你（Agent）每次进来先读什么

1. **本文件** —— 知道我们在做什么、每个目录是干什么的
2. **[handoff.md](handoff.md)** —— 上一个窗口做到哪了、下一步是什么
3. 要动实验就再读 [experiment/results.md](experiment/results.md) 和 [experiment/evaluation.md](experiment/evaluation.md)

**不要读完整个仓库再开始。** 上下文很贵，按需读。

---

## 目录

| 路径 | 是什么 | 什么时候看 |
|---|---|---|
| `related_work/` | 相关文献，每篇 `paper.pdf` + `source/` | 要引用、要对比方法时 |
| `reproduce/` | 重要 baseline 的复现，每个带一份复现报告 | 要确认 baseline 数字时 |
| `brainstorm.md` | 问题和假设是怎么聊出来的 | 想不通为什么这么设计时 |
| `experiment/` | 实验代码 + 每一版结果 | 跑实验 |
| `datasets/` | 数据集，处理说明见 `dataset.md` | 要动数据时 |
| `handoff.md` | 进度交接 | **每次都读** |

---

## 读论文的规矩

`related_work/` 里每篇都是 `paper.pdf` + `source/`（LaTeX 源码），按要问什么选：

- 泛读（讲了什么、什么方法）→ `paper.pdf`
- 细查（表里的数、公式、引用）→ `source/*.tex`
- 看图 → 只能 `paper.pdf`，图不在源码里

用 `fetch-paper.sh` 抓的源码已清洗过（旧稿在 `source/.stale/`，注释已剥、原文存 `.orig`）。
手动下的记得自己清一遍。宏多的论文先读 `commands.tex` / `macros.tex` 这类文件。


---

## 硬规矩

1. **不要自己发明术语。**解释你在做什么的时候用论文里已有的词
   （{{列出这个领域的标准术语}}），别造新词。
2. **改实验之前先说你要改什么、为什么。**不要直接动手跑。
3. **每版实验都要有名字。**命名规则见 `experiment/results.md`，不许出现 `test2` `final_v2_new`。
4. **跑完必须走标准化测评。**见 `experiment/evaluation.md`，不要临时挑指标。
5. **窗口快满了跟我说，我让你更新 handoff。**不要硬撑到被截断。

<!-- BEGIN reading additions; modified 2026-09-25. -->
## 文献与代码导读

文献任务先读 [research_scope.md](research_scope.md)、[执行流程](related_work/reading_workflow.md)
和 [单篇标准](related_work/reading_standard.md)。流程是轮次、额度、分工、先后关系与恢复的唯一正文。
主代理必须使用 collaboration 工具自动派发每篇作者和未参与写作的独立核验子代理，按并发容量分批。
脚本只记录状态和证据，不能替代真实派发、阅读或独立核验。

先初筛暂定级与阅读顺序，再获取材料、全文快读确认级、匹配 Paper-Notes、提取原文贡献、翻译和初稿、
源码对照、修订 note/qa、自检和独立核验。结论整合进 note，六问完整问题、答案及依据只放 qa；不另建重复总结。
Paper-Notes 准确匹配时依单篇标准复用并核验纠错；不可得时按本 workflow 的详细写作指引生成。
实际复用时仅在材料区保留一条来源（作者、原文链接、许可链接、已改编），不另设文末来源章。
详细搜索与纠错记录只入唯一内部状态，不新建公开 provenance；不导入星级、泛赞或自动推荐。
两次分级不单独强制记录调整理由。
survey 类型与 priority 正交，survey+inbox 只筛选、不展开引用链；晋级按流程使用正式额度。

最终阅读内容只保留最新定稿、来源、真实差异及局限。运行、条目、作者与核验者的内部引用，以及初筛、
额度、审阅和修复报告只保存在单个 `.workflow/execution_state.json`；输入使用内存或项目外临时文件。
正式目录采用论文标题或方法名，元数据用 title/name，关联用真实路径。其他正文、路径、元数据、源码与 PDF
不保留执行标识或额外映射流水号；标注使用标题、章节/公式和代码位置。官方 DOI/arXiv/commit 保留。
按单篇标准保留逐条英中配对 Highlights、主要部分译文及总结、详细 note 和 qa 六问答案及空白个人区。
note 每条原句后紧随忠实中文译文，与 translation_zh 中完整 Highlights 翻译保持一致。
translation_zh 独立交付摘要、Highlights、引言、相关工作、方法、不足、未来展望七部分，每节有明确的中文翻译区。
摘要、Introduction、Related Work 全部正文逐段译完；方法核心表示、目标与流程的关键段落完整翻译；
不足与展望译完作者全部相关论述。无独立标题须查等价/分散内容，确无内容在译文区声明并给核查范围。
摘要不可免除；材料缺失是未完成，不能冒充原文没有。note 的概括性解读不能替代译文，不为去重删除应译内容。
禁止用 note 跳转、概要、总结、术语表或覆盖表充当翻译；核验逐段回查原文，完整记录只入唯一内部状态文件。
note 按材料、Highlights、一句话总结、背景与动机、方法详解、实验与消融、局限与启发组织，代码差异融入相关模块。
独立核验必须核对方法机制、实验与消融证据及 qa 答案，不只检查结构。
每行表格可见文字合计 ≤160 字符，同时交付 Markdown 与三线表 HTML。

读懂代码后，直接在现有源码关键块旁加中文解释，不逐行注释、不重构、不改行为、无须专用分支。
按 [标注规范](related_work/code_annotation_guide.md) 完成适用五类核心对应；已独立核实的真实差异可作为阅读结论，
未确认缺口保持 partial / blocked。阅读完成、实现一致性和实验复现分开，未运行不声称验证。
只用选定同版 PDF 与代码，不记录 SHA256；不覆盖用户原始笔记、个人交互或 PDF 批注。
<!-- END reading additions -->
