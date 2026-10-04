# Local WorkOS — AI engineering handoff

Documentation date: 2026-10-04. Product version: 1.11.0. This document covers scoped source tools, project experience, portable deployment, investor returns, semantic planning, persistent revisions, versioned output archiving and cancellable durable jobs.

Read [PRD](<PRD.md>) first. It distinguishes implemented features, verified behavior and proposed additions. Earlier [README](<../README.md>), [architecture](<ARCHITECTURE.md>) and [testing](<TESTING.md>) descriptions can lag current code; do not remove password auth, editable PPTX or four valuation methods based on historical wording.

## Source vs install vs data
- The repository root is editable development source and the Git working tree.
- Windows installation is `%LOCALAPPDATA%/Programs/LocalWorkOS/<version>/<build>`. Changes in a source checkout are not hot-reloaded there. Preserve/update the existing startup shortcut when changing the install version. Portable distributions use their own isolated bundled Python; see [deployment](DEPLOYMENT.md).
- Runtime personal/demo SQLite, authentication and logs are under `%LOCALAPPDATA%/LocalWorkOS`, never in source control.
- OneDrive is an optional JSON/text mirror and backup target, not the live SQLite/WAL database or a bidirectional multi-master store.
- Remote access reaches one authoritative host through its dedicated tunnel; keep that host running and online. Do not attach divergent databases/sessions to the same tunnel from two machines.
- This public repository does not contain deployment-machine paths, business records, passwords, sessions, API keys or Cloudflare credentials.

## Files to read

| File | Purpose |
|---|---|
| [server](<../workos/server.py>) | HTTP routes, authentication, Host/Origin/CSRF, model adapters, upload/export |
| [model catalog](<../workos/model_catalog.py>) | Reviewed identities, canonical service/model resolution, grouped nonsecret catalog and honest statuses |
| [custom models](<../workos/custom_models.py>) | Private persistent endpoint/model definitions, stable independent identities, process-memory keys only |
| [clarifications](<../workos/clarifications.py>) | Bounded needs_input questions, clear financial aliases/numeric strings and recognized calculation-input guidance; no formula changes |
| [usage guide](<USAGE_GUIDE.md>) | Natural-language examples and actual work coverage, also served through authenticated /api/guidance |
| [jobs](<../workos/jobs.py>) | Durable jobs, frozen inputs, restart recovery, idempotent save |
| [cancellation](<../workos/cancellation.py>) | Workspace-scoped operation tokens, bounded pre-arrival cancellation markers, guarded commits and completed-action receipts |
| [AI progress](<../workos/ai_progress.py>) | Actual stage events, sanitized public execution records, task-type ETA ranges and completed-stage progress |
| [conversations](<../workos/conversations.py>) | Scoped persistent rounds, bounded successful-turn context, artifact snapshots and signatures |
| [project artifacts](<../workos/project_artifacts.py>) | Runtime-only folder matching/binding, immutable exports, hash manifests and authenticated downloads |
| [quality](<../workos/quality.py>) | Acceptance checks, critic schema and repair brief; no truth certification |
| [evidence harness](<../workos/evidence_harness.py>) | Provider-neutral bounded source tools, actual read coverage, strict final scope and repaired-draft validation |
| [project experience](<../workos/project_experience.py>) | Project/workspace scoped persistent preferences, confirmation, provenance, audit and backup; not weight training |
| [industry playbooks](<../workos/industry_playbooks.py>) | Generic task know-how conditional on user scope; no private historical facts |
| [investor returns](<../workos/investor_returns.py>) / [Excel verification](<../workos/return_excel.py>) | Investor cash-flow MOC/XIRR, real Excel recalculation and independent checking |
| [portable workbook author](<../workos/portable_return_workbook.py>) | Formula-preserving openpyxl author when approved artifact Node runtime is unavailable |
| [DSH adapter](<../workos/dsh_harness.py>) | Completion/exit checks and disposable scoped tool execution |
| [harness contract](<HARNESS.md>) | Quality modes, evidence ledger, compatibility and limitations |
| [store](<../workos/store.py>) | Allowed fields/defaults/type checks, associations, SQLite transactions, backups |
| [engine](<../workos/engine.py>) | Safe text extraction, chunks, strict local lexical retrieval, bounded selected-source model context and legacy calculations |
| [valuation](<../workos/valuation.py>) | Four deterministic methods and typed assumption schemas |
| [exports](<../workos/exports.py>) | DOCX, dynamic Word page references, formula XLSX and editable PPTX |
| [deck blocks](<../workos/deck_blocks.py>) | Explicit Markdown tables and validated chart JSON, no execution or missing-value inference |
| [agent](<../workos/agent.py>) | Bounded record, project, explicit-source workflow and selected-meeting actions, at most six steps |
| [password auth](<../workos/password_auth.py>) | Current public username login, local salted hashes, persistent opaque sessions, throttling |
| [Access auth](<../workos/access_auth.py>) | Optional legacy JWT verification; not required for password mode |
| [sync](<../workos/sync.py>) | Atomic outward mirror, previous revisions and empty-database restore |
| [memory](<../workos/memory.py>) | Read-only memory import/filtering and model-call privacy boundary |
| [main UI](<../web/app.js>) / [valuation UI](<../web/valuation.js>) | State, research/meeting/model workflow and export buttons |
| [API client](<../web/api-client.js>) | Same-origin requests; one bounded recovery for a write rejected with csrf_expired; shared refresh and workspace/abort guards |
| [auth UI](<../web/auth.js>) / [login](<../web/login.html>) | Same-origin login and local-only password setup |
| [launcher](<../launch.py>) | Stable startup, saved non-secret WORKOS settings and approved Office runtime paths |
| [installer](<../tools/install.ps1>) / [public starter](<../tools/start_public.py>) | Separate Windows deployment and idempotent dedicated connector startup |
| [portable packager](<../tools/package_windows.py>) / [portable verification](<../tools/verify_portable.py>) | Clean-HEAD whitelist, pinned embedded runtime, offline wheel dependencies, delivered-launcher smoke |
| [tests](<../tests/>) | Synthetic, isolated HTTP/SQLite, exports, model formulas and authentication regressions |

