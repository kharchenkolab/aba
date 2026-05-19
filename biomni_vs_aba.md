# BioMNI vs ABA: A Comparative Analysis

## 1. BioMNI: Quick Summary

**BioMNI** (Stanford SNAP lab, bioRxiv 2025) is a general-purpose biomedical AI agent designed to autonomously execute a wide range of research tasks across diverse biomedical subfields. A researcher types a natural-language request — "Plan a CRISPR screen for T cell exhaustion genes" or "Annotate cell types in this scRNA-seq file" — and the agent reasons, selects tools, executes them, inspects results, and iterates until it produces an answer.

### Concept

BioMNI's core claim is breadth: one agent, one interface, any biomedical task. It is organized around the **ReAct paradigm** (Reason → Act → Observe, loop until done), implemented as a LangGraph `StateGraph` that alternates between an LLM reasoning node and a code execution node. Unlike most tool-use agents, BioMNI exposes a single general-purpose `run_python_repl` (plus R and Bash variants) as its execution primitive, with domain-specific functions available as importable Python library calls from within that environment. The agent is stateless across sessions; within a session the full message history is maintained in LangGraph's `MemorySaver`.

### Architecture

| Layer | Implementation |
|---|---|
| Agent runtime | LangGraph `StateGraph` (generate → execute → generate loop) |
| Execution model | LLM emits `<execute>...</execute>` blocks; backend runs them as Python/R/Bash subprocesses |
| LLM backend | Anthropic Claude, OpenAI GPT-4, Gemini, Groq, Bedrock, local via SGLang |
| Tool library | ~18 domain modules (genomics, immunology, pharmacology, pathology…), 100s of importable functions |
| Tool selection | Optional LLM-based `ToolRetriever` filters relevant subset per query |
| Data lake | ~11 GB curated flat files: DepMap, GWAS catalog, MSigDB, BindingDB, miRTarBase, etc. (auto-downloaded from S3) |
| Knowledge retrieval | Know-How Library of lab protocols and best practices, retrieved by RAG |
| Web UI | Gradio (two-panel: main answers + inner reasoning/execution log) |
| Python API | `agent = A1(...); agent.go("task")` |
| Security posture | Explicitly unsafe: "LLM-generated code runs with full system privileges" |
| Custom reasoning model | Biomni-R0 (Qwen-32B, RL fine-tuned on agent interaction data) |
| Evaluation benchmark | Biomni-Eval1: 433 instances, 10 biomedical reasoning tasks |

BioMNI tools call external biomedical databases (UniProt, Ensembl, ClinVar, GEO, GnomAD, GWAS Catalog, etc.), run command-line bioinformatics tools (PLINK, samtools, MACS2, IQ-TREE), execute R and Python code, and consult the 11 GB data lake. An optional `ToolRetriever` module uses the LLM to dynamically select a relevant subset of tools for each query.

---

## 2. Comparison by Dimension

### 2.1 Conceptual Orientation

| | BioMNI | ABA |
|---|---|---|
| **Core metaphor** | Smart research assistant — answer any question | Scientific workspace — manage a project's analytical lineage |
| **Scope** | Breadth: any biomedical task across 18+ domains | Depth: one project, one dataset family, full provenance |
| **Interaction unit** | Task/question → answer | Scientific entity (dataset, figure, finding) → conversation scoped to it |
| **Success criterion** | Task completed | Validated finding with traceable provenance |

BioMNI optimizes for **discovery breadth** — helping a researcher quickly explore across domains (drug targets, variant interpretation, pathway analysis, image quantification) without learning separate tools. ABA optimizes for **analytical depth and trust** within a project — ensuring every figure can be traced to its exact input data, analysis parameters, and the chain of reasoning that produced it.

These are genuinely different aspirations, not just different implementations of the same idea. BioMNI is a question-answering accelerator; ABA is a project-level epistemic management system.

### 2.2 Focus Management

