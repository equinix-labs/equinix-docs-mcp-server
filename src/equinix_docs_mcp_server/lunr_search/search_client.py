import json
import re
from typing import Any, Dict, List, Optional

try:
    import requests  # type: ignore
except Exception:
    requests = None


class SearchDocument:
    def __init__(self, d: Dict[str, Any]):
        # properties in generated index are shortened: i, t, u, h, p, b, s
        self.i = int(d.get("i") or 0)
        self.t = d.get("t", "")
        self.u = d.get("u", "")
        self.raw = d


class SearchIndex:
    def __init__(
        self, documents: List[Dict[str, Any]], inverted_index: List[List[Any]]
    ):
        # documents: list of shortened documents
        self.documents = [SearchDocument(d) for d in documents]
        # inverted_index: the serialized lunr inverted index structure
        # We build a simple mapping term -> set(refs)
        self.inverted = {}

        def _collect_refs(obj, out_set: set):
            """Recursively walk an object (dict/list/scalar) and collect doc id-like refs.

            - dict: if it has a 't' key mapping to a dict, use those keys (lunr positions map)
                    otherwise, if dict keys look like numeric ids, add them; else recurse into values.
            - list/tuple: recurse into items and pick up int-like values (doc ids often ints).
            - int/str: add if it looks like an id.
            """
            if obj is None:
                return
            if isinstance(obj, dict):
                # common lunr serialization: {"_index":N, "t": {"123":{...}, ...}}
                if "t" in obj and isinstance(obj["t"], dict):
                    for k in obj["t"].keys():
                        out_set.add(str(k))
                    return
                # if keys themselves look like refs (numeric strings), add them
                added_key = False
                for k in obj.keys():
                    if isinstance(k, (int,)) or (isinstance(k, str) and k.isdigit()):
                        out_set.add(str(k))
                        added_key = True
                if added_key:
                    return
                # otherwise recurse into values
                for v in obj.values():
                    _collect_refs(v, out_set)
                return

            if isinstance(obj, (list, tuple)):
                # lunr often uses [docId, score, docId, score, ...]
                for item in obj:
                    if isinstance(item, int):
                        out_set.add(str(item))
                    elif isinstance(item, str) and item.isdigit():
                        out_set.add(item)
                    else:
                        _collect_refs(item, out_set)
                return

            # scalar
            if isinstance(obj, int):
                out_set.add(str(obj))
            elif isinstance(obj, str) and obj.isdigit():
                out_set.add(obj)

        for entry in inverted_index:
            if not entry:
                continue
            first = entry[0]
            # extract term
            if (
                isinstance(first, (list, tuple))
                and len(first) > 0
                and isinstance(first[0], str)
            ):
                term = first[0]
            elif isinstance(first, str):
                term = first
            else:
                term = str(first)

            refs = set()
            # collect refs from the remainder of the entry
            for part in entry[1:]:
                _collect_refs(part, refs)

            # fallback: sometimes refs are nested in the first element
            if not refs and isinstance(first, (list, tuple)) and len(first) > 1:
                for f in first[1:]:
                    _collect_refs(f, refs)

            # store mapping (possibly empty)
            self.inverted[term] = refs

    def search_term(self, term: str) -> List[SearchDocument]:
        # basic substring/wildcard handling: if term endswith '*' we do prefix match
        if term.endswith("*"):
            prefix = term[:-1]
            matches = set()
            for k, refs in self.inverted.items():
                if k.startswith(prefix):
                    matches.update(refs)
            return [d for d in self.documents if str(d.i) in matches]
        else:
            # exact term
            refs = self.inverted.get(term, set())
            return [d for d in self.documents if str(d.i) in refs]

    def search_with_pylunr(
        self, raw_query: str, limit: int = 8
    ) -> List[SearchDocument]:
        """Use python-lunr index if present (set externally as `pylunr_index`)."""
        idx = getattr(self, "pylunr_index", None)
        if not idx:
            return []
        try:
            hits = idx.search(raw_query)
        except Exception:
            # some queries may not be accepted by python-lunr; fall back to empty
            return []
        refs = [str(h.get("ref") or h.get("id") or h.get("_id")) for h in hits][:limit]
        return [d for d in self.documents if str(d.i) in set(refs)]


