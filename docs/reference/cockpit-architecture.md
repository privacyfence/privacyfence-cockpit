> Reference input from the maintainer's existing Mac-based cockpit, sanitised. It describes the tool this repository replaces; it is not a spec of this repository.

Cockpit: architecture
A reference description of a working "cockpit": a small tool that runs scheduled, model-free data syncs and dashboard publishing on top of PrivacyFence and privacyfence-sync. It is written for the PrivacyFence developers, who are thinking about a tool of this kind, and covers the workflows, the recipes, key management and the operating model.

Sanitised on purpose. This document describes structure and behaviour only. Every concrete identifier (Grist document ids, Salesforce report ids, Drive folder ids, Jira project keys, custom-field ids, Peek page slugs, token names, people, customers, amounts, ledger details) is replaced by a placeholder such as <SALES_DOC> or <REPORT_ID>. Nothing here is real data, and nothing in it can be used to reach a real system.

1. What it is, in one paragraph
A folder of recipes (YAML files for privacyfence-sync) plus a thin CLI (cockpit) that picks, orders, authenticates and runs them, and a small Mac app that wraps the CLI. The recipes copy business data from SaaS sources (CRM, issue tracker, asset register, Drive files, Google Sheets) into a Grist "datalake", derive new tables from it, and publish self-contained HTML dashboards to Peek pages. Every read and write goes through PrivacyFence, so every data movement is governed by the user's own auto-accept rules. No AI model runs in the loop. Claude is used to write and maintain recipes and to do the few jobs that need judgement (reading free-text reports); it is never in the scheduled path.

2. Design principles
Principle	What it means in practice
No model in the run	Runs are deterministic: YAML steps plus plain Python scripts. Same inputs, same outputs, no tokens spent.
No local state	All data and all run status live in Grist. A recipe reads "what did I load last?" from a Sync_Log table and appends a row at the end. The laptop holds only scratch files and terminal logs, and nothing reads them back.
Diff against the target, not a snapshot	Each run compares the source with what the target table holds now, so hand edits, half-finished runs and drift are corrected on the next run. A failed run needs no clean-up.
Idempotent and re-runnable	Re-running a recipe changes nothing when nothing changed. Rows are never deleted: a row that leaves the source is flagged out (In_<Source> = false) and can return.
Guard before write	A guard step stops the run before the first write when the parse is suspicious (warnings, empty source, row count or totals off, too many keys leaving).
Verify after write	A verify step compares what the destination reports (added / skipped) with what the plan promised.
Commit last	The Sync_Log row is written only after verify succeeds. That row is the commit: until it exists, the next run treats the source as not loaded.
One writer per table	Every table has exactly one writer (a recipe, a skill, or a human), and the table-name prefix says which.
Least privilege, by the user	Credentials for the sources never reach the cockpit. The cockpit holds only an AI-client token for PrivacyFence, and PrivacyFence auto-accept rules (created by the user with a passkey) decide what that token may touch unattended.
Data minimisation	Recipes select only the columns they need, sum or drop personal detail before it reaches a page, and never store some fields at all (see section 10).
3. The big picture
                         ┌──────────────────────────── user's Mac ─────────────────────────────┐
                         │                                                                      │
  Cockpit.app  ─────────►│  ./cockpit  (plain python3 CLI)                                       │
  (SwiftUI window)       │    select recipes by path → order by folder name → pick token        │
                         │    → run `privacyfence-sync run recipe.yaml` as a subprocess         │
                         │    → tee output to logs/, append a row to logs/runs.csv              │
                         │                                                                      │
                         │  .venv/  privacyfence-sync (pinned)  +  datalake-engine (editable)   │
                         │                                                                      │
                         │  macOS login Keychain: AI-client token(s)                            │
                         └───────────────────────────────┬──────────────────────────────────────┘
                                                         │ MCP, bearer token (AI-client token)
                                                         ▼
                         ┌────────────────────────── PrivacyFence ──────────────────────────────┐
                         │ policy + approval gate: auto-accept rules (scope · id · verbs ·       │
                         │ "may include personal data" box); denied calls surface as exit 3      │
                         │ holds the real credentials for every connector                        │
                         └───┬───────────┬───────────┬───────────┬───────────┬──────────────────┘
                             ▼           ▼           ▼           ▼           ▼
                        CRM reports   Issue       Asset       Drive /      Grist ◄──► Peek
                        (read)        tracker     register    Sheets       (datalake)  (pages)
                                      (export)    (export)    (download)   read+write  write