A critical architectural difference lies in what "context" the agent works in and how that context is determined.

**BioMNI:** Focus is entirely **chat-driven**. There is no concept of a "focused object" separate from what the user typed. The LangGraph graph uses a hardcoded `thread_id: 42`, meaning all conversations within a session are a single linear message stream. When a user looks at a figure embedded in the chat, the agent has no special awareness of it — the agent's context is the full chat history. The Gradio UI provides two separate panels (a "main" chatbot for high-level answers and an "inner loop" chatbot showing reasoning/code/observations), but both are just views into the same linear message sequence. There is no concept of "I am currently focused on Figure 3" distinct from "I most recently talked about Figure 3."

**ABA:** Focus is **UI-object-driven**. When a user navigates to a figure in the project tree, that figure becomes the focused object. The chat pane automatically loads the conversation thread *scoped to that figure* — a different thread than the conversation around a different figure or a dataset. The agent receives the `focus_object_id` as part of every chat message, so it always knows what scientific entity the conversation is about, independent of whether the user mentions it explicitly. This mirrors how scientific work actually proceeds: you look at a figure, think about it, ask questions about it — the object is the anchor, not the query text.

The practical consequence is substantial: in BioMNI, a 6-month project accumulates a single long global conversation; in ABA, each figure and finding has its own focused analytical thread that can be revisited independently.

### 2.3 Code Traceability vs. Provenance

These are related but distinct concepts, and BioMNI is often miscategorized here.

**BioMNI's code traceability:** The Gradio UI shows each `<execute>` block as an expandable "🛠️ Executing code..." panel containing the exact Python/R/Bash code that ran, followed by the observation output. The `_execution_results` list stores `{triggering_message, images, timestamp}` for each execution step. Users can expand these panels and see — within the current session — exactly what code produced each output. This is genuine and useful: you can audit the reasoning-to-code chain for any result in the session.

What BioMNI does **not** provide: versioning of input data, linkage from code to a specific commit of that input, reproducibility across sessions (the session state is in-memory), or any formal relationship between a piece of code and the scientific object it helped produce. The PDF export captures the full session as a document, but the session state itself is ephemeral.

**ABA's provenance:** Every artifact links to a specific lakeFS commit (the exact version of the input data), the Nextflow session ID (the exact computational run), the git revision (the exact code), and the parameters file. Branching ("exclude S4, rerun") creates a new lakeFS commit on a named branch; the comparison between branches is a first-class operation. Provenance survives session boundaries, restarts, and personnel changes.

In short: BioMNI shows you *what code ran in this conversation*. ABA guarantees *exactly what data + code + parameters produced this figure, forever*.

### 2.4 Entity Levels Tracked

| Level | BioMNI | ABA |
|---|---|---|
| **Specific number / statistic** | Yes — in the text response or printed output | Yes — in the figure or table object |
| **Figure / plot** | Yes — base64 image embedded inline in chat | Yes — a persisted `figure` object with URI, checksum, and provenance |
| **Analysis run** | No formal entity — execution steps in message history | Yes — `analysis_run` row with pipeline, input_ref, output_commit, trace URI |
| **Finding** | No — results are text in conversation | Yes — a `finding` object with evidence bundle and advisor notes |
| **Claim / panel** | No | Yes — promoted from findings, the terminal epistemic unit |
| **Relationship between entities** | No — flat message list | Yes — `object_edges` with typed relations (generated_from, supports, weakens) |
| **Branch** | No | Yes — `branch` object linking parent ref to child ref with change_set |

BioMNI's entity model is: messages, with code blocks and images embedded. The hierarchy is linear (step 1, step 2, …, answer). There is no formal "figure" entity that persists beyond the session, no "finding" that aggregates evidence from multiple conversations, and no typed relationships between results.

ABA's entity model is: projects → objects (datasets, figures, tables, findings, claims) → object_edges → artifact_files + analysis_runs → branches. This mirrors the actual epistemic structure of scientific work.