def tokenize(text: str, language: Optional[List[str]] = None) -> List[str]:
    if language is None:
        language = ["en"]
    # simplified tokenization matching the ts implementation
    if len(language) == 1 and language[0] in ("ja", "jp", "th"):
        # fallback: split on whitespace for this example
        return [t for t in re.findall(r"[^\s]+", text.lower())]

    if "zh" in language:
        # match words or contiguous CJK unified ideographs
        return re.findall(r"\w+|[\u4E00-\u9FFF]+", text.lower())

    return re.findall(r"[^-\s]+", text.lower())


class Client:
    def __init__(self, url: str, language: Optional[List[str]] = None):
        self.url = url
        self.language = language or ["en"]
        self.indexes: List[SearchIndex] = []
        # optional lunr Python package (not required)
        try:
            from lunr import lunr as _lunr  # type: ignore

            self._lunr = _lunr
        except Exception:
            self._lunr = None

    def load(self) -> None:
        # Accept either a URL or file path. If it's a URL, fetch; else read local file
        if self.url.startswith("http://") or self.url.startswith("https://"):
            if requests is None:
                raise RuntimeError("requests package not available to fetch URL")
            r = requests.get(self.url)
            r.raise_for_status()
            payload = r.json()
        else:
            with open(self.url, "r", encoding="utf8") as fh:
                payload = json.load(fh)

        # payload is an array of serialized indexes (one per type)
        self.indexes = []
        for item in payload:
            documents = item.get("documents", [])
            index = item.get("index", {})
            # invertedIndex path compatible with lunr serialization
            inverted = index.get("invertedIndex") or index.get("inverted_index") or []
            si = SearchIndex(documents, inverted)
            # If python-lunr is available, try to build a Lunr index for better parity
            _lunr = getattr(self, "_lunr", None)
            if _lunr:
                try:
                    # prepare documents for python-lunr: ensure ref is string
                    docs_for_lunr = []
                    for d in documents:
                        doc = {}
                        # keep the same keys used in JS: i (ref), t (title), s (section), b (breadcrumb)
                        doc["i"] = str(d.get("i") if d.get("i") is not None else "")
                        doc["t"] = d.get("t", "")
                        # join breadcrumb list if present
                        b = d.get("b")
                        doc["b"] = (
                            " ".join(b) if isinstance(b, (list, tuple)) else (b or "")
                        )
                        doc["s"] = d.get("s", "")
                        docs_for_lunr.append(doc)

                    # build index using fields t, s, b and ref 'i'
                    pylunr_idx = _lunr(docs_for_lunr, fields=("t", "s", "b"), ref="i")
                    # attach to SearchIndex for queries
                    setattr(si, "pylunr_index", pylunr_idx)
                except Exception:
                    # building a pylunr index is optional; ignore errors and fall back
                    setattr(si, "pylunr_index", None)

            self.indexes.append(si)

    def search(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        tokens = tokenize(query, self.language)
        if not tokens:
            return []

        # build simple AND of tokens, using prefix wildcard on last token
        last_prefix = tokens[-1] + "*"
        results = []
        for idx in self.indexes:
            # search by tokens
            # If python-lunr available for this index, use it for better results
            pylunr_hits = (
                idx.search_with_pylunr(query, limit=limit)
                if getattr(idx, "pylunr_index", None)
                else None
            )
            if pylunr_hits:
                for doc in pylunr_hits:
                    results.append({"id": doc.i, "title": doc.t, "url": doc.u})
                    if len(results) >= limit:
                        return results
                # skip fallback if pylunr returned results
                continue

            sets = []
            for t in tokens[:-1]:
                sets.append({str(d.i) for d in idx.search_term(t)})
            # last token with prefix
            sets.append({str(d.i) for d in idx.search_term(last_prefix)})

            if not sets:
                continue
            common = set.intersection(*sets) if len(sets) > 1 else sets[0]
            for doc in idx.documents:
                if str(doc.i) in common:
                    results.append({"id": doc.i, "title": doc.t, "url": doc.u})
                    if len(results) >= limit:
                        return results

        return results