Three groups of recipes run in a fixed order, so every stage sees fresh input:

  10-imports            source ──► Grist raw tables         (SF_*, Jira_*, Fin_*, Sheet_*, Notes_*)
        │
        ▼
  20-transformations    Grist ──► Grist derived tables      (T_*)
        │
        ▼
  30-dashboards         Grist ──► payload.json ──► HTML ──► Peek page
4. Components
Component	Role	Notes
cockpit	One Python 3 script, standard library only. Discovery, ordering, token lookup, subprocess run, logging, check / preflight / permissions / test / sync / token.	Needs no install. It bootstraps .venv itself.
privacyfence-sync	The recipe runner (PyPI, version-pinned in requirements.txt). Executes steps, talks to PrivacyFence over MCP, handles exit codes.	Bumping the pin is the upgrade path.
PrivacyFence	Gateway between the runner and the real systems. Applies policy and auto-accept rules; holds connector credentials.	The cockpit never sees source credentials.
datalake-engine	Shared Python library (editable install into .venv): one module per sync "step" that parses a source and defines its target table; plus the diff, guard, verify, pick and sync-log logic.	Each recipe's scripts/datalake.py is a three-line shim that imports it, because the runner refuses scripts outside the recipe folder.
Grist	The datalake and the state store. Several documents, split by domain (sales, finance, delivery/projects, manufacturing-reference, dashboards' own logs).	Tables are created once (with Claude's help); recipes only repeat.
Peek	Hosts each dashboard as a private, versioned page. A publish adds a new version.	One page per dashboard.
Cockpit.app	SwiftUI window around the CLI: recipe list with last result, run / stop, multi-select, log console, notifications.	Starts the CLI and nothing else, so tokens, logs and runs.csv are identical to terminal use.
Claude skills	Two skills handle the only model-dependent work: turning free-text project reports into keyed Grist rows, and writing a machine-readable "updates" block into meeting notes.	Not recipes. A human reviews the output before a recipe consumes it (section 7, pattern H).
5. Repository layout
cockpit                      the CLI
app/                         Swift source + build script for Cockpit.app (app itself is git-ignored)
requirements.txt             what `cockpit setup` installs: privacyfence-sync==<pinned>, the engine
.venv/                       the runner's own virtualenv (git-ignored)
lib/datalake-engine/         shared engine; steps/ has one module per sync
tests/                       offline tests with made-up data; live_replay.py for read-only replays
workflows/
  10-imports/   NN-name/     recipe.yaml, scripts/, (templates/), work/
  20-transformations/ NN-name/
  30-dashboards/  NN-name/   recipe.yaml, scripts/build_payload.py, templates/page.html
  _shared/<block>/           dashboard blocks shared by several pages (source + sync.py)
docs/                        one doc per topic: tables, recipes, required auto-accept rules
skills/                      the Claude skills (not recipes)
logs/                        one log per run + runs.csv (git-ignored, never read back by recipes)
work/                        per-run scratch (git-ignored: it holds source data)
Conventions

A group is a folder under workflows/; a recipe is a folder inside a group that holds recipe.yaml.
Run order is folder-name order, group by group. Number recipe folders (05-, 10-, 20-…) so a recipe that needs another one sorts after it.
A folder starting with _ or . is skipped (a cheap way to disable a recipe).
Paths on the command line may omit the group number (imports/10-sf-milestones).
6. Recipes
6.1 Anatomy
A recipe is a YAML document: version, name, work_dir, then an ordered list of steps. A step is one of:

Step kind	Shape	Purpose
Tool step	tool: <name> + args	A PrivacyFence tool call (export a report, list Drive files, update a Grist table, publish a Peek page…).
Script step	run: scripts/x.py + args	A local Python script inside the recipe folder. It parses, joins, plans and guards.
Template step	template: templates/x.html + data: <step>.<output>	Renders a self-contained HTML page from a JSON payload.
Useful step attributes: save_result / save_as / file_name (where a tool's output lands in the work folder), stdout (capture a script's JSON for later steps), outputs (named files a script produces), file (feed an earlier output into a tool, e.g. a CSV into a Grist update), skip_if_empty (no rows → skip the write), timeout_seconds.

Steps reference each other with placeholders: {file:<step>}, {file:<step>.<output>}, {output:<name>}, {work_dir}, and ${<step>.stdout:<json.path>} for values a script printed (for example the id of the file pick chose).

Exit codes seen by the cockpit (also its own exit code):

