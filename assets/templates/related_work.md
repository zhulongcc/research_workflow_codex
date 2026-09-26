# Related work

> 先看 `../research_scope.md`，按 [reading_workflow.md](reading_workflow.md) 执行检索、两次分级、阅读与独立核验。
> 等级表示对当前课题的重要性；单篇交付遵守 [reading_standard.md](reading_standard.md)。

## 怎么放

| 目录 | 放什么 |
|---|---|
| `ultra/` | 最重要、最相关，影响问题定义、核心方法或关键 baseline |
| `max/` | 强相关补充、重要前驱、同期竞争方案 |
| `mid/` | 辅助背景和备选路线 |
| `inbox/` | 只做筛选，不产生引用链扩展；晋级需要正式额度 |
| `survey/` | 综述类型单放，只存一份；仍有独立 priority |

相关度优先，结合影响力和年份，不只按引用量或新旧排序。初筛暂定级，全文快读后确认级；
两次分级不单独强制记录调整理由，旧 `priority_reason` 可保留但不是必填项。
`paper_type` 与 `priority` 正交；综述等级记在 metadata，不复制到 ultra/max/mid。
`survey + inbox` 按 inbox 处理；正式 ultra/max/mid 的最低阅读要求相同。
阅读未完成时在内部状态记录 partial / blocked，不把归档或生成骨架算作完成。
论文目录由标题或方法名生成可读名称；metadata 使用 `title`，代码关联使用实际路径。
运行、论文、仓库和代理的内部执行标识只存 `.workflow/execution_state.json`，不得用于交付目录或元数据。

## 每篇放什么

| 文件 | 必需职责 |
|---|---|
| `paper.pdf` + `source/` | 选定同版原文与论文源码；算法代码放 reproduce |
| `note.md` | 材料、英中配对 Highlights、一句话总结、背景、方法详解、实验与消融、局限与启发 |
| `translation_zh.md` | 摘要、Highlights、引言、相关工作、方法、不足、未来展望的独立译文与总结 |
| `qa.md` | 六问完整问题、Agent 答案与依据；个人交互区留空 |
| `reading_manifest.json` | 机器核查记录；不展示在读者正文 |
| `reading.html` | 三线表 HTML 阅读版，与 Markdown 同时交付 |
| `paper_annotated.pdf` | 开源工作的同版批注副本，含真实代码片段 |
| `paper_code_annotations.json` | 开源工作的逐项真实定位，关联 PDF 和源码 |

note 优先复用准确匹配且已核验的 Paper-Notes 内容，做必要纠错与补充；不可得时按单篇标准详细撰写。
实际复用时在材料区用一条来源保留作者、原文链接、许可链接与“已改编”，不设文末来源章。
Highlights 原句紧随中文译文，与 translation_zh 的完整翻译一致；六问只放 qa，代码差异融入方法。
note 保持精读后相对概括性解读，translation_zh 逐段翻译摘要、引言、相关工作全部正文，方法关键段落完整译完，
不足与展望翻译作者全部相关论述。每节独立中文翻译，禁止跳转 note 或用概要替译，也不为去重删减应译段落。
原文无独立标题时查找等价/分散内容；缺省声明、材料缺口及逐段核验按单篇标准执行，摘要不可免除。
不导入星级、泛赞或自动推荐；详细搜索与纠错记录仍只写入唯一内部状态。

开源工作的适用五类核心流程必须在 PDF、源码和 `code_map.md` 通过标题、章节/公式、代码位置完成对应，
规则见 [code_annotation_guide.md](code_annotation_guide.md)。缺失实现如实记录，不能宣称实现完全一致。
已核实差异与未确认缺口分开：前者可作为阅读结论收尾，后者保持 partial / blocked；实现不一致不等于没有读完。
所有表格每行全部单元格可见文字合计 ≤160 字符；长原文和分析放表外。

领域 map、阅读顺序见 `reading_guide.md`；精读提问见 `reading_qa.md`。
`index.md` 由 metadata 生成，手工笔记不要只写在那里。分级移动后检查手写链接。
轮次、额度、初筛和审阅过程保存在 `.workflow/execution_state.json`；读者页面只保留最新定稿。
