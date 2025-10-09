# Agentic Coding Guidelines

> A concise playbook for organizing agent-driven development, task orchestration, and engineering best practices for Python (FastAPI) backend projects.

---

## Purpose

Make agentic development predictable, auditable, and fast. Provide clear roles, file conventions, task lifecycle rules, testing and CI expectations, and templates so multiple agents (human or automated) can collaborate safely and efficiently.

---

## Key Roles

* **Architect** — creates feature plans and high-level designs in `.agents/plan.md`. Defines scope, acceptance criteria, success metrics, security/privacy concerns, and required integrations.
* **Orchestrator** — converts plans into an ordered work list (`.agents/work_list.md`), assigns tasks, enforces dependencies, and advances tasks through states.
* **Worker Agent / Developer** — picks tasks, implements code, writes tests, updates the work list and opens PRs.
* **QA Agent** — validates acceptance criteria, runs regression/integration tests, performs human or automated review of behavior.
* **Debug Agent** — focused on reproducing and triaging issues using the debug mode and producing clear R&D tasks when needed.

> Each agent must always write concise, timestamped updates to `.agents/work_list.md` for traceability.

---

## Repository metadata & files (convention)

```
/.agents/
  plan.md          # single-source-of-truth for current feature plans
  work_list.md     # Jira-like ticket list (task state, owner, timestamps, comments)
/docs/
  developer_guide.md
/tests/
src/ (or app/)
pyproject.toml
alembic/
.gitignore
.pre-commit-config.yaml

```

---

## `.agents/plan.md` — minimum template

```md
# Plan: <short-title>
- id: PLAN-<YYMM>-<n>
- owner: <Architect name>
- created_at: <ISO datetime>

## Overview
A 2–3 sentence summary of the feature and why it matters.

## Goals & Success Criteria
- measurables: e.g., API latency < 200ms for common endpoints, 95% unit test pass, etc.

## User stories (priority order)
- As a <role>, I want <capability> so that <value>.

## Acceptance Criteria (explicit)
- Given ..., When ..., Then ...  (BDD style)

## Dependencies & Risks
- external services, migrations, infra changes, security concerns

## Estimated scope (MVP)
- a small list of tasks that make the MVP

## Approval
- approved_by: <name>
- approved_at: <ISO datetime>
```

---

## `.agents/work_list.md` — schema & rules

Store tasks as a human-friendly table or simple YAML-like bullets. Keep it deterministic and machine-parseable.

**Recommended fields (per task):**

* `id` — unique (e.g., T-001)
* `type` — feature | bug | chore | rnd | hotfix
* `title`
* `description`
* `acceptance_criteria` (BDD: Given/When/Then)
* `assignee` — agent name
* `state` — backlog | ready | in-progress | blocked | in-rnd | in-review | qa | done | closed
* `priority` — P0|P1|P2
* `estimate_hours` (optional)
* `created_at`, `started_at`, `updated_at`, `ended_at`
* `blocked_by` — list of task ids
* `comments` — chronological log (timestamp + agent + short note)
* `reassigned` — history of reassignments
* `tags`

**Example (markdown table):**

| id    | type    | title                 | assignee   | state       | priority | updated_at        |
| ----- | ------- | --------------------- | ---------- | ----------- | -------- | ----------------- |
| T-001 | feature | Create auth endpoints | agent-auth | in-progress | P0       | 2025-10-01T13:00Z |

**Rules:**

* Every task must include at least one acceptance criteria in BDD form.
* Tasks should be *small* and independent; aim for <= 1–2 days of focused work.
* If a task exceeds the estimate, the agent must create smaller subtasks or an R&D task.
* If the current assignee does not update the task for **48 hours (2 days)**, any other agent may pick it up — but must add an explicit comment documenting why they reassigned it.
* Do **not** close tasks that were only partially completed; either leave open or mark `blocked` with a clear reason.

---

## Task lifecycle & hand-offs

1. **Plan** — Architect writes `.agents/plan.md` and gets approval.
2. **Backlog → Ready** — Orchestrator breaks plan into tasks and marks tasks `ready`.
3. **Pick & Start** — Worker agent claims a task (adds `assignee`, `started_at`) and moves it to `in-progress`.
4. **Implementation** — Write code, tests, documentation. Use debug mode for issues. Update `work_list.md` frequently.
5. **Code Review** — Open PR to `develop` (see Branching rules). PR must reference task id and include the BDD acceptance tests.
6. **QA** — QA agent validates acceptance criteria and test coverage.
7. **Done → Closed** — After tests & docs pass and PR merged, mark `done` and then `closed` after release verification.

**Blocking handling:** create a new `type: rnd` task with a clear research question, success criteria, owner and timebox (e.g., 3 working days). Link the R&D task in `blocked_by`.

---

## Branching, commits & PRs

* **Branching:** Follow the repo-level branching model. If repository uses `develop` as integration branch, create feature branches from `develop`. If the repo is trunk-based, create feature branches from `main`. (If your team has a rule to always branch from `main`, keep that rule; include it in `docs/developer_guide.md`.)
* **Branch naming:**

  * `feature/<short>-<T-001>`
  * `bugfix/<short>-<T-002>`
  * `hotfix/<short>`
