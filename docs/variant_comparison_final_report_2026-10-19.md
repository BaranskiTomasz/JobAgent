# JobAgent: czy zmieniać logikę dopasowywania ofert? Pełny raport porównawczy (2026-10-19)

**Metodologia:** Trzy warianty (0 — status quo, 1 — ewolucja, 2 — rewolucja)
zostały ocenione **niezależnie przez dwa różne modele** (Codex na GPT-5.6-Sol,
Mistral Vibe na GLM-5.3), każdy wg tego samego schematu i tego samego
zestawu realnych danych wyciągniętych dziś na żywo z bazy JobAgentWeb
(`docs/variant_analysis_data_snapshot_2026-10-19.md`). To 6 niezależnych,
w pełni udokumentowanych analiz (każda z cytatami do linii kodu i do
konkretnych liczb). Ten dokument je syntetyzuje. Pełne analizy źródłowe:
`docs/variant{0,1,2}_*_{codex,vibe}.md`.

---

## Fundament: co realnie mówią dane (nie opinie — liczby z produkcji)

- **5259 ofert w historii**: 1178 reviewed, 76 applied (61 różnych firm),
  221 odrzuconych ręcznie, **3783 auto-rejected (72%)**.
- **79% auto-rejectów i tak płaci za pełne wywołanie Sonnet** (próbka 500) —
  darmowy filtr deterministyczny wyłapuje tylko 21%.
- **7 na 7 dismissed items** to ten sam, w 100% spójny błąd kalibracji
  scorera: karanie za brak Pythona lub brak tytułu "Lead" na dobrych
  ofertach PHP — mimo że kandydat poprawił to ręcznie siedem razy.
- **Profil preferencji: tylko 11 sygnałów z 209 decyzji** (~1 na 19) —
  i ani jeden z tych 7 dismissed items nigdy nie stał się sygnałem.
- **7+ wystąpień** "duplikat, ta sama oferta w innym kraju, już
  aplikowałem" w próbce zaledwie 25 powodów odrzucenia — a deterministyczny
  filtr w ogóle nie sprawdza wymogu języka ani duplikatów cross-country
  (okno dedup w JobAgentWeb to tylko 21 dni, za krótkie na wzorzec
  powtarzający się miesiącami).
- **Ranking już działa nieźle**: apply rate 86.2% dla miejsc 1-5, spadając
  monotonicznie do 14.3% dla miejsc 16-20; precision@5=0.80, precision@10=0.90;
  tylko 2.4% (5 z 211) przypadków, gdzie ranking i decyzja użytkownika się
  rozjechały.
- **Koszt dzienny dziś: ~$2.25/typowy run** (91 ofert), z czego 85% to
  ekstrakcja (Haiku, $0.86) + scoring (Sonnet, $1.05) — **nie** embeddingi
  ani reranking (razem $0.0044, czyli szum).

Oba modele niezależnie doszły do tych samych wniosków z tych danych — to
jest silny sygnał wiarygodności, nie przypadek dwóch podobnych odpowiedzi.

---

## Tabela porównawcza — wynik każdej z 6 analiz

| Wariant | Ocena jakości dopasowania (Codex) | Ocena jakości (Vibe) | **Średnia** | Koszt dzienny (Codex) | Koszt dzienny (Vibe) | **Średnia** | Koszt wdrożenia |
|---|---:|---:|---:|---:|---:|---:|---|
| **0 — Status quo** | 7.5/10 | 7/10 | **7.25/10** | $1.90 | $2.25 | **$2.08/dzień** | $0 / 0h |
| **1 — Ewolucja** | 8.5/10 | 8/10 | **8.25/10** | $1.90 | $1.75 | **$1.83/dzień** | 7-10 dni / 6-9 dni ≈ **~7-9 dni** |
| **2 — Rewolucja** | 9/10 | 7/10 | **8.0/10** | $0.85 | $1.00 | **$0.93/dzień** | 20-30 dni (Codex) / 60-90h≈8-11 dni (Vibe) — **duży rozrzut, patrz niżej** |

---

## Ranking #1: który wariant jest najlepszy pod względem doboru ofert?

