# Brigade red-team usability review

Date: 2026-10-05
Reviewer: Steward builder, STW-41
Scope: the shipped Brigade code in this worktree, read end to end
Method: read every module, then drove the real app headless against the
committed fixtures. No code changed. Probe output is in
`evidence/STW-41-live-probe.txt`. The suite is green at 42 tests
(`evidence/STW-41-pytest.txt`).

This review is red-team. It looks for what a real user hits, not for style.
Each component states what it does now, the usability problem, and a concrete
better design. Findings are tagged `F1` onward. Recommendations are ranked at
the end.

---

## 1. Source adapters (pi, Claude, opencode)

Modules: `brigade_tui/sources/pi.py`, `claude.py`, `opencode.py`,
`base.py`.

### What it does now

Each adapter reads one harness store and returns `Session` objects.

- pi globs `*/*.jsonl`, scans the first 200 lines. It takes the id, cwd and
  start from the `session` record, the model from `model_change`, the role
  from a `session_info` name, and the title from the first user message. Last
  activity is the file mtime.
- Claude globs `*/*.jsonl`, scans the first 300 lines. It takes `sessionId`,
  `cwd`, the first timestamp, the model from an assistant record, and the
  title from the first user message. Last activity is the file mtime.
- opencode opens the SQLite store `mode=ro` and reads every row of the
  `session` table. It reports tokens and cost, which the other two do not.

### Usability problems a real user hits

`F1`. The schema is discovered by scanning a fixed number of leading lines,
but a `model_change` or `session_info` record can sit after those lines. A pi
session that switches model late, or is named as a role late, silently loses
its model or its role. The card then falls back to a short session id.

`F2`. Role detection is a whole-word scan for `planner`, `builder`,
`reviewer` in the pi `session_info` name or the opencode title. pi uses the
`session_info` name. opencode uses the title. Claude has no role path at all,
so a Claude session almost never names a role. The live probe shows the
result. The cards read `pi-fixtu` and `claude-f`, which are the first eight
characters of the session id. A user cannot tell what either session is.

`F3`. A source that fails is swallowed. `SessionStore.refresh` catches every
exception per source and continues (`store.py`, `refresh`). A missing
directory, a changed schema, or a locked opencode WAL store all render as
zero sessions with no message. The user sees an empty or short grid and
cannot tell "nothing is running" from "the adapter broke".

`F4`. The opencode `mode=ro` read of a live WAL database can fail under a
writer. SQLite needs the `-shm` sidecar to read a hot WAL. Any `sqlite3.Error`
returns `[]` (`opencode.py`, `snapshot`). The one harness that reports tokens
and cost is also the one most likely to disappear silently.

`F5`. pi reports no usage and Claude reports no usage. Only opencode fills
`Usage`. Every Tokens and Cost cell in the flat table is `-` for pi and
Claude, even though both transcripts carry usage data the scan already walks
past.

`F6`. A bad opencode timestamp maps to epoch 0 (`opencode.py`, `_from_ms`).
That session sorts to the bottom and, because `elapsed_seconds` measures from
`started_at`, displays an absurd elapsed span instead of an obvious error.

### Concrete better design

- Read the file header once, keyed by path and mtime, and cache it. This
  removes the 200 or 300 line cliff and cuts the per poll cost (fixes `F1`).
- Resolve a role from the Steward and pstack markers, not a title substring.
  Steward sessions already carry a role. Where no role exists, label the card
  with the project plus a short title, never an eight character UUID
  (fixes `F2`, `F11`).
- Give each adapter a health record: harness, root path, exists, session
  count, last error. Show it in a status strip. A silent failure becomes a
  visible line (fixes `F3`, `F4`, `F6`).
- Sum usage from the records the adapter already reads. pi message records
  and Claude assistant records both carry token counts (fixes `F5`).

---

## 2. Store and grouping

Module: `brigade_tui/store.py`, `model.py`.

### What it does now

`SessionStore.by_project` groups sessions by `ProjectRef.key`, which is the
absolute working directory. It drops scratch sessions, orders projects by the
newest session in each, and orders rows newest first inside a project.
`unique_labels` shortens a collision to a base name plus parent path segments.

