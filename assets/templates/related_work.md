# Related work

> 先看 `../research_scope.md` 再分级。等级表示对当前课题的重要性，不是论文质量排名。

## 怎么放

| 目录 | 放什么 |
|---|---|
| `ultra/` | 最重要、最相关，影响问题定义、核心方法或关键 baseline |
| `max/` | 强相关补充、重要前驱、同期竞争方案 |
| `mid/` | 辅助背景和备选路线 |
| `inbox/` | 待筛选，先不猜等级 |
| `survey/` | 精选的重要、相关、有影响力的综述，只存一份 |

相关度优先，结合影响力和年份，在 `metadata.json` 简记分级理由；不要只按引用量或新旧排序。
综述的等级也记在 metadata，不再复制到 ultra/max/mid。影响力依据没查到就留空。

## 每篇放什么

`paper.pdf` + `source/`：选定的最后一版原文和论文源码；算法代码放 reproduce。
`note.md`：精读提示与整理后的理解；`translation_zh.md`：中文译文；`qa.md`：具体问答。
`paper_annotated.pdf`：同版阅读副本，批注含对应代码块，规则见 `code_annotation_guide.md`。

领域 map、阅读顺序见 `reading_guide.md`；精读提问见 `reading_qa.md`。
`index.md` 由 metadata 生成，手工笔记不要只写在那里。分级移动后检查手写链接。