### 2.5 Agent Architecture

| | BioMNI | ABA |
|---|---|---|
| **Pattern** | Single ReAct agent | Multi-agent: Guide + advisor council (Methodologist, Skeptic, Explorer, Stylist) |
| **State management** | In-memory message list per session | Persistent per-object conversation in PostgreSQL |
| **Tool selection** | LLM retriever dynamically filters from large catalog | Allowlisted, schema-validated tool registry (Tool Gateway) |
| **Orchestration** | LangGraph `StateGraph` | LangGraph (planned), Tool Gateway |
| **Self-critique** | Optional `self_critic` mode (LLM generates feedback after each solution) | Structured Skeptic advisor as a separate agent with a distinct mandate |

BioMNI's self-critic mode has the LLM generate a critique of its own output, inject it as a `HumanMessage`, and iterate. This is useful. But it remains single-agent self-reflection: the model is criticizing what it just said using the same priors and biases. ABA's Skeptic is a separate agent with a different system prompt whose *job* is to disagree, flag methodological concerns, and surface what could go wrong — without the social pressure of agreeing with the Guide. This mirrors how peer review actually works.

### 2.6 Tool Ecosystem

| | BioMNI | ABA |
|---|---|---|
| **Scope** | ~18 domain modules, 100s of pre-built functions | 3 today (list files, read CSV, run Python), designed to grow |
| **Curation** | Community-contributed, Apache 2.0 base + per-tool licenses | Institutional allowlist, no community sourcing |
| **External APIs** | UniProt, GnomAD, Ensembl, GWAS Catalog, ClinVar, DepMap, etc. | Institution's own LIMS, lakeFS, Nextflow submission |
| **Authorization** | None — any tool can do anything | Explicit approval gates for consequential actions |
| **Audit** | Execution steps visible in session UI | Append-only audit log: actor, tool, args hash, result hash, timestamp |

BioMNI's breadth here is a significant practical advantage for an individual researcher: it works out of the box on diverse tasks without setup. ABA's tool set is minimal now, but its **Tool Gateway design is architecturally superior for institutional use**: tools are allowlisted, schema-validated, idempotent where possible, and consequential actions require human approval. BioMNI explicitly warns users it runs "with full system privileges" — appropriate for a single researcher's laptop, inappropriate for multi-user institutional infrastructure.

### 2.7 Data Management and Provenance

| | BioMNI | ABA |
|---|---|---|
| **Storage** | Local flat files, ~11 GB data lake | MinIO (S3-compatible) + lakeFS (Git-like versioning) |
| **Versioning** | None | Git-like branches and commits on every dataset |
| **Branching** | None | Core feature: branch from figure, rerun, compare |
| **Provenance** | Code visible in session; no input-data versioning | Full lineage: git rev + input lakeFS ref + Nextflow session + output commit |
| **Reproducibility** | Within-session code audit only | Cross-session, deterministic: same ref + params = same output |

### 2.8 Conversation and Session Model

| | BioMNI | ABA |
|---|---|---|
| **Persistence** | None by default; in-process MemorySaver only | PostgreSQL: full history survives restart, per-object scoping |
| **Context scope** | Global — one flat conversation about anything | Per-entity — each dataset, figure, finding has its own thread |
| **Recovery** | No — restart = empty context | Yes — reconnect picks up exactly where you left off |
| **Multi-user** | No | Designed for 2–4 person team, per-user identity, shared project state |

### 2.9 Security and Governance

| | BioMNI | ABA |
|---|---|---|
| **Execution model** | Full system privileges, "not production-ready" | Sandboxed subprocess, Tool Gateway, approval gates |
| **Authentication** | None | SSO at web app, re-checked in Tool Gateway |
| **PHI/sensitive data** | No protections | PHI not in tool schemas, PBAC on MinIO |
| **Audit** | None | Append-only log, every tool call |
| **Data security** | API keys in env vars, pre-commit hooks | TLS everywhere, SSE/KMS on MinIO, OIDC/LDAP identity |

