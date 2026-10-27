# Research Suite data schema (v1)

Three skills share these files so their output chains together:
`paper-scout` → `papers.json` → `claim-conflict-finder` → `claims.json` + `conflicts.json` → `research-dossier`.

All files live in one project folder (default `./research/<slug>/`). Every file is plain JSON, UTF-8, pretty-printed.

## papers.json

```json
{
  "schema": "research-suite/papers@1",
  "query": "the research question in the user's words",
  "search_terms": ["term one", "term two"],
  "created": "2026-10-27",
  "papers": [
    {
      "key": "smith2021graph",
      "title": "Graph neural networks for ...",
      "authors": ["Jane Smith", "Wei Chen"],
      "year": 2021,
      "venue": "Nature Machine Intelligence",
      "type": "article",
      "is_preprint": false,
      "doi": "10.1038/s42256-021-00001-1",
      "openalex_id": "W3123456789",
      "s2_id": "abc123...",
      "arxiv_id": "2101.00001",
      "url": "https://doi.org/10.1038/...",
      "oa_url": "https://arxiv.org/pdf/2101.00001",
      "abstract": "…",
      "cited_by": 412,
      "references": ["lee2019deep"],
      "topics": ["Graph neural network", "Drug discovery"],
      "sources": ["openalex", "semanticscholar"],
      "score": 0.83,
      "status": "included",
      "note": ""
    }
  ]
}
```

Rules
- `key` is the citation key used everywhere else: `<first-author-lastname><year><first-content-word-of-title>`, lowercase ASCII, with `a`, `b`, … appended on collision.
- `references` lists only keys **inside this file** (the in-set citation graph). Outside references are dropped.
- `status` is one of `candidate`, `included`, `excluded`. `note` records why a paper was excluded.
- Every paper must come from a real API record. Never type a paper in by hand unless the user supplied it, and then set `sources: ["user"]`.

## claims.json

```json
{
  "schema": "research-suite/claims@1",
  "aliases": { "gnn": "graph neural network", "rf": "random forest" },
  "claims": [
    {
      "id": "C001",
      "paper": "smith2021graph",
      "statement": "GNNs outperform random forests on molecular property prediction.",
      "subject": "graph neural network",
      "relation": "outperforms",
      "object": "random forest",
      "direction": "positive",
      "metric": "ROC-AUC",
      "value": 0.86,
      "comparison_value": 0.81,
      "unit": "",
      "dataset": "MoleculeNet BBBP",
      "population": "",
      "method": "5-fold scaffold split",
      "conditions": ["scaffold split", "no pretraining"],
      "sample_size": 2039,
      "evidence": "Table 2",
      "quote": "outperforms RF by 5 points of AUC",
      "source_text": "abstract",
      "confidence": "high"
    }
  ]
}
```

Field meanings
- `subject` / `relation` / `object`: a subject–predicate–object triple. Normalise names with `aliases` so the same thing has one spelling across papers.
- `direction`: the sign of the finding about subject→object:
  - `positive` — increases, improves, outperforms, is associated with more
  - `negative` — decreases, worsens, underperforms, is associated with less
  - `null` — no significant effect / no difference / comparable
  - `mixed` — depends on conditions inside the same paper (split into two claims when possible)
- `value` / `comparison_value`: numbers when the paper reports them; `null` otherwise.
- `conditions`: short tags for context that could change the outcome (dataset split, population, setting, scale).
- `source_text`: `abstract` or `full-text`. Claims read from abstracts only get `confidence` no higher than `medium`.
- `quote`: at most 25 words, copied exactly, so a reader can find it in the paper.

## conflicts.json (written by the conflict script)

```json
{
  "schema": "research-suite/conflicts@1",
  "topics": [ { "topic": "graph neural network | random forest | ROC-AUC", "claims": ["C001", "C007"], "verdict": "contested" } ],
  "pairs": [
    {
      "a": "C001", "b": "C007",
      "topic": "…",
      "kind": "contradiction",
      "severity": "high",
      "genuine": false,
      "differences": ["dataset", "conditions"],
      "explanation": "Opposite directions, but different datasets (BBBP vs Tox21) and split strategy."
    }
  ]
}
```

`kind` ∈ `contradiction` (opposite signs), `null-vs-effect`, `magnitude` (same sign, values differ beyond tolerance), `agreement`.
`genuine` is `true` when no context field differs — those are the conflicts worth a student's attention.

## synthesis.json (written by the agent for research-dossier)

```json
{
  "schema": "research-suite/synthesis@1",
  "title": "What we know about …",
  "question": "…",
  "author": "",
  "summary": "One paragraph answer to the question, citing [@smith2021graph].",
  "takeaways": ["Short sentence with a citation [@lee2019deep]."],
  "themes": [
    { "name": "Theme name", "summary": "Paragraph with [@key] citations.", "papers": ["smith2021graph"] }
  ],
  "debates": [ { "topic": "…", "summary": "Why the literature disagrees, with [@a] vs [@b].", "pairs": ["C001~C007"] } ],
  "open_questions": ["…"],
  "methods_note": "How the search was done, in one paragraph.",
  "annotations": { "smith2021graph": "One-to-three sentence annotation." }
}
```

Every `[@key]` must exist in `papers.json`; the dossier builder refuses to render otherwise.
