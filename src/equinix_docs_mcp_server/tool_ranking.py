"""BM25 ranking tuned for the generated Equinix API tools.

FastMCP's BM25 search indexes each tool's whole description and every
parameter description. Create and update operations document dozens of
request fields, so length normalization pushes them below small tools that
mention the query words once: "create network edge virtual device" did not
return ``network-edge_createVirtualDevice``. Its operationId also indexed as
one token, so "virtual device" could not match the name.

This index weights what identifies an operation (its name and title), splits
names into words, keeps the first paragraph of the description and the
family's name, and folds simple plurals so "ports" matches "port". Parameters
are left out: even their names made large request bodies rank lower.
"""

import math
import re
import unicodedata
from collections.abc import Sequence

from fastmcp.tools.base import Tool

_TOKEN = re.compile(r"[^\W_]{2,}")
# Boundaries inside identifiers: fooBar, HTTPServer, port2Port
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# How many times the name and title count against the rest of the document
_IDENTITY_WEIGHT = 5
# The family sentence that SpecManager appends to every operation description
_FAMILY_SENTENCE = re.compile(r"Part of the Equinix (.+?) API family")


def split_identifier(text: str) -> str:
    """Split camelCase, snake_case and kebab-case identifiers into words."""
    return _CAMEL.sub(" ", text).replace("_", " ").replace("-", " ")


def _stem(token: str) -> str:
    if len(token) <= 3:
        return token
    if token.endswith("ies"):
        return token[:-3] + "y"
    if token.endswith(("sses", "xes", "ches", "shes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens with simple plurals folded."""
    normalized = unicodedata.normalize("NFKC", split_identifier(text)).casefold()
    return [_stem(token) for token in _TOKEN.findall(normalized)]


def searchable_text(tool: Tool) -> str:
    """The text a tool is ranked on."""
    title = tool.title or (tool.annotations.title if tool.annotations else None) or ""
    identity = f"{split_identifier(tool.name)} {title}"
    description = (tool.description or "").strip()
    first_paragraph = description.split("\n\n", 1)[0]
    # Keeps product names that aren't in the family prefix, such as
    # "Digital LOA" for diloa
    family = _FAMILY_SENTENCE.search(description)
    family_name = family.group(1) if family else ""
    return " ".join([identity] * _IDENTITY_WEIGHT + [first_paragraph, family_name])


class ToolIndex:
    """BM25 Okapi over the tools' searchable text."""

    def __init__(self, tools: Sequence[Tool], k1: float = 1.5, b: float = 0.75):
        self.names = tuple(tool.name for tool in tools)
        self._k1, self._b = k1, b
        self._tf: list[dict[str, int]] = []
        self._df: dict[str, int] = {}
        documents = [tokenize(searchable_text(tool)) for tool in tools]
        self._lengths = [len(doc) for doc in documents]
        self._avg = sum(self._lengths) / len(documents) if documents else 0.0
        for doc in documents:
            tf: dict[str, int] = {}
            for token in doc:
                tf[token] = tf.get(token, 0) + 1
            for token in tf:
                self._df[token] = self._df.get(token, 0) + 1
            self._tf.append(tf)

    def query(self, text: str, top_k: int) -> list[int]:
        """Indices of the top_k best-matching tools, best first."""
        n = len(self._tf)
        scores = [0.0] * n
        for token in set(tokenize(text)):
            df = self._df.get(token)
            if not df:
                continue
            idf = math.log((n - df + 0.5) / (df + 0.5) + 1.0)
            for i, tf in enumerate(self._tf):
                count = tf.get(token, 0)
                if count:
                    norm = 1 - self._b + self._b * self._lengths[i] / self._avg
                    scores[i] += (
                        idf * count * (self._k1 + 1) / (count + self._k1 * norm)
                    )
        ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)
        return [i for i in ranked[:top_k] if scores[i] > 0]
