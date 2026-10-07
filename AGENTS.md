# Collaboration rules

These rules apply to all work in this repository.

1. Never create a commit, create or switch a branch, or push changes unless the user explicitly requests that exact operation in the current turn. Requests such as "do it" or "fix it" authorize code changes only.
2. Never deploy to production autonomously. Production deployment must be performed step by step, with the user explicitly directing each step before it is executed.
3. Never merge a pull request into `master` or `main`. A request such as "release it" does not authorize a merge.
4. Never add Codex attribution to commits. Do not add `Co-Authored-By`, Codex references, or equivalent attribution.
5. Add code comments only when they are necessary to explain behavior that cannot be made clear through the code itself.
6. New and modified files must follow current industry standards. Do not preserve a local repository convention when it conflicts with those standards.
7. Record durable decisions and working context incrementally during the task instead of postponing all documentation until the end.
8. Delegate bounded supporting work to a cheaper subagent model whenever it can complete the work reliably. Keep final integration, validation, and responsibility with the primary agent.
