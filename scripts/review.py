#!/usr/bin/env python3
"""Helper for the `code-review` skill.

Agent-neutral prompt assembly with selectable review profiles.
Recipes preserve their review strategies, caps, floors and output contracts.

Subcommands
  parse   [--fork|--inline] [--session-effort E] (--stdin | -- RAW ARGS...)
          Parse the typed arguments, update the sticky state file, measure the
          diff, and print a ROUTING block plus the exact `render` command to run.
  render  [--fork|--inline] [--session-effort E] --profile F --agent yes|no
          --rf none|spawn|message [--verify yes|no] (--stdin | -- RAW ARGS...)
          Print the complete review prompt for the resolved recipe cell.
  state   get | set-effort LEVEL | set-max N|all | clear-max | reset
  numstat [RANGE]
          Print added+deleted line count and the finder budget for RANGE.
  dump-blocks DIR
          Write the verbatim prompt blocks as markdown files for audit/fallback.

Stdlib only. Arguments are treated as data; nothing is passed to a shell.
"""
from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Constants (JS names in comments)
# --------------------------------------------------------------------------

AGENT = "subagent"  # ft
RF = "ReportFindings"  # jD
LEVELS = ["low", "medium", "high", "xhigh", "max"]  # Li / me
LEVEL_ALIASES = {"med": "medium"}  # Se
RF_HARD_CAP = 32  # Ie
NEAR_MISS_LEVEL = re.compile(r"^(low|med|hig|xhi|max)[a-z]*$", re.IGNORECASE)  # Ws
MAX_FINDINGS_RE = re.compile(  # po
    r"(^|\s)--max-findings(?:(?:\s*=\s*|\s+)(?!--)(\S+)|\s*=\S*)?(?=\s|$)"
)
RANGE_RE = re.compile(r"^[@\w][@\w./~^-]*\.\.\.?[@\w][@\w./~^-]*$")  # Qs
DEFAULT_STATE_FILE = Path.home() / ".agents" / "code-review-skill-state.json"
PROFILES = ("default", "parallel", "inline", "minimal")


def plural(n, singular, plural_form=None):  # I()
    return singular if n == 1 else (plural_form or singular + "s")


def W(e):  # W()
    return "all findings" if e == "all" else f"≤{e} {plural(e, 'finding')}"


def WE(e):  # we()
    return "**every finding**" if e == "all" else f"at most **{e} {plural(e, 'finding')}**"


# --------------------------------------------------------------------------
# Verbatim prompt blocks
# --------------------------------------------------------------------------

X = """## Phase 0 — Gather the diff

Run `git diff @{upstream}...HEAD` (or `git diff main...HEAD` / `git diff HEAD~1`
if there's no upstream) to get the unified diff under review. If there are
uncommitted changes, or the range diff is empty, also run `git diff HEAD` and
include the working-tree changes in scope — the review often runs before the
commit. If a PR number, branch name, or file path was passed as an argument,
review that target instead. Treat this diff as the review scope.
"""

YE = """Flag new code that re-implements something the codebase
already has — Search shared/utility modules and files adjacent to the change,
and name the existing helper to call instead.
"""  # ye

Q = """### Simplification

Flag unnecessary complexity the diff adds: redundant or derivable state,
copy-paste with slight variation, deep nesting, dead code left behind. Name
the simpler form that does the same job.
"""

Z = """### Efficiency

Flag wasted work the diff introduces: redundant computation or repeated I/O,
independent operations run sequentially, blocking work added to startup or
hot paths. Also flag long-lived objects built from closures or captured
environments — they keep the entire enclosing scope alive for the object's
lifetime (a memory leak when that scope holds large values); prefer a
class/struct that copies only the fields it needs. Name the cheaper
alternative.
"""

UE = """### Conventions (applicable agent instructions)

Find the instruction files that govern the changed code in the current agent
and environment. Include applicable user-level, repository-root, and ancestor
directory instructions, such as AGENTS.md and AGENTS.local.md. Follow the
host's scope and precedence rules, including local overrides. Read each
applicable file that exists, then check for clear violations of its rules.

Only flag a violation when you can quote the exact rule and the exact line
that breaks it — no style preferences, no vague "spirit of the doc"
inferences. Name the instruction file path and quote the rule so the report
can cite it. If no instructions apply, return nothing for this angle.
"""  # ue

OE = """### Altitude

Check that each change fixes the root cause at the right depth rather than
patching a symptom with a fragile bandaid. Special cases layered on shared
infrastructure are a sign the fix isn't deep enough — prefer the simpler, more
general change to the underlying mechanism over adding special cases, and name
that change.
"""  # oe

GS = """### Angle A — line-by-line diff scan

Read every hunk in the diff, line by line. Then read the enclosing function for
each hunk — bugs in unchanged lines of a touched function are in scope (the PR
re-exposes or fails to fix them). For every line ask: what input, state, timing,
or platform makes this line wrong? Look for inverted/wrong conditions,
off-by-one, null/undefined deref, missing `await`, falsy-zero checks,
wrong-variable copy-paste, error swallowed in catch, unescaped regex metachars.
"""  # gs

YS = """### Angle B — removed-behavior auditor

For every line the diff DELETES or replaces, name the invariant or behavior it
enforced, then search the new code for where that invariant is re-established.
If you can't find it, that's a candidate: a removed guard, a dropped error
path, a narrowed validation, a deleted test that was covering a real case.
"""  # ys

WS = """### Angle C — cross-file tracer

For each function the diff changes, find its callers (search for the symbol) and
check whether the change breaks any call site: a new precondition, a changed
return shape, a new exception, a timing/ordering dependency. Also check callees:
does a parallel change in the same PR make a call unsafe?
"""  # ws

VS = """### Angle D — language-pitfall specialist

Scan for the classic pitfalls of the diff's language/framework — for example:
JS falsy-zero, `==` coercion, closure-captured loop var; Python mutable default
args, late-binding closures; Go nil-map write, range-var capture; SQL injection;
timezone/DST drift; float equality. Flag any instance the diff introduces.
"""  # vs

BS = """### Angle E — wrapper/proxy correctness

When the PR adds or modifies a type that wraps another (cache, proxy, decorator,
adapter): check that every method routes to the wrapped instance and not back
through a registry/session/global — e.g. a caching provider holding a
`delegate` field that resolves IDs via `session.get(...)` instead of
`delegate.get(...)` will re-enter the cache or recurse. Also check that the
wrapper forwards all the methods the callers actually use.
"""  # bs

KE = (  # Ke
    f"If the {AGENT} tool is not available in your current tool set, do not error "
    "— perform each angle (and each verification) yourself, sequentially, in this context."
)

HT = f"{GS}\n{YS}\n{WS}"  # Ht
KS = f"{HT}\n{VS}\n{BS}"  # ks
GT = (  # Gt
    "### Reuse\n\nThe angles above hunt for bugs; this one and the next two hunt for cleanup in\n"
    "the changed code. " + YE
)
RE_NOTE = """Cleanup, altitude, and conventions candidates use the same
`file`/`line`/`summary` shape; in `failure_scenario`, state the concrete
cost (what is duplicated, wasted, harder to maintain, or which applicable instruction rule
is broken) instead of a crash. Correctness bugs always outrank cleanup,
altitude, and conventions findings when the output cap forces a cut.
"""  # re

CS = """- **CONFIRMED** — can name the inputs/state that trigger it and the wrong
  output or crash. Quote the line.
- **PLAUSIBLE** — mechanism is real, trigger is uncertain (timing, env,
  config). State what would confirm it.
- **REFUTED** — factually wrong (code doesn't say that) or guarded elsewhere.
  Quote the line that proves it."""  # Cs