**🥇 Wariant 1 (Ewolucja) — 8.25/10, zwycięzca obu analiz niezależnie.**
Oba modele zgadzają się, że to wariant o najlepszym stosunku jakość/wysiłek:
naprawia wszystkie 4 realne, zaobserwowane problemy (79% marnotrawstwa,
7/7 bias scorera, język, duplikaty), **nie ruszając** już i tak dobrze
działającego rankingu (86.2%→14.3%, divergence 2.4%). Codex stawia Wariant 2
nieznacznie wyżej (9 vs 8.5) bo teoretycznie ma wyższy sufit; Vibe jest
bardziej sceptyczny wobec Wariantu 2 ("czysty argument jakościowy jest
cienki... jeśli liczyłaby się tylko jakość, Wariant 1 zdecydowanie wygrywa
na jakość-na-godzinę-pracy") i ocenia go niżej niż Wariant 1 (7 vs 8).

**🥈 Wariant 2 (Rewolucja) — 8.0/10, drugi, ale z dużym rozrzutem (9 vs 7).**
Rozbieżność między modelami jest tu sama w sobie informacją: Codex wierzy,
że pełny redesign (wersjonowane fakty z dowodami, klastrowanie duplikatów,
model preferencji uczony z jawnych porównań parami) da wyraźnie lepszy
sufit jakości niż to, co da się osiągnąć samymi poprawkami promptów. Vibe
odpowiada: realny pułap jakości i tak jest ograniczony przez to, co już
widać w danych (86.2%/2.4%), więc rewolucja głównie **przenosi** te same
naprawy na nowszą infrastrukturę, zamiast dawać jakościowy skok — a do
tego dochodzi **realne ryzyko regresji** podczas "odgrzewania" wyzerowanego
profilu preferencji (patrz niżej).

**🥉 Wariant 0 (Status quo) — 7.25/10, najgorszy, ale nie "zły".**
Oba modele podkreślają: to NIE jest zepsuty system (2.4% divergence to
mało). To po prostu system, który **świadomie zostawia na stole** 3
konkretne, policzalne, już zaobserwowane problemy, bez żadnego powodu
poza bezczynnością.

---

## Ranking #2: który wariant jest najdroższy w codziennej pracy?

To pytanie ma kontrintuicyjną odpowiedź, zgodną w obu analizach:

**🔴 Najdroższy na co dzień: Wariant 0 (Status quo) — ~$2.08/dzień.**
Robienie "nic" nie jest darmowe w sensie operacyjnym — oznacza
**dalsze płacenie** za te same 79% zmarnowanych wywołań Sonnet, w
nieskończoność, każdego dnia zbierania nowych ofert. To jedyny wariant,
który nie obniża kosztu dziennego ani o centa.

**🟡 Środek: Wariant 1 (Ewolucja) — ~$1.83/dzień** (spadek ~12-20% vs
status quo). Oszczędność pochodzi wyłącznie z przechwycenia części tych
79% marnowanych wywołań Sonnet **za darmo**, bo dane do tego (`languages[]`,
`skills[]` z wymogami) **już są wyciągnięte przez extractor** — tylko
nikt ich nie czyta w filtrze dealbreakerów. Zero nowej infrastruktury,
zero nowych kosztów API poza jednorazowym re-embeddingiem (~$0.65-0.90).

**🟢 Najtańszy na co dzień: Wariant 2 (Rewolucja) — ~$0.93/dzień**
(spadek ~55-65% vs status quo). Tu oszczędność jest strukturalna: tani
model (np. `claude-haiku-5-5`, 10x tańszy niż Haiku 4.5) robi wstępną
ekstrakcję, a kosztowny Sonnet/Opus widzi tylko to, co faktycznie
przeszło bramki. Ale to tańszy **operacyjnie** kosztem **największej
jednorazowej inwestycji** (patrz tabela wdrożenia) i realnego ryzyka
przejściowego pogorszenia jakości.

**Wniosek:** im więcej inwestujesz na starcie, tym mniej płacisz codziennie
— to nie jest zaskakujące, ale skala jest: rewolucja redukuje dzienny koszt
ponad dwukrotnie względem bezczynności.

---

## Czy to ewolucja czy rewolucja — konkretna skala zmiany w kodzie

| | Wariant 0 | Wariant 1 | Wariant 2 |
|---|---|---|---|
| Pliki dotknięte | 0 | ~8 plików (dealbreakers, scorer, runner, rank_jobs, fusion, preference_agent, indexer, nowy harness) | ~12+ plików przepisanych/zastąpionych, kilka nowych modułów |
| Linie kodu | 0 | **+450-700 dodanych, 0 usuniętych** (oba modele zgodne co do rzędu wielkości) | **+2500-3500 dodanych, 800-2000 usuniętych** (duży rozrzut między modelami co do dokładnej skali) |
| Model danych / schemat bazy | bez zmian | **bez zmian** — te same tabele, ta sama kolumna JSON-text na embeddingi | **zmieniony** — nowe tabele (parowe porównania, kanoniczne identyfikatory ofert, wersjonowane cechy), natywne wektory zamiast JSON-tekstu |
| Wymaga przeliczenia historii? | nie | tylko re-embedding aktywnej puli (~$0.65-0.90, bo zmienia się tekst wejściowy) | tak — re-ekstrakcja ~5259 ofert (~$15-50) + re-embedding (~$0.17) |
| Werdykt | brak zmiany | **ewolucja** — zmieniają się prompty, filtry, kolejność, nic w warstwie danych | **rewolucja** — zmienia się schemat, dane preferencji, tożsamość ofert, sposób uczenia się systemu |

---

## Wariant 2 — to, czego nie da się pominąć: koszt "wyzerowania"

Oba modele policzyły to niezależnie i doszły do bardzo zbliżonych liczb:

- Jeśli profil preferencji zostanie wyzerowany i odbudowywany **tak samo
  jak dziś** (biernie, z aplikacji/odrzuceń): przy tempie ~209 decyzji na
  kilka miesięcy, osiągnięcie dzisiejszego poziomu (11 sygnałów) zajmie
  **~19-21 tygodni** (4-5 miesięcy) **gorszego niż dziś** dopasowania.
  To nie jest kosmetyczny koszt — to realne miesiące słabszego rankingu
  w trakcie aktywnego szukania pracy.
- **Oba modele niezależnie proponują to samo obejście**: jednorazowa
  sesja jawnych porównań parami ("która z tych dwóch ofert bardziej by Cię
  skłoniła do aplikacji?"), 20-40 par, ~45-90 minut Twojego czasu, od razu
  daje użyteczny profil "dnia pierwszego" zamiast czekać miesiącami.
  Z tym obejściem realny koszt "odgrzewania" spada do **~3-5 tygodni**
  zamiast 19-21.
- Werdykt obu modeli: **rób rewolucję wyłącznie z tym obejściem** — bez
  niego to zły handel (miesiące gorszego dopasowania za niepewny zysk
  jakościowy).

---

## Rzeczy, co do których WSZYSTKIE 6 analiz się zgadza (najwyższa pewność)

1. **Nie wdrażać pgvector/HNSW/dedykowanej bazy wektorowej dla jakości.**
   Przy puli ≤2000 ofert exact-search w NumPy ma perfekcyjny recall; ANN
   tylko by go pogorszył. Jedyny sensowny powód to nauka/CV (oddzielnie
   zaznaczone we wszystkich 6 raportach).
2. **Największy, najtańszy, natychmiastowy quick-win: nauczyć filtr
   dealbreakerów czytać pola, które extractor już wyciąga** —
   `languages[].requirement`, `skills[].requirement`. To naprawia język
   i część duplikatów za **zero dodatkowego kosztu API**, bo dane już
   istnieją w `structured_data`.
3. **Błąd scorera (Python/Lead) to poprawka promptu, nie infrastruktury** —
   da się naprawić w godzinę, niezależnie od wybranego wariantu.
4. **"Debata" (drugi "niezależny" głos) to w praktyce ten sam model
   (Sonnet) co scorer, inna rola** — realna niezależność wymagałaby innego
   dostawcy, ale żaden model nie ma dowodu, że to się opłaca bez
   wcześniejszego zmierzenia (stąd zgodna rekomendacja: najpierw harness
   ewaluacyjny, potem ewentualna zmiana modelu debaty).
5. **Brakuje harnessu offline do mierzenia zmian** — bez zamrożonego,
   oznakowanego zbioru testowego każda kolejna zmiana to zgadywanie, nie
   pomiar. To rekomendowane jako pierwszy krok niezależnie od wybranego
   wariantu.

---

## Rekomendacja końcowa (synteza orkiestratora, nie jednego modelu)

Biorąc pod uwagę wszystkie 6 analiz razem:

**Rób Wariant 1 (Ewolucję) teraz, niezależnie od dalszych decyzji.** Jest
tańszy we wdrożeniu (~7-9 dni pracy), najwyżej oceniony pod względem
jakości dopasowania w uśrednieniu, nie rusza niczego, co już działa
dobrze, i każda jego część (filtr języka, poprawka promptu, warstwa
duplikatów) jest niezależnie wdrażalna i odwracalna.

**Rozważ Wariant 2 (Rewolucję) jako osobny, świadomy projekt — nie
zamiennik Wariantu 1, tylko rozszerzenie** — jeśli priorytetem jest
realna nauka nowoczesnego podejścia do ranking/RAG z myślą o CV, **pod
warunkiem** zrobienia najpierw jawnej sesji porównań parami (żeby uniknąć
19-21-tygodniowego dołka jakości). Nie rób rewolucji wyłącznie dla
jakości dopasowania — oba modele zgadzają się, że czysty argument
jakościowy jest tu słabszy niż argument "nauka + niższy koszt
długoterminowy".

**Status quo nie jest neutralną opcją "poczekaj i zobacz"** — to opcja,
która kosztuje najwięcej dziennie, ze wszystkich trzech, bo świadomie
utrzymuje już policzalne marnotrawstwo.

---

## Źródła

- Dane: `docs/variant_analysis_data_snapshot_2026-10-19.md` (żywe dane z
  bazy, pociągnięte 2026-10-19)
- Schemat/kryteria wspólne dla wszystkich 6 analiz: `docs/variant_analysis_shared_criteria.md`
- Pełne analizy źródłowe (po ~250-400 linii każda, z cytatami do kodu):
  `docs/variant0_status_quo_codex.md`, `docs/variant0_status_quo_vibe.md`,
  `docs/variant1_evolution_codex.md`, `docs/variant1_evolution_vibe.md`,
  `docs/variant2_revolution_codex.md`, `docs/variant2_revolution_vibe.md`
- Wcześniejsza runda (ogólna analiza RAG, bez podziału na warianty):
  `docs/ai_rag_architecture_analysis_2026-10-19.md`,
  `docs/rag_modernization_proposal_codex.md`,
  `docs/rag_modernization_proposal_vibe.md`