Code	Meaning
0	OK, including "nothing to do"
1	Recipe or argument wrong
2	A step, transfer or script failed (guard and verify stops land here)
3	Denied by PrivacyFence (a missing auto-accept rule)
4	Not signed in / no token
10	Used inside a script step to mean "nothing to do, stop the run cleanly"; the runner reports it as OK
6.2 The canonical sync shape
Nearly every import follows the same eight-step skeleton:

version: 1
name: example-sync
work_dir: work/example-sync
steps:
  - id: report                    # 1 EXPORT the source in full, as a file (paged, no row cap)
    tool: salesforce_export_report          # or drive_download_file, jira_export_issues, assets_export_objects, ...
    args: {report_id: "<REPORT_ID>", cell_format: both, file_name: report.csv}

  - id: totals                    # 2 SOURCE TOTALS for self-checks (row count, sums)
    tool: salesforce_run_report
    args: {report_id: "<REPORT_ID>", summary_only: true}
    save_result: totals.json

  - id: target                    # 3 EXPORT THE TARGET as it is now: the diff basis (SQL: only the needed columns)
    tool: grist_export_sql
    args: {doc_id: "<DATALAKE_DOC>", sql: "SELECT ... FROM <TABLE> ORDER BY id", file_name: target.json}

  - id: plan                      # 4 PLAN: source vs target → upsert.csv, flag_out.csv, summary.json, sync_log.csv
    run: scripts/datalake.py
    args: [plan, <step-id>, --source, "{file:report}", --totals, "{file:totals}",
           --grist, "{file:target}", --sync-log, --out, "{work_dir}/plan"]
    outputs: {upsert: plan/upsert.csv, flag_out: plan/flag_out.csv, summary: plan/summary.json, sync_log: plan/sync_log.csv}

  - id: guard                     # 5 GUARD: stop before any write on warnings or implausible change
    run: scripts/datalake.py
    args: [guard, --summary, "{file:plan.summary}", --max-change, "0.15", --max-flagged-out, "0.10"]

  - id: upsert                    # 6 WRITE: only new / changed / returned rows
    tool: grist_update_csv
    file: plan.upsert
    skip_if_empty: true
    args: {doc_id: "<DATALAKE_DOC>", table_id: <TABLE>, key_column: <KEY>, create_missing: true, clear_empty: true}
    save_result: upsert_result.json

  - id: flag_out                  #   ...and flag keys that left the source (never delete)
    tool: grist_update_csv
    file: plan.flag_out
    skip_if_empty: true
    args: {doc_id: "<DATALAKE_DOC>", table_id: <TABLE>, key_column: <KEY>, clear_empty: true}
    save_result: flag_out_result.json

  - id: verify                    # 7 VERIFY: the destination's counts must equal the plan
    run: scripts/datalake.py
    args: [verify, --summary, "{file:plan.summary}", --upsert-result, "{file:upsert}", --flag-out-result, "{file:flag_out}"]

  - id: sync_log                  # 8 COMMIT: one Sync_Log row (also when nothing changed)
    tool: grist_import_csv
    file: plan.sync_log
    args: {doc_id: "<DATALAKE_DOC>", table_id: Sync_Log}
Why this shape: steps 1–5 are read-only, so a failure there leaves every system untouched; step 8 comes last so that a half-written run is simply redone from the target's real state.

