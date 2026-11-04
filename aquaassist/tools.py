"""Tool definitions exposed to Claude, and the dispatcher that runs them.

Routing lives here: the model sees these descriptions and decides, per message, whether to
call a tool, ask the user a question, or answer. Descriptions therefore say *when* to use
each tool, not just what it does.
"""
from __future__ import annotations

import json

from . import config, cost, process, retrieval

TOOLS = [
    {
        "name": "list_products",
        "description": "List every Norvale product brochure available (name, edition, one-line summary). "
                       "Use to see what exists before recommending, or when the user asks what we sell.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "search_product_docs",
        "description": "Search the product brochures (the only source of truth for product facts and specs). "
                       "Returns matching brochure pages with doc_id and page number for citation. "
                       "Call this before stating any product capability or specification. Use several focused "
                       "queries rather than one broad one. Optionally restrict to one doc_id.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords, e.g. 'variable feed water quality permeate'"},
                "doc_id": {"type": "string",
                           "description": "Optional: restrict to one brochure, e.g. 'aquatune-optimizer'"},
                "top_k": {"type": "integer", "description": "Pages to return (default 4)"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "estimate_service_cost",
        "description": "Estimate the cost of a field service visit from historical service reports. "
                       "Returns low/typical/high, a component breakdown, the report IDs used and caveats. "
                       "Needs the equipment family and task; quantity and country make it much more accurate. "
                       "If the user has not said what work is needed, ask before calling.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "equipment_family": {"type": "string", "enum": config.EQUIPMENT_FAMILIES},
                "task_type": {"type": "string", "enum": config.TASK_TYPES},
                "quantity": {"type": "integer", "description": "Main units: elements, modules, lamps, vessels..."},
                "country": {"type": "string", "description": "Customer country, e.g. 'Chile'"},
                "travel_hours": {"type": "number", "description": "Round-trip travel hours if the user knows them"},
                "output_currency": {"type": "string", "description": "ISO code, default USD"},
            },
            "required": ["equipment_family", "task_type"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_service_reports",
        "description": "List historical service reports matching optional filters (summary fields only). "
                       "Use to check what history exists, e.g. which tasks were done for a product or in a country.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "equipment_family": {"type": "string", "enum": config.EQUIPMENT_FAMILIES},
                "task_type": {"type": "string", "enum": config.TASK_TYPES},
                "country": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_service_report",
        "description": "Read the original text of one service report, e.g. to quote a finding or check an oddity.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"report_id": {"type": "string", "description": "e.g. 'FSR-0012'"}},
            "required": ["report_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "analyze_process_readings",
        "description": "Compare a customer's RO plant readings with the hourly history of a reference RO plant. "
                       "Returns percentiles, a like-for-like comparison at similar recovery/feed conditions, "
                       "what distinguished the reference plant's worst hours, and data-quality notes. "
                       "Use whenever the user gives operating numbers and asks if they are normal or what to do. "
                       "Pass every reading the user gave and omit the rest.",
        "input_schema": {
            "type": "object",
            "properties": {k: {"type": "number", "description": f"{label} ({unit})" if unit else label}
                           for k, (label, unit, _) in process.METRICS.items()},
            "required": [],
            "additionalProperties": False,
        },
    },
]


def run_tool(name: str, args: dict) -> str:
    """Execute a tool and return a JSON string for the tool_result block."""
    if name == "list_products":
        result = retrieval.catalog()
    elif name == "search_product_docs":
        hits = retrieval.default_index().search(args["query"], top_k=args.get("top_k") or 4, doc_id=args.get("doc_id"))
        result = [{"doc_id": c.doc_id, "product": c.product, "edition": c.edition, "page": c.page,
                   "score": round(s, 2), "text": c.text} for s, c in hits] or {"message": "No matching brochure pages."}
    elif name == "estimate_service_cost":
        result = cost.estimate(args["equipment_family"], args["task_type"], quantity=args.get("quantity"),
                               country=args.get("country"), travel_hours=args.get("travel_hours"),
                               output_currency=args.get("output_currency") or "USD")
    elif name == "find_service_reports":
        result = cost.find_reports(args.get("equipment_family"), args.get("task_type"), args.get("country"))
    elif name == "get_service_report":
        path = config.REPORT_DIR / f"{args['report_id'].upper()}.txt"
        result = path.read_text(encoding="utf-8") if path.exists() else {"error": "No such report."}
    elif name == "analyze_process_readings":
        readings = {k: float(v) for k, v in args.items() if k in process.METRICS and isinstance(v, (int, float))}
        result = process.analyze(readings)
    else:
        raise ValueError(f"Unknown tool {name}")
    return result if isinstance(result, str) else json.dumps(result, default=str)
