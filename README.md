# brigade-tui

A terminal UI version of IndyDevDan's
[super-simple-software-factory](https://github.com/disler/super-simple-software-factory).

Personal build project. Status: a working read-only view. The build brief is
`IDEAL_STATE.md`; the task list is `TASKS.org`.

Brigade reads the session stores that coding agents already write and shows
them. It never writes and it never launches an agent. v1 reads pi, Claude Code
and opencode stores.

## Run

The command is `brigade`. Start it in a Herdr pane:

```
brigade
```

Install or refresh it with `uv tool install --editable .` from this repo.

## Sources

| Harness | Store | How it is read |
|---|---|---|
| pi | `~/.pi/agent/sessions/<cwd-slug>/*.jsonl` | first records for id, cwd, model; every assistant `usage` record for tokens and cost; file mtime for last activity |
| Claude Code | `~/.claude/projects/<cwd-slug>/*.jsonl` | `sessionId`, `cwd`, assistant `model`; every distinct assistant `message.usage` for tokens; file mtime |
| opencode | `~/.local/share/opencode/opencode.db` | SQLite opened `mode=ro`, the `session` table |

Overrides: each source root is set by an environment variable with a default:

| Source | Variable | Default |
|---|---|---|
| pi | `PI_SESSION_DIR` | `~/.pi/agent/sessions` |
| Claude Code | `CLAUDE_PROJECTS_DIR` | `~/.claude/projects` |
| opencode | `OPENCODE_DB` | `~/.local/share/opencode/opencode.db` |

The tests copy the committed fixtures under `tests/fixtures/` into a temp root
and point these variables at the copy, so the suite runs with no pi, Claude
Code or opencode install. Rebuild the fixtures with
`python3 scripts/make-source-fixtures.py`.

## Configuration

Brigade resolves one config from four layers. Highest priority first:

1. environment variables: `PI_SESSION_DIR`, `CLAUDE_PROJECTS_DIR`,
   `OPENCODE_DB`
2. CLI flags: `--pi-dir`, `--claude-dir`, `--opencode-db`, `--refresh`,
   `--running-window`, `--waterfall-window`
3. the config file
4. built-in defaults

The config file is TOML. It lives at `~/.config/brigade/config.toml` unless
`--config` names another file:

```toml
[sources]
pi = "/tmp/pi"
claude = "/tmp/claude"
opencode = "/tmp/opencode.db"

[view]
refresh = 5
running_window = 300
waterfall_window = 900
```

A missing file is not an error. A malformed file stops the app and names the
path. The `[view]` values are seconds. `refresh` is the poll interval,
`running_window` is how long a session counts as running, and
`waterfall_window` is how much time the waterfall shows. Run
`brigade --help` for the flag list.

The TUI polls every `REFRESH_SECONDS` (3s) and groups sessions into one tab per
project directory. The default main view is a grid: one card per session,
showing the title, the agent or role, and the elapsed running time. A card is
coloured by state, and by nothing else: red blocked, green completed, blue
running. Running means the last activity is inside a 120s window; completed
means it is outside it; blocked is only set when a source reports it, and no
source does today, so no card is red from a real store. The tab bar drops into
a project waterfall, which is timing-only: no state word and no state colour.
The waterfall axis is human elapsed time (`m:ss`). It shows the last 10
minutes ending at now, so a longer project is clipped to that window, and it
draws a now marker at the current elapsed position on every row. Press
`escape` to return to the grid.

The grid is read-only and shows a banner that says so. It has no list cursor,
so `j`, `k`, `gg` and `G` do nothing while the grid is shown. They move the
flat table only inside a project view, so a list key never acts on a hidden
table.

A factory summary line sits above the tabs. It gives the whole factory in one
line: `projects, running, idle, blocked, done, $spend today`. The projects
count every project in the snapshot, including the projects past the 10 tab
cap. Spend today sums the reported cost of sessions whose last activity is
today. The blocked count is red, the same red the grid uses, so the one state
that needs the user stands out.

Each tab label carries that project's progress signal: `done/total`, the
freshness age of its newest activity, and the blocked count when a session
reports one. A blocked project is therefore visible from the tab bar, in the
blocked colour, without opening the tab.

Below the summary, a caps line states what the live view shows against what
the stores hold: `10 of 31 projects (tab cap 10)  ·  269 of 780 rows (row cap
50)`. The project count is the tabs in the bar out of every project in the
snapshot. The row count is the rows the tab tables render, each project capped
at the row cap, out of every row in the snapshot. A project or row hidden by a
cap is therefore stated, never a silent loss. The line is always visible, in
both the grid view and a project tab.

A session that names no role gets a meaningful lane and card label: the
project name plus a short title fragment, or the project name plus a readable
id such as `pi:01a0b3e8` when there is no title. A label is never a bare
truncated hash and never the whole raw prompt title.

Each row of the flat table shows harness, model, last activity, tokens and
cost. Spend is shown only when the source reports it.

Token totals come from the transcript itself. pi sums the `usage` block of
every assistant record, including its nested `cost.total`, so pi shows both
tokens and cost. Claude Code sums `message.usage` once per distinct
`message.id`: it writes several snapshots of one reply, and every snapshot
repeats the same usage, so counting each line would overcount. The Claude
transcript carries tokens and no cost, so its cost cell stays blank. That
leaves tokens non-blank for all three harnesses and cost non-blank for two of
the three. Usage totals are cached per file by size and mtime, so a poll only
re-reads a transcript that changed.

A raw transcript can be read without the TUI. `scripts/verify-usage.py` picks
a real pi session and a real Claude session, reads each through the source,
scans the same transcript with a second parser, and asserts the totals match.

## Session detail pane

One session detail pane opens from three entry points:

- a click on a grid card
- a click on a block in the project waterfall
- Enter on a row in the `/` search browser

All three open the same read-only pane. It shows the session id, title,
harness, model, working directory, start, last activity, elapsed time, state,
token totals and cost, then a read-only tail of the transcript the session was
read from. The tail is the last 40 message records, oldest first, with tool
and usage records skipped. A click on a card or block is a real mouse click;
the pane never writes and never launches an agent. Escape closes it.

The tail reader is `brigade_tui/transcript.py`. It reads a JSONL file
for pi and Claude Code and the opencode SQLite store for opencode, always
read-only. `scripts/verify-session-detail.py` reads a temp pi root, compares
the tail to an independent parse of the same file, and opens the pane from all
three entry points. `scripts/verify-session-detail-live.sh` does the same in a
real Herdr pane with real mouse events.

The live dashboard is deliberately small: the tab bar shows the 10 newest
projects and each tab shows the 50 newest rows. A caps line under the factory
summary states both caps out loud, so a hidden project or row is counted. Tab order is frozen once a
project owns a tab: a session update never reorders the bar, so a click target
never moves. A brand-new project takes the left end, and the active project
stays in the bar even when it falls outside the newest ten. Press `/` to open
the session browser. It lists every session from every project, newest first,
and filters as you type, so projects outside the top 10 and rows past the 50th
stay reachable. The browser is keyboard only from end to end: `down` or `tab`
moves from the filter box to the result rows, `j` and `k` move the row cursor,
`g` and `G` jump to the first and last row, and `enter` opens the detail pane
for the selected row. Every row carries the raw session id next to the readable
label, so a pasted id can be checked against the match. `scripts/verify-search-live.sh`
drives this in a real Herdr pane with no mouse.

Seed and scratch runs leave sessions whose cwd is a throwaway temp dir, such as
`/tmp/stw32/wt/STW-1`. Brigade hides those by that marker: they own no project
tab, so the live view stays free of test debris. A real project under `/home`
or the repo tree still shows. The flat table and the `/` search still reach a
hidden session by id, so nothing is lost. A session id or project key that
starts with `seed-` or `scratch-` is hidden too, so the Steward seed can mark
one outside a temp dir.

A Steward worktree is not its own project. A session whose cwd is a git
worktree (a `.git` file pointing at `.../.git/worktrees/<name>`) folds into
the owning checkout, so the main repo and all its worktrees share one tab.
The grid groups its cards under a project heading: `project-name (count)`.
An unattributed worktree path, one that does not resolve to an owner, is
debris and owns no tab or grid heading.

## Source health strip

The line above the tab bar is the source health strip. It names every source
Brigade is configured to read, its store path, how the store is opened, and
how many sessions it reported in the last poll. For example:

```
sources pi  ~/.pi/agent/sessions  [read]  255 sessions  |  claude  ~/.claude/projects  [read]  906 sessions  |  opencode  ~/.local/share/opencode/opencode.db  [ro]  199 sessions
```

A store that is missing, or that fails to read, shows `unavailable` or
`error` instead of a bare zero. Without the strip a missing store and an
empty store look the same: silent, zero-length, and wrong. The strip never
uses the three reserved state colours; those belong to the grid alone.
`scripts/verify-source-health.py` proves the strip against the real stores,
including the missing-store case.

## What lives here

- `IDEAL_STATE.md` — the single description of what done looks like. The build
  brief and the test harness. Do not split this into plan files or PRDs.
- `TASKS.org` — open work, org-mode headings.
- `AGENTS.md` — rules for any agent working in this repo.
- `steward.md` — the tracking contract for the Steward's live agent sessions.
- `sessions.jsonl` — the live session log the Steward writes (git-ignored).
