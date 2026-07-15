"""
info_tool.py - Information Tool (Tool 1 of 4).

Purpose
-------
Answers grounded questions about the menu, hours, and policies by searching
menu.json and faqs.json directly - no model call, no RAG/embeddings, just
deterministic keyword matching over structured data, per the project's
explicit "no RAG" constraint.

Input schema (GetRestaurantInfoInput)
-----------------------------------------
query           : str                                   (required, non-empty)
scope           : "menu" | "faq" | "all"   (default "all")

Output schema (GetRestaurantInfoOutput)
------------------------------------------
match_count     : int
menu_matches    : list[dict]   (full menu item records that matched)
faq_matches     : list[dict]   (full FAQ records that matched)
found_any       : bool

Error behavior
---------------
An empty/whitespace-only query raises ValueError - this is a programming
error (the agent should never construct an empty query), not a recoverable
user-facing case, so it's the one place in this tool layer that does
raise rather than returning an error status.  A query with zero matches is
NOT an error: it returns found_any=False so the calling workflow can
trigger its fallback / human-handoff path.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

from tools import data_loader

VALID_SCOPES = {"menu", "faq", "all"}

# Common words filtered out before matching. Without this, short/frequent
# words like "on" or "the" match as *substrings* of unrelated words
# ("onion", "season") across nearly every record, so a generic query like
# "what's on the menu today" would spuriously match almost the entire
# dataset instead of anything actually relevant.
STOPWORDS: Set[str] = {
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for",
    "have", "how", "i", "in", "is", "it", "of", "on", "or", "our", "that",
    "the", "there", "this", "to", "today", "want", "what", "what's", "whats",
    "when", "where", "which", "who", "why", "will", "with", "you", "your",
}


@dataclass
class GetRestaurantInfoInput:
    query: str
    scope: str = "all"


@dataclass
class GetRestaurantInfoOutput:
    match_count: int
    menu_matches: List[Dict[str, Any]] = field(default_factory=list)
    faq_matches: List[Dict[str, Any]] = field(default_factory=list)
    found_any: bool = False


def _normalize(text: str) -> List[str]:
    words = [w for w in text.lower().replace(",", " ").replace(".", " ").split() if w]
    meaningful = [w for w in words if w not in STOPWORDS]
    # If stripping stopwords leaves nothing (e.g. a query that's *only*
    # "what's on the"), fall back to the original words rather than
    # matching against an empty list, which search_menu/search_faqs would
    # otherwise treat as "no words to match" and return zero results.
    return meaningful or words


def _menu_item_haystack(item: Dict[str, Any]) -> str:
    parts = [
        item.get("name", ""),
        item.get("description", ""),
        item.get("category_name", ""),
        " ".join(item.get("allergens", [])),
        " ".join(item.get("dietary", [])),
    ]
    return " ".join(parts).lower()


def _faq_haystack(entry: Dict[str, Any]) -> str:
    return " ".join([entry.get("topic", ""), entry.get("question", ""), entry.get("answer", "")]).lower()


def _haystack_words(haystack: str) -> Set[str]:
    return set(haystack.replace(",", " ").replace(".", " ").replace("_", " ").split())


def search_menu(query_words: List[str]) -> List[Dict[str, Any]]:
    matches = []
    for item in data_loader.get_all_menu_items():
        haystack_words = _haystack_words(_menu_item_haystack(item))
        if any(word in haystack_words for word in query_words):
            matches.append(item)
    return matches


def search_faqs(query_words: List[str]) -> List[Dict[str, Any]]:
    matches = []
    for entry in data_loader.get_all_faqs():
        haystack_words = _haystack_words(_faq_haystack(entry))
        if any(word in haystack_words for word in query_words):
            matches.append(entry)
    return matches


def get_restaurant_info(payload: GetRestaurantInfoInput) -> GetRestaurantInfoOutput:
    if not payload.query or not payload.query.strip():
        raise ValueError("query must be a non-empty string.")

    scope = payload.scope if payload.scope in VALID_SCOPES else "all"
    query_words = _normalize(payload.query)

    menu_matches: List[Dict[str, Any]] = []
    faq_matches: List[Dict[str, Any]] = []

    if scope in ("menu", "all"):
        menu_matches = search_menu(query_words)
    if scope in ("faq", "all"):
        faq_matches = search_faqs(query_words)

    total = len(menu_matches) + len(faq_matches)
    return GetRestaurantInfoOutput(
        match_count=total,
        menu_matches=menu_matches,
        faq_matches=faq_matches,
        found_any=total > 0,
    )