BioMNI's security stance is explicitly developer/researcher-local. It is not appropriate for multi-user institutional deployment without significant additional infrastructure.

### 2.10 Scientific Workflow and Compute

| | BioMNI | ABA |
|---|---|---|
| **Workflow model** | One task → one answer | Ingest → QC → figure → branch → compare → finding → claim |
| **HPC/cluster** | Not supported | Nextflow on Slurm, `-resume` for cost-efficient recomputation |
| **Long-running jobs** | Timeout kills at 10 min default | Async Nextflow submission, status polling |
| **Findings/claims** | No formal entity | First-class objects with advisor notes and evidence bundles |
| **Branching** | No | Core: branch from any figure, automatic recomputation |

### 2.11 Deployment Model

| | BioMNI | ABA |
|---|---|---|
| **Target** | Local workstation or public cloud | On-prem institutional server + HPC |
| **Setup** | `conda activate biomni_e1; pip install biomni` | 4–5 services: web, Postgres, lakeFS, MinIO, job node |
| **Data download** | 11 GB auto-download from AWS S3 | Institutional data stays on-prem |
| **Web UI** | Gradio (local port or shareable link) | Custom React app (Vite + TypeScript) |
| **Maturity** | Published, installable, live web demo | MVP webapp done; full platform 7–9 person-months ahead |

---

## 3. Synthesis: Advantages and Disadvantages

### BioMNI: Where It Wins

**Breadth and immediate utility.** BioMNI's pre-built tool library spanning 18 biomedical domains is its greatest strength. A researcher can immediately run GWAS causal gene analysis, CRISPR screen planning, ADMET prediction, cell type annotation, pathway enrichment, and structure-function queries — all within one agent, without building any infrastructure. For exploratory research, hypothesis generation, and early-stage literature/database interrogation, BioMNI provides immediate practical value.

**Deployment simplicity.** `conda activate biomni_e1; pip install biomni` is a far lower barrier than standing up lakeFS, MinIO, PostgreSQL, Nextflow, and a custom web app. A single researcher can get productive in hours.

**Session-level code transparency.** The Gradio UI exposes every code block that ran, making the reasoning chain visible within a session. Users can audit exactly what the agent did, step by step.

**Open ecosystem.** Community-driven, with mechanisms for contributing tools and know-how. The Biomni-R0 fine-tuned model and Biomni-Eval1 benchmark position it as a platform for AI research in biomedicine.

**Model agnosticism.** Claude, GPT-4, Gemini, Bedrock, Groq, local SGLang — BioMNI works with any of them.

### BioMNI: Where It Falls Short

**No cross-session provenance.** While code is visible within a session, there is no versioning of input data. If you close the session and reopen, the code history is gone. There is no way to reproduce a figure from a previous session deterministically, because the input data version at that point is unrecorded.

**No data versioning or branching.** The "exclude S4, rerun, compare" workflow — standard in single-cell QC — has no natural representation. You'd have to prompt it fresh each time with no guarantee of consistent results.

**Single flat context.** All conversations accumulate in one global stream. There's no concept of "open Figure 3 and see its conversation thread." After a multi-month project, the chat history is unnavigable.

**No formal entity hierarchy.** BioMNI produces text and images, but no persistent `finding` objects, no typed relationships between results, no `claim` entities. The epistemic structure of scientific work (hypothesis → evidence → finding → conclusion) is not representable.

**Not suitable for institutional deployment.** Full system privileges, no auth, no audit trail, 11 GB download from external servers — none of this is acceptable at an institution with PHI, data governance requirements, or IRB obligations.

**Single-agent self-critique.** Asking the LLM to reflect on its own output is subject to the same biases as the original response. A separate adversarial agent is structurally different.

### ABA: Where It Wins

**Provenance as a first-class citizen.** Every figure links to its exact input lakeFS commit, Nextflow session, git revision, and parameters. Any result is reproducible to the bit.

