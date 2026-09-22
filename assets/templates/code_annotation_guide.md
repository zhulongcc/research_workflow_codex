# Paper—code annotations

> 同一模块用同一编号和颜色。论文里看代码，代码里能找回论文位置。

## 论文侧

在对应段落、公式、算法框或图示处高亮，批注写 **编号、文件/函数、真实代码片段、中文说明**。
例如 `paper_key:M01 | BLUE`。代码选关键的几行，保留缩进，不用伪代码冒充原实现。
输出 `paper_annotated.pdf`；与 `paper.pdf` 是同一版论文，不改正文、不盖住内容、不清除个人批注。

| 颜色名 | 对应内容 |
|---|---|
| YELLOW | 输入、数据处理 |
| BLUE | 模型、核心结构 |
| ORANGE | 训练、loss、更新 |
| GREEN | 推理、采样、输出 |
| PURPLE | 评价、指标 |

颜色分类型，编号分模块；编号同时写进 `code_map.md`。

## 代码侧

从选定的干净版本建 `reading/annotated` 分支，只加核心路径注释，不自动提交或推送。

```python
# [P2C paper_key:M01 | BLUE]
# 论文：第 N 页，Sec. X / Eq. Y
# 含义：这段代码做什么，输入输出是什么。
# 此处保留原始代码，不改计算逻辑。
# [END P2C paper_key:M01]
```

源码用颜色名与编号对应，不依赖编辑器配色。注释后检查行为，更新行范围，不能只凭函数名猜对应。

## Zotero

把标注 PDF 作为原论文条目的另一附件打开，不覆盖已有笔记。
外部批注可查看；要编辑，在阅读器中用 File → Import Annotations。导入会将批注移到 Zotero 数据库，
并从该 PDF 移除，所以操作副本。代码是批注里的普通文本，不保证语法高亮。

依据：[Zotero 官方说明](https://www.zotero.org/support/kb/annotations_in_database)。
有真实 PDF 和代码后才标注；完成后逐页核对位置、颜色与内容，不把模板当成果。
