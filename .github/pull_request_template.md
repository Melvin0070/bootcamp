# Activity N — Short Title

## What Was Broken

_Be specific: what failed, under what conditions, and why. Reference the root cause._

## What I Changed & Why

_Describe your fix. Call out trade-offs you considered (e.g., "chose DLQ over retry loop because...")._

## Architecture / Diagram

_Add a simple ASCII diagram, table, or before/after comparison showing the structural change._

```
Before:  [Trigger] → [Lambda] → ❌ timeout, no retry

After:   [Trigger] → [Lambda] → DLQ → [Retry Lambda]
                  ↑ reserved concurrency set to 10
```

## Edge Cases Handled

_List the edge cases explicitly covered by your fix:_
- e.g., duplicate messages → idempotency check skips re-processing
- e.g., Lambda timeout on retry → DLQ captures event, alarm fires

## How to Test

1. _Step to reproduce original bug_
2. _Step to verify fix works_
3. _Step to verify edge cases_

## Video Walkthrough

_Link to 2–5 min before/after video:_ 

---

## Rubric Self-Check (target 5/5 on all)

| Criterion | Self-score | Evidence |
|-----------|-----------|----------|
| Code Correctness — all cases + edge cases + extra validation | /5 | |
| Problem Solving & Architecture — scalable, best practices, trade-offs shown | /5 | |
| Code Quality — no hardcoded values, env vars/Secrets Manager, modular + commented | /5 | |
| PR Description — comprehensive, diagram included, edge cases, video linked | /5 | |
| Completeness — all required changes + tests + docs updated | /5 | |

---

## Checklist

- [ ] Fix is complete and handles all edge cases
- [ ] No hardcoded secrets — using env vars or Secrets Manager
- [ ] Tests written and passing
- [ ] Docs / comments updated where needed
- [ ] Architecture diagram or before/after included above
- [ ] Video link added above
