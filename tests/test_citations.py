import json

from aquaassist.citations import Source, cited_brochure_pages, cited_report_ids, extract_sources


def test_finds_brochure_citations_in_order_without_duplicates() -> None:
    text = "Use AquaTune [aquatune-optimizer p.1; ultraguard-uf p. 2] and again [aquatune-optimizer p.1]."
    assert cited_brochure_pages(text) == [("aquatune-optimizer", 1), ("ultraguard-uf", 2)]


def test_page_ranges_expand_to_each_page() -> None:
    text = "See [aquatune-optimizer p.1–3] and [ultraguard-uf pp. 1-2]."
    assert cited_brochure_pages(text) == [("aquatune-optimizer", 1), ("aquatune-optimizer", 2),
                                          ("aquatune-optimizer", 3), ("ultraguard-uf", 1), ("ultraguard-uf", 2)]


def test_edition_year_between_doc_and_page_is_not_a_document() -> None:
    text = "See [aquatune-optimizer p.1; clarion-ro-series 2025 p.1] and [clarion-ro-series 2025 p.2]."
    assert cited_brochure_pages(text) == [("aquatune-optimizer", 1), ("clarion-ro-series", 1),
                                          ("clarion-ro-series", 2)]


def test_bare_page_after_semicolon_belongs_to_previous_document() -> None:
    text = "See [aquatune-optimizer p.1; p.2] but not a lone [p.5]."
    assert cited_brochure_pages(text) == [("aquatune-optimizer", 1), ("aquatune-optimizer", 2)]


def test_implausible_ranges_keep_only_the_first_page() -> None:
    assert cited_brochure_pages("[a-doc p.3–1] [b-doc p.1-999]") == [("a-doc", 3), ("b-doc", 1)]


def test_finds_report_ids_without_duplicates() -> None:
    assert cited_report_ids("Based on FSR-0012, FSR-0003 and FSR-0012.") == ["FSR-0012", "FSR-0003"]


def test_report_ids_in_brackets_are_not_brochure_pages() -> None:
    assert cited_brochure_pages("[FSR-0012]") == []


def test_only_sources_returned_by_tools_are_verified() -> None:
    outputs = [
        ("search_product_docs", json.dumps([{"doc_id": "aquatune-optimizer", "page": 1, "text": "..."}])),
        ("estimate_service_cost", json.dumps({"components": {"onsite_hours": {"reports": ["FSR-0020"]}}})),
    ]
    reply = "See [aquatune-optimizer p.1] and [aquatune-optimizer p.3]; based on FSR-0020 and FSR-0099."
    assert extract_sources(reply, outputs) == [
        Source("brochure", "aquatune-optimizer", 1, True),
        Source("brochure", "aquatune-optimizer", 3, False),
        Source("service_report", "FSR-0020", None, True),
        Source("service_report", "FSR-0099", None, False),
    ]


def test_unparseable_search_output_is_ignored() -> None:
    assert extract_sources("[a-doc p.1]", [("search_product_docs", "not json")]) == [
        Source("brochure", "a-doc", 1, False)]
