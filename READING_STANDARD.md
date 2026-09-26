# 论文阅读交付标准

本 workflow 的唯一规范正文是 [阅读交付标准模板](assets/templates/reading_standard.md)。
修改阅读规则时维护该正文；初始化科研项目时复制为 `related_work/reading_standard.md`，避免双份规则漂移。

该标准要求所有正式收录论文采用统一最低交付：逐条英中配对 Highlights、规定主要部分的译文与总结、
详细 note、在 qa 中集中呈现的六问完整问题/答案/依据、开源工作五类核心流程的论文—代码真实双向标注。
note 的七章结构、材料区简短归属、Paper-Notes 精确匹配与必要纠错、不可得时的写作指引均以该正文为准。
不另设文末来源章；translation_zh 仍保留完整 Highlights 翻译，与 note 一致。
translation_zh 独立交付摘要、Highlights、引言、相关工作、方法、不足、未来展望七部分，每节有明确的中文翻译区。
摘要、Introduction、Related Work 与 Method 方法部分全章逐段译完，覆盖全部小节，不足及展望译完作者全部相关论述。
note 保持概括性精读定位；禁止用跳转 note、概要、总结、术语表或覆盖表代替译文，也不为去重删除应译内容。
无独立标题按标准查找等价/分散论述，确无内容须给核查范围；材料不可得不能写成原文没有，摘要、Highlights 和方法不能用缺省声明免除。
独立核验逐段对照原文覆盖与忠实性，记录只入唯一内部状态文件。
同时交付 Markdown 和三线表 HTML；每行表格所有单元格可见文字合计不超过 160 字符。

`reading_manifest.json` 由 agent 根据实际核查填写，工具检查结构与产物关联；
原文、译文、代码语义和标注可读性仍须独立复核。未核实缺口保持 partial / blocked；
已核实的论文与代码差异可作为阅读发现，不阻止阅读收尾。

本标准不包含本仓库个人的 Git 远端授权、8 次上下文压缩等会话约束；它们只适用根 [AGENTS.md](AGENTS.md) 的范围。

所有执行标识与完整审阅记录只保留在 `.workflow/execution_state.json`；其他交付使用论文标题、方法名、
章节/公式和真实路径。PDF 与源码不使用额外映射流水号，保留官方 DOI、arXiv 和代码 commit。
