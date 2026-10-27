---
name: paper-scout
description: Finds real, citable research papers for a research question using the free OpenAlex, Semantic Scholar and arXiv APIs, snowballs through references and citations, de-duplicates and ranks them, and delivers a reading list, a BibTeX file and an interactive citation-map web page. Use when a student or researcher asks to find papers, build a reading list, start a literature review, see how a field connects, or check what has been published on a topic. Never invents papers.
---

# Paper Scout

Turn a research question into a ranked, de-duplicated reading list of **real** papers, plus a visual map of how they cite each other.

Part of the Research Suite: `paper-scout` → `claim-conflict-finder` → `research-dossier`. All three read and write the same `papers.json` (see `references/schema.md`).

## Non-negotiable rules

1. **Never invent a paper, author, DOI, venue or citation count.** Every paper in the output must come from an API record returned by `scripts/scout.py` (or a source the user gave you). If the script cannot reach the APIs, say so plainly and follow "No terminal or no network" below; do not fill the gap from memory.
2. Mark preprints as **not peer-reviewed** every time you mention them.
3. Report coverage honestly: which sources answered, which failed, the year window, and what the search could have missed.

## Network and data access (disclosed)

- **Hosts contacted, read-only HTTPS GET requests only:** `api.openalex.org`, `api.semanticscholar.org`, `export.arxiv.org`. Nothing else is contacted, and no data about the user or their files is sent — only the search strings and paper IDs.
- **Environment variables read (both optional):** `S2_API_KEY` is sent only to `api.semanticscholar.org` as its documented `x-api-key` header; `OPENALEX_MAILTO` is sent only to `api.openalex.org` as its documented `mailto` parameter. Neither is stored or logged.
- **Files written:** only inside the `--out` project folder (`papers.json`, `references.bib`, `citation_map.html`). The generated HTML makes no network requests.

## 1. Clarify the question (one short exchange at most)

Collect what you can from the request; ask only for what is missing and matters:

- **Research question** in one sentence (required).
- **Year window** (default: last 10 years; include older work only through snowballing).
- **Field or discipline**, so you pick the right vocabulary.
- **Papers the user already knows** (titles or DOIs) — these become snowball seeds.
- **How many papers** they want to end with (default 20–30).

If the user is plainly in a hurry, skip questions and state the defaults you used.

## 2. Plan the search strings

Write 3–6 search strings that cover the question from different angles. Each string is 2–6 words.

- Core concept + outcome: `intermittent fasting weight loss`
- Synonyms and alternative names: `time-restricted eating`, `alternate-day fasting`
- The comparison or method: `continuous calorie restriction randomized trial`
- For health questions, think PICO (population, intervention, comparison, outcome).
- For CS/engineering, include the task name and the benchmark or method family.

Show the strings to the user in one line ("Searching for: …") and continue.

## 3. Run the pipeline

The script uses only the Python 3 standard library. Run it exactly as shipped. Pick a project folder, default `research/<short-slug>` in the current directory.

```bash
S=scripts/scout.py; OUT=research/<slug>
python3 $S search   --out $OUT --query "<research question>" --terms "<string 1>" "<string 2>" "<string 3>" --year-from 2016 --limit 25
python3 $S rank     --out $OUT --keep 40          # first pass so snowballing starts from the best papers
python3 $S snowball --out $OUT --top 5 --direction both --limit 15
python3 $S enrich   --out $OUT                    # fills abstracts and citation links by DOI
python3 $S rank     --out $OUT --keep 25          # final ranking; top 25 become "included"
python3 $S stats    --out $OUT --top 30
```

Options worth knowing:
- `--sources openalex,s2,arxiv` — drop `arxiv` outside physics, maths, CS and quantitative biology. Semantic Scholar's keyless tier is shared and often returns 429; the script then skips it for the rest of the run and continues with the other sources. That is normal — mention it in the search notes, and suggest the user set a free `S2_API_KEY` for fuller coverage.
- `--mailto you@example.com` or env `OPENALEX_MAILTO` puts you in OpenAlex's faster pool. Optional.
- env `S2_API_KEY` raises Semantic Scholar's rate limit. Optional.
- `snowball --keys key1 key2` seeds from specific papers (use the user's known papers here).
- `rank --extra word1 word2` adds relevance keywords the question implies but does not say.

## 4. Screen like a researcher (this is your judgement, not the script's)

The ranking is a starting point. Read the title and abstract of every `included` paper and of the top `candidate`s, then edit `papers.json` directly:

- Set `"status": "excluded"` and a one-line `"note"` for papers that are off-topic, wrong population/setting, duplicates of a later version, or retracted.
- Promote strong candidates to `"included"`.
- Keep a balance: seminal older work (high citations), the newest work (last 2 years), and at least one review or meta-analysis if one exists.
- When a question has two sides, make sure both sides are represented — the next skill looks for disagreements.

Then build the outputs:

```bash
python3 $S bib --out $OUT     # references.bib (included papers)
python3 $S map --out $OUT     # citation_map.html — self-contained, opens offline
```

## 5. Deliver

Give the user, in this order:

1. **Answer-first summary** (3–5 sentences): how big the literature is, the main clusters, and where the field is heading.
2. **Reading list** — a table of the top 10–15 included papers: `Year | Paper (first author et al.) | Venue | Citations | Why read it`. "Why read it" is one concrete line (seminal method, largest trial, newest review, opposing result…). Flag preprints.
3. **Start here**: the 3 papers to read first and in what order.
4. **Files**: `papers.json`, `references.bib`, `citation_map.html` (tell them to open the map in a browser: Network view shows research clusters, Timeline view shows how ideas developed).
5. **Search notes**: the strings used, sources that answered or failed, the year window, and known blind spots (non-English work, books, paywalled abstracts).
6. **Next step**: offer `claim-conflict-finder` to extract findings and see where these papers disagree.

Keep paper citations in the form *Author et al. (Year)* with the DOI link when you mention them.

## No terminal or no network

If you cannot run Python or the APIs are unreachable:

- Use whatever web search or fetch tools you have to query `https://api.openalex.org/works?search=<terms>` (JSON) or the Semantic Scholar / arXiv search pages.
- Record only papers you actually saw in a result, with their DOI or arXiv id. Write `papers.json` by hand in the schema format with `"sources": ["web"]`.
- Tell the user the map and BibTeX could not be generated automatically, and give the BibTeX as text if they need it.
- If you have no search tools at all, say you cannot search, and offer search strings the user can paste into Google Scholar, OpenAlex or Semantic Scholar. Do not produce a reading list from memory.

## Quality checklist before you reply

- [ ] Every listed paper has a DOI, arXiv id or URL from an API record.
- [ ] Preprints are flagged.
- [ ] Excluded papers have a note.
- [ ] Both sides of a contested question are represented, or you said the search found only one side.
- [ ] The map file and BibTeX exist and you named their paths.