## Full development dependencies
The full Windows portable package needs no globally installed Python for its core and Word/PPT/XLSX exports. Model providers remain separately configured, and investor XIRR verification requires installed Microsoft Excel. Package manifests identify exact source revision and runtime/dependency hashes; preview builds are not release packages.

The original [requirements](<../requirements.txt>) only declare the historical Word dependency. Use [development dependencies](<../requirements-development.txt>) for the full current test/export suite; CI now installs this full file. A locally passing suite is not proof of a successful remote CI run.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-development.txt
$env:PYTHONPATH = "$PWD/vendor"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:WORKOS_SYNC_ROOT = ""
$env:WORKOS_MEMORY_ROOT = ""
$env:WORKOS_PUBLIC_ORIGIN = ""
$env:WORKOS_PUBLIC_AUTH_MODE = "access"
.venv/Scripts/python.exe -m unittest discover -s tests -q
```

Core HTTP/SQLite and password mode use stdlib. The [vendor PDF parser](<../vendor/pypdf/>) is included. Model routes require the corresponding authorized DSH or compatible service; their credentials are not included. The optional meeting-PDF route requires the approved LibreOfficeKit/Node setup. Investor-return workbook authoring can use the full bundle's openpyxl author without Node/artifact-tool, while actual return recalculation still requires host Microsoft Excel. Do not search/install substitute Office renderers as an unreviewed fallback.

## Safe development
Do not use the launcher to create a development instance on a deployment host: it can reuse production 18866 and restore saved public/sync settings. Start the server directly on a free alternate port and an isolated temporary data directory.

```powershell
$testData = Join-Path $env:TEMP ("WorkOS-test-" + [guid]::NewGuid().ToString("N"))
.venv/Scripts/python.exe -m workos.server --port 18867 --data-dir $testData
```

Use synthetic data and mocked model responses. If the port is occupied, choose another rather than stopping unrelated processes. Browser scripts use WORKOS_E2E_URL or WORKOS_TEST_URL according to the script; read [TESTING](<TESTING.md>) before execution. For Host/security tests use raw HTTP headers (http.client); some fetch clients rewrite the reserved Host header.

## Verified limitations to preserve in requirements
Project experience is separate from original imported personal memory. Only explicitly durable user preferences and appropriately confirmed scoped entries can enter an external task context. Audit events alone do not become facts. Public execution records contain stages and tool actions, never private chain of thought. `/api/harness` and `/api/system/readiness` inspect capabilities without making model calls; availability is not upstream model-identity certification.

1. New research uploads retain immutable original bytes in data_dir/originals/{workspace}/{sha256}, with authenticated downloads and OneDrive copying/recovery. Older records cannot recover original bytes without reupload. JSON backups exclude original bytes; OneDrive mirrors include them. No automatic scanned-PDF OCR; Excel cached values may be stale.
2. Saved valuation deliverables persist method/assumptions/result; Store recomputes result on save/update/restore. UI restores a model for continued calculation/scenarios, and saved-record XLSX export regenerates linked formulas. Older body-only snapshots remain readable but cannot be assumed reloadable. Imported complex Excel is extracted for source-based review; it is not automatically rewritten or recalculated.
3. Citations locate extracted excerpts; [S#] number validation is not semantic support or fact verification. Extracted text remains editable even though uploaded originals are immutable; citations have not been changed into immutable version anchors.
4. The bounded assistant can organize projects, create subtasks, run explicit-source recipes and draft selected meeting minutes. It does not execute arbitrary files/shell, send email or modify an imported workbook. Recipe prompts are execution rules, not training of model weights.
5. Sync has no live bidirectional merge, periodic pull or stale-file reconciliation; last_sync is a local write time, not proof of cloud upload.
6. A full IC story, templates, three-statement integration, debt tranches/interim dividends/dilution are not implied by an editable file export.
7. Global paste now preserves all editable fields. Keep the browser regression: normal input/textarea/contenteditable paste must not create a document. The research page has one composer with explicit ask/action modes, not duplicated forms.

## Feature modification patterns
- Context is server-owned: reject client role history and private payload keys. Validate workspace/project/purpose/exact source-set and object metadata before a provider call. Queue captures conversation signatures and parent identity; retries preserve failed attempts with separate internal attempt turn IDs. A final saved workflow wins a later cancellation so successful context and archiving can finish. Do not complete an outer action assistant merely because one nested tool saved a record.
- Keep current manual content authoritative for revision bases. Meetings compare the original record again under token/store guards before save; workflow revisions create a child and reject a parent changed during generation. Expose context truncation. Failed/cancelled rounds do not enter successful model context. Record-only JSON backups and OneDrive record mirrors exclude conversations; conversation JSON backup and release database backup are separate.
- Stage logs describe observable actions, never raw prompts/tool arguments/credentials or private model reasoning. ETA is a preset task-type range, not a measured SLA. Use an indeterminate bar when no real stage percentage exists. Polling updates dedicated UI regions without clearing inputs; refresh recovers active operations without resending model requests.
- Output roots/aliases/bindings and export receipts live only in runtime JSON/SQLite. Match folder names within bounded scans, requiring explicit local selection for ambiguity. Confine writes/downloads to configured roots or managed storage; reject traversal, links, junctions and unknown reparse points. Only documented Microsoft Cloud Files tags are allowed as non-link OneDrive placeholders. Immutable version directories and hash manifests retain old bytes. Archive failure preserves the saved record; retry exports separately.
- Data additions: update [Store](<../workos/store.py>) schema/defaults/validation and test older records plus backup/restore. Meeting structured fields must have valid dimensions/indices; changing only summary intentionally invalidates stale structures.
- [Organization](<../workos/organization.py>) derives task_group/material_type/version_family/version_label locally from bounded filename/content evidence. Store runs it on create/update/startup/restore and custom subtask changes. Explicit task_group is a manual override; clearing it resumes automation. No new collection was added, so old version-1 backup topology remains valid. Never include memory or execute source instructions. Version families are project-scoped; recent import order is not semantic approval or finality.
- [Attachments](<../workos/attachments.py>) validates workspace and hash-only paths, verifies hashes on download/restore and keeps original bytes independent of edited text. Mirror sync deduplicates hashes and caches stat signatures to skip unchanged immutable originals during normal saves. Missing mirror originals are retried on subsequent sync; status counts missing bytes by workspace.
- Model edits: keep arithmetic in [valuation](<../workos/valuation.py>), then update exporter/UI/tests. Validate unit/period/ranges/missing/finite values. Actually recalculate edited workbooks and compare against Python, not only inspect formula strings. Snapshot columns remain labeled as original input results.
- NL task planning and financial parsing use the selected central catalog choice, including registered compatible connections. New UI AI work defaults to WorkBuddy DeepSeek V4.1 Flash; preserve saved preferences and legacy API defaults. A local bridge is not proof of offline inference, entitlement or upstream identity. Provider failures must remain distinct from missing conditions.
- PPT native tables/charts use [documented data blocks](<pptx-export.md>); preserve all rows/content or reject excessive output explicitly. Word TOC uses real bookmarked PAGEREF fields, not guessed page offsets.
- Preserve regression tests for original-file persistence, automatic version grouping, workflow source coverage, saved typed-model reload and formula XLSX export. Immutable citation anchors remain pending: extracted text can still change.

## Authentication and privacy contracts
Use [password deployment guide](<password-public.md>) for password mode and [Access guide](<cloudflare-access.md>) only when that mode is chosen.
Keep authentication on all remote workspace APIs/downloads, session CSRF, Host/Origin checks, nonce/cookie binding, fresh-before-submit challenges and IP-based throttling (not strict IP binding), login rate limits and local-only setup. Forwarded localhost must not become an anonymous bypass. Passwords remain salted hashes, session IDs hashed and local; never export them into backups, mirrors or source.
The source default closes public access; a specific deployment opts into HTTPS password mode. Single account is not enterprise RBAC. Localhost bypass is deliberate for the trusted host owner.
Keep kind=memory out of every model call. Never extract browser cookies or place tokens into docs, commands or repository remotes. Use only existing saved credentials; do not open auth popups automatically.

## Publishing safely
When the user requests publication, that instruction authorizes the release below; do not request the same approval again. Back up business records, inspect the diff and run isolated tests. Do not delete/move runtime data, authentication files or shared tunnel configuration. Installation copies tracked source into a separate stable directory and records its commit in the running health endpoint.

```powershell
# Source checkout; point to a verified existing Python with the needed dependencies
./tools/release.ps1 -Python "<existing-python-path>" -CommitMessage "Describe reviewed change" -Publish -Push
```

Stop/restart the current app through its supported launcher when releasing. Use [public starter](<../tools/start_public.py>) only with the deployment-owned dedicated UUID/config and password-protected origin. New code on OneDrive or GitHub is not proof that the running service was updated. New machines must provision authorized local runtime independently and must not run a second conflicting write authority.
The SOP runs isolated Python, Markdown, API transport, AI run/composer, authentication-client and Chrome/CDP tests; checks public-source privacy/history; commits reviewed source; backs up committed SQLite including WAL; installs and retargets only an existing authorized startup shortcut; restarts the app; verifies local version/commit, mirror status, public login and anonymous API denial; then pushes and compares remote SHA. Authentication/session/CSRF/logout persistence are exercised with synthetic credentials in tests. Read-only production probes do not obtain real cookies/passwords and do not claim an actual authenticated human login. The tunnel starter reuses the configured dedicated connector. Do not print credentials or cookie/CSRF values.

## Work recipes and coverage
[workflows](<../workos/workflows.py>) contains research brief, DD, IC Memo, discussion material, technology explainer, agreement review, interview preparation, expert-network request, email, project update, version comparison, model review and tabular meeting synthesis. Home planning uses the selected model to understand the task without reading source documents; an explicit recipe can bypass it. Generation uses the selected provider and explicit sources. Every selected document receives a bounded excerpt and a coverage entry. Missing ordinary inputs request clarification; memory/cross-project scope violations and invalid model output reject before saving. Local excerpt mode must never silently call an external model. Preserve requested audience, language, page count and purpose. Separate source facts, management forecasts, independent expert views and team assumptions.

Synchronous workflow requests use a workspace-scoped request_id and bounded in-process result cache; same-payload retries reuse a saved draft, while conflicting payload reuse rejects. UI work uses durable jobs with frozen inputs and generation_id recovery, not that cache as a permanent ledger. It retains identity across network retries and creates a new request when requirements/scope change or a clarification is answered. Provider calls have bounded deadlines; DSH execution is serialized and cancellation is checked before launch. On restart, unfinished work is marked interrupted and must be explicitly retried, with scope/provider/context validation.

Future priorities are immutable citation anchors, evidence/issue reconciliation, full template-based presentation production and imported-workbook editing/recalculation. These are not implied by current exports. Update PRD and handoff with implemented/pending status and verification evidence on every significant delivery; local tests do not prove remote CI or visual fidelity of every generated artifact.

## Conversational inputs and independent model connections (1.9.0)

- `needs_input` is a continuation outcome with message, up to three questions `{id,label,hint,options:[{value,label}]}`, known conditions, missing field paths and optional method/current assumptions. Model understanding can ask about a genuinely ambiguous goal; deterministic preflights can ask for selected materials, a transcript or financial conditions. Users can answer with ordinary text. Authentication, stale scope, cancellation and invalid provider output are not blanket-converted into questions.
- `ClarificationRequired` is the only typed exception intended for normal continuation. `financial_validation_clarification` recognizes specific deterministic input messages; it must not catch arbitrary ValueError subclasses or providers. `normalize_assumptions` copies explicit numeric strings, percentages in ratio fields and multiples, preserving unknown fields for explicit mapping/rejection. It never guesses currency, scale, dates, absent amounts or decimal percentage meaning.
- Conversation rounds may have status `needs_input`; they retain the question/current assumption snapshot without representing a successful artifact. Context remains limited to the same workspace/project/purpose/source set and metadata. A stopped/failed round never supplies successful context. Follow-up requests use new operation/job IDs; immutable already-saved results keep winning late cancellation races.
- 1.9.1 preserves compact completed-action receipts in the persisted assistant context when an action turn ends in clarification. Follow-up models receive the saved record references and can avoid repeating an earlier save. Keep receipts scoped/bounded, without complete tool arguments or source content, and preserve completed commits; do not rely only on the transient UI steps array.
- Semantic planning is bounded intent classification through the selected model; it does not execute actions or expand file access. Explicit recipe choices can skip planning. Research source coverage, quality checks and model reviews retain their budgets; a conversational UI does not imply full-corpus reading, browsing or truth verification.
- `POST /api/models/custom` registers endpoint + upstream ID; `GET` lists only public definitions/has_api_key, and `DELETE /api/models/custom/{identity}` removes that connection. Each canonical `custom-<hash>` mode resolves exactly one pair. Different endpoints with the same ID stay distinct; implicit ID-only selection is rejected when ambiguous. Removed choices cannot silently fall back.
- `custom-models.json` contains private definitions without keys. API keys are process-memory only, are not echoed or put in browser storage, and must be reentered after restart. Provider checks use only the matching connection key. Release backups include definitions and the project-folder configuration, excluding status cache and credentials; these files are never public source or record mirror content.

## Unified models (1.8.0)

`bootstrap.models` and `GET /api/models` expose `groups`, each containing exact `selection_id=mode:model_id`, canonical `mode/provider`, name, status and availability. This endpoint performs no discovery or model call. GPT uses installed DSH; compatible presets use the reviewed loopback bridge catalog, including proven newer aliases omitted by its stale `/models` map. Completion-only entries remain visible and disabled. Do not expand the execution allowlist from arbitrary discovery text or silently remap identities.

`POST /api/models/check` runs a cancellable synthetic JSON-only request and updates private `model-status.json`; it reads no project records, files or memory. Cache is scoped to a nonsecret endpoint/model hash and expires after24hours. Missing DSH overrides cached success. Failed selections can be retested in settings, but not submitted on work surfaces. Bridge output echoes requested IDs, so connection validation cannot independently establish upstream identity. Custom API credentials go only to their configured endpoint and never appear in catalog/cache/job snapshots; credentials remain runtime memory only.

Ask, agent, meeting, valuation and material generation share canonical resolution. Legacy known model IDs infer their fixed service; explicit mode/provider mismatch or configured custom-ID mismatch rejects before provider execution. Local-only ask remains lexical; rules meeting bypasses AI context. GPT action turns use DSH with the same scoped JSON tool protocol; arbitrary DSH tools stay disabled.

1.10.0投资回报：investor_returns.py为独立经济校验；可用的artifact-tool运行环境经return_workbook.mjs生成新XLSX，否则由portable_return_workbook.py使用full包内置openpyxl创建公式联动工作簿；return_excel.ps1在单独隐藏Excel实例中禁宏/禁外链重算；return_excel.py比较XIRR/MOC/持股/回收并缓存完全相同输入5分钟。两种工作簿作者均需真实Excel重算与独立现金流校验通过。return_sources.py限当前项目与安全绑定目录、展示文件/页/单元格与哈希、复核生成期间来源未变。完整条件自动保存/归档，新轮次保留结构化条件而不串接旧冲突描述。