**Branching and recomputation.** Creating a semantic branch and automatically rerunning the relevant pipeline with `-resume` is a genuinely novel UX for bioinformatics. It makes sensitivity analysis low-friction.

**Entity-centric analytical threads.** Conversations scoped to figures and findings, persisted and recoverable, is the right information architecture for multi-month projects.

**Multi-agent skepticism.** The Skeptic/Methodologist/Explorer/Stylist council is a principled approach to surfacing scientific concerns without burdening the Guide with adversarial thinking.

**Institutional security posture.** Approval gates, audit trails, PBAC, SSO, no external data transfer — designed for the environments where most funded science happens.

**HPC/Slurm integration.** Real single-cell analysis at scale needs cluster compute. BioMNI's 10-minute timeout is a non-starter for SCVI training or trajectory analysis.

### ABA: Where It Falls Short

**Not built yet.** The full platform is 7–9 person-months of engineering. The MVP webapp exists and works, but the lakeFS/MinIO/Nextflow/advisor layer is ahead.

**Narrow domain.** Designed around single-cell omics workflows. BioMNI spans genomics, pharmacology, pathology, physiology, microbiology, and more.

**Build cost.** 4–5 on-prem services to stand up before any analysis can happen.

**No pre-built scientific tool library.** Getting to BioMNI's breadth of domain-specific tools requires substantial tool development within the Tool Gateway.

---

## 4. Can BioMNI Serve as a Starting Point for ABA?

This is the most practically important question. The answer is: **partially, and selectively**. BioMNI provides usable components for some layers of ABA, but its core architectural choices conflict with ABA's requirements in ways that cannot be patched incrementally.

### What BioMNI Contributes Directly

**The tool library.** BioMNI's 18 domain modules are the most immediately reusable asset. The functions in `genomics.py`, `immunology.py`, `cell_biology.py`, etc., are standalone Python functions that take file paths and parameters and return structured results. These can be imported into ABA's Tool Gateway as allowlisted tools with minimal modification — they already follow the pattern of "accept inputs, return results, write output files." ABA would need to wrap them in schema validation and authorization checks, but the scientific logic is there. This is probably 6–12 months of tool development that doesn't need to be rebuilt.

**The data lake catalog.** The 80+ reference datasets in `env_desc.py` (DepMap, MSigDB, GWAS catalog, miRTarBase, etc.) and the logic for downloading and managing them represent significant curation work. ABA's Tool Gateway could expose these datasets via lakeFS-managed copies rather than raw flat files, preserving the scientific content while adding versioning. The catalog metadata (descriptions, licensing flags) can be reused directly.

**The Know-How Library.** BioMNI's RAG-retrieved protocol documents are directly useful for ABA's advisor agents, particularly the Methodologist, which needs domain knowledge to evaluate whether an analysis is appropriate for a given data type.

**The LLM routing layer.** BioMNI's `llm.py` factory handles Claude, OpenAI, Gemini, Bedrock, Groq, and local models through a clean abstraction. ABA would benefit from this rather than being locked into Claude-only.

**The `ToolRetriever`.** ABA's Tool Gateway will need dynamic tool selection as the catalog grows. BioMNI's retriever — which uses the LLM to match a query against tool descriptions — is a working implementation of exactly this.

**The Biomni-R0 model.** The RL fine-tuned reasoning model, trained specifically on biological tool-use tasks, could improve ABA's Guide agent quality compared to an off-the-shelf Claude prompt. It could serve as an alternative reasoning backbone, with Claude handling the higher-level planning and Biomni-R0 handling step-by-step execution.

### Where BioMNI's Architecture Conflicts with ABA's Requirements

