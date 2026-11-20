# Deep Research Evidence · v0.3.0

面向多来源科研问题的深度研究 Skill：拆分研究对象、机制、方法、时间和比较关系，规划有依赖与停止条件的检索，保留工具产物和原文定位，综合冲突证据并给出有边界的结论。

此目录为 GitHub 发布材料。**可加载 Skill 位于 `skill/`，入口为 `skill/SKILL.md`**；平台安装包位于 [dist/deep-research-evidence-0.3.0.skill](dist/deep-research-evidence-0.3.0.skill)。两者与完成测试的 v0.3.0 字节一致。仓库中的评测答案仅用于审查，未装入 Skill，也不能代替实际运行。

## 四类必需材料

| 材料 | 位置 | 内容 |
|---|---|---|
| Skill 本体 | [skill/SKILL.md](skill/SKILL.md) | 用途、触发条件、实际研究流程、输入输出与异常处置 |
| 必需脚本与资源 | [skill/scripts](skill/scripts)、[references](skill/references)、[templates](skill/templates) | 状态与恢复、批处理、证据／交付审计、格式验证、字段合同和模板 |
| 使用与方法说明 | [docs/USAGE.md](docs/USAGE.md) | 版本、安装、路径、依赖、权限、实际调用、方法、限制、失败处理与自查 |
| 最小调用示例 | [examples/minimal/README.md](examples/minimal/README.md) | 合成输入、真实 Agent 调用提示、准备脚本和明确标为示例的输出 |

## 快速开始

1. 下载或克隆本仓库，在仓库根目录操作；保留 `skill/` 的相对目录结构。
2. 使用 Python 3.9+。辅助脚本只有标准库依赖，无需 `pip install`、模型 API 密钥或额外 Agent 框架。
3. 将整个 `skill/` 目录安装至实际宿主支持的 Skill 位置，或让宿主直接读取 `skill/SKILL.md`。宿主需要实际的文件读取、Python 和任务所需检索工具；脚本本身不是模型或搜索服务。
4. 准备并实际运行最小示例：

```sh
python3 scripts/check_release.py
python3 examples/minimal/prepare.py
```

把 prepare 打印的提示交给具有本地文件与 Python 工具的实际 Agent。它需要读取真实输入并完成研究；prepare 不会生成答案。真实结果写 `examples/minimal/run/result.json`，工作记录写 `examples/minimal/run/work/`。

```sh
python3 skill/scripts/research_audit.py --work examples/minimal/run/work --output examples/minimal/run/work/audit.json
python3 skill/scripts/validate.py result --case examples/minimal/run/input/case.json --result examples/minimal/run/result.json
```

`result.example.json` 是示意输出；不得复制成真实运行结果。完整操作和恢复规则见示例 README。

## 方法与验证

v0.3.0 新增原子断言与证据情境绑定、原始要求和六类交付审计、可恢复批量记账。机制能检查记录矛盾和缺口，不能认证科学语义。

本地 89 项回归通过。三个新合成案例×三组共 9 次全部交付：新版科学可答要求完整且正确为 14/14，旧版与无 Skill 各为 13/14；差异来自局部证据限定措辞，三组核心计算均正确。新版结果写出耗时仍更长，不能宣称整体提速。样本较小，未验证真实文献真实性、统计显著性或官方得分。

[评分导向优化报告](docs/SCORE_OPTIMIZATION-v0.3.0.md) 逐条映射全部 16 个评分细项；[评估材料说明](evaluation/README.md) 解释公开证据与原始记录的边界。

```sh
python3 -m unittest discover -s tests -v
python3 skill/scripts/validate.py package --path skill
python3 scripts/check_release.py
python3 build_package.py
```

发布材料中的输入、模板与原始输出都已标明合成属性。使用真实来源时遵循其访问及使用条件；凭据不写入记录。来源见 [第三方说明](skill/THIRD_PARTY_NOTICES.txt)。本次整理没有替作者选择项目开源许可证；上游 MIT/Apache 标签仅描述设计参考，不能作为本项目许可证。

## 发布和维护

- [GitHub 上传步骤](UPLOAD_TO_GITHUB.md)：目标账号 `czeng-nju`，建议公开仓库 `deep-research-evidence`。
- 当前版本：0.3.0；整理日期：2026-11-20。安装包 SHA-256：`36be6e406feda45abada17ec928002c625dfae824077da285b55d86422530d20`。
- 维护时附版本、最小输入、脱敏错误与复现步骤；变更结论、证据或范围后必须重新审核，变更 Skill 后重新测试并使用新版本。
- 此次仅准备本地材料，由仓库所有者手动上传；未向 Bohrium 提交。