### Usability problems a real user hits

`F7`. A git worktree is its own project. The key is the raw cwd, so the main
repo and every Steward worktree become sibling tabs. A Steward run that
creates worktrees produces many one-session tabs named
`.local/share/dai/steward/worktrees/STW-41` and so on. The user wants one
project with several lanes, not ten tabs.

`F8`. The tab bar caps at ten projects and each tab caps at fifty rows
(`MAX_TABS`, `MAX_ROWS_PER_TAB`). The cap is silent. The tab bar gives no
hint that projects exist beyond ten, and the flat table gives no hint that
rows exist beyond fifty. The help text states the numbers, but the main view
does not.

`F9`. Scratch hiding is also silent. A session under `/tmp` is dropped
without a word (`model.py`, `is_scratch`). A legitimate project checked out
under `/tmp` vanishes and the user thinks it was never read.

`F10`. The tab order is by newest activity and is recomputed every poll. Any
project that becomes newest jumps to the left. The tab strip reorders under
the user's hand, so a click target moves between the look and the click.

### Concrete better design

- Resolve the repository root, then collapse worktrees into the owning
  project. Worktrees become lanes or a sub-label, not tabs (fixes `F7`).
- Show the caps in the view: `10 of 14 projects, press / for the rest`
  (fixes `F8`).
- Put the hidden session count in the status strip, with the reason
  (fixes `F9`).
- Freeze tab order for a live session, or order tabs by a stable key and show
  a recency marker. Keep the active tab pinned even when it drops past ten
  (fixes `F10`).

---

## 3. Grid main view

Module: `brigade_tui/grid.py`, `app.py`.

### What it does now

The grid is the default view. It builds one card per session, sorted by last
activity, and renders them left to right wrapped at 34 cells wide. A card
shows the title, the agent or role, and the elapsed time, all in one state
colour. The three state colours are red blocked, green completed, blue
running. Blocked is only set when a source reports it, and no source does.

### Usability problems a real user hits

`F11`. A card carries no project name. The grid mixes all projects. With ten
tabs and many sessions the grid is an undifferentiated wall. The user cannot
tell which project a card belongs to.

`F12`. The grid is a `Static` with `can_focus = True` and no cursor, no
selection, and no scroll. The app focuses it in grid mode. The probe shows
that `j` in grid mode moves the cursor of a hidden project table from row 0
to row 1, with nothing visible on screen. The user presses `j`, sees nothing,
and thinks the key is broken. `enter` on a card does nothing.

`F13`. The elapsed time measures from `started_at` to now while the session
is running. The live probe shows pi and Claude cards at `6650h 06m` because
the fixture start is old and the file mtime is recent. A real long lived
transcript shows a running time that is really its age, not its run length.

`F14`. There is no idle state. Running means activity inside 120 seconds,
completed means outside it. A session blocked on the user for five minutes
shows green, the colour for done. A crashed session also shows green. The one
state a Steward user cares about, needs me, is not shown. Red is never used
from a real store.

`F15`. The title is the first user prompt truncated to 100 characters. For
Steward and pstack sessions the first prompt is shared boilerplate, so many
cards carry the same title and the same agent fallback.

### Concrete better design

- Group the grid under project headings, or put the project label on each
  card (fixes `F11`).
- Make the grid a real focusable list with a visible selection cursor. Bind
  `j` and `k` and `gg` and `G` to that cursor in grid mode, and open a
  session detail on `enter` (fixes `F12`).
- Measure elapsed as the real run span. Cap it, or show last activity
  instead, when the span and the idle time disagree (fixes `F13`).
- Add an idle band between running and completed, and surface blocked from
  the sources that can report it. Show blocked even when empty, so red is a
  real signal (fixes `F14`).
- Fall back to a short project and role label, not a UUID (fixes `F15`).

---

## 4. Waterfall tab view

Module: `brigade_tui/timeline.py`.

### What it does now

One lane per role or session id. The x-axis is real elapsed seconds from the
earliest session start in the tab. Each session is one block from its start
to its last activity, coloured by model, with a model legend. The axis uses
`nice_step` and `axis_ticks`. No state word and no state colour appears.