**The execution model.** BioMNI's `<execute>...</execute>` approach — LLM emits raw code, backend runs it with full system privileges — is incompatible with ABA's Tool Gateway design. ABA requires tools to be pre-declared, schema-validated, and allowlisted; the agent should not be able to run arbitrary code against institutional data. BioMNI's `run_python_repl` is the opposite of this. ABA can retain a sandboxed `run_python` for exploratory analysis on the local dataset, but replacing it with BioMNI's open-ended REPL would undermine the governance model.

**The session/persistence model.** BioMNI's in-memory `MemorySaver` is fundamentally incompatible with ABA's per-object PostgreSQL-backed conversation threads. The LangGraph state structure would need to be rebuilt around database-persisted state with object-scoped thread IDs, rather than the hardcoded `thread_id: 42` BioMNI uses.

**The data management layer.** BioMNI's flat-file data lake has no versioning, no branching, no commit model. Adopting it for ABA would mean abandoning lakeFS/MinIO, which is the core of ABA's reproducibility guarantee. The datasets can be moved into lakeFS, but the BioMNI storage layer itself cannot.

**The agent focus model.** BioMNI's single global chat context is architecturally opposed to ABA's per-entity scoped threads. There is no concept in BioMNI of "current focused object" — this would need to be designed from scratch.

**The entity model.** BioMNI has no `figure`, `finding`, `claim`, or `branch` entity. Adding these is not a small modification — it requires a new data model (PostgreSQL schema), new API endpoints, new frontend components, and new agent tooling. BioMNI provides none of this scaffolding.

### Recommended Integration Strategy

Rather than forking BioMNI and trying to reshape it into ABA, the practical approach is:

1. **Use BioMNI's tool implementations as a library, not as a framework.** Import domain-specific functions from `biomni.tool.*` into ABA's Tool Gateway as pre-approved callable tools. Wrap them in schema validation, authorization, and audit logging. This gives ABA BioMNI's 6–12 months of domain tool development immediately.

2. **Adopt BioMNI's data lake catalog.** Migrate the reference datasets into lakeFS-managed MinIO buckets. Reuse the `env_desc.py` metadata catalog. This gives ABA's agents awareness of the same rich set of reference data, with versioning added on top.

3. **Port BioMNI's Know-How documents** into ABA's advisor knowledge base for the Methodologist and Skeptic agents.

4. **Use BioMNI's `ToolRetriever`** as the basis for ABA's Tool Gateway's dynamic tool selection module, extended with institution-specific tool metadata.

5. **Build ABA's own agent loop, persistence, entity model, and security layer from scratch**, using BioMNI's `react.py` as a reference for LangGraph patterns but not as a base to fork.

The key discipline is: treat BioMNI as a **scientific content provider** (tools, datasets, protocols) and ABA as a **platform** (governance, persistence, provenance, UX). These are complementary, not competing.

---

## 5. Positioning

These platforms are **not direct competitors** — they are designed for different stages and scales of research.

**BioMNI is a researcher's Swiss Army knife**: useful for individual exploration, hypothesis generation, quick database queries, and one-off analyses across a wide biomedical domain. It excels at the early, unstructured phase of a project.

**ABA is a project's analytical spine**: useful for sustained multi-month analysis where results need to be reproducible, findings need provenance, and a small team needs to share and build on each other's work.

A plausible integration: a researcher uses BioMNI to explore hypotheses and identify relevant databases or analysis approaches during discovery, then conducts confirmatory analysis inside ABA with full branching, provenance, and skeptical review before promoting results to findings. The tool libraries can be shared.

The deeper question is whether ABA's entity-centric, provenance-first design is the right frame for AI-assisted science. BioMNI's approach implicitly treats AI as a smarter search engine — you ask, it answers, you decide what to trust. ABA's approach treats AI as a collaborator embedded in a scientific process — the AI's outputs are first-class epistemic objects that need lineage, review, and institutional accountability. For science that ends up in Nature, a clinical trial, or a regulatory submission, the latter framing is more defensible.

---

*Report prepared 2026-05-18. BioMNI analyzed from commit at time of clone; ABA analyzed from current main branch.*