6.3 Recipe patterns
#	Pattern	When	How "is there something new?" is answered
A	Report snapshot	A CRM report is the full current truth (opportunities, milestones, product lines, quote lines, accounts)	Always diff. The plan decides whether anything changed. Source totals are used as a self-check.
B	Newest file in a folder	A Drive workbook is re-saved or versioned (YYYYMMDD_name_vN.xlsx)	drive_list_files on the folder → pick compares the newest file's name + modified time with the newest OK row in Sync_Log; exit 10 if already loaded.
C	Month by month	One file per month, re-saved while the month is open; history to backfill	pick-month returns the oldest month with no OK log row for that file's name and modified time. One month per run; cockpit run --repeat N loops while there is work. A Run_By tag is the logic version: bump it to reload every month after a logic change.
D	Single re-saved ledger	One export file that only grows at the end; rows have no natural key	pick-file; the key is file name + row number; the guard also caps the share of rows changed in place (a shifted or rebuilt file).
E	Sheets tab read	A native Google Sheet with several tabs	drive_sheets_get_values over explicit ranges (the plain download gives only the first tab as CSV). Ranges can also skip columns the recipe must not read.
F	Transformation	Derive tables from Grist tables (joins, roll-ups, eligibility rules, comparisons)	Reads Grist by SQL, writes Grist; same plan / guard / write / verify / commit tail.
G	Dashboard publish	Render a page from Grist	build script joins the exports and computes a SHA-256 of payload + template; compares it with the last Fingerprint in the dashboard's own sync log; exit 10 if unchanged. Otherwise template → peek_upload_page → log row.
H	Human-in-the-loop island	Free text needs judgement (project reports, meeting notes)	A Claude skill writes a strictly formatted block with a STATUS: draft marker. A human reviews it and sets reviewed. A recipe parses only reviewed blocks with plain Python; a reviewed block with problems stops at the guard, writes nothing and is retried until fixed.
6.4 What the recipe catalogue covers (generic)
Group	Recipes (generic description)	Sources → targets
Imports	CRM opportunities, invoicing milestones, product lines, quote lines, accounts	CRM reports → SF_* tables
Support contracts and device counts	Asset register export → Jira_Asset_* (counts only)
Cashflow forecast workbook; its "invoiced" block	Drive workbook → Fin_Cashflow_*
Project epics with a project code	Issue tracker export → Jira_Epic_Projects
Weekly project updates	Reviewed block in meeting notes (Drive doc) → Notes_Weekly_*
Master project list	Google Sheet tab → Sheet_Project_Map
FX rates, time-log allocation, employee cost allocation, ledger exports	Drive files → Fin_*, Jira_Allocation
Transformations	Partner yearly roll-up and size classes; support eligibility; support comparison (model vs. asset register)	Grist → T_*
People / project matching into the tables dashboards read	PMO_*, Notes_*, Manual_* → T_Projects, T_Reports, T_Allocations, T_Notes
Pay cost per employee and a pay-status filter	Ledger lines → T_Paycost, T_Pay_Status
Dashboards	Pipeline overview, delivery dashboard, cost-allocation dashboard	Grist → HTML → Peek
About two dozen recipes in total. Each is a self-contained folder; the cockpit does not know what any of them does.

7. The datalake engine
7.1 Step modules
A sync is a Python module in steps/ that exposes:

STEP: id, target (doc / table_id), key (unique business key), flag (the In_<Source> Bool column), columns (ordered (name, type) pairs) and optional json_cols;
build(source_path, info) → (meta, records, warnings): parse the source file, return normalised records, hard warnings (stop the run) and soft notes (written to the log as WARN, do not stop);
summarize(meta, records): extra counts and totals for the summary;
optional extra_outputs.
Every table also gets two bookkeeping columns: the flag column and Last_Changed.

7.2 Commands
Command	Does
list / schema <step>	Show steps; print the grist_create_table column JSON for a step's target (tables are created once, from this).
plan <step>	Parse the source, diff it against the target export, write upsert.csv, flag_out.csv, full.csv, cleared.json, summary.json, and (with --sync-log) sync_log.csv.
guard	Exit 1 (stop before any write) on warnings, an empty parse, row count moving by more than a threshold, too many keys flagged out, or (optionally) too many rows changed in place.
verify	After the writes: exit 1 if the destination's added / skipped counts differ from the plan.
pick, pick-month, pick-file	Choose the source file from a saved Drive listing and compare it with the Sync_Log (new / re-saved / up to date).
7.3 The diff
For every source record (keyed): absent in target → added; present with different values → updated (numbers compared with a tolerance; dates, Booleans and JSON columns normalised, so SQL and record exports compare equal); present and previously flagged out → returned; identical → untouched, keeping its Last_Changed. Target keys not in the source → flagged_out (once; already-out keys are counted as still_out). A value that became empty is also written to cleared.json, because an empty CSV cell cannot clear a value on its own (recipes pass clear_empty: true). Duplicate or missing keys in the target are reported as warnings: a unique key is a precondition of the write.

7.4 Guards (typical thresholds)
Check	Typical value
Source parsed to zero rows	stop
Any hard warning (missing report column, row count ≠ report's own row count, a total ≠ the report's total, duplicate keys)	stop
Rows moved vs. previous	15 % (monthly files: 35 %)
Keys that would be flagged out	10 % (monthly files: 25 %)
Rows changed in place (ledgers keyed by row)	30 %
A stopped run exits 2 and leaves the target untouched. Raising a threshold for one run is an explicit, reviewed edit.

7.5 The Sync_Log
One shared table per Grist document, append-only, never cleaned up automatically.