UNDERSCORE_S = """**PLAUSIBLE by default** — do not refute a candidate for being "speculative" or
"depends on runtime state" when the state is realistic: concurrency races,
nil/undefined on a rare-but-reachable path (error handler, cold cache, missing
optional field), falsy-zero treated as missing, off-by-one on a boundary the
code does not exclude, retry storms / partial failures, regex/allowlist that
lost an anchor. These are PLAUSIBLE.

**REFUTED** only when constructible from the code: factually wrong (quote the
actual line); provably impossible (type/constant/invariant — show it); already
handled in this diff (cite the guard); or pure style with no observable effect."""  # _s

QT = f"""## Phase 2 — Verify (1-vote, 3-state)

Dedup candidates that point at the same line/mechanism, keeping the one with
the most concrete failure scenario. For each remaining candidate, run **one
verifier** via the {AGENT} tool: give it the diff, the relevant
file(s), and the candidate, and have it return exactly one of:

{CS}

Keep candidates where the vote is CONFIRMED or PLAUSIBLE.
"""  # qt

SS = f"""## Phase 2 — Verify (1-vote, recall-biased)

Dedup near-duplicates (same defect, same location, same reason → keep one). For
each remaining candidate, run **one verifier** via the {AGENT} tool:
give it the diff, the relevant file(s), and the candidate; it returns exactly
one of **CONFIRMED / PLAUSIBLE / REFUTED**.

{UNDERSCORE_S}

Keep **CONFIRMED and PLAUSIBLE**. Drop REFUTED.
"""  # Ss

YT = """moved/extracted code that dropped a guard
or anchor; second-tier footguns (dataclass default evaluated once, `hash()`
non-determinism, lock-scope shrink, predicate methods with side effects);
setup/teardown asymmetry in tests; config defaults flipped."""  # Yt

ES = f"""## Phase 3 — Sweep for gaps

Run **one more finder** as a fresh reviewer who has the verified list. Re-read
the diff and enclosing functions looking ONLY for defects not already listed.
Do not re-derive or re-confirm anything already there — the job is gaps. Focus
on what the first pass tends to miss: {YT}

Surface **up to 8 additional candidates**, each naming a defect not already on
the list. If nothing new, return an empty sweep — do not pad.
"""  # Es

AE = f"{HT}\n{GT}\n{Q}\n{Z}\n{OE}\n{UE}"  # Ae
BT = f"{KS}\n{GT}\n{Q}\n{Z}\n{OE}\n{UE}"  # Bt

AS = f"""The {AGENT} tool isn't available in this context, so the usual
multi-agent fan-out and subagent verify pass can't run. Work through every
angle below yourself, in this same context, in one pass — do not skip angles
for lack of fan-out. Re-check each candidate against the diff before keeping
it; drop anything you can't back up with a concrete failure scenario.
"""  # As

XS_FOOTER = f"""
State clearly in your summary that this was a single-pass review done without
the {AGENT} tool, not the full multi-agent fan-out, so whoever reads
it isn't misled about what actually ran.
"""  # xs

NO = """### Reuse

The angles above hunt for bugs; this one and the next two hunt for cleanup in
the changed code. Flag new code that re-implements something the codebase
already has — Search shared/utility modules and files adjacent to the change,
and name the existing helper to call instead.
"""  # no (inline cells; same text as Gt)

AO = HT  # ao (inline cells; same text as Ht)

# -- flag blocks ------------------------------------------------------------

MS = """

## Posting to GitHub (--comment)

The `--comment` flag was passed. After producing the findings list, if the
review target is a GitHub PR, post each finding as an inline PR comment via
`mcp__github_inline_comment__create_inline_comment` (one call per finding;
include a suggestion block only when it fully fixes the issue). If that tool
is not available in this session, fall back to `gh api` (repos/{owner}/{repo}/pulls/{pr}/comments)
or print the findings instead. If the target is not a PR, print the findings
to the terminal and note that `--comment` was ignored.
"""  # Ms

GO = (  # go
    f"call {RF} again with the same findings, each\n"
    "carrying an `outcome`: `fixed`, `no_change_needed` (the finding was wrong or\n"
    "already handled), or `skipped` (real but not applied). Do not repeat the\n"
    "findings as text"
)

JS = f"""

## If findings are fixed later

Whenever reported findings get fixed later in this session - the user asks you
to fix them, or later work fixes them incidentally - you MUST {GO}.
Make that call immediately after the fixes land, before any prose summary; the
host UI's per-finding status updates only from it, and without it the findings
stay marked unresolved.
"""  # js

FS = """After the call, give one line per finding with its outcome: this session can
also be read from a client that does not show the tool call.
"""  # Fs

HS = """

## After the review

After the findings are reported (and applied, when --fix was passed): if `/verify` has not run this session and the diff has a runtime surface (not test-only or docs-only per the pre-ship exemptions), invoke `/verify` now — this review checks that the diff reads right; `/verify` checks that it runs right.
"""  # Hs (uEe = "verify")


def US(target):  # Us(e)
    iid = gitlab_mr_iid(target)
    repo = gitlab_repo(target)
    r = f" -R {repo}" if repo else ""
    cmd = f"glab mr note{(' ' + iid) if iid else ''}{r} -m \"<body>\""
    where = "" if repo else " from inside that project's checkout"
    return f"""

## Posting to GitLab (--comment)

The `--comment` flag was passed. After producing the findings list, if the
review target is a GitLab merge request, post the findings as one general MR
note via `{cmd}`{where}
(every finding with its file:line, the issue, and the suggested fix). glab has no single verb for line-anchored
comments; those require `glab api projects/:id/merge_requests/:iid/discussions`,
so post the general note unless the user asks for inline threads. If glab is
not available in this session, print the findings instead. If the target is
not an MR, print the findings to the terminal and note that `--comment` was
ignored.
"""


def BS_FIX(uses_rf):  # Bs(e)
    tail = (
        f"Then {GO}; after the call, give one line per skipped finding saying why."
        if uses_rf
        else "Finish with a brief summary of what was fixed\nand what was skipped."
    )
    return f"""

## Applying fixes (--fix)

The `--fix` flag was passed. After producing the findings list, apply the
findings to the working tree instead of stopping at the report: fix each one
directly — correctness bugs and reuse/simplification/efficiency cleanups alike.
Skip any finding whose fix would change intended behavior, require changes well
outside the reviewed diff, or that you judge to be a false positive — note the
skip rather than arguing with it. {tail}
"""


# -- output contracts -------------------------------------------------------

def ZT(e):  # zt(e): JSON contract
    head = (
        "with one object for every finding that survives, no maximum"
        if e == "all"
        else f"of at most {e} {plural(e, 'object')}"
    )
    keep = (
        "Keep every finding that survives. There is no maximum."
        if e == "all"
        else f"If more than {e} survive, keep the {e} most\nsevere."
    )
    return f"""## Output

Return findings as a JSON array {head}:

```json
[
  {{
    "file": "path/to/file.ext",
    "line": 123,
    "summary": "one-sentence statement of the bug",
    "failure_scenario": "concrete inputs/state → wrong output/crash"
  }}
]
```

Ranked most-severe first. {keep} If nothing survives verification, return `[]`. Do not call the
{RF} tool even if it is available - this review's
output contract is the JSON block above.
"""


