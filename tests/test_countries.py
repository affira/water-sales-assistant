from aquaassist import cost
from aquaassist.countries import INFERRED_FLAG, harmonize_countries, normalize_country, same_country


def test_aliases_and_empty_values_are_normalised() -> None:
    assert normalize_country("United Arab Emirates") == "UAE"
    assert normalize_country(" united kingdom ") == "UK"
    assert normalize_country("Chile") == "Chile"
    assert normalize_country("null") == normalize_country(None) == ""


def test_same_country_ignores_spelling_and_blank() -> None:
    assert same_country("UAE", "United Arab Emirates")
    assert not same_country("", "")


def record(rid: str, customer: str, country: str) -> dict:
    return {"report_id": rid, "customer": customer, "country": country, "checks": []}


def test_blank_country_is_filled_from_same_customer_and_flagged() -> None:
    records = harmonize_countries([record("A", "Thames Valley Pharma Ltd", "UK"),
                                   record("B", "Thames Valley Pharma Ltd", "")])
    assert records[1]["country"] == "UK"
    assert records[1]["checks"] == [INFERRED_FLAG]
    assert records[0]["checks"] == []


def test_ambiguous_or_unknown_customer_stays_blank() -> None:
    records = harmonize_countries([record("A", "Multi Co", "Spain"), record("B", "Multi Co", "Chile"),
                                   record("C", "Multi Co", ""), record("D", "Lone Co", "")])
    assert records[2]["country"] == "" and records[3]["country"] == ""


def test_harmonizing_twice_does_not_duplicate_flags() -> None:
    records = [record("A", "X", "UK"), record("B", "X", "")]
    harmonize_countries(harmonize_countries(records))
    assert records[1]["checks"] == [INFERRED_FLAG]


def test_cost_lookup_accepts_country_aliases(records) -> None:
    by_alias = cost.find_reports(country="United Arab Emirates", records=records)
    assert by_alias and by_alias == cost.find_reports(country="UAE", records=records)
