---
name: code-review
description: >-
  Review the current diff, or a PR number/branch/path target, for correctness
  bugs (plus reuse/simplification/efficiency cleanups where the selected profile
  covers them) at the effort level given first, before any target
  (low|medium|high|xhigh|max: from few high-confidence findings to many, some
  uncertain); with no level given, it reuses the level you typed last. Pass
  --comment to post findings as inline PR comments, or --fix to apply them to
  the working tree after the review. Pass --max-findings N to report up to n
  findings, or --max-findings all for every finding. The choice stays until
  you pass --max-findings default.
license: MIT
metadata:
  version: 0.1.0
  category: code-quality
  tags: [code-review, pull-request, diff, bugs, correctness]
---

# Code review

Review code with portable argument routing, sticky options, effort-level
recipes and output contracts. `scripts/review.py` parses arguments, remembers
options, measures the diff and assembles the review prompt.

Requirements: macOS or Linux, git, Python 3.9+, shell execution, file reading
and text search. Use the host's tools for these capabilities; tool names vary.
Subagents and ReportFindings are optional. Skill instructions do not grant
permissions beyond those of the current environment.

Use `max` only when the user or applicable instructions explicitly request it;
it is the costliest level and can start many reviewer subagents.

Arguments after the host's skill invocation:

```text
[low|medium|high|xhigh|max] [--fix] [--comment] [PR-number|branch|path] [--max-findings N|all|default]
```

## Routing

Resolve this skill's directory from the loaded SKILL.md location. Run the
following with the user's review arguments and the session effort if known
(otherwise pass an empty string). Quote arguments as shell data; alternatively
use `--stdin` to pass the complete argument string through standard input.
Do not depend on host-specific variable substitution or load-time execution.

```bash
python3 -I "<this skill's directory>/scripts/review.py" parse --inline --session-effort "<session effort or empty>" -- <arguments>
```

`parse` records an explicit level or finding limit so later reviews reuse it.
If Python or shell execution is unavailable, use the manual procedure below.

## Step 1 — decide what the script cannot know

1. **Review profile** (`--profile`). Use `default` unless the user requests
   `parallel`, `inline`, or `minimal`. `CODE_REVIEW_SKILL_PROFILE` overrides
   this choice. Unknown values fall back to `default`. Never select a profile
   from the model ID. The table below defines each profile at each effort.
2. **Subagents** (`--agent`). Use `yes` only if the host provides a permitted
   subagent capability; otherwise `no`. Map references to the subagent tool
   in the rendered prompt to that capability.
3. **ReportFindings** (`--rf`). Use the ROUTING block's `rf_candidate` only if
   `ReportFindings` is in your current tool list; otherwise `none`. Pass
   `--verify yes` only when `--rf spawn` and a `/verify` skill is listed in your
   available skills; otherwise `--verify no`.

## Step 2 — render and run the recipe

Run the `next:` command from the ROUTING block with the four placeholders
filled in, through the host's shell tool. Its output is the complete review prompt:
the notices to open with, the review target, the recipe for the resolved level
(phases, finder angles, verification, output contract) and the blocks for
`--comment` and `--fix` when those flags were passed. Follow it to the letter.
The recipe is self-contained; do not read `references/` unless the command
fails (exit code 2), in which case use the manual procedure at the end.

## Step 3 — print the report (JSON contract only)

The built-in ran as a forked subagent and the harness turned its JSON array
into a readable list. Here nothing reformats your output, so when the prompt's
output contract is the JSON array (not a `ReportFindings` call), build the
array as instructed and print its rendering instead of the raw JSON:

- No findings: the single line `No findings.`
- Otherwise: `N findings, most severe first:` (for one finding: `1 finding:`),
  a blank line, then the entries separated by blank lines:

  ```text
  1. `path/to/file.ext:123`: one-sentence statement of the bug

     Failure scenario: concrete inputs/state → wrong output/crash
  ```

  Continuation lines are indented to the width of the `1. ` prefix.

The low-effort cells define their own one-line-per-finding format and the
`(none)` marker; keep those as written. When the contract is a `ReportFindings`
call, the tool call is the report; print text only when the recipe explicitly
requires an echo (message mode or `minimal-pass`).

## Routing table (the built-in's `ce` table)

| level  | `default` | `parallel`       | `inline`        | `minimal`         |
|--------|----------------------------------|-----------------|-----------------|-----------------|
| low    | `low`                            | `low-targeted`   | `inline-low`    | `low`           |
| medium | `inline-high`                    | `medium`        | `inline-medium`    | `minimal-pass`       |
| high   | `inline-high`                    | `high` †        | `inline-high`   | `minimal-pass`       |
| xhigh  | `inline-xhigh`                   | `xhigh` †       | `inline-xhigh`  | `inline-xhigh`  |
| max    | `max`                            | `max` †         | `max`           | `max`           |

† adds the finder-budget hint: about `ceil(diff lines / 150)` finder
subagents, between 2 and 8.

- `medium`, `high`, `xhigh`, `max`: finder angles run as independent subagents,
  one verifier per candidate, a sweep at `xhigh`/`max`. Without a permitted subagent tool
  they become a single-pass inline review that says so in its summary.
- `inline-*`: all angles run inline in your own context, dedup only, no verify,
  with a findings floor of half the cap.
- `minimal-pass`: a minimal single careful pass using `ReportFindings` when
  enabled, otherwise the text-rendered JSON contract.
- Default caps: `low` 4, `medium` 8, `high` 10, `xhigh`/`max` 15,
  `inline-low` 8, `inline-medium` 8, `inline-high` 10, `inline-xhigh` 15,
  `minimal-pass` 15. `low-targeted` has no cap (it targets `min(files, 4)`). In
  `ReportFindings` mode, and always for `minimal-pass`, the limit is 32 at most.

## Invariants

- The level is the first token: `low`, `medium` (`med`), `high`, `xhigh`,
  `max`. With no level: the level typed last, else your session effort, else
  `medium`. `ultra` is the cloud review, which a skill cannot start: it runs a
  local `max` review and opens with the "isn't available in this environment"
  notice.
- `--post` and `--no-post` belong to the cloud review; they are accepted and
  ignored with the notice the rendered prompt gives you. `--max-findings` takes
  a whole number, `all` or `default`; anything else is ignored with a notice.
- The notices in the rendered prompt reach the user as one short line at the
  start of your report.
- Never call `ReportFindings` when the contract is the JSON array, even if the
  tool is available. Never print the findings as text when the contract is a
  `ReportFindings` call, except for a recipe
  that explicitly requires a text echo.
- Correctness bugs outrank cleanup, altitude and conventions findings when the
  cap forces a cut.
- `--fix` applies the findings after the report; skip, and say why, any fix
  that would change intended behavior, reach well outside the diff, or looks
  like a false positive.

## Manual procedure (only if `review.py` cannot run)

Read `references/routing.md` for the argument grammar, the level and
`--max-findings` resolution rules, the state file and the routing table. Then
read `references/composition.md`, which lists for each cell the blocks that
make it up, in order, with their caps, and points to the generated block files
under `references/blocks/`. Assemble the prompt by hand, then continue with
Steps 1 to 3.