Column	Meaning
Sync_Time, Sync_Date	When
Table_Name	Which target this row is about (this is how a recipe finds its rows)
Report_Rows, Added, Updated, Returned, Flagged_Out, Report_Total	The run's counts
Status	OK or WARN (soft notes); only OK rows count as "loaded"
Notes	Warnings and soft notes, truncated
Source_Name, Source_Modified, Source_Id, Run_By	File-driven syncs: which file version was loaded and under which logic version
Dashboards keep their own publish log in a separate "dashboards" document, with the payload SHA-256 as Fingerprint.

8. Data model and ownership
8.1 Grist documents, split by domain
Document (placeholder)	Holds
<SALES_DOC>	CRM mirrors, hand-kept product categories and promises, sales transformations
<FINANCE_DOC>	Cashflow, FX, cost allocation, ledger lines, pay cost, time-log allocation
<DELIVERY_DOC>	Project reports, Jira epics, weekly notes, asset-register counts, people and teams
<REFERENCE_DOC>	A read-only reference table owned by another team (stock)
<DASHBOARD_LOG_DOC>	One publish log per dashboard
8.2 Table prefixes say who writes
Prefix	Writer	Editable by hand?
SF_, Jira_, Fin_, Sheet_, Notes_	An import recipe from one source	No: overwritten on the next run
PMO_	A Claude skill (free-text reports)	No
T_	A transformation recipe	No: change the rule or a Manual_ input instead
Manual_	A human (Claude only when asked)	Yes: the only tables people type into
Sync_Log	Every recipe, append-only	No
Rules that follow from this: fix a wrong value at its source (or in a Manual_ table), never in a derived table; imports and transformations can be dropped and rebuilt by re-running their recipe; Manual_ tables and Sync_Log cannot. Grist has no access rules here, so the convention is made visible: read-only tables sit on pages named "Imports (read only)" / "Results (read only)", manual ones on "Edit here", and each read-only table's description starts with "Written by , do not edit".

