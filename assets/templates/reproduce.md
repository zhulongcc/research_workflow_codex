# Reproduce

> 按用途分类，不把类别和仓库名混在同一级。

具身示例：`policy_models/openpi`、`agent_frameworks/<仓库>`、`evaluation/<仓库>`。
通用项目先用 `baselines/<仓库>`，按需扩充。

每个条目放 `repo/`、`note.md`、`code_map.md`、`reproduction_report.md`。
取得真实代码后生成 `code_tree.md`；metadata 简记来源、采用版本、论文关联和注释分支。

先保留干净的代码版本，再建 `reading/annotated` 分支，只注释核心路径，不改计算逻辑。
论文批注、源码注释和 code_map 使用同一模块编号与颜色名。
一个仓库关联多篇论文时不要重复 clone。demo 跑通不等于复现论文指标。
