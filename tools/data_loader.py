"""
data_loader.py — Loads the static, read-only domain knowledge (menu, table
inventory, FAQs) that the Information and Analysis tools query against.

This is the "Data layer" piece of the architecture for everything that
never changes while the agent is running. Dynamic, mutable state
(reservations) lives in db_helper.py / bookings.db instead — see
PHASE1_NOTES.md for the rationale.

Data is loaded once per process and cached in module-level globals, since
the files are small and read-only; there is no need to re-read disk on
every tool call.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

_menu_cache: Optional[Dict[str, Any]] = None
_tables_cache: Optional[Dict[str, Any]] = None
_faqs_cache: Optional[Dict[str, Any]] = None


def _load_json(filename: str) -> Dict[str, Any]:
    path = DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Domain data file not found: {path}. Check the DATA_DIR environment "
            "variable or that the data/ folder was copied into the container."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_menu() -> Dict[str, Any]:
    global _menu_cache
    if _menu_cache is None:
        _menu_cache = _load_json("menu.json")
    return _menu_cache


def load_tables() -> Dict[str, Any]:
    global _tables_cache
    if _tables_cache is None:
        _tables_cache = _load_json("tables.json")
    return _tables_cache


def load_faqs() -> Dict[str, Any]:
    global _faqs_cache
    if _faqs_cache is None:
        _faqs_cache = _load_json("faqs.json")
    return _faqs_cache


def get_all_menu_items() -> List[Dict[str, Any]]:
    """Flattens menu.json's categories into a single list of items, with the
    category folded into each item for easier filtering/searching."""
    menu = load_menu()
    items = []
    for category in menu["categories"]:
        for item in category["items"]:
            flat_item = dict(item)
            flat_item["category_id"] = category["category_id"]
            flat_item["category_name"] = category["category_name"]
            items.append(flat_item)
    return items


def get_all_tables() -> List[Dict[str, Any]]:
    return load_tables()["tables"]


def get_table_by_id(table_id: int) -> Optional[Dict[str, Any]]:
    for table in get_all_tables():
        if table["table_id"] == table_id:
            return table
    return None


def get_service_hours() -> Dict[str, Dict[str, str]]:
    return load_tables()["service_hours"]


def get_slot_length_minutes() -> int:
    return load_tables()["slot_length_minutes"]


def get_max_table_capacity() -> int:
    return max(t["capacity"] for t in get_all_tables())


def get_all_faqs() -> List[Dict[str, Any]]:
    return load_faqs()["faqs"]
