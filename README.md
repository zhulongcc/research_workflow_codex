# research-workflow

> A Claude Code skill that organizes a research project into a fixed file structure,
> so both you and every fresh agent window know where to read context and where to
> write results. **中文说明在下面。**

> 本项目基于 [skJack/research-workflow](https://github.com/skJack/research-workflow)，
> 感谢原作者 skJack。保留原项目的 Apache-2.0 许可证，修改说明见 [NOTICE](NOTICE)。

用 Agent 协作做科研的项目工作流，做成了 Claude Code skill。

核心想法很简单：**科研项目里人和 Agent 的协作，靠一套固定的文件结构来承载**——
每个文档职责单一，每个新开的 Agent 窗口都知道先读什么、往哪写。

## 本仓库的协作规则

在本仓库中工作的每个新对话都必须先读取根目录的 [AGENTS.md](AGENTS.md)：允许本地 Git 提交，远端写入须经用户明确同意；每个对话最多 8 次上下文自动压缩，第 7 次提醒，第 8 次保存交接并自动续开；允许自主使用 multi-agents。

压缩计数和自动续接依赖平台提供的事件与工具，能力边界及无法自动执行时的处理方式见规则文件。此规则只用于本仓库，`assets/templates/` 中的模板仍用于生成其他科研项目。

## 安装

```bash
git clone https://github.com/zhulongcc/research_workflow_codex.git ~/.claude/skills/research-workflow
```

Cursor 用户放 `~/.cursor/skills/` 或 `.agents/skills/`。

装完对 Claude 说「开个新坑做 X」，它会问你三个问题（项目名、目标、领域术语），
然后把整套结构建好。

## 文件结构

```
你的项目/
├── AGENTS.md              项目总纲：在做什么、硬规矩 —— 每个新窗口第一个读
├── handoff.md             进度交接 —— 每个窗口结束前更新，下一个窗口靠它接上
├── brainstorm.md          问题和假设的讨论记录，倒序
├── related_work/          文献，每篇 paper.pdf + source/
├── reproduce/                   baseline 复现 + 复现报告
├── experiment/
│   ├── results.md         每版实验一行，不覆盖不删
│   └── evaluation.md      标准化测评，防止临时挑指标
└── datasets/ + dataset.md  数据和处理说明
```

## 两个脚本

**建骨架**（幂等，已有文件不覆盖）：

```bash
bash ~/.claude/skills/research-workflow/scripts/init-project.sh ~/Code/my-paper
```

**抓论文**（PDF + LaTeX 源码都下，源码自动清洗——隔离作者打包进来的旧稿、剥掉注释掉的正文）：

```bash
bash ~/.claude/skills/research-workflow/scripts/fetch-paper.sh 2301.11305 detectgpt
```

## 文献阅读的必交付与检查

完整执行顺序见 [三轮文献工作流](READING_WORKFLOW.md)：主 Agent 统一初筛，自动一篇一 sub-agent；
材料获取、全文快读、译文与笔记初稿、源码注释、笔记修订按前置条件推进，再由独立 sub-agent 核验。
每次最多三轮，共享严格上限 ultra 40、max 30、mid 20、inbox 10；每轮不设单独额度，不为凑数扩展。
总量包括首轮，综述按等级计数，重试不重复扣额。主 Agent 合并新发现与积压候选，保留真实停止原因。

本地工具记录阶段并检查前置条件、轮次、共享额度和实物证据；主 Agent 按技能要求调用可用的多代理工具。
工具自身不启动模型或实验。执行标识、各次调研状态和审阅报告统一保存在项目唯一的 `.workflow/execution_state.json`：

```bash
python scripts/literature_run.py --project /path/to/project init <内部运行引用>
python scripts/literature_run.py --project /path/to/project --run <内部运行引用> status
```

详细候选格式、阶段推进和独立复核证据见统一规范，输入字段协议可用对应子命令的 `--help` 查看。
执行用 JSON 从标准输入或项目外临时文件导入，不另在项目中保存。已有笔记可以复用，但不能跳过本轮证据核查。

目录与正文采用论文标题或方法名，论文和代码按实际路径关联。临时标识从生成阶段就不能进入交付物；
完成前再次检查元数据、正文、HTML、批注和对应源码。论文—代码对照采用标题、章节/公式和代码位置，
不添加额外编号或成对标记。DOI、arXiv 编号、正式代码版本保留。

正式收录的论文必须完成主要部分译文、英中配对 Highlights、note 中连贯的精读阐述，以及 qa 中六问的完整答案与依据。精读优先复用对应的 Paper-Notes 解读，按选定论文核实纠错，必要归属压缩到材料区一行；没有可用解读时，按规范从问题、设计、机制到实验证据展开。开源工作还必须交付核心流程的 PDF 批注、源码对应注释与映射表。具体范围见 [阅读标准](READING_STANDARD.md)。`inbox` 可以待处理，创建文件和下载材料不代表完成阅读。

Markdown 是源文件；HTML 阅读版使用三线表。每条表格记录全部单元格的可见文字合计最多 160 字符，长解释放表外。首次使用检查与渲染工具时安装 [阅读工具依赖](requirements-reading.txt)：

```bash
python -m pip install -r requirements-reading.txt
python scripts/reading_artifacts.py check /path/to/project/related_work/ultra/paper-title \
  --repo-entry /path/to/project/reproduce/baselines/method-name
python scripts/reading_artifacts.py render /path/to/project/related_work/ultra/paper-title \
  --repo-entry /path/to/project/reproduce/baselines/method-name
```

检查分别返回 `reading_status`、`correspondence_status` 与 `workflow_ready`。有证据的实现差异可完成阅读；
尚未核实的缺口不能报完成。旧的整体 `status=partial` 仍可能表示已确认差异，阶段推进使用 `workflow_ready`。
静态论文—代码对照不等于已运行实验；脚本检查也不能代替原文与实现的独立复核。

最终 HTML 默认只呈现当前阅读定稿，不插入初筛历史、审阅意见或验收横幅。过程状态与检查报告单独保存；
需要内部诊断副本时，使用 `render --diagnostic --output <internal-path>`。结构检查通过不自动生成研究结论。

新模板只用于新文件；重新初始化不会覆盖已有笔记。已有项目应先保存旧文档，再逐篇补充，保留用户自己的问答与批注。本轮先以 Gaussian Haircut 对齐样板，确认后再推广其余论文。

## 工作节奏

```
抓文献 → 复现关键 baseline → 提假设 → 跑实验 → （循环）
```

复现放在提假设之前：论文只报平均值和成功配置，
真正的问题往往在自己跑一遍才看得见的地方。

## 内置的几条警示

- 效果突然变好，先查数据泄漏（split 是不是按样本分了）
- verify 中间过程，不只是看代码——小问题都藏在输入输出的处理里
- 每一版实验都命名、都留着，失败的也留
- 测评照 `evaluation.md` 走，不临时挑指标
- 上下文快满就更新 `handoff.md`，别硬撑到被截断

<!-- BEGIN reading additions; modified 2026-09-22 (R3). -->
## 文献与代码导读

通过上面的 clone 命令可安装当前仓库版本。
脚本需要 Python 3.10+。Windows 可用 Python 入口，阅读阶段的常用命令见 [三轮文献工作流](READING_WORKFLOW.md)。

- `research_scope.md`：研究范围，文献分级的依据。
- `related_work/`：ultra / max / mid 分级，survey 单放；每篇带中文译文、Note 和问答提示。
- `reproduce/`：按用途分类，带代码树、初学者导读和论文—代码对应；自己的方法放 `method/`。

论文端用同色 PDF 批注放代码片段，代码端直接在现有源码的关键代码块旁添加中文解释性注释，无需创建或切换专用分支。
只用本次确认的最后一版 PDF 与对应代码，不记录 SHA256。
<!-- END reading additions -->

## License

Apache License 2.0
