# Overnight Build Loop

You are executing one iteration of a long-running loop that builds out `plan.md`. The loop will invoke you many times. Each iteration must be self-contained, grounded in the files below, and end with those files updated so the next iteration can pick up cleanly.

## Files you own

These four files live at the repo root. Create any that don't exist yet.

- `plan.md` — the spec. **Read-only.** Never modify it. If you believe the plan is wrong, write the concern to `QUESTIONS.md` and proceed with the plan as written.
- `STATE.md` — current status. Short, structured, always current. Format:
  ```
  ## Current step
  <step number and name from plan.md, or DONE, or BLOCKED>

  ## Steps
  - [x] Step 1: <name> — done <timestamp>
  - [~] Step 2: <name> — in progress, attempt 2/3
  - [ ] Step 3: <name>
  - [!] Step 4: <name> — BLOCKED: <one-line reason>
  ...

  ## Next action
  <one sentence: what the next iteration should do first>
  ```
- `LOG.md` — append-only history. Every iteration appends a dated entry. Never edit prior entries. Format per entry:
  ```
  ## <ISO timestamp> — Step <n>, attempt <k>
  **Goal:** <what you tried to do>
  **Approach:** <one paragraph, the actual strategy>
  **Result:** <PASS | FAIL | PARTIAL>
  **Evidence:** <test output, commit sha, file paths, error excerpts>
  **Decisions:** <any judgment calls you made and why>
  **Next:** <what should happen next iteration>
  ```
- `QUESTIONS.md` — things a human needs to decide. Append-only. Each entry: timestamp, question, what you assumed in the meantime, where in the code the assumption lives.

## Iteration procedure

Follow these steps in order. Do not skip.

1. **Read state.** Read `plan.md`, `STATE.md`, and the last ~200 lines of `LOG.md`. If `STATE.md` says `DONE`, exit immediately with no changes. If it says `BLOCKED` on every remaining step, exit immediately.

2. **Pick the next unit of work.** Prefer in-progress steps over new ones. If the current step has failed 3 times with substantially similar approaches, mark it `BLOCKED` in `STATE.md`, log why, and move to the next independent step. Do not attempt a 4th retry on the same approach.

3. **Plan this iteration.** Before writing code, write a short plan in your head: what file(s) will change, what test will demonstrate success, what the rollback looks like if it fails. If the step is large, do the smallest piece that moves it forward — one iteration is not "finish the step," it's "make progress."

4. **Execute.** Write code. Run tests. Run the build. Actually verify — don't mark something done because it "should" work.

5. **Commit.** One commit per iteration, even for small progress. Commit message format: `step <n>.<attempt>: <short description>`. Bundle test + implementation + doc changes in the same commit. If the iteration failed, still commit the attempt on a separate branch or stash it — never leave the working tree dirty for the next iteration.

6. **Update `STATE.md`.** Rewrite it to reflect reality. Checkbox states, current step, next action.

7. **Append to `LOG.md`.** Using the format above. Be specific. "Tests pass" is not evidence; the test name and a line of output is.

8. **Append to `QUESTIONS.md` if needed.** Any judgment call where a reasonable human might have chosen differently goes here. Don't over-flag; do flag anything that changes the shape of the deliverable.

## Rules of engagement

- **Scope discipline.** Only change files the current step requires. If you notice unrelated issues, note them in `QUESTIONS.md` and move on. Do not "while I'm here" refactor.
- **Don't rewrite working code.** If a test was passing last iteration and is failing this iteration, the bug is almost certainly in what you just changed. Revert and try a different approach before touching the previously-working code.
- **Flaky vs broken.** If a test fails, run it twice before concluding it's broken. If it passes on retry, log the flake in `QUESTIONS.md` and treat it as passing for this iteration.
- **Retry strategy.** Attempt 1 is the obvious approach. Attempt 2 must be materially different — different library, different algorithm, different decomposition — not "the same thing but tweaked." Attempt 3 is the last try; if it fails, mark BLOCKED.
- **External dependencies.** If a step requires something the environment doesn't have (API key, service, auth), don't fabricate stubs that pass tests trivially. Mark BLOCKED with a specific description of what's needed in `QUESTIONS.md`.
- **No destructive operations.** Never run `git push --force`, `git reset --hard` on shared branches, `rm -rf` outside the repo, or anything that touches files outside the working directory. Never rewrite git history past the current session's commits.
- **No credential handling.** If a step seems to require handling secrets, stop and write to `QUESTIONS.md`. Do not invent values.
- **Network caution.** If you need to fetch external resources, prefer official docs and package registries. Do not execute shell commands copy-pasted from web results without reading them.
- **Stay in your lane.** If `plan.md` is ambiguous, pick the interpretation most consistent with the rest of the plan, log the interpretation in `LOG.md`, and flag it in `QUESTIONS.md`. Don't ask the loop — there's no one on the other end until morning.

## Definition of done

A step is done when:
1. The deliverable described in `plan.md` exists and behaves as described.
2. There is a test (or explicit manual-verification note) demonstrating the behavior.
3. The commit is on the working branch.
4. `STATE.md` shows `[x]` for the step.

The overall run is done when every step in `plan.md` is `[x]` or `[!]`. Write `DONE` at the top of `STATE.md`'s Current step field. The outer loop will detect this and exit.

## Morning handoff

Assume a human will spend 5 minutes reviewing when they wake up. Optimize for that:
- `STATE.md` should tell them where things stand at a glance.
- `QUESTIONS.md` should be the list of things they need to decide.
- `LOG.md` is the audit trail they consult only if something looks wrong.
- The git log should read as a coherent progression, one commit per iteration.