8.3 Keys and joins
Every table has a deterministic text key (Opportunity_Id, Contract_Key, month|employee|project, file name|row). Cross-source joins go through one business code that exists in every system (a project code carried by the CRM record, the issue tracker's project field and the project report). Name matching is limited to normalised spelling (case, hyphens, word order); everything else is an explicit alias row in a Manual_ table, and unmatched names are listed in the run's log row instead of being guessed.

9. Dashboards
9.1 Build and publish
The recipe exports only the columns it needs from Grist (SQL), within an explicit date window where one applies.
build_payload.py joins them into one JSON payload, runs its own guards (no rows, too many keys missing from the joined source), and computes sha256(payload + template). The report date is part of the hash, so a page gets at most one new version a day when nothing changed.
If the hash equals the newest OK Fingerprint in the dashboard log: exit 10, clean stop.
The template step fills a PRIVACYFENCE_SYNC_DATA slot in a self-contained HTML template (no server, no build chain, no external data fetch).
peek_upload_page publishes a new version of the page (private).
Only then is the log row written.
9.2 Shared blocks
The runner renders one self-contained template per recipe and only runs files inside the recipe folder, so a block used by two dashboards cannot be included at run time. Instead each block lives once in workflows/_shared/<block>/ (CSS, HTML, JS, recipe data steps, and a Python payload builder in the engine) with a sync.py that copies it between marker comments (>>> shared:<block>:css … <<<) into every template and recipe that carries them. cockpit sync copies, cockpit sync --check reports, and a test fails when any copy is stale or a dashboard grew its own copy of the payload code.

9.3 Page behaviour
Filters, tabs, sort and search state live in the URL query string, so a view can be shared as a link. Colour tokens and base classes come from one shared base style.

10. Key management and access control
This is the part most specific to PrivacyFence. There are four independent layers, and the cockpit owns only the first.

 layer 1  AI-client token        who is calling PrivacyFence             (cockpit: Keychain, lookup chain)
 layer 2  auto-accept rules      what that token may do unattended       (user, in PrivacyFence, passkey)
 layer 3  connector credentials  how PrivacyFence reaches each system    (PrivacyFence only; never the cockpit)
 layer 4  recipe design          what a recipe asks for in the first place (least data, fixed columns)
10.1 Layer 1: tokens
What is stored. Only an AI-client token issued by the PrivacyFence companion app. No Salesforce, Google, Atlassian or Grist credential exists anywhere in the repository or the cockpit.

Where. The macOS login Keychain, generic-password items under one service name (privacyfence-sync), one item per account (see below). Never in a file, never in git, never in a recipe.

How it is entered. cockpit token set <name> calls security add-generic-password … -w without a value on the command line, so the tool prompts for it: the secret does not appear in shell history or process arguments. The item is created with access limited to /usr/bin/security. token show only reports present / missing; token delete removes it.

Typo protection. token set refuses a name that is not a known recipe, workflow group or default, and suggests the nearest match, because a token stored under a misspelt name would silently never be read.

Granularity: one token or many. The lookup is most specific first:

Order	Source	Use
1	$PRIVACYFENCE_TOKEN in the environment	CI, one-off override. The cockpit warns when it hides a Keychain item.
2	Keychain item named after the recipe	A narrow token for one sensitive recipe
3	Keychain item named after its workflow group (imports, transformations, dashboards)	One token per stage
4	Keychain item default	One token for everything
5	privacyfence-sync's own stored sign-in	Organisation (server) mode, login --server URL
A recipe token always wins over its group's token, so a group token can be combined with a tighter token for a single sensitive recipe. The same chain applies to run, preflight and test --live.

Injection. The token is placed only in the child process environment (PRIVACYFENCE_TOKEN) for the one recipe being run. The log header records only the source of the token (keychain:<name>, $PRIVACYFENCE_TOKEN, stored sign-in), never its value.

Expiry and rotation. preflight reports the token label and the days until it expires. Rotating a token is token set <name> again (the Keychain item is updated in place).

10.2 Layer 2: PrivacyFence auto-accept rules
A token alone does not let an unattended run do anything that needs approval. The user creates auto-accept rules in PrivacyFence (Settings → Auto-accept), with a passkey, so only the user can create them; Claude cannot propose or approve them. A rule has:

Field	Example (generic)
Scope	CRM report · Jira project · Assets schema · Drive folder or file · Grist document · Peek page
Resource id	The exact report id / project keys / folder id / document id / page slug
Verbs	read, search, download, create, update (and update (new versions) for Peek)
Personal-data box	"Exports / downloads / new versions may include personal data", ticked per rule where the data holds names, emails or ids
The cockpit helps in three ways, all offline or read-only:

cockpit permissions reads each recipe's steps and prints the exact tool names it calls (for a scoped token) and the rules it needs: scope, the resource id extracted from the step arguments (report id, Jira keys from the JQL, Drive folder from the listing query, Grist doc_id, Peek slug) and the verbs to tick. --union merges everything into one list for a single default token; --by-group merges per workflow group. Resources that are only known at run time (computed in a step) are listed separately, because the rule must cover whatever they reach. A tool with no known rule is flagged as unknown.
cockpit preflight runs privacyfence-sync preflight per recipe with that recipe's token and runs nothing: it shows which steps are out of the token's scope, which would need an approval card (so an unattended run would be denied), and the token's remaining lifetime. Exit 3 means at least one step would be denied. --attended checks for a run that waits for approvals.
--attended first runs. The first run of a new recipe is attended: an approval card appears for every missing rule, the user adds the rules, and from then on the unattended run should show no card.
10.3 The tool → rule mapping the cockpit encodes
Tool	Scope	Verb	Personal-data box
salesforce_export_report	CRM report	read	exports may include personal data
salesforce_run_report	CRM report	read	n/a
jira_export_issues	Jira project	search	exports may include personal data
assets_export_objects	Assets schema	read, search	n/a
drive_download_file	Drive folder	download	downloads may include personal data
drive_sheets_get_values	Drive file or folder	read	n/a
drive_list_files	(never asks)	n/a	n/a
grist_export_records, grist_export_sql	Grist document	read	exports may include personal data
grist_get_records	Grist document	read	n/a
grist_update_csv, grist_update_records	Grist document	update	n/a
grist_import_csv, grist_add_records	Grist document	create	n/a
peek_upload_page	Peek page	update (new versions)	new versions may include personal data
Note: this table is hand-maintained in the cockpit from the runner's documentation (see section 15).

10.4 Layer 3: connector credentials
Entirely inside PrivacyFence. The cockpit's threat surface is therefore one revocable, expiring, scope-limited bearer token per account name, plus whatever the auto-accept rules allow it to do.

10.5 What is, and is not, in git
In git	Never in git
Recipes, scripts, templates, tests (made-up data), docs	Tokens, Keychain contents
Resource ids that appear in recipes and rules (these are identifiers, not secrets, but keep the repository private)	work/ scratch (holds source data with names)
The Swift app source	logs/, runs.csv, .replay.yaml, .venv/, the built app
Binary assets that hold personal data (e.g. staff photos), exported Grist data used in experiments
10.6 Operating rules that follow
Start with one default token while building; split to group tokens and then per-recipe tokens as recipes become sensitive (finance and pay data are the obvious candidates).
After token set, always preflight before scheduling.
Treat a denied run (exit 3) as a rule gap, not a bug: add the rule, do not widen the token.
A token that stops working shows as exit 4 (not signed in); the fix is token set, not a recipe edit.
11. Handling confidential and personal data
Technique	Example (generic)
Select columns, not tables	Target exports are SQL with explicit column lists; a dashboard reads only what its payload uses.
Window the data	Date-bounded queries; "all years" only where a feature needs history, and noted in the recipe.
Aggregate before the page	Time-log hours are summed per month and project in SQL, so no person reaches the page.
Count, don't copy	Device serial numbers are counted per contract and never stored in Grist.
Skip columns on read	Sheet reads use ranges that leave out manager-name columns; workbook parsers never read identifier columns.
Raw rows stay in scratch	Worklog rows (thousands per month) are read inside the script and never uploaded. Downloaded daily-notes documents are deleted by plan after the one block is read.
Fixed filters	Pay lines are selected by an explicit voucher pattern, re-checked in the script, so unrelated ledger lines never enter a table.
Unmatched stays visible	Names that cannot be matched are left empty and listed in the log row, not guessed.
Private pages	Dashboards are private Peek pages; versions are added, never published publicly by a recipe.
Scratch is ignored	work/ and logs/ are git-ignored; nothing reads them back.
12. Safety nets and testing
Layer	What it catches	How to run
check	Recipe syntax and structure, no network	./cockpit check [PATH]
Offline tests	Engine diff and guard logic, every parser against generated workbooks and reports with made-up values, payload joins, no-change detection, shared-block drift, and structural rules for every recipe (passes check, scripts inside its folder, no local state, ends with a Sync_Log write, uses the date window)	./cockpit test (-k for a subset)
preflight	Token scope, rules, expiry	./cockpit preflight [PATH]
Live replay	What a real run would do now, without writing: runs each recipe against the real sources only up to its first write (exports, downloads, pick, plan, guard, page render).	./cockpit test --live [PATH]
Dry run	Which recipes would run, in what order	./cockpit run -n
How live replay stays safe: the replay recipe is cut from the current recipe.yaml every time, so it cannot drift; it keeps only steps up to the first writing tool; any tool not on a short read-only allowlist ends the replay, so a new write tool is never replayed by accident. In replay, "nothing to do" stops are disabled so the current source is always parsed, and the summary shows per recipe the rows that would be added, updated, flagged out, plus guard warnings.

13. Operating the cockpit
13.1 Commands
Command	Does
setup [--recreate]	Create .venv (newest Python 3.11+ on the PATH) and install the pinned requirements. Done automatically by check / run / test when requirements.txt or the engine's pyproject.toml hash changed.
list	Every group and recipe with its last result (from logs/runs.csv)
check, preflight, permissions	See sections 10 and 12
run [PATH…]	Run everything, a group, or recipes, in folder order, each once. Options: --attended, --dry-run, --stop-on-error, --repeat N, --notify, --server URL. Default is keep going after a failure.
test [--live]	See section 12
sync [--check]	Copy shared dashboard blocks
`token set	show
13.2 What a run records
Output streams to the terminal and to logs/<date>/<time>_<group>_<recipe>.log (the header shows the command and the token source, the footer the exit code). logs/runs.csv gets one row per run: start, recipe, mode (attended / unattended / replay), exit code, seconds, log path. The cockpit's exit code is that of the first failing recipe, and --notify raises a macOS notification naming the failed recipes. None of this is read back by a recipe: the authoritative status is always in Grist.

13.3 The Mac app
A SwiftUI window built by one script (swiftc, iconutil; needs only the command line tools). Sidebar of groups and recipes with last result; toolbar Run (Cmd-R) and Stop (Cmd-.); multi-select (Cmd/Shift-click) runs the selection in folder order; a log console (Cmd-L) opens by itself when a job fails; a More menu has attended, stop-on-error, dry run, check, tests, live replay and sync; notifications when a job finishes in the background. It only spawns the CLI, so it adds no new secret store and no new behaviour. The project path is baked in at build time.

13.4 Scheduling
A launchd agent calling ./cockpit run --notify is the intended mechanism. Two practical constraints: a background job cannot read ~/Documents (move the folder out, or grant the job Full Disk Access), and the login Keychain must be unlocked for the job's user. Because runs are idempotent and stop cleanly on "nothing to do", running often is cheap.

14. Extending it
New recipe: create workflows/<group>/<NN-name>/recipe.yaml in the right group (a source → Grist copy is an import; Grist → Grist is a transformation; anything that publishes a page is a dashboard). Keep scripts inside the folder; for the engine, copy an existing three-line scripts/datalake.py shim. Run check, add the rules shown by permissions, do an attended first run, then preflight.
New engine step: add a module under steps/ (editable install, no reinstall) and a test with made-up data.
New shared dashboard block: put it in workflows/_shared/<name>/ with a sync.py and marker comments; run sync.
New table: create it once in Grist from datalake schema <step> (column ids are derived from labels: verify them after creation), give it the right prefix, and document its writer.
Logic change that must reload history: bump the Run_By tag in the recipe's pick and plan steps.
15. Observations for the PrivacyFence team
Evidence from building and running this, offered as input rather than as a spec.

What worked well

The check / preflight / --attended ladder made it safe to add recipes without ever running a write against a production target by accident.
Auto-accept rules scoped by resource id and verb map cleanly onto recipes, and the "personal data" box forces a conscious decision where names are involved.
Export-style tools (*_export_*, drive_download_file with file_name) that write to a work folder instead of returning data to a model are what makes a model-free pipeline possible and keeps data out of any model context.
exit 10 as "nothing to do, stop successfully" made cheap, frequent, idempotent schedules natural.
Friction points that led to workarounds

Rule derivation is reimplemented client-side. cockpit permissions carries a hand-maintained tool → scope/verb/box table and parses JQL, Drive queries and SQL-free arguments to guess the resource each rule must name. A runner-provided "required rules" output (privacyfence-sync permissions recipe.yaml) would remove the duplicate and its drift risk.
Run-time resources. When a resource is computed by an earlier step (for example a file id chosen by a script), the rule can only be stated as "whatever it reaches", which is hard to review. Declaring the allowed resource set in the recipe would make this checkable.
Token granularity is stitched together by name convention. Per-recipe / per-group / default lookup, and the typo guard, live in the cockpit. First-class named token profiles (with their own scopes and expiry) in the companion app would replace it.
No native schedule story. Unattended runs depend on launchd, Keychain availability and filesystem permissions (~/Documents). A supported scheduler or a headless/organisation mode recipe would help.
State is entirely user-built. The Sync_Log commit pattern, pick logic, fingerprints and diff/flag-out semantics are all custom. A first-class "incremental source" and "idempotent upsert with flag-out" primitive in the runner would remove most of the engine.
Template fan-out. Because a recipe can only run files inside its own folder and render one template, shared dashboard code is copy-synced between folders by a marker-based script. Shared includes would remove that.
Soft failures. There is no first-class "warn but continue" status; the engine writes WARN into its own log table.
Appendix A: glossary
Term	Meaning
Recipe	A recipe.yaml plus its scripts/, templates/, work/; one sync or one dashboard.
Group	A numbered folder under workflows/: imports, transformations, dashboards.
Datalake	The set of Grist documents holding mirrored and derived data.
Plan	The diff of source vs. target, producing the minimal CSVs to write.
Guard / verify	Stop-before-write checks / compare-after-write checks.
Commit	The Sync_Log row written after verify; defines "already loaded".
Flag out	Mark a row as no longer in its source (In_<Source> = false) instead of deleting it.
Fingerprint	SHA-256 of a dashboard's payload + template, stored in its publish log for no-change detection.
Auto-accept rule	A user-created PrivacyFence rule (scope, id, verbs, personal-data box) that lets an unattended run proceed without an approval card.
Attended / unattended	Whether the run waits for approval cards (first runs) or must pass on existing rules (scheduled runs).
Live replay	A read-only run of each recipe up to its first write, showing what a real run would do now.
Appendix B: a day in the life
A scheduled cockpit run --notify starts.
Imports: each source recipe exports, diffs against Grist, guards, writes only the differences, verifies, appends a Sync_Log row. File-driven recipes stop at once ("nothing to do") unless a newer file or month exists.
Transformations: derived tables are recomputed from fresh imports; only rows that differ are written.
Dashboards: payloads are rebuilt; if the fingerprint equals the last publish the run stops, otherwise a new page version is published and logged.
The cockpit prints a summary, appends to runs.csv, and notifies only if something failed. The user's only manual chores are the human-in-the-loop islands (reviewing a drafted block) and the hand-kept Manual_ tables.
