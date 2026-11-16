# Composition: how each recipe cell is assembled

Recipe cell dispatch and full prompt assembly, implemented by `scripts/review.py render`. Block names
in backticks are the built-in's minified variable names; each block's generated
text is in `blocks/*.md` under the same name.

## Full prompt order (`Xs`)

1. Preamble notice (`Zs`): ultra fallback / unrecognized level / reused level,
   plus the `--post` paragraph. May be empty.
2. `--max-findings` notice wrapper (`Ce` around `wo`). May be empty.
3. `Review target: \`<target>\`` + blank line, only when a target was given.
4. Finder budget hint (`Js`), only for cells marked † in `routing.md` and
   only when a permitted subagent tool is available.
5. The recipe cell (table below), with the output contract `zt` (JSON) or
   `Vt` (ReportFindings) and the resolved cap.
6. `--comment` block: `Ms` (GitHub) or `Us` (GitLab), when the flag was passed.
7. `--fix` block `Bs` (two variants: ReportFindings mode re-reports with
   outcomes; JSON mode ends with a summary), when the flag was passed.
8. `js` "If findings are fixed later" — ReportFindings mode, cell ≠ `minimal-pass`.
9. `Fs` echo line — ReportFindings "message" mode, cell ≠ `minimal-pass`.
10. `Hs` "After the review" (`/verify` hand-off) — ReportFindings "spawn" mode,
    a `/verify` skill exists, cell not `measuredExternal`.

Shared sub-blocks: `X` Phase 0; correctness angles `gs` A, `ys` B, `ws` C,
`vs` D, `bs` E (`Ht` = A+B+C, `ks` = A–E, `ao` = A+B+C for the inline cells);
cleanup angles `Gt`/`no` Reuse, `Q` Simplification, `Z` Efficiency, `oe`
Altitude, `ue` Conventions; `re` cleanup-shape note; `Ke` Subagent-unavailable
sentence; `Ae` = `Ht` + the 5 cleanup angles; `Bt` = `ks` + the 5 cleanup
angles; verifier blocks `qt` (3-state) / `Ss` (recall-biased) built on `Cs` /
`_s`; sweep `Es` built on `Yt`; inline-fallback `As` lead-in and `xs` footer.

## Cells (`Ns`)

| cell | builder | structure | default cap | notes |
|------|---------|-----------|-------------|-------|
| `low` | `Kt(usesRF, cap=4)` | Turn 1 read the diff (skip test/fixture hunks, no subagents, no full-file reads); Turn 2 flag hunk-visible correctness bugs, duplicated helpers, dead code | 4 | own output format: one line per finding or `(none)`; RF variant reports `{level, findings}` |
| `low-targeted` | `Xt(usesRF)` | same two turns; targets `min(files_changed, 4)` findings, extra pass on the largest file and removed blocks | none | `--max-findings` has no effect (notice) |
| `inline-low` | `so(usesRF, cap=8)` | same two turns without the skip-tests sentence; floor `min(files_changed, min(4, cap))` | 8 | |
| `medium` | `Jt(output, agent, cap=8)` | precision lead-in; `X`; Phase 1: 8 finder angles (`Ae`) as independent subagents, ≤6 candidates each; `re`; pass-through note; Phase 2 `qt` (1-vote, 3-state); output | 8 | no permitted subagent tool → `Xe` inline fallback (8 angles, dedup + self-check, `xs` footer) |
| `high` | `Zt(output, agent, cap=10)` | recall lead-in; same Phase 1 as `medium`; Phase 2 `Ss` (recall-biased); output | 10 | no permitted subagent tool → `Xe` |
| `xhigh` / `max` | `eo(level)(output, agent, cap=15)` | recall lead-in ("extra-high"/"maximum"); Phase 1: 10 angles (`Bt`) ≤8 candidates each; `re`; `qt` + "recall mode" note; Phase 3 sweep `Es`; output | 15 | no permitted subagent tool → `Xe` with the sweep paragraph (`Yt` focus) |
| `inline-medium` | `lo = ro(tag, lead, 8)` | correctness lead-in; `X`; Phase 1: 8 angles run inline (`ao`, `no`, `Q`, `Z`, `oe`, `ue`), ≤6 each; `re`; pass-through note; Phase 2 dedup only; output with floor `io` | 8 | floor = `max(1, floor(min(cap, 8)/2))`; "nothing survives verification" → "nothing survives" |
| `inline-high` | `co = ro(tag, lead, 10)` | as above with the high-effort recall lead-in | 10 | floor base 10 |
| `inline-xhigh` | `Is(output, cap=15)` | extra-high lead-in; Phase 1: 10 angles inline (`ao`, `vs`, `bs`, `no`, `Q`, `Z`, `oe`, `ue`), ≤8 each; `re`; Phase 2 dedup only ("Do NOT drop on uncertainty"); Phase 3 inline sweep; output with floor `io(…, 15)` | 15 | |
| `minimal-pass` | `Qe(cap=15, uses_rf=True)` | one paragraph: gather the diff, review as a careful senior engineer, submit via ReportFindings, restate one line per finding | 15 | uses text-rendered JSON when reporting is disabled; hard cap 32 |

Cap substitution: `W(n)` → `≤n findings` / `all findings` in the tag line;
`we(n)` → `at most **n findings**` / `**every finding**` in the low cells;
`zt`/`Vt` spell out "of at most n objects" / "at most n entries" and "If more
than n survive, keep the n most severe." or the "no maximum" variants.

## Subagent availability

Only `medium`, `high`, `xhigh` and `max` change shape when a permitted subagent tool is
missing (they become the `Xe` single-pass inline form). The `low*`, `inline-*` and
`minimal-pass` cells never use subagents.

## Output contract and rendering

- `zt(cap)` — JSON array of `{file, line, summary, failure_scenario}`, most
  severe first, `[]` when nothing survives; explicitly forbids calling
  `ReportFindings`. The built-in's fork formatter (`jt`) rendered this array
  as `N findings, most severe first:` followed by numbered `` `file:line`: summary ``
  entries with a `Failure scenario:` paragraph each, or `No findings.`; the
  skill asks the model to print that rendering itself (SKILL.md Step 3).
- `Vt(cap)` — one `ReportFindings` call with `{level, findings}`; each finding
  has `file`, `line`, `summary`, `short_summary` (≤60 chars), `failure_scenario`,
  `category` and `verdict` when a verify pass ran. Max 32 findings (tool schema).