def VT(e):  # Vt(e): ReportFindings contract
    count = (
        "every finding that survives, no maximum,"
        if e == "all"
        else f"at most {e} {plural(e, 'entry', 'entries')}"
    )
    keep = (
        "Keep every finding that survives. There is no maximum."
        if e == "all"
        else f"If more than {e} survive, keep the {e} most severe."
    )
    return f"""## Output

Call the {RF} tool once to report this review's results
with `{{level, findings}}`. `findings` is {count} ranked
most-severe first; each entry has `file`, `line`, `summary`,
`short_summary` — the claim compressed to ≤60 characters, no rationale
or consequence clause — `failure_scenario`, and `category` — a short kebab-case slug for the angle
that produced it (`correctness`, `simplification`, `efficiency`,
`reuse`, `altitude`, `conventions`, or a more specific slug like
`test-coverage` when one fits better) — plus `verdict` when a verify pass
produced one. {keep} If
nothing survives verification, call it with an empty array. Do not also print
the findings as text, and do not create or publish an artifact of the review -
the tool call is the report.
"""


# -- recipe cells -----------------------------------------------------------

LOW_TURN2 = """## Turn 2 — findings

Flag runtime-correctness bugs visible from the hunk alone: inverted/wrong
condition, off-by-one, null/undefined deref where adjacent lines show the value
can be absent, removed guard, falsy-zero check, missing `await`,
wrong-variable copy-paste, error swallowed in a catch that should propagate.
Also flag — still from the hunk alone — new code that duplicates an existing
helper visible in the diff context, and dead code the diff leaves behind.

Do **not** flag style, naming, perf, missing tests, or anything outside the
hunk.

"""

LOW_TURN1_SKIP_TESTS = """## Turn 1 — read

One tool call: read the unified diff (`git diff @{upstream}...HEAD; git diff HEAD`
to cover both committed and uncommitted changes, or `git diff main...HEAD` /
the target passed as an argument). Skip test/fixture
hunks (`test/`, `spec/`, `__tests__/`, `*_test.*`, `*.test.*`,
`fixtures/`, `testdata/`) — test-file changes are not reviewed at this level.
No subagents, no full-file reads.

"""

LOW_TURN1_PLAIN = """## Turn 1 — read

One tool call: read the unified diff (`git diff @{upstream}...HEAD; git diff HEAD`
to cover both committed and uncommitted changes, or `git diff main...HEAD` /
the target passed as an argument). No subagents, no full-file reads.

"""


def KT(uses_rf, n=4):  # Kt(e, n=4): cell "low"
    if uses_rf:
        out = f"""Report {WE(n)}, most-severe first, in one
{RF} call with `{{level, findings}}` — each entry has
`file`, `line`, `summary`, `short_summary` (≤60 characters), and
`failure_scenario`. If nothing qualifies, call it with an empty findings
array. Do not also print the findings as text.
"""
    else:
        out = f"""Output {WE(n)}, most-severe first, one line each:
`path/to/file.ext:123 — what's wrong and the concrete failure`. If nothing
qualifies, output exactly `(none)`. Do not call the
{RF} tool even if it is available.
"""
    return (
        f"`low effort → 1 diff pass → no verify → {W(n)}`\n\n"
        + LOW_TURN1_SKIP_TESTS
        + LOW_TURN2
        + out
    )


def XT(uses_rf):  # Xt(e): cell "low-targeted"
    if uses_rf:
        out = f"""Target **min(files_changed, 4) findings**, most-severe first, reported
in one {RF} call with `{{level, findings}}` — each
entry has `file`, `line`, `summary`, `short_summary` (≤60 characters),
and `failure_scenario`. If you have fewer, do one more pass focused on the
largest changed file and on any **removed** code blocks. Call it with an
empty findings array only if the diff is trivially correct after that pass.
Do not also print the findings as text.
"""
    else:
        out = """Target **min(files_changed, 4) findings**, most-severe first, one
line each: `path/to/file.ext:123 — what's wrong and the concrete failure`.
If you have fewer, do one more pass focused on the largest changed file
and on any **removed** code blocks. Output `(none)` only if the diff is
trivially correct after that pass.
"""
    return (
        "`low effort → 1 diff pass → no verify → ≥min(files,4) findings`\n\n"
        + LOW_TURN1_SKIP_TESTS
        + LOW_TURN2
        + out
    )


def SO(uses_rf, n=8):  # so(e, n=8): cell "inline-low"
    s = 4 if n == "all" else min(4, n)
    if uses_rf:
        out = f"""Report {WE(n)}, most-severe first, in one
{RF} call with `{{level, findings}}` — each entry has
`file`, `line`, `summary`, `short_summary` (≤60 characters), and
`failure_scenario`.
Target at least min(files_changed, {s}) findings — if you see fewer, widen to other hunks in the same diff before stopping. If fewer than {s} genuine findings exist, report what you have. Do not also print the findings as text.
"""
    else:
        out = f"""Output {WE(n)}, most-severe first, one line each:
`path/to/file.ext:123 — what's wrong and the concrete failure`.
Target at least min(files_changed, {s}) findings — if you see fewer, widen to other hunks in the same diff before stopping. If fewer than {s} genuine findings exist, emit what you have.
"""
    return (
        f"`low effort → 1 diff pass → no verify → {W(n)}`\n\n"
        + LOW_TURN1_PLAIN
        + LOW_TURN2
        + out
    )


def XE(tag, lead_in, angle_count, angles, cap, output, sweep_focus=None):  # Xe({...})
    sweep = (
        f"""
## Phase 3 — Sweep for gaps

Take one more pass yourself (same context, no subagent) as a fresh reviewer
who has the deduplicated list. Re-read the diff and enclosing functions
looking ONLY for defects not already listed: {sweep_focus}
"""
        if sweep_focus
        else ""
    )
    return f"""`{tag}`

{lead_in}

{AS}
{X}## Phase 1 — Find candidates ({angle_count} angles, single pass)

Work through **{angle_count} angles** yourself, in sequence, in this same
context — do not spawn subagents. Each surfaces candidate findings with
`file`, `line`, a one-line `summary`, and a concrete `failure_scenario`.

{angles}
{RE_NOTE}
## Phase 2 — Dedup and self-check (no subagent verify)

Dedup near-duplicates (same defect, same location, same reason → keep one).
Re-check each remaining candidate yourself against the diff before keeping it.
{sweep}
{output(cap)}{XS_FOOTER}"""


MEDIUM_LEAD = (
    "You are reviewing for **precision** at medium effort: every finding you surface\n"
    "should be one a maintainer would act on."
)
HIGH_LEAD = (
    "You are reviewing for **recall** at high effort: catch every real bug a careful\n"
    "reviewer would catch in one sitting. At this level, catching real bugs matters\n"
    "more than avoiding false positives. Err on the side of surfacing."
)
PASS_THROUGH_VERIFY = (
    "Pass every candidate with a nameable failure scenario through — finders that\n"
    "silently drop half-believed candidates bypass the verify step and are the\n"
    "dominant cause of misses.\n"
)
PHASE1_8 = (
    "## Phase 1 — Find candidates (3 correctness angles + 3 cleanup angles + 1 altitude angle "
    "+ 1 conventions angle, up to 6 each)\n"
)
PHASE1_10 = (
    "## Phase 1 — Find candidates (5 correctness angles + 3 cleanup angles + 1 altitude angle "
    "+ 1 conventions angle, up to 8 each)\n"
)


def JT(output, agent_available=True, s=8):  # Jt(e, n=true, s=8): cell "medium"
    if not agent_available:
        return XE(
            f"medium effort → {AGENT} tool unavailable → single-pass inline → {W(s)}",
            MEDIUM_LEAD, 8, AE, s, output,
        )
    return f"""`medium effort → 3+5 angles × 6 candidates → 1-vote verify → {W(s)}`

{MEDIUM_LEAD}

{X}
{PHASE1_8}
Run **8 independent finder angles** via the {AGENT} tool. Each
surfaces **up to 6 candidate findings** with `file`, `line`, a one-line
`summary`, and a concrete `failure_scenario`. {KE}

{AE}
{RE_NOTE}
{PASS_THROUGH_VERIFY}
{QT}
{output(s)}"""