* **Commit message format:**

  * `type(scope): Short summary` e.g., `feat(auth): add refresh token endpoint (T-001)`
  * types: feat, fix, docs, style, refactor, perf, test, chore
* **Pull Requests:**

  * Target branch: `develop` (or `main` if trunk-based).
  * PR template should include: summary, linked task id(s), testing instructions, migration steps, security notes, screenshots (if UI), and checkboxes for the completion checklist.

---

## Definition of Done (DoD) — checklist before closing a task

* [ ] Code compiles and all tests pass locally and in CI.
* [ ] Unit tests + integration tests written (BDD scenarios captured).
* [ ] Coverage maintained (team target, default 90% — see NOTES below).
* [ ] Linting/formatting (black, ruff/flake) applied; pre-commit hooks run.
* [ ] Docs updated (`docs/developer_guide.md`, changelog if needed).
* [ ] Migration scripts (alembic) added and reviewed (if DB changes).
* [ ] PR approved by at least one reviewer (or as per team rules).
* [ ] Deployment steps or runbook updated (if relevant).

**NOTE on coverage:** 90% is a strict target — consider focusing 90% coverage on critical modules and business logic if achieving 90% across the entire repo is not practical. Record coverage gaps and rationale in the PR.

---

## Testing & BDD

* Use **BDD** for acceptance criteria: `Given` / `When` / `Then`.
* Tests to write:

  * Unit tests for pure functions and business logic.
  * Integration tests for DB interaction and services (use `pytest`, `httpx` for HTTP tests).
  * End-to-end or contract tests for critical flows.
* Test data: generate deterministic fixtures using `faker` and factory patterns.
* Use coverage measurement (coverage.py) in CI and fail builds if coverage drops below configured threshold.

**BDD example:**

```gherkin
Scenario: Refresh token returns new access token
  Given a valid refresh token
  When the client calls POST /auth/refresh
  Then the response status is 200 and body contains `access_token` and `expires_in`.
```

---

## Coding standards & tools

* Language: Python 3.11+ (follow repo policy).
* Frameworks & libraries (recommended): `fastapi`, `sqlmodel`, `alembic`, `pytest`, `httpx`, `pydanticai`, `FastA2A` (if using agentic architecture), `qdrant`/`neo4j`/`postgresql` per requirement.
* Package manager: `uv` is an acceptable modern choice — add installation instructions in `docs/developer_guide.md` if used.
* Formatting & linting: `black`, `ruff`, `isort`. Enforce via `pre-commit`.
* Type hints: use `mypy` or ruff’s type checks; keep public function signatures typed.
* Docstrings: module + function docstrings for public APIs. Keep architecture notes in `docs/` not chat logs.

---

## CI / CD (example minimal pipeline)

1. Checkout + setup Python (+ `uv` or chosen package manager)
2. Install dependencies
3. Run linting & formatting checks
4. Run unit tests & coverage
5. Run integration tests (use a test DB / docker-compose / ephemeral infra)
6. Build artifact (Docker image)
7. Optional: run migration dry-run and smoke tests on staging
8. Deploy to staging (protected) → run post-deploy tests

Include automated gating: PRs must be green in CI and pass security scans before merging.

---

## Debugging & observability

* Use structured logging and include correlation ids in requests to trace flows.
* Agents must attach steps to reproduce, logs, and a minimal reproduction case in the task description.
* Use a `debug` mode for dev agents; capture full traces and keep logs for at least X days (team policy).

---

## When to create R&D tasks

* Unknown root cause after 2 attempted reproductions.
* New integration with third-party service where behavior is unclear.
* Performance investigations needing benchmarking.

R&D tasks should be timeboxed and have a clear deliverable: PoC, benchmark report, or proposed change with risk analysis.

---

## Developer guide structure (`docs/developer_guide.md`)

* Getting started (prereqs, `uv` install, env setup)
* Local dev: run services, run tests, add a new migration
* Branching & PR process
* How to write BDD tests (examples)
* How to use `.agents/*` files
* Debugging runbook
* Deployment & rollback steps

---

## Templates (short)

**PR description template**

```
Title: [T-001] Short summary
Linked tasks: T-001
Summary: 1–2 sentences
Testing steps: how to run locally
Checklist:
- [ ] Tests added
- [ ] Lint passed
- [ ] Docs updated
```

**Task BDD template**

```
Given <context>
When <action>
Then <expected result>
```

---

## Collaboration & etiquette

* Write short, descriptive comments in `work_list.md` with timestamps.
* Keep messages factual — no speculative decisions without an R&D task.
* If you reassign a task, record the reason and the timebox for ownership.

---

## Appendix: FastAPI-specific notes

* Use `sqlmodel` and `alembic` for schema + migrations.
* Use dependency injection for DB/session management.
* Keep endpoints small; prefer service layer for business logic to make unit testing easy.
* Document APIs with OpenAPI (swagger, redoc) and include example requests/responses.

---

## Final notes

* This document is a living artifact. Architects and Orchestrators should keep it up to date in `docs/developer_guide.md`.
* If any team-wide rule conflicts with this guide, update the guide and call out the exception in `docs/developer_guide.md`.

---

*End of guide — v1.1*
