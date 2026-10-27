"""Offline tests: API calls are replaced with fixtures shaped like real OpenAlex / Semantic Scholar / arXiv responses."""
import importlib.util
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("scout", ROOT / "scripts" / "scout.py")
scout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scout)
scout.time.sleep = lambda s: None


def work(i, refs=(), year=2020, preprint=False):
    return {
        "id": f"https://openalex.org/W{100 + i}", "doi": f"https://doi.org/10.5555/fixture.{i}",
        "display_name": f"Fixture paper {i} on graph learning", "publication_year": year,
        "type": "preprint" if preprint else "article",
        "primary_location": {"source": {"display_name": "bioRxiv" if preprint else "Journal of Fixtures",
                                        "type": "repository" if preprint else "journal"}},
        "authorships": [{"author": {"display_name": f"Ada Fixture{i}"}}, {"author": {"display_name": "Bo Test"}}],
        "cited_by_count": 10 * i, "abstract_inverted_index": {"graph": [0], "learning": [1], "works": [2]},
        "open_access": {"oa_url": None}, "topics": [{"display_name": "Graph learning" if i % 2 else "Molecules"}],
        "referenced_works": [f"https://openalex.org/W{100 + r}" for r in refs],
    }


WORKS = [work(0, year=2015), work(1, [0], 2017), work(2, [0, 1], 2019), work(3, [1, 2], 2021, preprint=True),
         work(4, [2, 3], 2023)]
S2 = {"data": [{"paperId": "s2x", "title": "Fixture paper 2 on graph learning", "year": 2019,
                "externalIds": {"DOI": "10.5555/FIXTURE.2"}, "venue": "Journal of Fixtures", "citationCount": 999,
                "authors": [{"name": "Ada Fixture2"}], "abstract": "a longer abstract about graph learning methods"}]}
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2301.00001v3</id><published>2023-01-02T00:00:00Z</published>
<title>An arXiv-only fixture on graph learning</title><summary>graph learning</summary>
<author><name>Cy Arxiv</name></author><category term="cs.LG"/></entry></feed>"""


def fake(url, headers=None, retries=4, raw=False, first_delay=2.0):
    u = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(u.query))
    if "arxiv" in u.netloc:
        return ATOM
    if "semanticscholar" in u.netloc:
        return S2
    f = q.get("filter", "")
    if f.startswith("cites:"):
        oid = f.split(":")[1]
        return {"results": [w for w in WORKS if f"https://openalex.org/{oid}" in w["referenced_works"]]}
    if f.startswith("ids.openalex:"):
        ids = set(f.split(":", 1)[1].split("|"))
        return {"results": [w for w in WORKS if w["id"].rsplit("/", 1)[1] in ids]}
    if f.startswith("doi:"):
        ds = set(f.split(":", 1)[1].split("|"))
        return {"results": [w for w in WORKS if scout.norm_doi(w["doi"]) in ds]}
    return {"results": WORKS[2:]}


scout.http_json = fake


class ScoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        scout.main(list(args) + ["--out", self.out])

    def papers(self):
        return json.loads((Path(self.out) / "papers.json").read_text())["papers"]

    def test_pipeline(self):
        self.run_cli("search", "--query", "graph learning", "--limit", "10")
        ps = self.papers()
        # 3 OpenAlex + 1 arXiv; the S2 record merges into fixture 2 by DOI (case-insensitive)
        self.assertEqual(len(ps), 4)
        merged = next(p for p in ps if p["doi"] == "10.5555/fixture.2")
        self.assertEqual(merged["cited_by"], 999)
        self.assertIn("semanticscholar", merged["sources"])
        self.assertTrue(next(p for p in ps if p["arxiv_id"] == "2301.00001")["is_preprint"])
        self.run_cli("rank", "--keep", "3")
        self.run_cli("snowball", "--top", "2", "--direction", "both")
        ps = self.papers()
        self.assertEqual(len({p["doi"] for p in ps if p["doi"]}), 5)
        refs = {p["key"]: p["references"] for p in ps}
        self.assertTrue(any(refs.values()), "in-set citation links should be rebuilt")
        self.run_cli("bib")
        bib = (Path(self.out) / "references.bib").read_text()
        self.assertIn("@article{", bib)
        self.run_cli("map")
        html = (Path(self.out) / "citation_map.html").read_text()
        self.assertNotIn("/*__DATA__*/null", html)
        self.assertIn('"edges"', html)

    def test_keys_unique_and_ascii(self):
        st = scout.Store(Path(self.out) / "papers.json")
        a = st.add({"title": "Ünïcode title about graphs", "authors": ["José Núñez"], "year": 2020})[0]
        b = st.add({"title": "Unicode title about graphs again", "authors": ["Jose Nunez"], "year": 2020})[0]
        self.assertEqual(a["key"], "nunez2020unicode")
        self.assertEqual(b["key"], "nunez2020unicodea")

    def test_rate_limited_source_is_skipped(self):
        import urllib.error
        calls = []
        real = scout.http_json

        def limited(url, headers=None, retries=4, raw=False, first_delay=2.0):
            if "semanticscholar" in url:
                calls.append(url)
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
            return fake(url, headers, retries, raw)

        scout.http_json = limited
        try:
            self.run_cli("search", "--query", "graph learning", "--terms", "graph learning", "graph models",
                         "--sources", "openalex,s2", "--limit", "5")
        finally:
            scout.http_json = real
        self.assertEqual(len(calls), 1, "after one 429 the source must be skipped for later terms")
        self.assertTrue(self.papers(), "other sources still return papers")

    def test_abstract_reconstruction(self):
        self.assertEqual(scout.openalex_abstract({"b": [1], "a": [0], "c": [2]}), "a b c")


if __name__ == "__main__":
    unittest.main()
