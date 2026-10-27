#!/usr/bin/env python3
"""paper-scout: find, de-duplicate, rank and map research papers.

Standard library only. Talks to three free scholarly APIs:
  - OpenAlex          https://api.openalex.org
  - Semantic Scholar  https://api.semanticscholar.org
  - arXiv             http://export.arxiv.org

Subcommands (run `scout.py <cmd> -h` for options):
  search    query the APIs and merge results into papers.json
  snowball  follow references / citations of chosen papers (OpenAlex)
  enrich    fill in OpenAlex metadata (abstracts, citation links) by DOI
  rank      score papers and mark the top N as "included"
  bib       write references.bib
  map       write citation_map.html (self-contained, no network needed)
  stats     print a summary table

Data format: see references/schema.md.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

VERSION = "1.0.0"
OPENALEX = "https://api.openalex.org"
S2 = "https://api.semanticscholar.org/graph/v1"
ARXIV = "http://export.arxiv.org/api/query"

STOP = set("""a an the of for and or in on at to from by with without into onto over under via using use
based towards toward new novel study studies analysis approach approaches method methods model models
is are was were be been being this that these those its their our we it as vs versus between among
than do does how what why when which can may effect effects role impact""".split())

PREPRINT_VENUES = ("arxiv", "biorxiv", "medrxiv", "ssrn", "research square", "preprints.org",
                   "chemrxiv", "psyarxiv", "osf preprints", "techrxiv")


# ----------------------------------------------------------------------------- utils

def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", ascii_fold(t).lower())


def norm_doi(d: str | None) -> str:
    if not d:
        return ""
    d = d.strip().lower()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:", "", d)
    return d


def norm_arxiv(a: str | None) -> str:
    if not a:
        return ""
    a = a.strip()
    a = re.sub(r"^(https?://)?arxiv\.org/(abs|pdf)/", "", a)
    a = re.sub(r"\.pdf$", "", a)
    return re.sub(r"v\d+$", "", a)


def short_oa(oid: str | None) -> str:
    if not oid:
        return ""
    return oid.rstrip("/").split("/")[-1]


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", ascii_fold(text).lower())
    return [stem(w) for w in words if w not in STOP and len(w) > 1]


def stem(w: str) -> str:
    for suf in ("ization", "isation", "ations", "ation", "ments", "ment", "ness", "ities", "ity",
                "ings", "ing", "ies", "ers", "er", "es", "s", "ed", "al"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def today() -> str:
    return _dt.date.today().isoformat()


def http_json(url: str, headers: dict | None = None, retries: int = 4, raw: bool = False):
    h = {"User-Agent": f"research-suite-paper-scout/{VERSION}"}
    if headers:
        h.update(headers)
    delay = 2.0
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read().decode("utf-8", "replace")
                return body if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = float(e.headers.get("Retry-After") or delay)
                log(f"  … {e.code} from {urllib.parse.urlsplit(url).netloc}, retrying in {wait:.0f}s")
                time.sleep(wait)
                delay *= 2
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise


# ----------------------------------------------------------------------------- parsers

def openalex_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = []
    for word, idxs in inv.items():
        for i in idxs:
            pos.append((i, word))
    pos.sort()
    return " ".join(w for _, w in pos)


def parse_openalex(w: dict) -> dict:
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    venue = src.get("display_name") or ""
    ids = w.get("ids") or {}
    arxiv = ""
    for l in w.get("locations") or []:
        u = (l.get("landing_page_url") or "")
        if "arxiv.org/abs/" in u:
            arxiv = norm_arxiv(u)
            break
    typ = w.get("type") or ""
    is_pre = typ == "preprint" or src.get("type") == "repository" or venue.lower().startswith(PREPRINT_VENUES)
    topics = [t.get("display_name") for t in (w.get("topics") or [])[:3] if t.get("display_name")]
    if not topics:
        topics = [c.get("display_name") for c in (w.get("concepts") or [])[:3] if c.get("display_name")]
    oa = (w.get("open_access") or {}).get("oa_url") or ""
    doi = norm_doi(w.get("doi") or ids.get("doi"))
    return {
        "title": (w.get("display_name") or w.get("title") or "").strip(),
        "authors": [a.get("author", {}).get("display_name", "") for a in (w.get("authorships") or [])
                    if a.get("author")],
        "year": w.get("publication_year"),
        "venue": venue,
        "type": typ or "article",
        "is_preprint": bool(is_pre),
        "doi": doi,
        "openalex_id": short_oa(w.get("id")),
        "arxiv_id": arxiv,
        "url": f"https://doi.org/{doi}" if doi else (loc.get("landing_page_url") or w.get("id") or ""),
        "oa_url": oa,
        "abstract": openalex_abstract(w.get("abstract_inverted_index")),
        "cited_by": w.get("cited_by_count") or 0,
        "topics": topics,
        "sources": ["openalex"],
        "_ref_ids": [short_oa(r) for r in (w.get("referenced_works") or [])],
    }


def parse_s2(p: dict) -> dict:
    ext = p.get("externalIds") or {}
    doi = norm_doi(ext.get("DOI"))
    arxiv = norm_arxiv(ext.get("ArXiv"))
    venue = p.get("venue") or ((p.get("journal") or {}).get("name") or "")
    pubtypes = [t.lower() for t in (p.get("publicationTypes") or [])]
    is_pre = (not venue and bool(arxiv)) or venue.lower().startswith(PREPRINT_VENUES)
    return {
        "title": (p.get("title") or "").strip(),
        "authors": [a.get("name", "") for a in (p.get("authors") or [])],
        "year": p.get("year"),
        "venue": venue or ("arXiv" if arxiv else ""),
        "type": "review" if "review" in pubtypes else ("preprint" if is_pre else "article"),
        "is_preprint": bool(is_pre),
        "doi": doi,
        "s2_id": p.get("paperId") or "",
        "arxiv_id": arxiv,
        "url": f"https://doi.org/{doi}" if doi else (p.get("url") or ""),
        "oa_url": ((p.get("openAccessPdf") or {}).get("url") or ""),
        "abstract": p.get("abstract") or "",
        "cited_by": p.get("citationCount") or 0,
        "topics": [f for f in (p.get("fieldsOfStudy") or [])][:3],
        "sources": ["semanticscholar"],
    }


ATOM = "{http://www.w3.org/2005/Atom}"
ARX = "{http://arxiv.org/schemas/atom}"


def parse_arxiv_feed(xml_text: str) -> list[dict]:
    out = []
    root = ET.fromstring(xml_text)
    for e in root.findall(f"{ATOM}entry"):
        aid = norm_arxiv((e.findtext(f"{ATOM}id") or ""))
        title = re.sub(r"\s+", " ", e.findtext(f"{ATOM}title") or "").strip()
        if not title or title.lower() == "error":
            continue
        pub = e.findtext(f"{ATOM}published") or ""
        doi = norm_doi(e.findtext(f"{ARX}doi"))
        jref = (e.findtext(f"{ARX}journal_ref") or "").strip()
        cats = [c.get("term") for c in e.findall(f"{ATOM}category") if c.get("term")]
        out.append({
            "title": title,
            "authors": [a.findtext(f"{ATOM}name") or "" for a in e.findall(f"{ATOM}author")],
            "year": int(pub[:4]) if pub[:4].isdigit() else None,
            "venue": jref or "arXiv",
            "type": "article" if jref or doi else "preprint",
            "is_preprint": not (jref or doi),
            "doi": doi,
            "arxiv_id": aid,
            "url": f"https://doi.org/{doi}" if doi else f"https://arxiv.org/abs/{aid}",
            "oa_url": f"https://arxiv.org/pdf/{aid}",
            "abstract": re.sub(r"\s+", " ", e.findtext(f"{ATOM}summary") or "").strip(),
            "cited_by": 0,
            "topics": cats[:3],
            "sources": ["arxiv"],
        })
    return out


# ----------------------------------------------------------------------------- API calls

def oa_params(extra: dict, mailto: str | None) -> str:
    p = dict(extra)
    if mailto:
        p["mailto"] = mailto
    return urllib.parse.urlencode(p, safe=":|,")


def search_openalex(q: str, y0: int | None, y1: int | None, limit: int, mailto: str | None) -> list[dict]:
    filt = []
    if y0:
        filt.append(f"from_publication_date:{y0}-01-01")
    if y1:
        filt.append(f"to_publication_date:{y1}-12-31")
    params = {"search": q, "per-page": min(limit, 200), "sort": "relevance_score:desc"}
    if filt:
        params["filter"] = ",".join(filt)
    data = http_json(f"{OPENALEX}/works?{oa_params(params, mailto)}")
    return [parse_openalex(w) for w in data.get("results", [])][:limit]


def search_s2(q: str, y0: int | None, y1: int | None, limit: int) -> list[dict]:
    fields = "title,abstract,year,authors,venue,journal,citationCount,externalIds,openAccessPdf,url,publicationTypes,fieldsOfStudy"
    params = {"query": q, "limit": min(limit, 100), "fields": fields}
    if y0 or y1:
        params["year"] = f"{y0 or ''}-{y1 or ''}"
    headers = {}
    if os.environ.get("S2_API_KEY"):
        headers["x-api-key"] = os.environ["S2_API_KEY"]
    data = http_json(f"{S2}/paper/search?{urllib.parse.urlencode(params)}", headers=headers)
    return [parse_s2(p) for p in (data.get("data") or [])][:limit]


def search_arxiv(q: str, y0: int | None, y1: int | None, limit: int) -> list[dict]:
    terms = " AND ".join(f"all:{t}" for t in re.findall(r"[\w\-]+", q)[:8]) or f"all:{q}"
    params = {"search_query": terms, "max_results": min(limit * 2, 100), "sortBy": "relevance"}
    xml_text = http_json(f"{ARXIV}?{urllib.parse.urlencode(params)}", raw=True)
    time.sleep(3)  # arXiv asks for 3 s between calls
    rows = parse_arxiv_feed(xml_text)
    rows = [r for r in rows if r["year"] and (not y0 or r["year"] >= y0) and (not y1 or r["year"] <= y1)]
    return rows[:limit]


def openalex_batch(ids: list[str], field: str, mailto: str | None) -> list[dict]:
    """Fetch OpenAlex works by ids.openalex or doi, 50 at a time."""
    out = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        params = {"filter": f"{field}:{'|'.join(chunk)}", "per-page": 50}
        data = http_json(f"{OPENALEX}/works?{oa_params(params, mailto)}")
        out.extend(parse_openalex(w) for w in data.get("results", []))
    return out


def openalex_citing(oid: str, limit: int, mailto: str | None) -> list[dict]:
    params = {"filter": f"cites:{oid}", "per-page": min(limit, 200), "sort": "cited_by_count:desc"}
    data = http_json(f"{OPENALEX}/works?{oa_params(params, mailto)}")
    return [parse_openalex(w) for w in data.get("results", [])][:limit]


# ----------------------------------------------------------------------------- store

class Store:
    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            self.data = json.loads(path.read_text("utf-8"))
        else:
            self.data = {"schema": "research-suite/papers@1", "query": "", "search_terms": [],
                         "created": today(), "papers": []}
        self.papers: list[dict] = self.data["papers"]

    def find(self, rec: dict) -> dict | None:
        doi, ax, oa, s2 = rec.get("doi"), rec.get("arxiv_id"), rec.get("openalex_id"), rec.get("s2_id")
        nt = norm_title(rec.get("title", ""))
        for p in self.papers:
            if (doi and p.get("doi") == doi) or (ax and p.get("arxiv_id") == ax) \
                    or (oa and p.get("openalex_id") == oa) or (s2 and p.get("s2_id") == s2):
                return p
        if len(nt) > 15:
            for p in self.papers:
                if norm_title(p.get("title", "")) == nt:
                    return p
        return None

    def add(self, rec: dict, status: str = "candidate") -> tuple[dict, bool]:
        if not rec.get("title"):
            return rec, False
        p = self.find(rec)
        if p is None:
            rec = dict(rec)
            rec.setdefault("status", status)
            rec.setdefault("note", "")
            rec["key"] = self.make_key(rec)
            self.papers.append(rec)
            return rec, True
        for k, v in rec.items():
            if k == "sources":
                p["sources"] = sorted(set(p.get("sources", [])) | set(v))
            elif k == "cited_by":
                p["cited_by"] = max(p.get("cited_by") or 0, v or 0)
            elif k == "is_preprint":
                # a peer-reviewed version wins over a preprint flag
                p["is_preprint"] = bool(p.get("is_preprint", True) and v)
            elif k in ("venue", "type") and p.get("is_preprint") and not rec.get("is_preprint") and v:
                p[k] = v
            elif k == "abstract" and v and len(v) > len(p.get("abstract") or ""):
                p[k] = v
            elif not p.get(k) and v:
                p[k] = v
        return p, False

    def make_key(self, rec: dict) -> str:
        first = (rec.get("authors") or ["anon"])[0] or "anon"
        last = re.sub(r"[^a-z]", "", ascii_fold(first.split(",")[0] if "," in first else first.split()[-1]).lower()) or "anon"
        word = next((w for w in re.findall(r"[a-z0-9]+", ascii_fold(rec.get("title", "")).lower())
                     if w not in STOP and len(w) > 2), "paper")
        base = f"{last}{rec.get('year') or 'nd'}{word}"
        keys = {p["key"] for p in self.papers}
        if base not in keys:
            return base
        for c in "abcdefghijklmnopqrstuvwxyz":
            if base + c not in keys:
                return base + c
        return base + str(len(keys))

    def by_key(self) -> dict[str, dict]:
        return {p["key"]: p for p in self.papers}

    def relink(self) -> int:
        """Rebuild in-set `references` from OpenAlex reference ids."""
        oa2key = {p["openalex_id"]: p["key"] for p in self.papers if p.get("openalex_id")}
        all_keys = set(self.by_key())
        n = 0
        for p in self.papers:
            refs = sorted({oa2key[r] for r in p.get("_ref_ids", []) if r in oa2key and oa2key[r] != p["key"]})
            keep_manual = [r for r in p.get("references", []) if r not in refs and r in all_keys]
            p["references"] = refs + keep_manual
            n += len(p["references"])
        return n

    def save(self) -> None:
        self.relink()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), "utf-8")


# ----------------------------------------------------------------------------- commands

def cmd_search(a) -> None:
    st = Store(Path(a.out) / "papers.json")
    if a.query and not st.data.get("query"):
        st.data["query"] = a.query
    terms = a.terms or [a.query]
    st.data["search_terms"] = sorted(set(st.data.get("search_terms", [])) | set(terms))
    sources = [s.strip() for s in a.sources.split(",")]
    log_rows = st.data.setdefault("search_log", [])
    for term in terms:
        for src in sources:
            try:
                if src == "openalex":
                    rows = search_openalex(term, a.year_from, a.year_to, a.limit, a.mailto)
                elif src in ("s2", "semanticscholar"):
                    rows = search_s2(term, a.year_from, a.year_to, a.limit)
                elif src == "arxiv":
                    rows = search_arxiv(term, a.year_from, a.year_to, a.limit)
                else:
                    log(f"unknown source {src}")
                    continue
            except Exception as e:  # keep going with other sources
                log(f"  ! {src} failed for '{term}': {e}")
                log_rows.append({"term": term, "source": src, "found": 0, "new": 0, "error": str(e)[:200], "date": today()})
                continue
            new = sum(1 for r in rows if st.add(r)[1])
            log(f"  {src:<15} '{term}': {len(rows)} found, {new} new")
            log_rows.append({"term": term, "source": src, "found": len(rows), "new": new, "date": today()})
    st.save()
    log(f"papers.json now holds {len(st.papers)} papers → {st.path}")


def cmd_enrich(a) -> None:
    st = Store(Path(a.out) / "papers.json")
    need = [p for p in st.papers if p.get("doi") and (not p.get("openalex_id") or not p.get("abstract"))]
    log(f"enriching {len(need)} papers from OpenAlex by DOI")
    got = openalex_batch([p["doi"] for p in need], "doi", a.mailto) if need else []
    for r in got:
        st.add(r)
    st.save()
    log(f"done: {len(got)} matched")


def pick(st: Store, keys: list[str] | None, top: int | None) -> list[dict]:
    if keys:
        bk = st.by_key()
        missing = [k for k in keys if k not in bk]
        if missing:
            sys.exit(f"unknown keys: {', '.join(missing)}")
        return [bk[k] for k in keys]
    pool = [p for p in st.papers if p.get("status") != "excluded"]
    pool.sort(key=lambda p: (p.get("score") or 0, p.get("cited_by") or 0), reverse=True)
    return pool[: top or 5]


def cmd_snowball(a) -> None:
    st = Store(Path(a.out) / "papers.json")
    seeds = pick(st, a.keys, a.top)
    # make sure seeds have OpenAlex ids
    no_oa = [p for p in seeds if not p.get("openalex_id") and p.get("doi")]
    for r in openalex_batch([p["doi"] for p in no_oa], "doi", a.mailto) if no_oa else []:
        st.add(r)
    added = 0
    for s in seeds:
        oid = s.get("openalex_id")
        if not oid:
            log(f"  skip {s['key']}: no OpenAlex id (no DOI)")
            continue
        if a.direction in ("backward", "both"):
            refs = s.get("_ref_ids", [])[: a.limit * 3]
            rows = openalex_batch(refs, "ids.openalex", a.mailto) if refs else []
            rows.sort(key=lambda r: r.get("cited_by") or 0, reverse=True)
            n = sum(1 for r in rows[: a.limit] if st.add(r)[1])
            log(f"  ← {s['key']}: {len(rows)} references fetched, {n} new")
            added += n
        if a.direction in ("forward", "both"):
            rows = openalex_citing(oid, a.limit, a.mailto)
            n = sum(1 for r in rows if st.add(r)[1])
            log(f"  → {s['key']}: {len(rows)} citing papers, {n} new")
            added += n
    st.data.setdefault("search_log", []).append({"term": f"snowball:{a.direction}", "source": "openalex",
                                                 "found": added, "new": added, "seeds": [s["key"] for s in seeds],
                                                 "date": today()})
    st.save()
    log(f"snowball added {added}; total {len(st.papers)}")


def cmd_rank(a) -> None:
    st = Store(Path(a.out) / "papers.json")
    q = a.query or st.data.get("query") or " ".join(st.data.get("search_terms", []))
    qt = set(tokens(q + " " + " ".join(a.extra or [])))
    if not qt:
        sys.exit("need a query to rank against (use --query)")
    this_year = _dt.date.today().year
    years = [p["year"] for p in st.papers if p.get("year")]
    y0, y1 = (min(years), max(years)) if years else (this_year, this_year)
    rates = []
    for p in st.papers:
        age = max(1, this_year - (p.get("year") or this_year) + 1)
        rates.append(math.log1p((p.get("cited_by") or 0) / age))
    rmax = max(rates) if rates and max(rates) > 0 else 1.0
    insets = Counter(r for p in st.papers for r in p.get("references", []))
    imax = max(insets.values()) if insets else 1
    for p, rate in zip(st.papers, rates):
        tt = set(tokens(p.get("title", "")))
        at = set(tokens(p.get("abstract", "")))
        rel = (2 * len(qt & tt) + len(qt & at)) / (3 * len(qt))
        rel = min(1.0, rel * 1.25)
        impact = 0.7 * rate / rmax + 0.3 * insets.get(p["key"], 0) / imax
        recency = (p["year"] - y0) / (y1 - y0) if p.get("year") and y1 > y0 else 0.5
        score = 0.55 * rel + 0.30 * impact + 0.15 * recency
        if not p.get("abstract"):
            score *= 0.9
        p["score"] = round(score, 3)
    ranked = sorted([p for p in st.papers if p.get("status") != "excluded"],
                    key=lambda p: p["score"], reverse=True)
    for i, p in enumerate(ranked):
        if p.get("status") != "excluded":
            p["status"] = "included" if i < a.keep and p["score"] >= a.min_score else "candidate"
    st.save()
    print_table(ranked[: max(a.keep, 15)])


def print_table(rows: list[dict]) -> None:
    print(f"{'#':>3}  {'score':>5}  {'cites':>6}  {'year':>4}  {'status':<9} {'key':<26} title")
    for i, p in enumerate(rows, 1):
        flag = " ⚠preprint" if p.get("is_preprint") else ""
        print(f"{i:>3}  {p.get('score') or 0:>5.2f}  {p.get('cited_by') or 0:>6}  {p.get('year') or '':>4}  "
              f"{p.get('status',''):<9} {p['key']:<26} {p['title'][:80]}{flag}")


def cmd_stats(a) -> None:
    st = Store(Path(a.out) / "papers.json")
    ps = st.papers
    by_status = Counter(p.get("status") for p in ps)
    print(f"query: {st.data.get('query')}")
    print(f"papers: {len(ps)}  " + "  ".join(f"{k}: {v}" for k, v in by_status.items()))
    print(f"with abstract: {sum(1 for p in ps if p.get('abstract'))}  preprints: {sum(1 for p in ps if p.get('is_preprint'))}"
          f"  in-set citation links: {sum(len(p.get('references', [])) for p in ps)}")
    yrs = Counter(p.get("year") for p in ps if p.get("year"))
    print("by year: " + "  ".join(f"{y}:{n}" for y, n in sorted(yrs.items())))
    src = Counter(s for p in ps for s in p.get("sources", []))
    print("by source: " + "  ".join(f"{k}:{v}" for k, v in src.items()))
    rows = sorted([p for p in ps if p.get("status") != "excluded"], key=lambda p: p.get("score") or 0, reverse=True)
    print_table(rows[: a.top])


# ----------------------------------------------------------------------------- bibtex

def bib_escape(s: str) -> str:
    s = s.replace("\\", "\\textbackslash{}")
    for ch in "&%$#_":
        s = s.replace(ch, "\\" + ch)
    return s


def to_bibtex(p: dict) -> str:
    kind = "misc" if p.get("is_preprint") else ("inproceedings" if re.search(
        r"conference|proceedings|symposium|workshop", p.get("venue", ""), re.I) else "article")
    f = {"title": "{" + bib_escape(p.get("title", "")) + "}",
         "author": " and ".join(bib_escape(a) for a in p.get("authors", []) if a) or "Anonymous",
         "year": str(p.get("year") or "")}
    if kind == "article":
        f["journal"] = bib_escape(p.get("venue", ""))
    elif kind == "inproceedings":
        f["booktitle"] = bib_escape(p.get("venue", ""))
    else:
        f["howpublished"] = bib_escape(p.get("venue") or "Preprint")
    if p.get("doi"):
        f["doi"] = p["doi"]
    if p.get("arxiv_id"):
        f["eprint"] = p["arxiv_id"]
        f["archivePrefix"] = "arXiv"
    if p.get("url"):
        f["url"] = p["url"]
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in f.items() if v)
    return f"@{kind}{{{p['key']},\n{body}\n}}\n"


def cmd_bib(a) -> None:
    st = Store(Path(a.out) / "papers.json")
    rows = st.papers if a.all else [p for p in st.papers if p.get("status") == "included"]
    path = Path(a.out) / "references.bib"
    path.write_text("\n".join(to_bibtex(p) for p in sorted(rows, key=lambda p: p["key"])), "utf-8")
    log(f"wrote {len(rows)} entries → {path}")


# ----------------------------------------------------------------------------- map

def communities(keys: list[str], edges: list[tuple[str, str]], seed_order: list[str]) -> dict[str, int]:
    """Deterministic label propagation on the undirected in-set citation graph."""
    nb = defaultdict(set)
    for s, t in edges:
        nb[s].add(t)
        nb[t].add(s)
    label = {k: i for i, k in enumerate(seed_order)}
    for _ in range(30):
        changed = False
        for k in seed_order:
            if not nb[k]:
                continue
            c = Counter(label[n] for n in nb[k])
            best = max(c.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            if c[best] > c.get(label[k], 0) and best != label[k]:
                label[k] = best
                changed = True
        if not changed:
            break
    # renumber by size; singletons → -1
    size = Counter(label.values())
    order = [l for l, n in size.most_common() if n > 1]
    remap = {l: i for i, l in enumerate(order)}
    return {k: remap.get(label[k], -1) for k in keys}


def cluster_name(members: list[dict]) -> str:
    c = Counter(t for p in members for t in (p.get("topics") or [])[:2])
    if c:
        return c.most_common(1)[0][0]
    words = Counter(w for p in members for w in set(re.findall(r"[a-z]{4,}", p.get("title", "").lower()))
                    if w not in STOP)
    return " / ".join(w for w, _ in words.most_common(2)) or "cluster"


def cmd_map(a) -> None:
    st = Store(Path(a.out) / "papers.json")
    rows = st.papers if a.all else [p for p in st.papers if p.get("status") != "excluded"]
    keys = {p["key"] for p in rows}
    edges = [(p["key"], r) for p in rows for r in p.get("references", []) if r in keys]
    order = [p["key"] for p in sorted(rows, key=lambda p: p.get("score") or 0, reverse=True)]
    comm = communities(list(keys), edges, order)
    biggest = Counter(comm.values()).most_common(1)[0][1] if comm else 0
    if rows and biggest > 0.7 * len(rows) and len(rows) >= 12:
        # one giant citation community says little — group by primary topic instead
        prim = {p["key"]: (p.get("topics") or ["Other"])[0] for p in rows}
        tc = Counter(prim.values())
        names = [t for t, n in tc.most_common(8) if n >= 2]
        comm = {k: (names.index(t) if t in names else -1) for k, t in prim.items()}
        log("citation graph is one dense component; clustering by topic instead")
    groups = defaultdict(list)
    for p in rows:
        groups[comm[p["key"]]].append(p)
    clusters = []
    used = set()
    for cid in sorted(g for g in groups if g >= 0):
        name = cluster_name(groups[cid])
        if name in used:
            name = f"{name} ({cid + 1})"
        used.add(name)
        clusters.append({"id": cid, "name": name, "size": len(groups[cid])})
    nodes = []
    for p in rows:
        nodes.append({
            "key": p["key"], "title": p.get("title", ""), "authors": p.get("authors", [])[:6],
            "n_authors": len(p.get("authors", [])), "year": p.get("year"), "venue": p.get("venue", ""),
            "cited_by": p.get("cited_by") or 0, "doi": p.get("doi", ""), "url": p.get("url", ""),
            "oa_url": p.get("oa_url", ""), "abstract": (p.get("abstract") or "")[:1200],
            "score": p.get("score") or 0, "status": p.get("status", ""), "preprint": bool(p.get("is_preprint")),
            "cluster": comm[p["key"]], "topics": p.get("topics", []),
        })
    payload = {"title": a.title or st.data.get("query") or "Citation map", "generated": today(),
               "query": st.data.get("query", ""), "nodes": nodes,
               "edges": [{"s": s, "t": t} for s, t in edges], "clusters": clusters}
    tpl = load_template("citation_map.html")
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    out = tpl.replace("/*__DATA__*/null", data).replace("__TITLE__", html.escape(payload["title"]))
    path = Path(a.out) / "citation_map.html"
    path.write_text(out, "utf-8")
    log(f"wrote map: {len(nodes)} papers, {len(edges)} citation links, {len(clusters)} clusters → {path}")


def load_template(name: str) -> str:
    """Use the embedded copy when present (packaged skill), else the assets/ folder (development)."""
    emb = globals().get("EMBEDDED_TEMPLATES", {})
    if name in emb:
        return emb[name]
    return (Path(__file__).resolve().parent.parent / "assets" / name).read_text("utf-8")


# ----------------------------------------------------------------------------- main

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="scout.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--out", default="research", help="project folder holding papers.json")
        p.add_argument("--mailto", default=os.environ.get("OPENALEX_MAILTO"),
                       help="email for OpenAlex's polite pool (optional)")

    s = sub.add_parser("search", help="search APIs and merge into papers.json")
    common(s)
    s.add_argument("--query", required=True, help="the research question or topic")
    s.add_argument("--terms", nargs="*", help="search strings to run (default: the query)")
    s.add_argument("--sources", default="openalex,s2,arxiv")
    s.add_argument("--year-from", type=int)
    s.add_argument("--year-to", type=int)
    s.add_argument("--limit", type=int, default=25, help="results per source per term")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("snowball", help="follow references/citations via OpenAlex")
    common(s)
    s.add_argument("--keys", nargs="*", help="seed paper keys (default: top-ranked)")
    s.add_argument("--top", type=int, default=5, help="number of top papers to use as seeds")
    s.add_argument("--direction", choices=["backward", "forward", "both"], default="both")
    s.add_argument("--limit", type=int, default=15, help="max papers per seed per direction")
    s.set_defaults(fn=cmd_snowball)

    s = sub.add_parser("enrich", help="fill OpenAlex metadata by DOI")
    common(s)
    s.set_defaults(fn=cmd_enrich)

    s = sub.add_parser("rank", help="score papers and mark top N included")
    common(s)
    s.add_argument("--query", help="override the stored query")
    s.add_argument("--extra", nargs="*", help="extra keywords that signal relevance")
    s.add_argument("--keep", type=int, default=30)
    s.add_argument("--min-score", type=float, default=0.15)
    s.set_defaults(fn=cmd_rank)

    s = sub.add_parser("bib", help="write references.bib")
    common(s)
    s.add_argument("--all", action="store_true", help="include candidates too")
    s.set_defaults(fn=cmd_bib)

    s = sub.add_parser("map", help="write citation_map.html")
    common(s)
    s.add_argument("--all", action="store_true", help="include excluded papers")
    s.add_argument("--title")
    s.set_defaults(fn=cmd_map)

    s = sub.add_parser("stats", help="print a summary")
    common(s)
    s.add_argument("--top", type=int, default=20)
    s.set_defaults(fn=cmd_stats)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
