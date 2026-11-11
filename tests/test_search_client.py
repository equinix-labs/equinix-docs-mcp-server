"""Tests for searching docs.equinix.com's serialized lunr index."""

import json

import pytest
from lunr import lunr

from equinix_docs_mcp_server.lunr_search.search_client import Client


def _serialized(documents, fields=("t",)):
    """Build an index the way docusaurus-search-local does (stemmed)."""
    index = lunr(ref="i", fields=list(fields), documents=documents)
    return {"documents": documents, "index": index.serialize()}


@pytest.fixture
def index_file(tmp_path):
    titles = [
        {"i": 1, "t": "Monitoring Port Utilization", "u": "/fabric/ports/util"},
        {"i": 2, "t": "Connections Inventory", "u": "/fabric/connections"},
        {"i": 3, "t": "Fabric Release Notes", "u": "/releases/fabric"},
    ]
    headings = [
        {
            "i": 10,
            "t": "Connection Status",
            "u": "/fabric/connections",
            "h": "#connection-status",
            "p": 2,
        },
        {
            "i": 11,
            "t": "Port Details",
            "u": "/releases/fabric",
            "h": "#port-details",
            "p": 3,
        },
    ]
    descriptions = [
        {
            "i": 1,
            "t": "Monitor bandwidth utilization of your ports.",
            "s": "Monitoring Port Utilization",
            "u": "/fabric/ports/util",
            "p": 1,
        },
    ]
    keywords = [
        {
            "i": 1,
            "t": "port utilization, monitoring",
            "s": "Monitoring Port Utilization",
            "u": "/fabric/ports/util",
            "p": 1,
        },
    ]
    # Many weak hits on one long page must not outrank a strong title hit.
    content = [
        {
            "i": 100 + n,
            "t": f"Port change {n}: utilization graphs updated.",
            "s": f"Release {n}",
            "u": "/releases/fabric",
            "h": f"#r{n}",
            "p": 3,
        }
        for n in range(20)
    ] + [
        {
            "i": 200,
            "t": "Check the status of each connection here.",
            "s": "Connection Status",
            "u": "/fabric/connections",
            "h": "#connection-status",
            "p": 2,
        },
    ]
    payload = [
        _serialized(titles),
        _serialized(headings),
        _serialized(descriptions),
        _serialized(keywords),
        _serialized(content),
    ]
    path = tmp_path / "search-index.json"
    path.write_text(json.dumps(payload))
    return path


@pytest.fixture
def client(index_file):
    c = Client(str(index_file))
    c.load()
    return c


def test_index_terms_are_stemmed(index_file):
    """Guards the bug: stored terms are stems, not the words people type."""
    payload = json.loads(index_file.read_text())
    terms = {entry[0] for entry in payload[4]["index"]["invertedIndex"]}
    assert "connect" in terms
    assert "connection" not in terms


@pytest.mark.parametrize(
    "query, first_url",
    [
        ("connection status", "/fabric/connections"),
        ("port utilization", "/fabric/ports/util"),
        ("Port Utilization", "/fabric/ports/util"),
        ("monitoring ports utilization", "/fabric/ports/util"),
    ],
)
def test_multi_word_queries_match(client, query, first_url):
    results = client.search(query)
    assert results, query
    assert results[0]["url"] == first_url


def test_result_points_at_matching_section(client):
    top = client.search("connection status")[0]
    assert top["title"] == "Connections Inventory"
    assert top["section"] == "Connection Status"
    assert top["anchor"] == "#connection-status"


def test_title_match_links_to_page(client):
    top = client.search("port utilization")[0]
    assert top["title"] == "Monitoring Port Utilization"
    assert top["anchor"] == ""
    assert top["section"] is None


def test_results_are_one_per_page_and_limited(client):
    results = client.search("port utilization", limit=2)
    assert len(results) == 2
    assert len({r["url"] for r in results}) == 2


@pytest.mark.parametrize(
    "query",
    ['"connection status"', "connection:status", "-connection +status~", "status*"],
)
def test_query_syntax_characters_do_not_break_search(client, query):
    assert any(r["url"] == "/fabric/connections" for r in client.search(query))


@pytest.mark.parametrize("query", ["", "   ", "+++", '""'])
def test_empty_queries_return_nothing(client, query):
    assert client.search(query) == []


def test_no_match(client):
    assert client.search("kubernetes") == []
