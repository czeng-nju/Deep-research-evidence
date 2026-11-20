# 使用、方法与资源说明

## 安装、版本与运行条件

版本为 0.3.0。使用 Python 3.9 或更新版本；4 个运行脚本仅依赖标准库及同目录模块，不需要额外下载包或配置密钥。宿主 Agent 负责真实检索、阅读、计算、判断和写答案；Python 脚本仅维护可检查的记录。

有两种安装方式：将整个 `skill/` 文件夹放到宿主实际支持的技能目录，或者让有文件工具的 Agent 直接读取 `skill/SKILL.md` 并按其流程执行。若宿主接受 `.skill` 安装包，可使用 `dist/deep-research-evidence-0.3.0.skill`。它是 ZIP 格式，解压后根目录含 SKILL.md、scripts、references、templates 及第三方说明；不能只复制单个 SKILL.md 后省略链接资源。

宿主和安装位置由实际平台决定，本 Skill 不假设作者电脑上的目录、账户、API 或额外多 Agent。Bohrium 的平台运行路径和预算见 [适配说明](../skill/references/bohrium-runtime.md)，以实际题目和环境提供的约束优先。

封闭示例只需读仓库文件、执行 Python、写入 `examples/minimal/run/`；开放研究还需宿主提供允许访问的搜索、抓取、PDF／表格工具。没有相应能力时记录未完成项，不编造调用。脚本无需访问用户主目录；不将密码或 token 写入事件、公开仓库和证据表。

## 输入和输出

输入是用户的真实研究问题、允许来源、附件及实际输出合同。结构化 case 可包含 `case_id`、`question`、`materials` 和 `output_contract`；示例的材料路径相对 case.json。不要求普通自然语言请求伪造 case ID 或比赛合同。

输出优先满足用户的实际格式。最小示例使用五字段 JSON：`case_id`、`status`、`answer`、`evidence`、`uncertainty`。每条 evidence 写来源 ID、实际定位和该来源确实支持的表述；多来源推断需要明确全部前提，不能让单个来源承担全部结论。

工作目录保存原始要求、子问题和依赖、检索动作、产物快照和哈希、证据、结论、审核、失败与恢复记录。内部 `state.json` 不是最终答案；未知不编值。原始要求仍未处理时使用 partial/blocked，按要求正确说明未知可以是完整回答。

## 完整调用步骤

1. 阅读实际问题、来源边界和输出合同，识别对象、机制、方法、时间、比较与证据要求。
2. 若有真实 case 文件，运行 `python3 skill/scripts/research_state.py init --case CASE_PATH --work WORK_DIR`；已有工作目录按恢复接口继续，不重新初始化覆盖。
3. 按 [记录字段](../skill/references/records.md) 保存原始要求、依赖问题、检索顺序、实际工具参数、来源范围和停止条件。
4. 用宿主真实工具获取资料、读取原文、计算；保留原始与解析产物，登记实际位置和哈希。已观察到的多个记录可按 [批处理接口](../skill/references/batch.md) 一次记账。
5. 把结论拆成事实、推断和未知，按 [情境及交付合同](../skill/references/claim-scope.md) 绑定每个断言的证据和适用条件；登记 annotations 文件并真实审核。
6. 比较设计、对象、条件、单位、终点和时间，保留真正冲突，区分版本更新和不具可比性的结果。方法细节见 [研究判断](../skill/references/research-method.md)。
7. 评估问题拆解、检索覆盖、证据回链、冲突、综合结论和研究空白六类内容，用实际记录引用说明，不只打勾。
8. 写符合真实合同的结果，并运行审计与结果验证。按 [最小示例](../examples/minimal/README.md) 可完成一次无网络的实际宿主调用。

```sh
python3 skill/scripts/research_audit.py --work WORK_DIR --output AUDIT_PATH
python3 skill/scripts/validate.py result --case CASE_PATH --result RESULT_PATH
```

占位路径必须替换成实际文件；详细可运行路径已在最小示例提供。脚本的 `--help` 列出参数。

## 脚本、配置和来源

| 文件／目录 | 用途与依赖 |
|---|---|
| `skill/scripts/research_state.py` | 状态、事件、不可变产物、缓存、恢复、预算与失效传播；标准库 |
| `skill/scripts/record_batch.py` | 顺序调用同目录 research_state 的允许操作，保留逐步回执；标准库 |
| `skill/scripts/research_audit.py` | 调用同目录 validate，检查已标注的情境一致性和交付缺口；标准库 |
| `skill/scripts/validate.py` | 状态、结果合同和 Skill 包的有限结构检查；标准库 |
| `skill/references/` | 实际运行合同和方法规则；必须随 Skill 分发 |
| `skill/templates/` | 研究记录模板；须用真实任务填充 |
| `examples/minimal/` | 本次整理新增合成示例；不用于评价优化增益，不包含正式题答案 |
| `tests/` | 可独立运行的 89 项脚本回归；不属于加载 Skill 的依赖 |
| `evaluation/` | 历史合成对照的输入、输出和评审副本；不属于运行资源，也不得向新任务提供答案 |

资源由本项目工作过程整理；原始用户 Skill 作为方法基线。设计参考、固定版本及使用条件说明见 [THIRD_PARTY_NOTICES.txt](../skill/THIRD_PARTY_NOTICES.txt)。未把参考项目代码或其运行依赖打包进本 Skill；没有外部必需个人文件。

## 失败处理与适用边界

- 检索为空：记录实际查询与范围，有限改写；不能把一次空结果当作领域不存在证据。
- 临时错误或限流：在剩余总预算内有限重试；权限不足不能无限重试。
- 解析失败：使用允许且实际可用的替代解析方式；记录原始／解析对应关系，不虚构 PDF 页码。
- 批次失败：此前成功步骤保留；相同内容重跑跳过已提交动作。修订操作内容需新批次 ID；批处理不是整体事务，不支持多写者并发写同一账本。
- 证据冲突：保留双方、检查可比性；不能为通过检查而扩大证据范围或删去原始要求。
- 审计退出码：1 表示矛盾／无效记录，2 表示缺少可核验信息或交付缺口，0 只表示有限记录检查完成；科学语义仍须审核。

适用于多来源研究比较、文献综合和限定材料研究；不替代单篇逐句精读、正式偏倚审查或领域专家。复杂表格、OCR、机制和统计推断需要真实工具及专业复核。宿主原生调用数和 token 不可见时记未知。

## 自查与维护

先执行 `python3 scripts/check_release.py` 验证 Skill／安装包一致性与说明链接，再运行 `python3 -m unittest discover -s tests -v`。按最小示例由实际 Agent 执行后，核查结果、实际工具记录、原文行号、冲突和未知。结构通过、字段齐全或模拟数据成功均不能保证科学正确。

修改必须保留历史失败和版本，不覆盖已发布的原始试验。报告问题时只提供必要的脱敏材料。此整理没有添加项目 LICENSE；第三方设计来源中的许可标签不等于本项目的许可声明。
