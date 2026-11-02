import json

from aquaassist import cost, process, retrieval
from aquaassist.extraction import validate
from aquaassist.tools import TOOLS, run_tool


def test_membrane_estimate_for_country_without_history(records):
    est = cost.estimate("ClarionRO", "membrane_replacement", quantity=12, country="Chile", records=records)
    assert est["status"] == "ok"
    low, typ, high = est["estimate"]["low"], est["estimate"]["typical"], est["estimate"]["high"]
    assert low < typ < high
    # 12 elements at roughly USD 600-800 each dominate the cost.
    assert 7_000 < typ < 14_000
    assert any("No ClarionRO membrane_replacement visits recorded in Chile" in c for c in est["caveats"])
    # Chile has itemized reports on other equipment, so local rates and travel are used.
    assert "Chile" in est["components"]["labour_rate_per_h"]["basis"]
    assert "Chile" in est["components"]["travel_hours"]["method"]


def test_customer_supplied_parts_excluded_from_unit_price(records):
    est = cost.estimate("ClarionRO", "membrane_replacement", quantity=12, country="USA", records=records)
    assert est["components"]["main_part_unit_price"]["low"] > 300
    assert any("customer-supplied" in c for c in est["caveats"])


def test_unknown_task_reports_no_history(records):
    est = cost.estimate("LumenUV", "pump_overhaul", records=records)
    assert est["status"] == "no_matching_history"
    assert "uv_lamp_replacement" in est["tasks_with_history_for_this_equipment"]


def test_validation_flags():
    rec = {"line_items": [{"description": "x", "category": "part", "quantity": 2, "unit_price": 10, "line_total": 25}],
           "stated_total": 25, "onsite_hours": 5, "travel_hours": 5, "cost_status": "itemized"}
    flags = validate(rec)
    assert any("2 x 10" in f for f in flags)
    assert any("copy-paste" in f for f in flags)


def test_retrieval_finds_optimizer_for_changing_feed():
    hits = retrieval.default_index().search("permeate quality drops when feed water changes", top_k=3)
    assert "aquatune-optimizer" in {c.doc_id for _, c in hits}


def test_process_analysis_flags_high_salt_passage():
    res = process.analyze({"permeate_conductivity_uS_cm": 62, "recovery_pct": 72, "feed_conductivity_uS_cm": 2400})
    pc = res["vs_all_hours"]["permeate_conductivity_uS_cm"]
    assert pc["percentile_in_reference"] > 90
    assert res["vs_similar_operating_hours"]["matching_hours"] >= 50
    assert res["reference_dataset"]["hours_permeate_sensor_stuck"] > 0


def test_every_tool_schema_is_strict_and_dispatchable():
    def unions(schema):
        return sum(1 for p in schema["properties"].values() if "anyOf" in p or isinstance(p.get("type"), list))

    strict = [t["input_schema"] for t in TOOLS if t.get("strict")]
    # API limits across all strict schemas in one request: 24 optional params, 16 union-typed params.
    assert sum(len(s["properties"]) - len(s["required"]) for s in strict) <= 24
    assert sum(unions(s) for s in strict) <= 16
    for tool in TOOLS:
        assert tool["input_schema"]["additionalProperties"] is False
    assert json.loads(run_tool("list_products", {}))