### Usability problems a real user hits

`F16`. The axis is labelled in raw seconds. The probe shows ticks at `0s`,
`5000000s`, `10000000s`, `15000000s`, `23940252s`. Any project longer than an
hour is unreadable. A human wants `1h 20m`.

`F17`. There is no scroll, no pan, and no zoom. `plot_width` is the pane
width minus the label column, and `.timeline` sets `overflow-x: hidden`. A
long project is compressed into one screen, and a short project is stretched.
The block geometry changes meaning with the pane width.

`F18`. A block is one session, so a long session that pauses and resumes is
one solid bar across the pause. Only separate sessions on the same role show
a hand-back. There is no way to see the gap inside a session.

`F19`. The chart cannot be driven. There is no lane cursor, no block
selection, and `enter` does nothing. The legend at the bottom is clipped when
the pane is short. A user can look at the timing but cannot open anything.

`F20`. The waterfall is timing only by design, so a finished block and a
running block look the same until the user reads the right edge. There is no
now marker.

### Concrete better design

- Format durations for humans: `90s`, `12m`, `2h 05m` (fixes `F16`).
- Add a time window control, for example last 15 minutes or all time, and a
  horizontal pan or zoom. Keep the axis stable while the cursor moves
  (fixes `F17`).
- Draw a now marker and split or shade the idle span inside a session
  (fixes `F18`, `F20`).
- Make lanes and blocks selectable. `j` and `k` move, `enter` opens the
  session detail. Put the legend in the header, not after the last lane
  (fixes `F19`).

---

## 5. Search

Module: `app.py`, `SearchScreen`.

### What it does now

`/` opens a modal browser over every session from the last poll. It lists
Project, Harness, Model, Last, Tokens, Cost and Session, newest first, and
filters as text is typed. The filter matches project key and label, harness,
model, title and session id.

### Usability problems a real user hits

`F21`. The result table is not keyboard reachable. The filter input keeps
focus, so `j`, `k` and the arrow keys do nothing. The probe shows the cursor
stays at row 0 after `j` and after `Down`. The only way into the table is a
mouse click. A vim user is stuck at the filter box.

`F22`. A result row cannot be opened. There is no `enter` action and no
detail view, so search finds a session and then dead ends.

`F23`. The Session column shows the lane label, which is the role or the
first eight id characters, not the raw session id (`session_label`). A user
who pastes a full session id into the filter cannot verify the match in the
row.

### Concrete better design

- Drop the modal or add a focus toggle. Let the input hand off to the table on
  `down` or `tab`, and restore vim `j` and `k` inside the table (fixes
  `F21`).
- Add `enter` to open the session detail pane from a result (fixes `F22`).
- Show the short id and the matched field, so a search result is verifiable
  (fixes `F23`).

---

## 6. Read-only guarantee

Modules: `opencode.py`, `sources/base.py`, `store.py`, `app.py`.

### What it does now

pi and Claude open files with `path.open(encoding="utf-8")`, a read. opencode
opens `file:<db>?mode=ro`. `SessionStore.refresh` only reads. The app spawns
nothing and kills nothing. The help screen and the subtitle both say
read-only. Tests hash the opencode db before and after a browse.

### Usability problems a real user hits

`F24`. The guarantee is a claim in text, not a fact on screen. A cautious
user cannot see which stores were opened, in what mode, or that nothing was
written. The one place it could be proven, the opencode store, is the one
that can silently fail under `mode=ro` (see `F4`).

`F25`. There is no protection against a future source that writes. Nothing
in the store contract enforces read-only. `SessionSource.snapshot` is a
docstring promise, not a type or a runtime guard.

### Concrete better design

- Show a status strip with each store, its resolved path, its open mode, and
  its row count. That makes the read-only claim visible and makes `F4`
  visible at the same time (fixes `F24`).
- Keep the hash check as a test, and add a runtime guard that every source
  object exposes no write path. The check is cheap and it stops a regression
  (fixes `F25`).

---

## 7. Config and environment variables

Modules: `sources/*.py` defaults, `app.py` constants, `README.md`.

### What it does now