def ZT_HIGH(output, agent_available=True, s=10):  # Zt(e, n=true, s=10): cell "high"
    if not agent_available:
        return XE(
            f"high effort → {AGENT} tool unavailable → single-pass inline → {W(s)}",
            HIGH_LEAD, 8, AE, s, output,
        )
    return f"""`high effort → 3+5 angles × 6 candidates → 1-vote verify (recall-biased) → {W(s)}`

{HIGH_LEAD}

{X}
{PHASE1_8}
Run **8 independent finder angles** via the {AGENT} tool. Each
surfaces **up to 6 candidate findings** with `file`, `line`, a one-line
`summary`, and a concrete `failure_scenario`. {KE}

{AE}
{RE_NOTE}
{PASS_THROUGH_VERIFY}
{SS}
{output(s)}"""


def EO(level):  # eo(e): cells "xhigh" (to) and "max" (oo)
    depth = "maximum" if level == "max" else "extra-high"
    lead = (
        f"You are reviewing for **recall** at {depth} effort: catch every real bug. At\n"
        "this level, catching real bugs matters more than avoiding false positives — a\n"
        "missed bug ships. Err on the side of surfacing."
    )

    def build(output, agent_available=True, r=15):
        if not agent_available:
            return XE(
                f"{level} effort → {AGENT} tool unavailable → single-pass inline → {W(r)}",
                lead, 10, BT, r, output, sweep_focus=YT,
            )
        return f"""`{level} effort → 5+5 angles × 8 candidates → 1-vote verify → sweep → {W(r)}`

{lead}

{X}
{PHASE1_10}
Run **10 independent finder angles** via the {AGENT} tool. Each
surfaces **up to 8 candidate findings**. Do NOT let one angle's conclusions
suppress another's — if two angles flag the same line for different reasons,
record both. {KE}

{BT}
{RE_NOTE}
{QT}
This is recall mode — a single non-REFUTED vote carries the finding. Do NOT
drop on uncertainty.

{ES}
{output(r)}"""

    return build


TO = EO("xhigh")  # to
OO = EO("max")  # oo


def IO(output, n):  # io(e, n): findings floor for the inline inline cells
    def wrapped(s):
        r = max(1, math.floor((n if s == "all" else min(s, n)) / 2))
        text = output(s).replace(
            "## Output\n",
            "## Output\n\n"
            f"Target **at least {r} {plural(r, 'finding')}**. If fewer genuine findings exist, "
            "emit what you have — do not invent to hit the floor.\n",
            1,
        )
        return text.replace("nothing survives verification", "nothing survives")

    return wrapped


def RO(tag, lead_in, cap):  # ro(e, n, s): shared by lo / co
    def build(output, h=None):
        if h is None:
            h = cap
        return f"""`{tag} → {W(h)}`

{lead_in}

{X}
{PHASE1_8}
Run **8 independent finder angles** in sequence yourself, in THIS context — do NOT spawn subagents for them. Each
surfaces **up to 6 candidate findings** with `file`, `line`, a one-line
`summary`, and a concrete `failure_scenario`.

{AO}
{NO}
{Q}
{Z}
{OE}
{UE}
{RE_NOTE}
Pass every candidate with a nameable failure scenario through — finders that
silently drop half-believed candidates are the dominant cause of misses.

## Phase 2 — Dedup only (no verify)

Pool all candidates. Dedup near-duplicates only (same defect, same location, same reason → keep one). Do NOT run verifiers; do NOT re-judge. Sort by severity.

{IO(output, cap)(h)}"""

    return build


LO = RO(  # lo: cell "inline-medium"
    "medium effort → 8 inline angles → dedup (no verify)",
    "You are reviewing for **correctness bugs**: surface every plausible bug. At this\n"
    "level, catching real bugs matters more than avoiding false positives — err on\n"
    "the side of surfacing.",
    8,
)
CO = RO(  # co: cell "inline-high"
    "high effort → 8 inline angles → dedup (no verify)",
    HIGH_LEAD,
    10,
)


def IS(output, n=15):  # Is(e, n=15): cell "inline-xhigh" (uo)
    return f"""`xhigh effort → 10 inline angles → dedup (no verify) → sweep → {W(n)}`

You are reviewing for **recall** at extra-high effort: catch every real bug. At
this level, catching real bugs matters more than avoiding false positives — a
missed bug ships. Err on the side of surfacing.

{X}
{PHASE1_10}
Run **10 independent finder angles** in sequence yourself, in THIS context — do NOT spawn subagents for them. Each
surfaces **up to 8 candidate findings**. Do NOT let one angle's conclusions
suppress another's — if two angles flag the same line for different reasons,
record both.

{AO}
{VS}
{BS}
{NO}
{Q}
{Z}
{OE}
{UE}
{RE_NOTE}
## Phase 2 — Dedup only (no verify)

Pool all candidates. Dedup near-duplicates only (same defect, same location, same reason → keep one). Do NOT run verifiers; do NOT re-judge. Sort by severity. Do NOT drop on uncertainty.

## Phase 3 — Sweep for gaps

Take one more pass (same context — no subagent) as a fresh reviewer who has the deduplicated list. Re-read
the diff and enclosing functions looking ONLY for defects not already listed.
Do not re-derive or re-confirm anything already there — the job is gaps. Focus
on what the first pass tends to miss: moved/extracted code that dropped a guard
or anchor; second-tier footguns (dataclass default evaluated once, `hash()`
non-determinism, lock-scope shrink, predicate methods with side effects);
setup/teardown asymmetry in tests; config defaults flipped.

Surface **up to 8 additional candidates**, each naming a defect not already on
the list. If nothing new, return nothing from this phase — do not pad.

{IO(output, 15)(n)}"""


UO = IS  # uo


def QE(e=15, uses_rf=True):  # Qe: minimal recipe; text fallback without reporting tool
    count = "every finding" if e == "all" else f"at most {e} {plural(e, 'finding')}"
    prompt = f"""`minimal prompt → single careful diff pass → {W(e)}`

You are reviewing a pull request for real bugs. Run `git diff @{{upstream}}...HEAD` (or `git diff main...HEAD` / `git diff HEAD~1`
if there's no upstream) to get the unified diff under review. If there are
uncommitted changes, or the range diff is empty, also run `git diff HEAD` and
include the working-tree changes in scope — the review often runs before the
commit. If a PR number, branch name, or file path was passed as an argument,
review that target instead. Treat this diff as the review scope.

Review the diff as a careful senior engineer would: read every hunk, open the surrounding files for context as needed (file reading, text search, git log/blame/show), and hunt for correctness issues — wrong or inverted conditions, off-by-one, null/undefined dereference, missing `await`, dropped error handling, removed guards or validations, broken callers of changed functions, races. Prefer real failure modes over style; every finding needs a concrete scenario in which the code misbehaves.

When you are done, submit {count} via the {RF} tool, filling its fields as defined — for each: the file path and start line, a severity, and a comment that states the issue and the concrete scenario in which the code misbehaves. Quality over quantity: include everything you genuinely believe is a real issue, and nothing you don't.

After the tool call, also restate the findings in your final reply — one line each, `file:line — summary` — so they stay visible in sessions that do not render tool output.
"""
    if uses_rf:
        return prompt
    return prompt.split("When you are done, submit", 1)[0] + ZT(e)



# --------------------------------------------------------------------------
# Routing table (ce) and cell dispatch (Ns)
# --------------------------------------------------------------------------

