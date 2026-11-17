---
name: docs-maintenance
description: '任务实施或收尾时创建、追加、修改和同步 README.md 与 docs，维护主题归属、业务核心工作链路及变更影响。'
---

# Task Documentation Maintenance

## Responsibility and Scope

- **Scenario:** a task changes requirements, design, behavior, or usage that maintained documentation must explain.
- **Problem:** integrate each changed rule into its owning topic without scattering facts or introducing competing statements.
- **Approach:** establish the confirmed change, locate its authoritative home, and select Create, Append, or Modify.
- **Output:** a JSON professional handoff with document decisions, integrated content, and the checks needed to complete the update.
- **Outside scope:** collection-wide responsibility redesign and deciding disputed product or architecture policy.

The consuming Agent resolves decisions with the relevant owners, applies authorized changes, runs applicable checks, and delivers to the user.

## Document Ownership

Read the task's confirmed changes, relevant architecture, parent README, index, and existing topics. Follow the repository's language and organization.

From the business and architecture, identify the needed documents and existing ownership before placing changes. State each affected document's responsibility (questions and owning layer), topic (coherent capability or design concern), and scope (included content and adjacent homes) briefly at its beginning. Place content by these boundaries, not by the task or implementation file that prompted it.

| Document | Responsibility |
| --- | --- |
| Root `README.md` | Project introduction and usage: prerequisites, setup, configuration, running, and navigation. |
| Nested `README.md` | Layer responsibility, design conventions, dependencies, extension points, and directory maintenance rules. |
| Documentation index | Directory scope, organization, reading routes, and authoritative topic entries. |
| Topic document | The requirements, architecture, design, or operating contract owned by that topic. |

Respect business/domain layering or the established documentation layers: `300-requirements/` owns goals and observable behavior; `400-architecture/` owns boundaries, dependencies, flows, and trade-offs; `500-design/` owns detailed technical and interaction contracts.

Give each rule one authoritative home. For document-to-document references, cite only development documentation and keep references sparse, using them for navigation or summaries. Each referenced topic owns its complete logic and facts. Use stable repository-relative links and follow repository policy for external contracts.

A nested README defines directory rules for placement and neighboring homes, subdirectory roles, supported document types, naming and stable identifiers, and maintenance when content changes, moves, merges, or retires. Keep these rules aligned with the actual directory.

## Scenarios and Writing Strategies

Find existing coverage before choosing an action. Choose per rule; one task may require different actions for different rules.

When statements conflict, distinguish a missed historical update from a conflict in the underlying facts or behavior. Reconcile missed updates against confirmed rules; refer unresolved factual or behavioral conflicts to the user or business owner as possible defects rather than choosing a rule yourself.

| Action | Condition and strategy |
| --- | --- |
| Create | A new requirement cannot fit an existing topic and has an independent responsibility, reader need, and maintenance boundary. Define its scope and add navigation. |
| Append | Integrate a compatible new definition or rule into its owning section or matrix without changing established meaning. |
| Modify | A confirmed requirement changes or contradicts a maintained rule. Replace the authoritative statement, preserving valid exceptions instead of appending a competing rule. |

A single field, event, or task does not justify a narrow document. A shared capability may warrant an independent topic when several workflows depend on the same contract.

Necessary task-local moves or consolidation remain within maintenance when they preserve established responsibilities. Broader reassignment across topics requires an explicit structural decision; identify its scope for the Agent to confirm.

## Content Rules

Use unnumbered topic names for document titles and major section headings. Express structure through heading levels and ordered steps through numbered lists.

Synthesize facts by topic, distinguishing confirmed rules, implementation, operating restrictions, proposals, and history. Organize by concepts, responsibilities, flows, and rules. Use matrices for comparable structures, diagrams for relationships, and prose for rationale. Integrate changes into the maintained structure rather than dated supplements.

Establish relevant definitions and agreed rules (口径): meaning, source, identity, counting basis, triggers, time and units, missing values, errors, and compatibility. For configuration, distinguish module defaults, caller overrides, and observed values.

Use shared rules plus a difference matrix for related variants. Keep business identities, write boundaries, recovery policies, and compatibility exceptions explicit even when implementations reuse code.

### 主题业务核心工作链路

当受维护主题承载业务流程时，在其权威主题文档中展示该主题的核心工作链路。即使任务只改变其中一环，也先核对现有链路及受影响的上下游，再更新相应节点、关系和说明；配置、术语等非链路主题可按读者需要引用所属业务的权威链路，不复制或虚构流程。链路或归属尚不明确时记录缺口。

1. 依据已确认口径、实际实现和现有文档，识别主题边界、触发与输入、主要处理节点、输出及下游接收方。区分数据来源、执行先后、并行或汇合关系；仅纳入理解该主题所需的环节。
2. 按实际行为展示关键条件与分支，包括适用时的成功、跳过、拒绝、异常、重试、恢复及其后果；明确重试从哪里开始、是否产生写入，以及变更对上下游、状态或运行约束的影响。事实未确认时标明缺口，不将推测画成现状。
3. 推荐使用 Mermaid 展示链路和分支，并说明连线含义；图中无法清楚表达的条件、差异和影响用相邻文字或表格补充。渲染环境不支持 Mermaid 或图形不适合该主题时，使用清晰的文字或表格展示，并说明限制。
4. 共享规则保留在其权威主题，业务专属链路和差异保留在各自主题；跨主题通过引用衔接，避免复制整条流程或制造相互竞争的口径。

For workflow and integration documents, specify the handoff contract:

| Element | Required information |
| --- | --- |
| Input | Upstream source, selection or version, identity, preconditions, and who guarantees them. |
| Output | Recipient or destination, structure, and write or mutation boundary. |
| Success | Observable completion signal, such as a committed receipt or publication check, and when downstream work may proceed. |
| Acceptance | Verification method and expected, measurable results. |
| Failure | Failure conditions, partial or unknown outcomes, and downstream consequences. |
| Repair | Responsible party, correction steps, retry or rollback boundary, and recovery verification. |

## Task Lifecycle and Checks

- **During the task:** record confirmed decisions with their implementation status; distinguish proposed design, delivered behavior, and operating restrictions.
- **At completion:** reconcile those statements with what was delivered, replace superseded assumptions, and retain remaining restrictions.

Keep reproducible instructions and acceptance criteria in maintained topics. Keep individual execution results and task progress in delivery records.

Align the affected prose, matrices, diagrams, examples, scope statements, and summaries. For local moves or merges, preserve unique rules and update directory maintenance rules, reading routes, inbound links, anchors, and assets together.

核对核心链路及变更影响与已确认口径、实现、其他受影响文档一致，检查来源关系与执行顺序、分支及结果是否表达准确。新增或修改 Mermaid 时，使用项目已有条件检查语法与渲染；验证工具或环境不可用时记录未验证范围和继续条件，不将未验证的图表表述为已通过。

Check ownership and action selection before handoff. After applying changes, the Agent checks affected references and runs applicable existing documentation checks. Report only performed checks as results and identify remaining work; a proposal alone does not establish that maintained documents are current.

## Professional Handoff

Return substantive task details as JSON. The Agent applying this method chooses the fields and structure for the task and downstream use. Provide enough reasoning and integrated content to apply the update, with unresolved decisions and remaining work explicit.

涉及业务链路时，交接其权威归属、核心流程、受影响节点及上下游后果，区分已核实事实与缺口，并说明图文一致性、链接及图表验证的实际结果。
