# JobAgent — analiza architektury AI/RAG

Analiza wykonana wyłącznie na podstawie kodu repozytorium `JobAgent` (plus
kilka plików w `JobAgentWeb`, tam gdzie `JobAgent` jest tylko cienkim
klientem HTTP wobec wspólnej bazy danych — zaznaczone wprost poniżej).
Każdy punkt oznaczony `[KOD]` (wynika wprost z kodu/configu) lub `[DOMYSŁ]`
(moje wnioskowanie, bo kod nie mówi tego wprost).

---

## 1. Stack

**[KOD]**
- Python ≥3.12.
- `anthropic` (oficjalne Python SDK Claude) — scoring, ranking, ekstrakcja,
  debata, query expansion, destylacja preferencji.
- `voyageai` — embeddingi i cross-encoder rerank.
- `flask` + `flask-sock` — lokalny serwer/WebSocket (panel sterowania
  kolektora, nie część pipeline'u AI).
- `playwright` — automatyzacja przeglądarki dla 3 źródeł kolektora
  (LinkedIn, itpracuj, theprotocol).
- `PyPDF2`, `pydantic`, `httpx`, `python-dotenv`.
- **Brak LangChain, LlamaIndex, czy jakiegokolwiek frameworka RAG** — cały
  pipeline to ręcznie pisane wywołania SDK Anthropic/Voyage.

**[DOMYSŁ]** Brak frameworka to świadoma decyzja architektoniczna (widać to
po bardzo precyzyjnych, projektowych komentarzach w kodzie — np. przy RRF,
HyDE, fingerprintach), nie zaniedbanie.

---

## 2. Pozyskiwanie danych

**[KOD]**
- W `JobAgent` kolektor obsługuje 3 źródła przez przeglądarkę (Playwright):
  `collector/sources/linkedin.py`, `itpracuj.py`, `theprotocol.py`.
- Reszta źródeł (w danych widoczne jako `justjoin`, `jobscollider`,
  `pracuj.pl` itp.) pochodzi z serwera `JobAgentWeb` — poza zakresem tego
  repo, nie analizowałem tamtego kodu kolektorów.
- Deduplikacja na poziomie kolektora: zbiór już znanych URL-i
  (`job_repository.get_all_urls()`) ładowany przed startem, early-stop przy
  trafieniu na znany URL.
- Deduplikacja cross-source (różne serwisy, ten sam job) żyje w
  `JobAgentWeb/dedup_repo.py`: dopasowanie firma/lokalizacja/treść +
  priorytet źródła (ATS > LinkedIn > inne), próg auto-merge confidence
  **0.60**, okno czasowe **21 dni** (`DEFAULT_WINDOW_DAYS`). Powyżej tego
  okna duplikat (np. ta sama oferta wystawiona ponownie po >3 tygodniach)
  **nie** zostanie automatycznie scalony.
- Częstotliwość: `scripts/run_all.py` nie ma wbudowanego harmonogramu — to
  zwykły skrypt CLI. `--days` domyślnie = "dni od ostatniego udanego
  zbierania" (`session_repository.days_since_last_collection()`).
  Harmonogram (cron?) nie jest w tym repo — **brak** (albo poza zakresem).
- Pola wyciągane przy zbieraniu (surowe): title, company, location, url,
  source, source_id, description, search_query, posted_at,
  source_structured_data.
- Normalizacja/czyszczenie opisu: `collector/utils.py::strip_description_junk`
  + per-source limit długości (`_MAX_DESCRIPTION_CHARS_BY_SOURCE`), krótszy
  wynik niż próg (400 znaków) oznaczany jako "niepełny" i opatrywany notką
  dla LLM (nie karać za brak danych, których źródło po prostu nie dało).

---

## 3. Embeddingi

**[KOD]**
- Model: **`voyage-3-large`** (Voyage AI), rerank: **`rerank-2`**.
- Embedding dokumentu (strona "indeksowania"): `title + " at " + company`,
  opcjonalnie `Location: ...`, plus **jeden** fragment opisu obciętego do
  2000 znaków (`embeddings/indexer.py::_job_to_text`). Jeden wektor na
  ofertę, nic więcej.
- Embedding zapytania przy wyszukiwaniu ma **dwa tryby**:
  1. Jeśli kandydat ma historię aplikacji: **max-sim k-NN** — similarity
     liczona osobno wobec *każdego* wektora ofert, do których aplikowano
     (nie centroid!), minus 0.3× max-similarity wobec wektorów ofert
     odrzuconych. Uzasadnienie w komentarzu: centroid nad wielomodalną
     historią (różne typy ról) ląduje w "semantycznej ziemi niczyjej".
  2. Jeśli brak historii: **HyDE** (Hypothetical Document Embeddings, Gao et
     al. 2022, cytowane wprost w kodzie) — tani model (Haiku,
     `CLAUDE_EXTRACT_MODEL`) generuje syntetyczne "idealne ogłoszenie o
     pracę" na podstawie CV/kwestionariusza, i **to** jest embedowane jako
     wektor zapytania, zamiast surowego tekstu CV (bo CV pisane jest w
     "głosie kandydata", a ogłoszenia w "głosie oferty" — inny rejestr
     językowy, gorsze dopasowanie wprost).
- Wymiar wektora: **nie podany wprost w kodzie** — `voyage-3-large` ma wg
  dokumentacji Voyage 1024 wymiary domyślnie [DOMYSŁ, bo kod nigdy nie
  odczytuje/nie waliduje wymiaru jawnie, po prostu przekazuje listę floatów
  do Postgresa jako JSON].
- Co NIE jest embedowane: `avoided_tech` (świadomie pominięte — embedding
  nie reprezentuje negacji, dodanie słowa tylko przyciągnęłoby więcej tego,
  czego kandydat chce unikać).

---

## 4. Chunking

**[KOD]** **Brak** dzielenia na wiele fragmentów/wektorów per dokument.
Jedna oferta = jeden wektor. "Chunking" sprowadza się do:
- twardego obcięcia opisu do 2000 znaków przy embedowaniu,
- per-source limitu znaków przy ogólnym czyszczeniu opisu (`build_excerpt`),
- obcięcia zapytania rerankera do 2000 znaków (`_MAX_QUERY_CHARS`).

To nie jest RAG w sensie "pocięty dokument + retrieval fragmentów" — to
klasyczne dense retrieval na poziomie całych ofert.

---

## 5. Przechowywanie

**[KOD]** (sprawdzone w `JobAgentWeb`, bo to tam żyją wektory)
- Tabela Postgres `job_embeddings(job_id PK, embedding TEXT, model TEXT,
  text_hash TEXT, created_at)`.
- Wektor trzymany jako **zwykły TEXT z JSON-em** (`json.dumps(embedding)`),
  **nie pgvector**, nie żaden ANN-index (brak HNSW/IVFFlat).
- Similarity liczona **brute-force w Pythonie przez NumPy**
  (`embeddings_repo.py::score_by_similarity`: macierz wszystkich wektorów ×
  wektor zapytania, kosinus ręcznie). Żadnego SQL-owego operatora
  wektorowego.
- Pula rankingowa ograniczona do **2000** ofert na raz
  (`RANKING_POOL_LIMIT` w `scripts/rank_jobs.py`) — to jest limit, przy
  którym brute-force NumPy jeszcze ma sens.

**[DOMYSŁ]** Brak pgvector/ANN to świadomy wybór "na razie wystarczy" przy
tej skali (dziesiątki–niskie tysiące ofert na użytkownika), nie
przeoczenie — kod jawnie komentuje unikanie przesyłania surowych wektorów
przez HTTP ze względu na rozmiar, czyli autor myśli o wydajności, po prostu
inny jej aspekt.

---

## 6. Wyszukiwanie

**[KOD]**
- Metryka: **kosinus** (cosine similarity), liczona ręcznie (p. wyżej).
- Liczba wyników: cała aktywna pula (do 2000) jest **zawsze** oceniana
  embeddingiem — to nie jest "top-k retrieval" w klasycznym sensie, tylko
  ranking całej puli, z którego dopiero później wycina się top-N.
- Filtry po metadanych: **tak, ale wcześniej i osobno** — twardy,
  deterministyczny filtr dealbreakerów (`evaluator/dealbreakers.py`) działa
  na `structured_data` (pensja, remote/hybrid, kraj, seniority, typ firmy,
  język pracy, brak pensji) **przed** jakimkolwiek wywołaniem LLM czy
  embeddingu — zero kosztu AI dla ofert odrzuconych tu.
- Wyszukiwanie słowo-kluczowe / hybrydowe: **brak** osobnego BM25/
  pełnotekstowego silnika. Jedyny element "keyword" to prefiltr tytułów przy
  zbieraniu (`collector/filters.py`, `query_matcher.py`) — to filtr na
  etapie kolekcji, nie na etapie wyszukiwania/rankingu.

---

## 7. Ranking — wszystkie etapy po kolei

**[KOD]**, cały łańcuch z `scripts/rank_jobs.py` + `evaluator/runner.py`:

1. **Dealbreaker filter** (deterministyczny, zero LLM) — odrzuca ewidentne
   niedopasowania na podstawie `structured_data` (pensja, tryb pracy, kraj,
   seniority, typ firmy, język, brak pensji jeśli user tego nie chce).
2. **LLM scoring** — model **`claude-sonnet-5`** (`CLAUDE_MODEL`), 0–10,
   wymuszone użycie narzędzia (`tool_choice` na `submit_score`), zwraca
   sub-scores (stack/seniority/company/compensation fit) + pros/cons +
   `overall_score` + `score_reason`. Prompt cache'owany (`ephemeral`).
   Próg auto-odrzutu: **≤1.0**. Próg wejścia do dalszego rankingu:
   **>2.0** (`RANKING.min_score_for_ranking`).
3. **Embedding similarity** (Voyage, max-sim k-NN lub HyDE, p. pkt 3) —
   liczona dla **całej** przetrwałej puli.
4. **RRF (Reciprocal Rank Fusion)**, `k=60` — łączy rangę embeddingu z rangą
   score'u LLM z punktu 2. Standardowa formuła IR (Cormack et al.), cytowana
   wprost w komentarzu.
5. **Cross-encoder rerank** — model **`rerank-2`** (Voyage), na top
   **50** (`RANKING.top_n_rerank`) z puli po RRF. Wynik ponownie wchodzi do
   RRF (teraz 3 nogi: embedding + LLM score + rerank_score), chyba że Voyage
   padł (wtedy flaga `_rerank_unreliable` i ta noga jest pomijana).
6. **Listwise ranking** — model **`claude-opus-4-8`** (`CLAUDE_RANK_MODEL`),
   z **extended/adaptive thinking** (`effort: high`), na top **20**
   (`RANKING.top_n_listwise`) + **exploration slots** (3/dzień, losowe
   oferty spoza top-N, deterministycznie wybierane hashem `data:job_id`,
   żeby nie "zasypać" dobrych ofert, które pipeline systematycznie chowa
   niżej). Output: JSON w tagach `<ranking>` (nie `tool_choice`, bo extended
   thinking jest niekompatybilne z wymuszonym narzędziem — kod to wprost
   komentuje), parsowany regexem + `json.loads`, z fallbackiem na kolejność
   z rerankera przy błędzie.
7. **Debate / druga opinia** — **TEN SAM model co scoring, `claude-sonnet-5`**
   (nie inny provider, nie Opus — osobne, niezależne wywołanie z innym
   promptem/rolą). Flaguje `dealbreaker_risk` (ofertę degraduje na koniec
   listy), `overrated`/`underrated` (przesunięcie o 3 pozycje,
   `_RANK_NUDGE`). Wymuszone narzędzie (`tool_choice`).
8. **Would-apply flag** (faza 1 planu auto-apply, **nic nie wysyła**) —
   próg: score ≥7.0 **i** ranga ≤10 (po debacie, licząc tylko nie-
   exploration oferty) **i** brak flagi `dealbreaker_risk`.
9. **Cache wyniku** (`ranker/rank_cache.py`) — jeśli dokładnie ten sam zbiór
   ofert był już rankowany z tym samym fingerprintem (p. pkt 9 niżej),
   Opus+debata **nie** są uruchamiane ponownie.

**Ile kandydatów przechodzi dalej na każdym etapie** (liczby z configu):
cała pula (≤2000) → odrzucone dealbreakerem i score'em ≤1 wypadają → reszta
oceniana embeddingiem → top 50 do rerankera → top 20 (+3 exploration) do
Opusa/debaty → finalna ranga 1..N.

---

## 8. Prompty

**[KOD]**
- **Nie ma** osobnego katalogu/pliku z "templates promptów" — prompty są
  budowane programistycznie, inline, w modułach, które z nich korzystają
  (`evaluator/scorer.py::build_system_prompt`, `ranker/listwise.py`,
  `ranker/debate.py`, `evaluator/profile.py` dla HyDE).
- Struktura system promptu scorera (najbogatszy): profil kandydata (z CV) →
  kwestionariusz → "LEARNED PREFERENCE PROFILE" (sygnały
  `ACCEPT/REJECT/INFER` z confidence `ABSOLUTE/HIGH/MEDIUM/LOW` i licznikiem
  dowodów `n=X/Y`, destylowane osobno przez `preference_agent/runner.py`
  modelem `claude-opus-4-8`) → przykłady few-shot (oferty aplikowane/
  odrzucone, z prawdziwym powodem odrzucenia) → sekcja kalibracji (konkretne
  przypadki, gdzie ranking wcześniej się pomylił względem realnej decyzji
  kandydata, `evaluation/harness.py::divergence_cases`) → jawna reguła
  precedencji (MUST HAVE > kwestionariusz > wyuczony profil > PREFERRED) →
  kryteria MUST HAVE/PREFERRED → rubryka punktacji 0–10 opisana słownie po
  przedziałach.
- Format wymuszany: **tak, przez `tool_choice` wymuszający konkretne
  narzędzie** (scorer, debata, ekstraktor) — model fizycznie nie może
  odpowiedzieć czymkolwiek innym niż zdefiniowany JSON Schema. Jedyny
  wyjątek: listwise ranking, bo extended thinking nie współpracuje z
  wymuszonym `tool_choice` — tam format wymuszony instrukcją (tagi
  `<ranking>...</ranking>`) + parsowanie regexem, z pełnym fallbackiem przy
  porażce.
- Walidacja wyniku: `try/except` wokół konwersji typów (np.
  `float(overall_score)`), explicit sprawdzenie `stop_reason == "max_tokens"`
  (ucięta odpowiedź **nigdy** nie jest traktowana jako kompletna — zwraca
  pusty wynik do ponowienia następnym razem, nie fałszywe zero), sprawdzenie
  obecności bloku `tool_use`. Scorer **nigdy nie podnosi wyjątku na
  zewnątrz** — celowo, żeby jedna zepsuta oferta nie przerwała całego
  batcha (`evaluator/runner.py` na tym polega).

---

## 9. Utrzymanie

**[KOD]**
- Nowe oferty: indeksowane leniwie — `rank_jobs.py` przed rankingiem sam
  sprawdza, których ofert embedding metadata (model+hash tekstu) nie
  zgadza się z aktualnym stanem, i domyka tylko brakujące.
- **Fingerprinting zamiast ręcznej inwalidacji cache**: `score_fingerprint`
  i `ranking_fingerprint` (`evaluator/fingerprints.py`) hashują **razem**:
  nazwę modelu, **bajty plików źródłowych** (`evaluator/scorer.py`,
  `ranker/listwise.py` itd. — czyli zmiana kodu promptu **automatycznie**
  unieważnia cache), treść promptu, dane oferty. Każda oferta jest
  re-scorowana/re-rankowana tylko jeśli coś w tym łańcuchu faktycznie się
  zmieniło.
- Zmiana modelu embeddingów: `_job_to_text` hash + `VOYAGE_EMBED_MODEL` w
  metadanych — zmiana stałej `VOYAGE_EMBED_MODEL` w configu sprawi, że
  **wszystkie** oferty przy następnym `rank_jobs.py` zostaną uznane za
  "nieposindeksowane" i przeliczone na nowo (metadata nie będzie pasować).
  Brak osobnego skryptu migracyjnego/backfill — reindeksacja dzieje się
  "organicznie" przy najbliższym uruchomieniu.
- Wygasłe oferty: **nie znalazłem** w `JobAgent` logiki czyszczącej stare
  oferty inaczej niż ręczne skrypty (`scripts/cleanup_low_score_new.py`,
  `scripts/rescore_new.py`, `scripts/reevaluate_rejected.py`) — to narzędzia
  do ręcznego/okresowego odpalenia, nie automatyczny TTL. [DOMYSŁ: usuwanie
  faktycznie wygasłych ofert może żyć po stronie JobAgentWeb, poza
  zakresem tej analizy].
- Schema ekstrakcji ma jawny numer wersji (`FACT_SCHEMA_VERSION = 6` w
  `extractor/runner.py`) — `job_repository.get_missing_facts(schema_version)`
  pozwala dociągnąć/przeliczyć tylko oferty z przestarzałą wersją po zmianie
  schematu, bez pełnego re-runu.

---

## 10. Ewaluacja i jakość

**[KOD]**
- **57 plików testowych** (`tests/unit`, `tests/integration`, `tests/e2e`),
  marker `e2e` do testów faktycznie wołających Anthropic API (domyślnie
  pomijane, uruchamiane jawnie przez `pytest -m e2e`).
- **Brak** klasycznych metryk IR/RAG (precision@k, NDCG, recall) w sensie
  akademickim.
- Jest za to **prawdziwa metryka produkcyjna z pętli zwrotnej**:
  `get_would_apply_stats` liczy **precyzję** flagi "would apply" =
  `applied / (applied + rejected)` wśród ofert, które pipeline sam by
  zaaplikował (faza 1 auto-apply, nic nie wysyła, tylko się sprawdza).
- **Divergence cases** (`get_divergence_cases`): automatyczne wykrywanie,
  gdzie ranking i rzeczywista decyzja kandydata się rozjechały (ranga ≤5,
  ale odrzucone = "false positive"/przeszacowane; ranga ≥16, ale
  zaaplikowane = "false negative"/niedoszacowane) — te przypadki są
  **wprost wstrzykiwane z powrotem** do system promptu scorera jako sekcja
  kalibracji, więc model dostaje realny feedback ze swoich błędów.

**[DOMYSŁ]** To jest ewaluacja zaprojektowana pod ciągłe uczenie się
systemu na własnych pomyłkach, nie pod jednorazowy benchmark — spójne z
resztą architektury (fingerprinty, preference distillation).

---

## 11. Koszty, limity, obsługa błędów API

**[KOD]**
- Cennik modeli trzymany w `config.MODEL_COSTS` (USD/1M tokenów, osobno
  input/output), z komentarzem "zweryfikowane live, lipiec 2026" —
  świadome, ręczne utrzymywanie, nie pobierane z API dostawcy.
- Prompt caching: tylko Anthropic, `ephemeral` (5 min TTL), mnożniki kosztu
  zapisu/odczytu cache w configu (`CACHE_WRITE_MULTIPLIER=1.25`,
  `CACHE_READ_MULTIPLIER=0.1`).
- Logowanie zużycia: `db/repositories/usage_repository.py::log_anthropic`
  (per moduł: scorer/ranker/debate/extractor/catalog_gate/hyde_query) i
  `log_voyage_embed`/`log_voyage_rerank`, plus `record_run_summary` per
  cały przebieg pipeline'u, `get_cost_per_100()` jako znormalizowana metryka.
- Retry: `ranker/retry.py::call_with_retry` — max 3 próby, liniowy backoff
  (30s, 60s), tylko dla typowanych błędów przejściowych Anthropic
  (`APIConnectionError`, `APITimeoutError`, `RateLimitError`,
  `InternalServerError`) albo custom `retry_if` (np. tekst "overloaded" dla
  scorera, typowane błędy Voyage dla embeddingu/rerankera). Błędy
  nieprzejściowe (400, auth) **nie** są retry'owane.
- Timeouty: nie skonfigurowane jawnie per-call w analizowanym kodzie —
  polegają na domyślnych timeoutach SDK [DOMYSŁ, bo nie znalazłem
  explicitnego `timeout=` przy wywołaniach `messages.create`].
- Cache wyników (nie tylko promptu): `ranker/rank_cache.py` omija cały
  drogi etap Opus+debata, jeśli zbiór ofert i fingerprint się nie zmieniły
  (p. pkt 9) — to realna, architektoniczna oszczędność kosztu, nie tylko
  cache promptu po stronie Anthropic.

---

## 12. Ograniczenia i rzeczy do poprawy

**[DOMYSŁ]**, moja ocena po przejrzeniu kodu:

1. **Brak prawdziwego indeksu wektorowego** (pgvector/HNSW/FAISS) — przy
   obecnym limicie 2000 ofert na użytkownika brute-force NumPy działa, ale
   to twardy sufit skalowalności, nie rozwiązanie docelowe.
2. **Jedna reprezentacja na ofertę** (brak chunkingu) — długi, złożony opis
   (benefity + wymagania + o firmie) jest ściśnięty w jeden wektor po
   obcięciu do 2000 znaków; coś poza tym limitem jest dla embeddingu
   niewidoczne (choć LLM-owe etapy dalej widzą pełniejszy excerpt z
   własnym, osobnym limitem).
3. **"Debata" to w praktyce ten sam model**, nie niezależny drugi provider —
   realnie ogranicza to, ile nowej informacji może wnieść "druga opinia"
   względem prawdziwie niezależnego sprawdzenia (np. innym dostawcą).
4. **Brak formalnej ewaluacji offline** (złoty zbiór testowy z etykietami,
   metryki IR) — jakość mierzona wyłącznie online, na żywych decyzjach
   kandydata; nowa zmiana promptu/modelu nie ma gdzie być sprawdzona przed
   wdrożeniem poza samym `pytest`.
5. **Brak jawnych timeoutów** na wywołaniach API — przy prawdziwym zawieszeniu
   się połączenia (nie error, tylko brak odpowiedzi) pipeline może wisieć
   dłużej niż oczekiwane 30/60/90s retry.
6. **Czyszczenie wygasłych ofert** wygląda na ręczne/skryptowe, nie
   zautomatyzowane w tym repo.

---

## Jak to opisać na rozmowie rekrutacyjnej (5 zdań, bez podkoloryzowania)

Zbudowałem pipeline dopasowania ofert pracy do kandydata, który łączy
deterministyczny filtr reguł (dealbreakery z ustrukturyzowanych danych) z
wielostopniowym rankingiem AI: embeddingi Voyage do szerokiego retrievalu
(max-sim k-NN wobec historii aplikacji albo HyDE przy zimnym starcie),
Reciprocal Rank Fusion łączący to z oceną LLM, cross-encoder rerank na
skróconej puli, a na koniec listwise ranking Claude Opus z extended
thinking plus niezależna "druga opinia" tym samym modelem w innej roli,
która może zdemotować albo delikatnie przesunąć pozycję oferty. Całość ma
wbudowaną pętlę uczenia się: rozjazdy między rankingiem a realnymi
decyzjami kandydata (aplikował mimo niskiej rangi, albo odrzucił mimo
wysokiej) trafiają z powrotem do promptu jako konkretne przypadki
kalibracyjne, a osobny model destyluje całą historię aplikacji w
ustrukturyzowany profil preferencji z poziomem pewności per sygnał. Koszt i
niezawodność są pierwszoklasowym elementem projektu, nie dodatkiem: każde
wywołanie modelu jest logowane per etap z realną ceną, fingerprinty oparte
o hash kodu źródłowego automatycznie unieważniają cache tylko wtedy, gdy
coś faktycznie się zmieniło, a drogi etap Opus+debata jest pomijany, gdy
pula kandydatów się nie zmieniła. Świadomie nie ma tu wektorowego indeksu
ANN ani frameworka RAG — to brute-force NumPy na ograniczonej puli do 2000
ofert, co jest uczciwym kompromisem przy tej skali, a nie przeoczeniem.
Całość mierzę nie akademickimi metrykami IR, tylko realną precyzją
produkcyjną: jaki odsetek ofert, które system sam by zaaplikował, kandydat
faktycznie zaaplikował zamiast odrzucić.
