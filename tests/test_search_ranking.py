"""Ranking regression test for search_tools against the real tool catalog.

The catalog snapshot (fixtures/search_corpus.json) is refreshed by
scripts/export_search_corpus.py; the cases (fixtures/search_queries.yaml)
record how well each query ranks today. A change to ranking, to how
SpecManager rewrites descriptions, or to the upstream specs that makes a
case rank worse fails here. See issue #71.

Run ``python tests/test_search_ranking.py`` for a table of every case.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import yaml
from fastmcp.tools.base import Tool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from equinix_docs_mcp_server.tool_ranking import ToolIndex  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CORPUS = json.loads((FIXTURES / "search_corpus.json").read_text(encoding="utf-8"))
CASES: List[Dict[str, Any]] = yaml.safe_load(
    (FIXTURES / "search_queries.yaml").read_text(encoding="utf-8")
)
# The ranks a case can be held to; `within` values come from this list.
BOUNDS = (1, 3, 5, 10, 15)


def _tool(entry: Dict[str, Any]) -> Tool:
    def fn() -> None:
        return None

    return Tool.from_function(fn, name=entry["name"]).model_copy(
        update={
            "title": entry["title"] or None,
            "description": entry["description"],
            "tags": set(entry["tags"]),
        }
    )


TOOLS = [_tool(entry) for entry in CORPUS]
NAMES = [tool.name for tool in TOOLS]
INDEX = ToolIndex(TOOLS)


def best_rank(case: Dict[str, Any]) -> Optional[int]:
    """1-based rank of the best expected tool, or None if none matched."""
    ranked = [NAMES[i] for i in INDEX.query(case["query"], len(TOOLS))]
    ranks = [ranked.index(name) + 1 for name in case["expect"] if name in ranked]
    return min(ranks) if ranks else None


def _describe(case: Dict[str, Any], rank: Optional[int]) -> str:
    top = [NAMES[i] for i in INDEX.query(case["query"], 5)]
    found = f"#{rank}" if rank else "not matched"
    return (
        f"{case['query']!r}: best expected tool {found}, needs top "
        f"{case['within']}. Expected one of {case['expect']}; top 5: {top}"
    )


def test_cases_are_well_formed():
    names = set(NAMES)
    for case in CASES:
        assert set(case) <= {"query", "expect", "within", "gap", "source"}, case
        assert case["within"] in BOUNDS, case
        missing = [name for name in case["expect"] if name not in names]
        assert not missing, f"{case['query']!r}: not in the corpus: {missing}"


def _params():
    params = []
    for case in CASES:
        marks = []
        if case.get("gap"):
            marks.append(pytest.mark.xfail(reason=case["gap"], strict=True))
        params.append(pytest.param(case, id=case["query"], marks=marks))
    return params


@pytest.mark.parametrize("case", _params())
def test_query_ranks_expected_tool(case):
    rank = best_rank(case)
    assert rank is not None and rank <= case["within"], _describe(case, rank)


def test_no_case_could_be_tightened():
    """`within` should be the tightest bound the case meets today."""
    loose = []
    for case in CASES:
        rank = best_rank(case)
        if case.get("gap") or rank is None or rank > BOUNDS[-1]:
            continue
        tightest = next(bound for bound in BOUNDS if bound >= rank)
        if tightest < case["within"]:
            loose.append(f"{case['query']!r}: #{rank}, so within: {tightest}")
    assert not loose, "Tighten these cases:\n" + "\n".join(loose)


if __name__ == "__main__":
    ok = 0
    for case in CASES:
        rank = best_rank(case)
        passed = rank is not None and rank <= case["within"]
        ok += passed and not case.get("gap")
        status = "gap " if case.get("gap") else ("ok  " if passed else "FAIL")
        print(f"{status} {str(rank or '-'):>3}/{case['within']:<3} {case['query']}")
    first = sum(best_rank(c) == 1 for c in CASES)
    top5 = sum((best_rank(c) or 99) <= 5 for c in CASES)
    print(
        f"\n{len(CASES)} cases: {first} ranked first, {top5} in the top 5, "
        f"{sum(bool(c.get('gap')) for c in CASES)} known gaps"
    )
