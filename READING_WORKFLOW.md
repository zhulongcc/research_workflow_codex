# 文献阅读执行流程

唯一流程正文为 [reading_workflow.md](assets/templates/reading_workflow.md)。
初始化项目时复制到 `related_work/reading_workflow.md`；维护流程只修改该正文，其他文件引用它。

本流程约束检索、分级、额度、自动子代理派发、材料获取、单篇阅读与代码对照、独立核验、
轮次整合、恢复和最终阅读推荐。内容与格式标准另见 [READING_STANDARD.md](READING_STANDARD.md)。
单篇流程包含解读来源匹配、详细 note 与 qa 答案、源码对照后的修订；具体内容只在单篇标准维护。
本仓库个人会话规则继续由根 [AGENTS.md](AGENTS.md) 约束，不随流程模板分发。

所有执行标识与完整审阅记录只保留在 `.workflow/execution_state.json`；其他交付使用论文标题、方法名、
章节/公式和真实路径。PDF 与源码不使用额外映射流水号，保留官方 DOI、arXiv 和代码 commit。
