# code-review

An agent-neutral code review skill with effort-level recipes, sticky options,
optional subagent review, inline verification, findings reports, fixes, and
GitHub/GitLab comments. All recipes are available through generic review
profiles, independent of the coding agent or model.

## Installation and invocation

Place or link `code-review` in your coding agent's skill discovery directory.
For hosts that support the standard agents path, use `.agents/skills/code-review`
for a project or `~/.agents/skills/code-review` for personal use. Other hosts may
require a different location. Follow that host's discovery rules.

Invoke the skill using the host's supported syntax, for example `$code-review`
or `/code-review`, followed by:

```text
[low|medium|high|xhigh|max] [--fix] [--comment] [PR-number|branch|path] [--max-findings N|all|default]
```

The level comes first; `med` means `medium`. With no level, the skill reuses
its last explicit level, then the known session effort, then `medium`.
`--max-findings` persists until `--max-findings default` clears it. `--fix`
applies findings after reporting. `--comment` posts inline comments to a GitHub
PR or a general note to a GitLab MR. Without those flags a review does not fix
code or post comments. `ultra` falls back to local `max`; cloud posting flags
are accepted and ignored with the existing notices.

Requirements: macOS or Linux, Python 3.9+, git, shell execution, file reading,
and text search. The helper uses only Python's standard library. Posting needs
appropriate authenticated host tools (such as `gh` or `glab`) or connectors.
Subagents, ReportFindings, and a verify skill are optional integrations.

## Portable execution

SKILL.md tells the agent to resolve the skill directory, run `review.py parse`,
fill the generated render command's capability arguments, then follow its
prompt. There are no load-time shell blocks or vendor-specific variables.
Arguments must be passed as shell data or via `--stdin`. The profile defaults to `default` and is never inferred from a model ID. Subagent references mean the host's available, permitted
delegation capability, regardless of tool name. Without it the existing inline
fallback applies. Instructions do not grant additional permissions.

The conventions angle reads applicable instruction files using host scope and
precedence rules, including AGENTS.md and local overrides. A finding must cite an exact rule and violating line.

## Configuration and state

| Variable | Effect |
|---|---|
| `CODE_REVIEW_SKILL_STATE` | Explicit sticky-state path; `/dev/null` disables persistence |
| `CODE_REVIEW_SKILL_PROFILE` | Overrides `--profile`: `default`, `parallel`, `inline`, or `minimal` |
| `CODE_REVIEW_SKILL_RF_MODE` | Candidate reporting mode: `none`, `spawn`, or `message`; invalid values produce a routing error |

Default state is `~/.agents/code-review-skill-state.json`. No legacy paths
are searched. An explicit `CODE_REVIEW_SKILL_STATE` can select another file;
`/dev/null` disables persistence. State keys remain `codeReviewLastEffort` and
`codeReviewLastMaxFindings`.

Only `CODE_REVIEW_SKILL_RF_MODE` selects a candidate reporting mode; without
it the candidate is `none`. The agent must confirm ReportFindings exists
before enabling tool reporting. Otherwise findings use the readable text
format, including the `minimal-pass` recipe, which retains its 32-finding cap.

Profiles preserve distinct routing strategies: `default` retains the normal
routing; `parallel` emphasizes finder/verifier delegation with inline fallback;
`inline` emphasizes in-context angles; `minimal` uses a compact single pass at
medium/high. All profiles use the maximum recipe at `max`. The full effort
mapping is in SKILL.md. To select a profile, set `CODE_REVIEW_SKILL_PROFILE` or
pass `--profile` to the helper's render command before `--`.

Model-based routing and legacy environment settings are no longer supported.
Existing state files outside the default path are left untouched.

Manual state management, from the skill directory:

```bash
python3 -I scripts/review.py state get
python3 -I scripts/review.py state set-effort high
python3 -I scripts/review.py state clear-max
```

## Files and maintenance

- `SKILL.md`: invocation, capability decisions, routing table and report format.
- `scripts/review.py`: parsing, state, diff sizing, rendering and block generation.
- `references/routing.md`: exact grammar, resolution, state and reporting rules.
- `references/composition.md`: recipe composition, caps and fallback behavior.
- `references/blocks/*.md`: generated prompts for inspection and manual fallback.

Edit prompt text in the helper, then regenerate from the skill directory:

```bash
python3 -I scripts/review.py dump-blocks references/blocks
```

Block names remain as internal audit identifiers. The effort argument selects
a review recipe; it cannot change the host model's reasoning settings. Cloud
review is unavailable. Recipe caps, floors, and output contracts are preserved.
