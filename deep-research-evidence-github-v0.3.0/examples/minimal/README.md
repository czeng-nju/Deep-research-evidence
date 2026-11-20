# 最小调用示例（v0.3.0）

本目录用两份独立编写的**合成材料**演示输入、实际调用和输出接口，不是真实论文、实验、正式比赛题或用于证明质量增益的保留集。`result.example.json` 是人工编写的**示意输出**，已在其 answer 内标明；它不能代替 Agent 实际研究，也不随 prepare 复制到运行目录。

## 文件与输入

- `case.json`：问题、两份允许来源、路径规则及五字段输出合同。
- `materials/filter-a.txt`、`materials/filter-b.txt`：六行合成文本，物理行号从 1 开始。
- `task.md`：中文任务及证据、适用范围和未知要求。
- `agent-prompt.txt`：交给真实宿主 Agent 的完整调用提示。
- `prepare.py`：只复制输入、打印提示；不调用模型，不生成答案或伪造账本。
- `result.example.json`：供读者了解接口的示意输出；不得给执行本例的 Agent 当作答案。

需要 Python 3.9+（标准库）和能够读取本地文件、运行 Python 的实际宿主 Agent；不需要网络、API 密钥或额外软件包。文件权限只需读取本仓库，并写入本例 `run/` 目录。

## 真实运行步骤

在**仓库根目录**执行：

```sh
python3 examples/minimal/prepare.py
```

此步骤仅创建 `examples/minimal/run/input/`。把打印出的提示交给实际 Agent：要求它加载 `skill/SKILL.md`，读取新复制的 case 和材料，实际完成拆解、有限检索、阅读、计算、证据综合及复核。也可以直接复制 `agent-prompt.txt` 的内容。宿主怎样启动或加载 Skill 取决于该宿主，但提示、文件和路径均已给齐。

Agent 使用以下初始化命令，之后按 Skill 文档登记真实记录；不要单独运行初始化后就声称完成研究：

```sh
python3 skill/scripts/research_state.py init --case examples/minimal/run/input/case.json --work examples/minimal/run/work
```

真实结果写入 `examples/minimal/run/result.json`，过程产物保存在 `examples/minimal/run/work/`。完成后，实际执行并检查报告：

```sh
python3 skill/scripts/research_audit.py --work examples/minimal/run/work --output examples/minimal/run/work/audit.json
python3 skill/scripts/validate.py result --case examples/minimal/run/input/case.json --result examples/minimal/run/result.json
```

审计退出码 0 仅表示有限记录检查完成；1 表示无效或矛盾记录；2 表示缺少可核验信息或交付缺口。结构通过不等于科学正确；仍需逐条核对来源贡献、范围、计算和不确定性。

## 输出含义

`case_id` 对应真实输入；`status` 为 complete、partial 或 blocked；`answer` 直接回答问题；`evidence` 的每条只陈述本来源实际支持的内容，并带物理行号；`uncertainty` 如实列出不能确定的事项。有依据地回答未知不等于漏做任务。依赖和工具记录等保存在工作目录，不强塞进禁止额外字段的最终 JSON。

读者可以查看 `result.example.json` 理解格式；其结构自查命令如下，**这不是一次真实 Agent 测试**：

```sh
python3 skill/scripts/validate.py result --case examples/minimal/case.json --result examples/minimal/result.example.json
```

## 失败与恢复

若 `run/` 已存在，prepare 明确拒绝覆盖。检查并保留已有结果，按 Skill 恢复同一账本；如需独立重跑，先把旧 `run/` 另存为其他名称，再重新准备。不得删掉失败记录后把重跑冒称首次成功。

材料缺失、脚本不可用或工具报错时记录真实限制，保留已完成工作，必要时输出 partial/blocked。不要把示例答案复制成缺失结果，不要编造调用或成功审计，也不要因材料未提供显著性或机制而自行补上结论。本例为封闭来源任务，无需访问互联网。
