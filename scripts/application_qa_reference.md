# Application Q&A reference — Tomasz Baranski

Reusable answers to recurring application-form fields (cover letter
prompts, screening questions, salary expectations, etc.), built up as we
go per `docs/apply_workflow.md`. Before drafting an answer to any
non-trivial field, check here first for something to reuse/adapt; after
Tomasz approves and sends an application, add the final wording here
(or update an existing entry if the question repeats with new phrasing).

Grounded in `scripts/cv_reference_data.md` — don't invent facts here that
aren't already confirmed there.

## Format

Each entry: the question (or its general topic, if wording varies by
site), the answer used, and which application(s) it came from.

---

## "Attach a message for the employer" / "Introduce yourself (Linkedin/Github links)" (justjoin.it)

General-purpose intro note for a backend/PHP-Symfony role where the employer
also cares about AI/LLM work (adjust the specific stack line per job —
this one leaned into the posting's "AI-first" framing and Python/Node.js
curiosity). Draws on Consensus (CQRS/Hexagonal/Kafka), eMAG (RabbitMQ at
scale), and JobAgent (embeddings + AI ranking pipeline) as the recurring
proof points — see `cv_reference_data.md` for the underlying facts.

```
Dzień dobry,

Od ponad 9 lat pracuję jako backend developer w PHP/Symfony, obecnie jako
Senior Backend Engineer w Consensus — rozwijam produkcyjny system SaaS w
architekturze heksagonalnej z CQRS oraz event-driven workflow na Kafce
(idempotentne konsumery, retry/observability). Wcześniej w eMAG budowałem
mikroserwisy e-commerce z RabbitMQ przy skali milionów wiadomości dziennie.

Równolegle na co dzień pracuję z AI/LLM (Claude Code, Anthropic SDK,
budowa agentów, automatyzacja w Pythonie) — od zera zbudowałem własny
system wspierający poszukiwanie pracy oparty o embeddingi i wielostopniowy
ranking AI, więc podejście "AI-first" z Waszego ogłoszenia jest mi bliskie
praktycznie, nie tylko teoretycznie. Jestem otwarty na poszerzenie stacku
produkcyjnego o Pythona/Node.js.

LinkedIn: linkedin.com/in/baranskitomasz

Chętnie porozmawiam o roli.

Pozdrawiam,
Tomasz Barański
```

Used for: Logintrade S.A. — Backend Engineer (PHP/Symfony), justjoin.it,
applied 2026-11-07. When reusing: swap the "Jestem otwarty na..." line and
which stack facts get emphasized to match the specific posting — don't
paste this verbatim everywhere.

## "Dodatkowy komentarz" (matchtly.com / justjoin.it external ATS)

Same recurring proof points as above, but framed for a pure PHP/MySQL/
e-commerce role with no AI angle — don't force AI/LLM content into a
posting that doesn't ask for it. Leans on eMAG (e-commerce platform scale,
MySQL optimization, RabbitMQ) and Consensus (CQRS/Hexagonal, production
SaaS, CI/CD), plus Redis and Docker as supporting facts.

```
Dzień dobry,

Od ponad 9 lat pracuję jako backend developer w PHP, z ponad 4 latami w
produkcji z Symfony i Doctrine. W eMAG budowałem mikroserwisy platformy
e-commerce działającej w Rumunii, na Węgrzech i w Bułgarii — projektowanie
i optymalizacja zapytań MySQL pod realnym, dużym ruchem, integracje przez
RabbitMQ obsługujące do milionów wiadomości dziennie. Obecnie jako Senior
Backend Engineer w Consensus rozwijam produkcyjny system SaaS (PHP 8.3/
Symfony 7, Kubernetes/Helm, CI/CD jako standard), z naciskiem na
architekturę (CQRS, Hexagonal Architecture) i niezawodność aplikacji pod
obciążeniem.

Mam też praktyczne doświadczenie z Redis (cache, wysokoprzepustowe
odczyty) oraz Dockerem.

Chętnie porozmawiam o roli.

Pozdrawiam,
Tomasz Barański
```

Used for: Ideamotive — Senior PHP Developer (k/m), e-commerce/SaaS,
justjoin.it via matchtly.com ATS, applied 2026-11-09.

## "Dostępność" / "Stawka godzinowa (B2B)" fields (matchtly.com)

Not facts from `cv_reference_data.md` — these are negotiable terms only
Tomasz can set. Don't invent a number; draft-fill only if explicitly
instructed, otherwise leave blank and ask him. Confirmed values so far:
**Dostępność: 14 dni** (notice period, given 2026-11-09, likely stable —
confirm before reusing elsewhere); **Stawka godzinowa: use the midpoint of
the posting's own posted B2B rate range** (e.g. posting said "120-160
PLN/h netto" → filled "120-160 PLN/h netto" directly since the field took
free text; a number-only field instead → use the midpoint, e.g. Strive's
"130-150 PLN" posted range → filled "140").

## justjoin.it application mechanics (not a Q&A field, but worth recording)

- Logged-out: form requires First/Last name + Email + CV + a mandatory
  "I'm creating an account, I accept ToS" checkbox (this creates a
  justjoin.it account tied to the applicant's email).
- Logged-in (Tomasz's existing justjoin.it account): Email is pre-filled,
  no ToS checkbox, and the CV picker defaults to whatever's already saved
  on the profile — **the saved profile CV was named `CV_baranski_tomasz.pdf`,
  which violates the `TomaszBaranskiCv.pdf`-only rule**, so always use
  "Upload a new CV" and attach the correctly-named file instead of
  accepting the pre-selected one.
- The "Attach a message" toggle must be clicked by exact coordinate/ref
  right after a screenshot — reusing a stale element ref after a file
  upload has, twice, hit the CV delete button instead and silently
  removed the attached file. Always re-screenshot and re-find the toggle
  immediately before clicking it, and re-verify the CV is still attached
  afterward.

## matchtly.com application mechanics (Ideamotive's external ATS, reached via justjoin.it)

- A genuinely distinct external form: Imię/Nazwisko/E-mail/Numer
  telefonu/Kraj (required), Miasto, LinkedIn (required), Dostępność
  (required), Stawka godzinowa / Wynagrodzenie miesięczne (optional),
  CV (required), Dodatkowy komentarz, 2 required consent checkboxes + 1
  optional (future recruitment — leave unchecked by default).
- **Never leave `TomaszBaranskiCv.pdf` on disk pointing at a different
  job's CV content while a matchtly.com tab with that file already
  uploaded is still open** — overwriting the source file mid-session
  cleared the already-attached CV in the open tab (file input reset to
  "No file chosen"), requiring a re-upload. Finish one application's
  submission (or at least get it to "ready, tab left open for Tomasz")
  before reusing the shared `TomaszBaranskiCv.pdf` path for the next job.
- The LinkedIn field's "Podaj poprawny adres URL" validation message can
  get stuck showing even after the field holds a genuinely valid URL
  (`https://www.linkedin.com/...`), including after a real character-by-
  character retype (not just a programmatic value set) and blur — looks
  like a stale/non-reactive validation display bug on their end, not a
  real data problem. Don't burn more time chasing it; flag it to Tomasz
  and let him retry a keystroke right before submitting if it blocks him.
