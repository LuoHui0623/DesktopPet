---
name: docs-refactor
description: 'Use when reviewing or restructuring an existing README.md and docs collection: 文档审查、文档重构、文档整合、治理计划、职责重划、主题合并拆分. Establish authoritative topic homes and prepare evidence-backed structural and content changes.'
---

# Documentation Review and Refactoring

## Responsibility and Scope

- **Scenario:** an existing document set needs review, a governance plan, or structural refactoring.
- **Problem:** unclear responsibilities, fragmented topics, repeated or conflicting rules, and unreliable reading routes.
- **Approach:** map ownership with evidence and select justified retain, modify, merge, split, move, or retire actions.
- **Output:** a JSON professional handoff with strengths, findings, proposed topic homes, and a bounded refactoring plan.
- **Outside scope:** deciding disputed business or architecture policy and final approval of structural changes.

The consuming Agent confirms material decisions, applies authorized changes, runs applicable checks, and delivers to the user.

## Scope and Document Ownership

Establish the requested repositories, directories, readers, and deliverable: review, plan, or content for authorized changes. Read the relevant architecture, parent README, indexes, directory rules, and topics. Follow repository language and organization.

Determine the needed document structure from the business and architecture, then map existing documents and their responsibilities within it. Define each topic's scope, readers, authoritative rules, and handoff relationships. State responsibility, topic, and scope briefly at the beginning of each resulting document.

| Document | Responsibility |
| --- | --- |
| Root `README.md` | Project introduction, prerequisites, setup, configuration, running, and navigation. |
| Nested `README.md` | Layer responsibility, design conventions, dependencies, extension points, and directory maintenance rules. |
| Documentation index | Directory scope, organization, reading routes, and authoritative topic entries. |
| Topic document | The coherent requirements, architecture, design, or operating contract of its topic. |

Respect business/domain layering or the established documentation layers: `300-requirements/` owns goals and observable behavior; `400-architecture/` owns boundaries, dependencies, flows, and trade-offs; `500-design/` owns detailed technical and interaction contracts.

Give each rule one authoritative home. For document-to-document references, cite only development documentation and keep references sparse, using them for navigation or summaries. Each referenced topic owns its complete logic and facts. Use stable repository-relative links and follow repository policy for external contracts.

A nested README defines directory rules for placement and neighboring homes, subdirectory roles, supported document types, naming and stable identifiers, and maintenance when content changes, moves, merges, or retires. Keep these rules aligned with the actual directory.

Trace conclusions to specific documents and sections. Consult schemas or implementation only where needed to establish a concrete claim.

## Scenarios and Restructuring Strategies

When statements conflict, distinguish a missed historical update from a conflict in the underlying facts or behavior. Reconcile missed updates against confirmed rules; refer unresolved factual or behavioral conflicts to the user or business owner as possible defects rather than choosing a rule yourself.

| Action | Condition and strategy |
| --- | --- |
| Retain | Distinct responsibilities, readers, or contracts justify the boundaries. Explain what works and preserve it. |
| Modify | A topic remains valid but its rules or references need correction. Update the authoritative statements and affected views. |
| Merge | Documents fragment one responsibility, reader need, and maintenance boundary. Consolidate while preserving unique rules and exceptions. |
| Split | One document owns independently maintained concerns. Separate by stable responsibility, reader need, and maintenance boundary. |
| Move | A coherent topic belongs in another layer or location. Relocate it to its owning home and preserve reading routes. |
| Retire | A document is obsolete or superseded. Account for unique content and specify a replacement where needed before retiring it. |

Preserve sound boundaries rather than optimizing for fewer files. Similar wording or shared code does not prove that responsibilities should merge. Keep workflow steps distinct when their inputs, write boundaries, completion signals, or failure and recovery contracts differ.

A single field, event, task, or implementation file does not justify a narrow topic. A shared capability may warrant an independent topic when several workflows depend on the same contract.

## Content and Contract Rules

Use unnumbered topic names for document titles and major section headings. Express structure through heading levels and ordered steps through numbered lists.

Synthesize facts by topic. Distinguish confirmed requirements, intended design, implemented behavior, operating restrictions, and historical compatibility.

Clarify definitions and agreed rules (口径): meaning, source, identity, counting basis, triggers, time and units, missing values, errors, and compatibility. Distinguish module defaults, caller overrides, and observed runtime values.

Use shared rules plus a difference matrix for related variants. Preserve business identities, write boundaries, recovery policies, and valid compatibility exceptions. Organize by concepts, responsibilities, flows, and rules; use matrices for comparable structures, diagrams for relationships, and prose for rationale.

For workflow and integration documents, specify the handoff contract:

| Element | Required information |
| --- | --- |
| Input | Upstream source, selection or version, identity, preconditions, and who guarantees them. |
| Output | Recipient or destination, structure, and write or mutation boundary. |
| Success | Observable completion signal, such as a committed receipt or publication check, and when downstream work may proceed. |
| Acceptance | Verification method and expected, measurable results. |
| Failure | Failure conditions, partial or unknown outcomes, and downstream consequences. |
| Repair | Responsible party, correction steps, retry or rollback boundary, and recovery verification. |

Distinguish data-source relationships from execution order.

Keep reproducible instructions and acceptance criteria in maintained topics. Keep individual execution results and task progress in delivery records.

## Refactoring Plan and Verification

For each structural choice, explain the reason, resulting responsibility and scope, unique rules and exceptions to preserve, replacement home where needed, and affected references. Provide a bounded sequence with actual dependencies.

At each resulting home, choose the writing action per rule: Create for an independent new requirement, Append for a compatible new rule, or Modify to replace a confirmed conflicting rule. Integrate into owning sections rather than dated supplements.

For review or planning requests, deliver analysis and proposals only. Identify material structural decisions for confirmation before the Agent applies authorized changes.

Update directory rules, indexes, reading routes, inbound links, anchors, and assets with moves, merges, or retirement. Align affected prose, matrices, diagrams, examples, scope statements, and summaries.

After applying changes, the Agent verifies that unique content and exceptions have a destination, each rule has a clear home, distinct views agree, and topics remain reachable. Check affected references and run applicable existing documentation checks. Distinguish proposed work from applied changes and planned checks from actual results; identify remaining decisions without claiming an unverified outcome.

## Professional Handoff

Return substantive task details as JSON. The Agent applying this method chooses the fields and structure for the task and downstream use. Provide enough evidence, reasoning, proposed content, and preservation decisions to continue, with unresolved choices and remaining checks explicit.