def _cell(cell, measured_external=False, finder_budget_hint=False, model_effort="typed"):
    return {
        "cell": cell,
        "measuredExternal": measured_external,
        "finderBudgetHint": finder_budget_hint,
        "modelEffort": model_effort,
    }


ROUTING = {  # ce
    "default": {
        "low": _cell("low"),
        "medium": _cell("inline-high", measured_external=True),
        "high": _cell("inline-high", measured_external=True),
        "xhigh": _cell("inline-xhigh", measured_external=True),
        "max": _cell("max"),
    },
    "parallel": {
        "low": _cell("low-targeted", model_effort="medium"),
        "medium": _cell("medium"),
        "high": _cell("high", finder_budget_hint=True),
        "xhigh": _cell("xhigh", finder_budget_hint=True),
        "max": _cell("max", finder_budget_hint=True),
    },
    "inline": {
        "low": _cell("inline-low", measured_external=True),
        "medium": _cell("inline-medium", measured_external=True),
        "high": _cell("inline-high", measured_external=True),
        "xhigh": _cell("inline-xhigh", measured_external=True),
        "max": _cell("max"),
    },
    "minimal": {
        "low": _cell("low"),
        "medium": _cell("minimal-pass", measured_external=True),
        "high": _cell("minimal-pass", measured_external=True),
        "xhigh": _cell("inline-xhigh", measured_external=True),
        "max": _cell("max"),
    },
}


def render_cell(cell, output, agent_available=True, uses_rf=False, max_findings=None):  # Ns()
    if cell == "low":
        return KT(uses_rf, 4 if max_findings is None else max_findings)
    if cell == "low-targeted":
        return XT(uses_rf)
    if cell == "medium":
        return JT(output, agent_available, 8 if max_findings is None else max_findings)
    if cell == "high":
        return ZT_HIGH(output, agent_available, 10 if max_findings is None else max_findings)
    if cell == "xhigh":
        return TO(output, agent_available, 15 if max_findings is None else max_findings)
    if cell == "max":
        return OO(output, agent_available, 15 if max_findings is None else max_findings)
    if cell == "inline-low":
        return SO(uses_rf, 8 if max_findings is None else max_findings)
    if cell == "inline-medium":
        return LO(output, max_findings)
    if cell == "inline-high":
        return CO(output, max_findings)
    if cell == "inline-xhigh":
        return UO(output, 15 if max_findings is None else max_findings)
    if cell == "minimal-pass":
        return QE(15 if max_findings is None else max_findings, uses_rf)
    raise ValueError(f"unknown cell {cell!r}")


# --------------------------------------------------------------------------
# Argument parsing (Ve / Te / Ze / R1)
# --------------------------------------------------------------------------

def parse_level(token):  # R1(e)
    n = token.strip().lower()
    r = LEVEL_ALIASES.get(n, n)
    return r if r in LEVELS else None


def split_flags(text, flag_names):  # Ve(e, n)
    s = text.strip()
    raw_first = s.split(None, 1)[0] if s else ""
    flags = set()
    rest = s
    for flag in flag_names:
        new = re.sub(rf"(?:^|\s)--{re.escape(flag)}(?=\s|$)", "", rest)
        if new != rest:
            flags.add(flag)
            rest = new.strip()
    return raw_first, flags, rest


def join_target(tokens):  # Ze(e)
    if not tokens:
        return ""
    first = tokens[0].replace("`", "")
    first = re.sub(r"^#", "", first)
    return " ".join(t for t in [first, *tokens[1:]] if t)


def parse_args(raw):  # Te(e)
    raw_first, _, _ = split_flags(raw, [])
    stripped = MAX_FINDINGS_RE.sub(r"\1", raw)
    _, flags, rest = split_flags(stripped, ["comment", "fix", "post", "no-post"])
    comment = "comment" in flags
    fix = "fix" in flags
    post = "post" in flags
    matches = list(MAX_FINDINGS_RE.finditer(raw))
    last = matches[-1] if matches else None
    c = (last.group(2) or "").lower() if last else None
    s = int(c) if (c is not None and re.fullmatch(r"\d+", c)) else 0
    if c in ("all", "default"):
        max_findings = c
    elif 0 < s <= 2**53 - 1:
        max_findings = s
    else:
        max_findings = None
    max_findings_ignored = c is not None and max_findings is None
    tokens = [t for t in rest.split() if t]
    first = tokens[0] if tokens else ""
    base = {
        "comment": comment, "fix": fix, "post": post,
        "maxFindings": max_findings, "maxFindingsIgnored": max_findings_ignored,
        "unrecognizedLevel": None, "ultraFallback": False, "explicit": None,
    }
    if raw_first.lower() == "ultra":
        return {**base, "target": join_target(tokens[1:]), "ultraFallback": True}
    level = None if first.lower() == "ultra" else parse_level(first)
    if level is not None:
        return {**base, "explicit": level, "target": join_target(tokens[1:])}
    near_miss = bool(NEAR_MISS_LEVEL.match(first)) if first else False
    return {
        **base,
        "target": join_target(tokens),
        "unrecognizedLevel": first if near_miss else None,
    }


# --------------------------------------------------------------------------
# Sticky state (codeReviewLastEffort / codeReviewLastMaxFindings)
# --------------------------------------------------------------------------

def state_path():
    override = os.environ.get("CODE_REVIEW_SKILL_STATE")
    return Path(override).expanduser() if override else DEFAULT_STATE_FILE


def state_enabled():
    return str(state_path()) != "/dev/null"


def read_state():
    if not state_enabled():
        return {}
    try:
        p = state_path()
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(data):
    if not state_enabled():
        return
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def last_effort(state):  # qs()
    e = state.get("codeReviewLastEffort")
    return e if isinstance(e, str) and e in LEVELS else None


def valid_last_max(value):
    return value == "all" or (isinstance(value, int) and not isinstance(value, bool) and value > 0)


def remember_typed_args(parsed):  # onUserTypedArgs()
    saved = []
    if not state_enabled():
        return saved
    state = read_state()
    changed = False
    if parsed["explicit"] is not None and state.get("codeReviewLastEffort") != parsed["explicit"]:
        state["codeReviewLastEffort"] = parsed["explicit"]
        saved.append(f"effort={parsed['explicit']}")
        changed = True
    mf = parsed["maxFindings"]
    if mf is not None:
        new = None if mf == "default" else mf
        if state.get("codeReviewLastMaxFindings") != new:
            if new is None:
                state.pop("codeReviewLastMaxFindings", None)
                saved.append("max-findings cleared")
            else:
                state["codeReviewLastMaxFindings"] = new
                saved.append(f"max-findings={new}")
            changed = True
    if changed:
        write_state(state)
    return saved


# --------------------------------------------------------------------------
# Level and max-findings resolution (st / vo / yo / wo)
# --------------------------------------------------------------------------

def reusable_last_level(parsed, state):  # st()
    if parsed["explicit"] is None and not parsed["ultraFallback"]:
        return last_effort(state)
    return None


def resolve_level(parsed, state, session_effort):  # vo() + jI()
    if parsed["ultraFallback"]:
        return "max", "ultra_fallback"
    if parsed["explicit"] is not None:
        return parsed["explicit"], "explicit"
    last = reusable_last_level(parsed, state)
    if last is not None:
        return last, "last_used"
    se = (session_effort or "").strip()
    if se and not se.startswith("${"):
        return (se if se in LEVELS else "high"), "session_effort"
    return "medium", "default"