Three environment variables set the roots, each with a documented default:
`PI_SESSION_DIR`, `CLAUDE_PROJECTS_DIR`, `OPENCODE_DB`. The poll interval is
`REFRESH_SECONDS` (3s), the running window is `RUNNING_WINDOW_SECONDS`
(120s), and the caps are `MAX_TABS` (10) and `MAX_ROWS_PER_TAB` (50). All are
module constants. `detect_sources` exists but the app always builds
`default_sources`.

### Usability problems a real user hits

`F26`. There is no config file and no CLI flag. A user who wants a longer
running window, a faster poll, or a temp root for a test must set env vars
before launch. There is no `--pi-dir` style flag even though `SCOPE.md`
mentions a `--db` path.

`F27`. A typo in an env var path yields an empty source with no error. The
user cannot see which root the app actually used.

`F28`. `detect_sources` is dead code. The app does not use it, so all three
adapters are always built. This is harmless today but the intent and the code
disagree.

### Concrete better design

- Add `~/.config/brigade/config.toml` plus CLI flags that override it, and
  keep the env vars as the highest priority. Show the effective roots in the
  status strip and in `?` (fixes `F26`, `F27`).
- Call `detect_sources` or delete it. Do not keep a second default path that
  the app ignores (fixes `F28`).

---

## 8. Tab and focus behaviour

Module: `app.py`.

### What it does now

`_render` caps the project list to ten, rebuilds panes when the key set
changes, and rebuilds each pane on every order change. `h` and `l` move tabs
and wrap. A tab click calls `enter_project_view`. A poll never calls it. The
grid stays the default. `escape` returns to the grid. `t` toggles the flat
table under the timeline.

### Usability problems a real user hits

`F29`. The tab bar reorders whenever the newest project changes, and the
whole pane set is cleared and rebuilt. The click target moves under the hand.
The active tab is preserved only while its key stays in the top ten. If the
active project drops past ten, the rebuild drops the active choice and the
default first pane takes over. That is a focus steal in the one place the ISA
wanted none.

`F30`. `t` changes state in grid mode even though the table is not visible.
The probe shows `_show_table` flipping `False` to `True` while the grid
stays up. The next tab entry then shows the table instead of the timeline.
Hidden state changes with no feedback.

`F31`. In grid mode `j` and `k` and `gg` and `G` act on the hidden active
table (see `F12`). The grid has focus but no interaction, so the keys are
silently wrong.

`F32`. The mouse wheel over the grid is not wired. `.timeline` and the
`DataTable` scroll, but `#grid` has no `overflow-y` rule. The ISA requires
wheel scrolling, and the grid is the default view.

### Concrete better design

- Diff the pane list instead of clearing it. Give panes stable ids from the
  project key, not the index. Pin the active tab. Reorder only on an explicit
  user sort action (fixes `F29`).
- Make `t` a no-op in grid mode, or move the table toggle into the project
  view only, so state cannot change off screen (fixes `F30`).
- Put a real cursor on the grid and route the movement keys to it, or show a
  clear hint that the grid is a summary and the keys belong to a project
  (fixes `F31`).
- Add `overflow-y: auto` to `#grid` and a scrollbar (fixes `F32`).

---

## The two questions

### Q1. What should a user see to understand at a glance how all current projects are progressing?

Right now the user sees a wall of session cards. Sessions are not projects,
and a card carries no project name. The answer is a three level glance, top
to bottom.

1. One factory summary line. `4 projects, 7 running, 2 idle, 0 blocked, 3
   done, $1.24 today`. This is the whole factory in one line. It answers "is
   anything on fire" before the eye moves.

2. The tab bar as a project progress strip. Keep the project name. Add a per
   project glyph that reads as progress, for example `STW-41 builder 4m,
   reviewer waiting`, or a compact `3 run / 1 blocked / 2 done`. Mark the one
   project that needs the user with the blocked colour. Keep the newest
   activity age next to each project so freshness is visible.

3. A project grouped grid. Group cards under a project heading. Inside a
   project, order the cards by the pipeline, planner then builder then
   reviewer, so the row reads left to right as progress. Show each card in
   its state colour, with the role, the elapsed run time, and a model chip.

