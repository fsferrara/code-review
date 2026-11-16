# Routing: arguments, sticky options, level resolution, cells

This is the human-readable version of what `scripts/review.py parse` and
`render` do. It mirrors the built-in's `Te()`, `Ve()`, `Ze()`, `R1()`, `st()`,
`vo()`, `yo()`, `wo()`, `onUserTypedArgs()`, the `ce` table and `nt()`. Read it
to audit the script or to route by hand when the script cannot run.

## 1. Argument grammar (`Te`)

Input: the raw argument string typed after `/code-review`.

1. `rawFirstToken` = the first whitespace-separated token of the trimmed input
   (before any flag removal). If it is `ultra` (case-insensitive) the review is
   the **ultra fallback**: level `max`, target = the remaining tokens after
   removing flags and `--max-findings`.
2. Remove every `--max-findings` occurrence with the regex
   `(^|\s)--max-findings(?:(?:\s*=\s*|\s+)(?!--)(\S+)|\s*=\S*)?(?=\s|$)`; the
   **last** occurrence wins. Its value, lower-cased: `all` or `default` are
   kept as words; a whole number above zero is kept as a number; anything else
   (including no value, `0`, or a following flag) makes `maxFindingsIgnored`
   true.
3. Remove the flags `--comment`, `--fix`, `--post`, `--no-post` anywhere in the
   remaining string (`(?:^|\s)--<flag>(?=\s|$)`). `--post`/`--no-post` are
   recorded but only produce the "ignored" notice.
4. Split the rest on whitespace. If the first token is a level (`low`,
   `medium`, `high`, `xhigh`, `max`, case-insensitive; `med` means `medium`)
   it is the **explicit** level and the target is the remaining tokens.
   Otherwise, if it matches `^(low|med|hig|xhi|max)[a-z]*$` (case-insensitive)
   it is an **unrecognized level** (notice) and the whole rest is the target;
   else the whole rest is the target.
5. Target normalization (`Ze`): strip backticks from the first token and a
   leading `#` (so `` `#123` `` becomes `123`), then join with single spaces.

## 2. Sticky options and state file

Sticky options are stored in a JSON file:

- Explicit `$CODE_REVIEW_SKILL_STATE` takes precedence; `/dev/null` disables state.
- Otherwise use `~/.agents/code-review-skill-state.json`. No other state
  paths are searched automatically. Existing files elsewhere are untouched.
- Shape: `{"codeReviewLastEffort": "high", "codeReviewLastMaxFindings": 3}`.
  The limit may also be `"all"`; absence means the usual limit.
- `parse` stores explicit levels and limits. `--max-findings default` clears
  the stored limit. Invalid values do not overwrite state.
- Manual management: `python3 -I scripts/review.py state get|set-effort L|set-max N|all|clear-max|reset`.

## 3. Level resolution (`vo` + `st` + `jI`)

In order:

1. ultra fallback → `max`
2. explicit level
3. `codeReviewLastEffort` from the state file ("last used"; produces the
   "No effort level given — reusing X" notice)
4. the session effort (`--session-effort`, supplied by the host when known): a level name
   is used as is; any other non-empty value means `high`
5. `medium`

## 4. `--max-findings` resolution (`yo`) and notice (`wo`)

- `reused` = no `--max-findings` typed, cell is not `low-targeted`, and the
  state file holds a valid value (`all` or a whole number above zero).
- `asked` = the stored value when reused; else the typed value (`default`
  means none).
- `stated` (the cap actually written into the prompt) = none for
  `low-targeted`; `32` when ReportFindings is used or the cell is `minimal-pass`
  and `asked` is `all` or above 32; else `asked`.
- Notice text (`wo`), joined with spaces, in this order when they apply:
  - ignored value: "`--max-findings` was ignored: type a whole number above
    zero, `all`, or `default` after it." plus " Using the usual limit." unless
    reused;
  - `asked` set but `stated` none: "`--max-findings` does not change this
    review: low effort here has no maximum.";
  - `stated` differs from `asked`: "The user set `--max-findings N` last time. "
    (only when reused) + "This review reports at most 32 findings, so the
    limit is 32 here.";
  - reused and equal: "Reporting up to N findings, the limit the user set last
    time with `--max-findings`." or "Reporting every finding, as the user chose
    last time with `--max-findings all`."