def resolve_max_findings(parsed, cell, uses_rf, state):  # yo()
    h = state.get("codeReviewLastMaxFindings")
    reused = parsed["maxFindings"] is None and cell != "low-targeted" and valid_last_max(h)
    asked = h if reused else (None if parsed["maxFindings"] == "default" else parsed["maxFindings"])
    if cell == "low-targeted":
        stated = None
    elif (uses_rf or cell == "minimal-pass") and (asked == "all" or (asked is not None and asked > RF_HARD_CAP)):
        stated = RF_HARD_CAP
    else:
        stated = asked
    return {"asked": asked, "stated": stated, "reused": reused}


def max_findings_notice(asked, stated, reused, ignored, who):  # wo()
    out = []
    if ignored:
        out.append(
            "`--max-findings` was ignored: type a whole number above zero, `all`, or `default` after it."
            + ("" if reused else " Using the usual limit.")
        )
    cap_note = f"This review reports at most {RF_HARD_CAP} findings, so the limit is {RF_HARD_CAP} here."
    if asked is not None and stated is None:
        out.append("`--max-findings` does not change this review: low effort here has no maximum.")
    elif stated != asked:
        out.append(
            f"{'You' if who == 'you' else 'The user'} set `--max-findings {asked}` last time. {cap_note}"
            if reused
            else cap_note
        )
    elif reused and asked is not None:
        out.append(
            f"Reporting every finding, as {who} chose last time with `--max-findings all`."
            if asked == "all"
            else f"Reporting up to {asked} {plural(asked, 'finding')}, the limit {who} set last time with `--max-findings`."
        )
    return " ".join(out)


# --------------------------------------------------------------------------
# Preamble notices (Zs) and finder budget (Js / Qs)
# --------------------------------------------------------------------------

def preamble(parsed, level, last_used, as_fork, gitlab):  # Zs()
    comment, post, fix = parsed["comment"], parsed["post"], parsed["fix"]
    if comment:
        why = (
            "when the target is a GitLab merge request, your `--comment` is what posts the findings as one general MR note via glab"
            if gitlab
            else "when the target is a GitHub PR, your `--comment` is what posts the findings as inline PR comments"
        )
    else:
        why = (
            "this local review will not post to GitLab; `--comment` is the flag that posts local findings as a general MR note"
            if gitlab
            else "this local review will not post to GitHub; `--comment` is the flag that posts local findings as inline PR comments"
        )

    def with_post_notice(p):
        if not post:
            return p
        return (
            f"{p}(The typed `--post` applies only to the `/code-review ultra` cloud review and was ignored "
            f"— {why}. Tell the user this in one short line.)\n\n"
        )

    if parsed["ultraFallback"]:
        # The skill never has cloud-review access: the built-in's "not available" branch.
        if fix:
            return with_post_notice(f"(Running a local {level}-effort review and applying its findings.)\n\n")
        return with_post_notice(
            f"(ultra (cloud review) isn't available in this environment. Falling back to a local {level}-effort review.)\n\n"
        )

    change_hint = "typing a level (for example `/code-review high`) changes it"

    def wrap(p):
        if as_fork:
            return (
                f"({p} Open your report with one short line telling the user this, and that {change_hint}; "
                "that opening line reaches them with the findings.)\n\n"
            )
        return f"({p} Tell the user this in one short line as you begin, including that {change_hint}.)\n\n"

    if parsed["unrecognizedLevel"] is not None:
        p = (
            f"Ignoring unrecognized effort \"{parsed['unrecognizedLevel']}\"; valid: {', '.join(LEVELS)}. "
            f"Using {level}{', the level the user typed last time' if last_used == level else ''}."
        )
        return with_post_notice(wrap(p) if last_used is not None else f"({p})\n\n")
    if last_used is not None:
        p = f"reusing {last_used}, the level the user typed last time{f'; running at {level} here' if level != last_used else ''}"
        return with_post_notice(wrap(f"No effort level given — {p}."))
    return with_post_notice("")


def max_findings_preamble(notice, reused, as_fork):  # Ce in Xs()
    if not notice:
        return ""
    back = "passing `--max-findings default` goes back to the usual limit" if reused else None
    if as_fork:
        return (
            f"({notice} Open your report with one short line telling the user this"
            f"{f', and that {back}' if back else ''}; that opening line reaches them with the findings.)\n\n"
        )
    return f"({notice} Tell the user this in one short line as you begin{f', including that {back}' if back else ''}.)\n\n"