The key change is that progress is a project property, not a session
property. Today the only project signal is the tab label, which holds no
state. The grid holds state but no project. The two must meet in the middle.

### Q2. How should a user drill into one project or one session for depth?

Today there is one drill level, the project waterfall, and no session level
at all.

A good drill path has three levels, with `escape` backing out one level and a
breadcrumb showing the level.

1. Factory to project. `enter` on a tab, or a click, opens the project view.
   This exists. Keep the waterfall as the timing picture. Add a project
   header line with the project path, the session count, the total spend, and
   the newest activity.

2. Project to session. Make the waterfall lanes and blocks selectable. `j`
   and `k` move a block cursor, `enter` opens the session detail. The detail
   pane shows title, harness, model, directory, start, last activity, state,
   tokens, and cost, and then the read-only transcript tail. The transcript
   tail is the single most valuable depth feature. It answers "what is it
   actually doing". It reads the same JSONL or SQLite row the adapter already
   reads, so it keeps the read-only promise.

3. Anywhere to session. `enter` on a grid card and `enter` on a search row
   open the same detail pane. The detail pane is one widget with one shape,
   reached from three places, so the user learns it once.

The breadcrumb reads `factory / STW-41 / builder`. `escape` from the detail
returns to the project, and `escape` again returns to the factory.

---

## Ranked recommendations

The rank is by user value for the Steward use case, highest first. The first
three change what Dan can see. The rest remove friction and bugs.

| Rank | Recommendation | Findings fixed | Why it ranks here |
|------|----------------|----------------|-------------------|
| 1 | Collapse git worktrees into the owning project, and group the grid by project | F7, F11 | A Steward run makes many worktrees. Today each is a tab and a nameless card. This is the largest signal to noise win. |
| 2 | Add a session detail pane with a read-only transcript tail, opened from grid, waterfall and search | F19, F22, Q2 | Turns the board into a steering tool. The transcript is the depth the user actually wants, at no cost to the read-only promise. |
| 3 | Add the factory summary line and per project progress in the tab bar | Q1, F14 | Answers "how is everything going" in one look. Surfaces blocked, which is the one state that needs the user. |
| 4 | Fix elapsed time to the real run span, and split running, idle and completed | F13, F14 | `6650h 06m running` is a lie on screen. A wrong number erodes trust in every other number. |
| 5 | Add a source health status strip with store path, open mode and count | F3, F4, F24, F27 | Silent adapter failure is the worst failure. The strip makes the read-only claim visible too. |
| 6 | Stabilise tab order and pane identity, and pin the active tab | F10, F29 | The click target must not move. The focus steal violates the ISA intent. |
| 7 | Make the grid navigable or clearly read only, and stop keys acting on hidden tables | F12, F15, F30, F31 | A key that does nothing visible reads as a broken app. |
| 8 | Format the waterfall axis for humans, add a time window and a now marker | F16, F17, F18, F20 | Makes long projects readable and shows what is live. |
| 9 | Show the tab and row caps in the view | F8, F9 | A hidden cap is a hidden project. The user must know more exist. |
| 10 | Make search keyboard reachable and openable, and show the raw id | F21, F22, F23 | Search is the escape hatch for the caps. It must not dead end. |
| 11 | Derive meaningful labels when no role is known | F2, F11, F15 | `pi-fixtu` tells the user nothing. A short project plus title does. |
| 12 | Add usage totals from pi and Claude transcripts | F5 | Tokens and cost are `-` for two of three harnesses. The data is already on disk. |
| 13 | Add a config file and CLI flags, and remove the dead source path | F26, F27, F28 | Convenience and clarity. Lower value than the visibility fixes. |

---

## Evidence appendix

- `evidence/STW-41-live-probe.txt` holds the real headless render. It shows
  the grid cards, the waterfall axis at `23940252s`, the lane blocks, the
  hidden table moving on `j` in grid mode, `t` flipping state in grid mode,
  and the search cursor staying put on `j`.
- `evidence/STW-41-pytest.txt` holds `42 passed`.
- Fixture roots for the probe: `tests/fixtures/pi`, `tests/fixtures/claude`,
  `tests/fixtures/opencode/opencode.db`.
