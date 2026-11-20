# Job application workflow (standing procedure)

In effect from 2026-11-07 until Tomasz says otherwise. Applies to every job
application Claude does through the browser from here on — not a one-off
for the pilot run.

## Trigger

Tomasz names a specific job offer (usually from the would-apply shortlist,
see `scripts/cv_reference_data.md` for CV source material and the
would-apply query in `db/repositories/job_repository.get_would_apply_stats`
for how the shortlist itself is produced).

## Steps, one application at a time

1. **Open the offer in Chrome.** Read the full posting: employer, role,
   and — critically — every field the application form will ask for
   (cover letter? screening questions? salary expectation? notice
   period?). Don't start filling the form blind.

2. **Prepare the CV.**
   - If a tailored CV for this job doesn't exist yet in
     `data/cv_exports/`, generate one with `scripts/generate_cv.py`,
     pulling facts from `scripts/cv_reference_data.md` (never invent
     experience not listed there).
   - **Before uploading anywhere, copy it to a fixed filename:
     `TomaszBaranskiCv.pdf`.** This exact name, every single time,
     regardless of which job it's for. Never let a company/slug-named
     file (`logintrade.pdf`, `strive.pdf`, ...) reach an upload field —
     those names stay internal to `data/cv_exports/` for Claude's own
     organization only.

3. **Open the application form and fill it in.**
   - Name, surname, email, phone, LinkedIn — fill directly, no
     confirmation needed, these never vary.
   - CV upload — attach the renamed `TomaszBaranskiCv.pdf`.
   - Any other field (cover letter, "why this role", salary
     expectation, notice period, work authorization, etc.):
     - First check `scripts/application_qa_reference.md` for an
       existing answer to the same or a similar question — reuse/adapt
       it rather than drafting from scratch.
     - If it's genuinely new, draft a reasonable answer grounded in
       `scripts/cv_reference_data.md` and fill it in.
     - These are drafts. Tomasz reviews and can edit any of them before
       anything is sent.

4. **Stop before the final submit/send button.** Never click it.
   This is a hard rule, not a default that can be inferred away by "the
   form looked complete."

5. **Tell Tomasz it's ready for review** — which job, what got filled
   in (especially anything beyond name/contact/CV), and where to look
   (the open Chrome tab).

6. **After Tomasz approves (with or without edits) and sends it
   himself:**
   - Record the final version of any non-trivial field answer in
     `scripts/application_qa_reference.md`, keyed by the question
     (or its general topic if the exact wording varies by site), so
     future applications can reuse it instead of redrafting.
   - Mark the job `applied` in JobAgentWeb
     (`job_repository.update_status(job_id, "applied")`) only once
     Tomasz confirms it was actually sent — never mark it applied
     preemptively just because the form was filled in.

## Hard constraints

- **No automated submission on LinkedIn Easy Apply.** LinkedIn's ToS
  prohibits automation and real accounts get flagged/banned for it.
  Manual only there, unless Tomasz explicitly overrides this for a
  specific case.
- **Never click final submit without Tomasz's go-ahead**, even if a
  form looks straightforward and all fields are filled correctly.
- **Never upload a CV under any name other than `TomaszBaranskiCv.pdf`.**

## Open questions / things to tighten once we've done a few

- Whether to batch several ready-for-review applications before
  interrupting Tomasz, or interrupt after each one (default: after
  each one, until he says otherwise).
- Whether screenshots should accompany the "ready for review" message
  by default.