- The notice is wrapped (`Ce`) as "(… Tell the user this in one short line as
  you begin[, including that passing `--max-findings default` goes back to
  the usual limit].)" inline, or the "Open your report with one short line…"
  form when the review runs as a fork.

## 5. Preamble notices (`Zs`)

See `blocks/notices.md` for the exact strings. Order of precedence: ultra
fallback (with or without `--fix`) → unrecognized level (wrapped only when a
last-used level exists) → "No effort level given — reusing X" → nothing. A
typed `--post` appends its own "(The typed `--post` applies only to the
`/code-review ultra` cloud review and was ignored — …)" paragraph in every
case. The wording of the `--post` paragraph depends on whether `--comment` was
passed and whether the target is a GitLab merge request.

## 6. Review profile and cell

Profiles are independent of model identity. `CODE_REVIEW_SKILL_PROFILE` takes
precedence over `--profile`; without either, use `default`. Values are trimmed
and lowercased; unknown values fall back to `default`.

| level  | default        | parallel          | inline          | minimal           |
|--------|----------------|------------------|-----------------|-----------------|
| low    | `low`          | `low-targeted` ‡  | `inline-low` *  | `low`           |
| medium | `inline-high` *| `medium`         | `inline-medium` *  | `minimal-pass` *     |
| high   | `inline-high` *| `high` †         | `inline-high` * | `minimal-pass` *     |
| xhigh  | `inline-xhigh`*| `xhigh` †        | `inline-xhigh` *| `inline-xhigh` *|
| max    | `max`          | `max` †          | `max`           | `max`           |

† `finderBudgetHint`: when a permitted subagent tool is available, measure the diff with
`git diff --numstat` (range `@{upstream}...HEAD`, or the target when it looks
like a range) and prepend the hint with `ceil(lines/150)` subagents clamped to
2–8. ‡ the retained effort hint is `medium`; the skill cannot change host reasoning settings.
`*` `measuredExternal`: the built-in never appends the `/verify` hand-off to
these cells.

## 7. Output mode (`nt`)

Use `ReportFindings` only when the actual tool is available. Otherwise pass
`--rf none`, including for `minimal-pass`, which keeps its minimal review but uses
the text-rendered JSON contract.

`parse` reports an environment-based `rf_candidate`:

1. `CODE_REVIEW_SKILL_RF_MODE=none|spawn|message`, if set (invalid values
   produce a routing error and require correction).
2. Otherwise `none`.

These signals select a candidate, not permission or proof of tool availability.

Differences between the modes in the assembled prompt:

- `spawn`/`message`: `Vt` output contract, hard cap 32, "If findings are fixed
  later" block (except `minimal-pass`); `--fix` ends with the re-report instruction.
- `message` only: the `Fs` echo line ("After the call, give one line per
  finding with its outcome…"), except `minimal-pass`.
- `spawn` only: the "After the review" `/verify` hand-off, when a `/verify`
  skill exists and the cell is not `measuredExternal`.
- `none`: `zt` JSON contract; the model prints the rendered list itself (see
  SKILL.md Step 3 and `blocks/outputs.md`).

## 8. Manual fallback procedure

1. Parse the arguments with sections 1–4; read the state file by hand.
2. Pick the profile and cell with section 6; check whether permitted subagent capabilities and
   `ReportFindings` are available (section 7).
3. Open `composition.md` and assemble the cell from the blocks it lists.
4. Prepend the notices (section 5, `blocks/notices.md`), the
   `Review target: \`<target>\`` line when a target was given, and the finder
   budget hint when the table says so. Append the `--comment` / `--fix` /
   fixed-later / `/verify` blocks per section 7.
5. Update the state file as section 2 describes.