def git_numstat_lines(range_spec):  # Qs()
    cmd = [
        "git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "-c", "core.askPass=",
        "diff", "--no-ext-diff", "--no-textconv", "--numstat", "--end-of-options", range_spec, "--",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    total = 0
    for line in proc.stdout.split("\n"):
        m = re.match(r"^(\d+)\t(\d+)\t", line)
        if m:
            total += int(m.group(1)) + int(m.group(2))
    return total if total > 0 else None


def diff_size(target):
    if not target:
        return git_numstat_lines("@{upstream}...HEAD"), "@{upstream}...HEAD"
    if len(target) <= 256 and RANGE_RE.match(target):
        return git_numstat_lines(target), target
    return None, None


def finder_budget(lines):
    return max(2, min(8, math.ceil(lines / 150)))


def finder_budget_hint(target, lines):  # Js() text
    if lines is None:
        return ""
    y = finder_budget(lines)
    if not target:
        return (
            f"The committed diff (@{{upstream}}...HEAD) is about {lines} lines. Uncommitted changes aren't counted here, "
            f"so treat this as a floor — start with about {y} finder subagents (min 2, max 8) and scale up if Phase 0 "
            "finds additional working-tree scope.\n\n"
        )
    return (
        f"This diff is about {lines} lines. Spawn about {y} finder subagents (min 2, max 8) — scale your "
        "investigation depth to the diff size rather than using a fixed large fleet.\n\n"
    )


# --------------------------------------------------------------------------
# Host detection (GitHub vs GitLab)
# --------------------------------------------------------------------------

def gitlab_mr_iid(target):  # eei() approximation
    for pat in (r"^!(\d+)$", r"/merge_requests/(\d+)", r"^(\d+)$"):
        m = re.search(pat, target or "")
        if m:
            return m.group(1)
    return None


def gitlab_repo(target):  # tei() approximation
    m = re.match(r"^https?://[^/]+/(.+?)/-/merge_requests/\d+", target or "")
    return m.group(1) if m else None


def origin_url():
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def detect_host(target):  # ZZs() approximation
    t = target or ""
    if re.match(r"^!\d+$", t) or "/-/merge_requests/" in t or "gitlab" in t.lower():
        return "gitlab"
    if "github.com" in t.lower() or "/pull/" in t:
        return "github"
    if re.match(r"^https?://", t):
        return "github"
    return "gitlab" if "gitlab" in origin_url().lower() else "github"


def report_findings_candidate():  # nt() approximation (environment part)
    mode = os.environ.get("CODE_REVIEW_SKILL_RF_MODE")
    if mode is not None:
        if mode not in ("none", "spawn", "message"):
            raise ValueError("CODE_REVIEW_SKILL_RF_MODE must be none|spawn|message")
        return mode
    return "none"


# --------------------------------------------------------------------------
# Profile normalization
# --------------------------------------------------------------------------

def normalize_profile(value):
    v = (value or "").strip().lower()
    return v if v in PROFILES else "default"


# --------------------------------------------------------------------------
# Full prompt assembly (Xs)
# --------------------------------------------------------------------------

def assemble(raw, *, profile, agent_available, rf_mode, verify, as_fork, session_effort):
    parsed = parse_args(raw)
    state = read_state()
    level, _ = resolve_level(parsed, state, session_effort)
    last_used = reusable_last_level(parsed, state)
    fam = normalize_profile(os.environ.get("CODE_REVIEW_SKILL_PROFILE", profile))
    route = ROUTING[fam][level]
    cell = route["cell"]
    uses_rf = rf_mode in ("spawn", "message")
    output = VT if uses_rf else ZT
    mf = resolve_max_findings(parsed, cell, uses_rf, state)
    target = parsed["target"]
    gitlab = detect_host(target) == "gitlab"
    notice = max_findings_notice(mf["asked"], mf["stated"], mf["reused"], parsed["maxFindingsIgnored"], "the user")
    parts = [
        preamble(parsed, level, last_used, as_fork, gitlab),
        max_findings_preamble(notice, mf["reused"], as_fork),
        f"Review target: `{target}`\n\n" if target else "",
    ]
    if route["finderBudgetHint"] and agent_available:
        lines, _ = diff_size(target)
        parts.append(finder_budget_hint(target, lines))
    parts.append(render_cell(cell, output, agent_available, uses_rf, mf["stated"]))
    if parsed["comment"]:
        parts.append(US(target) if gitlab else MS)
    if parsed["fix"]:
        parts.append(BS_FIX(uses_rf))
    is_bmin = cell == "minimal-pass"
    if uses_rf and not is_bmin:
        parts.append(JS)
    if rf_mode == "message" and not is_bmin:
        parts.append(FS)
    if verify and rf_mode == "spawn" and not route["measuredExternal"]:
        parts.append(HS)
    return "".join(parts), {"level": level, "profile": fam, "cell": cell, "maxFindings": mf}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def read_raw_args(argv):
    """Return (raw_args_string, remaining_options)."""
    if "--stdin" in argv:
        argv = [a for a in argv if a != "--stdin"]
        raw = sys.stdin.read()
        return raw.strip("\r\n"), argv
    if "--" in argv:
        i = argv.index("--")
        return " ".join(argv[i + 1:]), argv[:i]
    return "", argv


def option(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def cmd_parse(argv):
    raw, opts = read_raw_args(argv)
    as_fork = "--fork" in opts
    session_effort = option(opts, "--session-effort", "")
    parsed = parse_args(raw)
    state_before = read_state()
    saved = remember_typed_args(parsed)
    level, source = resolve_level(parsed, state_before, session_effort)
    last_used = reusable_last_level(parsed, state_before)
    target = parsed["target"]
    host = detect_host(target)
    lines, measured = diff_size(target)
    budget = finder_budget(lines) if lines else None
    rf_candidate = report_findings_candidate()
    flags = " ".join(
        f"{k}={'yes' if parsed[k] else 'no'}" for k in ("comment", "fix", "post")
    )
    mf = parsed["maxFindings"]
    last_mf = state_before.get("codeReviewLastMaxFindings")
    script = Path(__file__).resolve()
    render_cmd = (
        f"python3 -I {shlex.quote(str(script))} render {'--fork' if as_fork else '--inline'}"
        f" --session-effort {shlex.quote(session_effort or '')}"
        " --profile <PROFILE> --agent <yes|no> --rf <none|spawn|message> --verify <yes|no>"
        f" -- {shlex.quote(raw)}"
    )
    print("<!-- code-review ROUTING (generated by scripts/review.py parse) -->")
    print(f"raw_args: {raw!r}")
    print(f"level: {level} (source: {source})")
    print(f"last_used_level: {last_used or 'none'}")
    print(f"unrecognized_level: {parsed['unrecognizedLevel'] or 'none'}")
    print(f"ultra_fallback: {'yes' if parsed['ultraFallback'] else 'no'}")
    print(f"target: {target!r}" if target else "target: none (review the current diff)")
    print(f"host: {host}")
    print(f"flags: {flags}")
    print(
        "max_findings: "
        + (f"typed={mf}" if mf is not None else "typed=none")
        + f" last_used={last_mf if valid_last_max(last_mf) else 'none'}"
        + f" ignored={'yes' if parsed['maxFindingsIgnored'] else 'no'}"
    )
    print(f"diff_lines: {lines if lines else 'unknown'}" + (f" ({measured})" if measured else ""))
    print(f"finder_budget: {budget if budget else 'n/a'} (used only when the routing table says so)")
    print(f"rf_candidate: {rf_candidate} (use it only if {RF} is in your tool list; otherwise none)")
    print(f"state_file: {state_path()}" + (f" (saved: {', '.join(saved)})" if saved else " (unchanged)"))
    print("next: " + render_cmd)
    print("<!-- end ROUTING -->")


def cmd_render(argv):
    raw, opts = read_raw_args(argv)
    as_fork = "--fork" in opts
    session_effort = option(opts, "--session-effort", "")
    profile = option(opts, "--profile", "default")
    agent = option(opts, "--agent", "yes").lower() in ("yes", "y", "true", "1")
    rf_mode = option(opts, "--rf", "none").lower()
    if rf_mode not in ("none", "spawn", "message"):
        print(f"review.py: --rf must be none|spawn|message, got {rf_mode!r}", file=sys.stderr)
        return 2
    verify = option(opts, "--verify", "no").lower() in ("yes", "y", "true", "1")
    try:
        text, _ = assemble(
            raw, profile=profile, agent_available=agent, rf_mode=rf_mode, verify=verify,
            as_fork=as_fork, session_effort=session_effort,
        )
    except Exception as exc:  # noqa: BLE001 - report and let the skill fall back
        print(f"review.py: cannot render the review prompt: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


def cmd_state(argv):
    action = argv[0] if argv else "get"
    state = read_state()
    if action == "get":
        print(json.dumps({"path": str(state_path()), "state": state}, indent=2))
        return 0
    if action == "set-effort" and len(argv) > 1:
        level = parse_level(argv[1])
        if level is None:
            print(f"review.py: not a level: {argv[1]!r}", file=sys.stderr)
            return 2
        state["codeReviewLastEffort"] = level
    elif action == "set-max" and len(argv) > 1:
        v = argv[1].lower()
        if v == "all":
            state["codeReviewLastMaxFindings"] = "all"
        elif v.isdigit() and int(v) > 0:
            state["codeReviewLastMaxFindings"] = int(v)
        else:
            print(f"review.py: not a limit: {argv[1]!r}", file=sys.stderr)
            return 2
    elif action == "clear-max":
        state.pop("codeReviewLastMaxFindings", None)
    elif action == "reset":
        state = {}
    else:
        print(__doc__, file=sys.stderr)
        return 2
    write_state(state)
    print(json.dumps({"path": str(state_path()), "state": state}, indent=2))
    return 0


def cmd_numstat(argv):
    spec = argv[0] if argv else "@{upstream}...HEAD"
    lines = git_numstat_lines(spec)
    if lines is None:
        print(f"range: {spec}\nlines: unknown")
        return 0
    print(f"range: {spec}\nlines: {lines}\nfinder_budget: {finder_budget(lines)}")
    return 0


BLOCK_FILES = {
    "phase-and-angles.md": [
        ("X", "Phase 0 — Gather the diff", X),
        ("gs", "Angle A — line-by-line diff scan", GS),
        ("ys", "Angle B — removed-behavior auditor", YS),
        ("ws", "Angle C — cross-file tracer", WS),
        ("vs", "Angle D — language-pitfall specialist", VS),
        ("bs", "Angle E — wrapper/proxy correctness", BS),
        ("ye", "Reuse (core sentence, shared with /simplify)", YE),
        ("Gt", "Reuse (angle heading + intro, subagent cells)", GT),
        ("no", "Reuse (angle heading + intro, inline inline cells; same text)", NO),
        ("Q", "Simplification", Q),
        ("Z", "Efficiency", Z),
        ("oe", "Altitude", OE),
        ("ue", "Conventions (applicable agent instructions)", UE),
        ("re", "Cleanup candidate shape and ranking note", RE_NOTE),
        ("Ke", "Agent-tool-unavailable sentence", KE),
    ],
    "verify-and-sweep.md": [
        ("Cs", "Verifier states (3-state)", CS),
        ("_s", "Recall-biased verifier rules (PLAUSIBLE by default)", UNDERSCORE_S),
        ("qt", "Phase 2 — Verify (1-vote, 3-state)", QT),
        ("Ss", "Phase 2 — Verify (1-vote, recall-biased)", SS),
        ("Yt", "Sweep focus list", YT),
        ("Es", "Phase 3 — Sweep for gaps (subagent version)", ES),
        ("As", "Inline fallback lead-in", AS),
        ("xs", "Inline fallback footer", XS_FOOTER),
    ],
    "outputs.md": [
        ("zt(8)", "JSON output contract (cap 8 shown)", ZT(8)),
        ("zt(all)", "JSON output contract (no maximum)", ZT("all")),
        ("Vt(8)", "ReportFindings output contract (cap 8 shown)", VT(8)),
        ("Vt(all)", "ReportFindings output contract (no maximum)", VT("all")),
        ("io", "Findings floor inserted by the inline inline cells (floor 5 shown)",
         IO(ZT, 10)(10).split("## Output\n", 1)[1].split("Return findings", 1)[0]),
    ],
    "cells.md": [
        ("Kt", "cell `low` (JSON mode, cap 4)", KT(False)),
        ("Kt+RF", "cell `low` (ReportFindings mode, cap 4)", KT(True)),
        ("Xt", "cell `low-targeted` (JSON mode)", XT(False)),
        ("Xt+RF", "cell `low-targeted` (ReportFindings mode)", XT(True)),
        ("so", "cell `inline-low` (JSON mode, cap 8)", SO(False)),
        ("so+RF", "cell `inline-low` (ReportFindings mode, cap 8)", SO(True)),
        ("Jt", "cell `medium` (JSON mode, cap 8)", JT(ZT)),
        ("Jt-noagent", "cell `medium` when the subagent tool is unavailable (Xe)", JT(ZT, False)),
        ("Zt", "cell `high` (JSON mode, cap 10)", ZT_HIGH(ZT)),
        ("Zt-noagent", "cell `high` when the subagent tool is unavailable (Xe)", ZT_HIGH(ZT, False)),
        ("to", "cell `xhigh` (JSON mode, cap 15)", TO(ZT)),
        ("oo", "cell `max` (JSON mode, cap 15)", OO(ZT)),
        ("oo-noagent", "cell `max` when the subagent tool is unavailable (Xe + sweep)", OO(ZT, False)),
        ("lo", "cell `inline-medium` (JSON mode, cap 8, floor 4)", LO(ZT)),
        ("co", "cell `inline-high` (JSON mode, cap 10, floor 5)", CO(ZT)),
        ("uo", "cell `inline-xhigh` (JSON mode, cap 15, floor 7)", UO(ZT)),
        ("Qe", "cell `minimal-pass` (cap 15; ReportFindings mode)", QE()),
        ("Qe-text", "cell `minimal-pass` (cap 15; text fallback)", QE(uses_rf=False)),
    ],
    "flags.md": [
        ("Ms", "--comment, GitHub", MS),
        ("Us", "--comment, GitLab (target `!12` shown)", US("!12")),
        ("Bs", "--fix (JSON mode)", BS_FIX(False)),
        ("Bs+RF", "--fix (ReportFindings mode)", BS_FIX(True)),
        ("js", "If findings are fixed later (ReportFindings mode)", JS),
        ("Fs", "Echo line (ReportFindings message mode)", FS),
        ("Hs", "After the review (/verify hand-off)", HS),
    ],
}


def _notices_markdown():
    rows = []
    base = {"comment": False, "fix": False, "post": False, "maxFindings": None, "maxFindingsIgnored": False,
            "unrecognizedLevel": None, "ultraFallback": False, "explicit": None, "target": ""}
    samples = [
        ("Zs: reuse last level, inline", preamble({**base}, "high", "high", False, False)),
        ("Zs: reuse last level, fork", preamble({**base}, "high", "high", True, False)),
        ("Zs: unrecognized level, no last level", preamble({**base, "unrecognizedLevel": "highest"}, "medium", None, False, False)),
        ("Zs: unrecognized level, with last level", preamble({**base, "unrecognizedLevel": "highest"}, "high", "high", False, False)),
        ("Zs: ultra fallback", preamble({**base, "ultraFallback": True}, "max", None, False, False)),
        ("Zs: ultra fallback with --fix", preamble({**base, "ultraFallback": True, "fix": True}, "max", None, False, False)),
        ("Zs: --post ignored (GitHub, no --comment)", preamble({**base, "post": True}, "medium", None, False, False)),
        ("Zs: --post ignored (GitLab, with --comment)", preamble({**base, "post": True, "comment": True}, "medium", None, False, True)),
        ("wo: reused numeric limit", max_findings_notice(3, 3, True, False, "the user")),
        ("wo: reused `all`", max_findings_notice("all", "all", True, False, "the user")),
        ("wo: reused limit above the 32 cap (RF mode)", max_findings_notice(40, 32, True, False, "the user")),
        ("wo: typed limit above the 32 cap (RF mode)", max_findings_notice(40, 32, False, False, "the user")),
        ("wo: ignored value", max_findings_notice(None, None, False, True, "the user")),
        ("wo: low-targeted has no maximum", max_findings_notice(5, None, False, False, "the user")),
        ("Ce: inline wrapper", max_findings_preamble("Reporting up to 3 findings, the limit the user set last time with `--max-findings`.", True, False)),
        ("Ce: fork wrapper", max_findings_preamble("Reporting up to 3 findings, the limit the user set last time with `--max-findings`.", True, True)),
        ("Js: upstream range", finder_budget_hint("", 420)),
        ("Js: explicit range target", finder_budget_hint("main...HEAD", 420)),
    ]
    for title, text in samples:
        rows.append((title.split(":")[0], title, text))
    return rows


def cmd_dump_blocks(argv):
    if not argv:
        print("usage: review.py dump-blocks DIR", file=sys.stderr)
        return 2
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    files = dict(BLOCK_FILES)
    files["notices.md"] = _notices_markdown()
    header = (
        "<!-- GENERATED by `python3 -I scripts/review.py dump-blocks references/blocks` — do not edit by hand.\n"
        "     Source of truth: scripts/review.py (portable review recipes).\n"
        "     Each block is shown between ~~~~ fences; the fences are not part of the text. -->\n\n"
    )
    for name, blocks in files.items():
        title = name[:-3].replace("-", " ").title()
        body = [header, f"# {title}\n\n"]
        for js_name, label, text in blocks:
            body.append(f"<!-- block: {js_name} -->\n## `{js_name}` — {label}\n\n~~~~text\n{text}\n~~~~\n\n")
        (out / name).write_text("".join(body), encoding="utf-8")
        print(f"wrote {out / name}")
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "parse":
        try:
            cmd_parse(rest)
        except Exception as exc:  # noqa: BLE001 - never abort the skill load
            print("<!-- code-review ROUTING (generated by scripts/review.py parse) -->")
            print(f"error: {exc}")
            print("Fall back to the manual procedure in SKILL.md.")
            print("<!-- end ROUTING -->")
        return 0
    if cmd == "render":
        return cmd_render(rest)
    if cmd == "state":
        return cmd_state(rest)
    if cmd == "numstat":
        return cmd_numstat(rest)
    if cmd == "dump-blocks":
        return cmd_dump_blocks(rest)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
