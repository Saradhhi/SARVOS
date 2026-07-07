"""
Research agent config. Uses DuckDuckGo's OFFICIAL Instant Answer API
(api.duckduckgo.com) -- their only sanctioned, documented API for
programmatic access. Free, no API key.

REAL PIVOT, based on live testing + follow-up research, not a guess: an
earlier version of this agent scraped html.duckduckgo.com (their
unofficial, no-JS results page). That's explicitly against DuckDuckGo's
terms and actively resisted -- confirmed directly: a live request
returned HTTP 202 with a generic homepage instead of results, which
matches documented behavior ("DuckDuckGo actively fights against
automated requests using its HTML endpoints... expect 202, 403, and
similar errors"). Continuing to reverse-engineer around that felt like
the wrong thing to keep doing, not just a hard bug to fix -- so this now
uses their real API instead.

REAL TRADEOFF, stated plainly: the Instant Answer API is NOT a full
web-search API. It returns curated instant-answer content (definitions,
topic abstracts, disambiguation, related topics) for well-known
entities/concepts -- it does NOT return ranked results for general or
current-event queries the way a search engine results page would. Many
reasonable queries will come back with nothing. That's a real, inherent
coverage gap, not a bug -- see agents/research.py's docstring for how
this is surfaced honestly rather than papered over.
"""

from __future__ import annotations

import os

SEARCH_URL_TEMPLATE = os.environ.get(
    "SARVOS_SEARCH_URL_TEMPLATE", "https://api.duckduckgo.com/"
)

PAGE_LOAD_TIMEOUT_MS = int(os.environ.get("SARVOS_RESEARCH_TIMEOUT_MS", "15000"))

# Caps how many RelatedTopics entries are included when there's no direct
# Abstract/Answer/Definition -- related topics can be a long list.
MAX_RESULTS = int(os.environ.get("SARVOS_RESEARCH_MAX_RESULTS", "5"))

# Each result's snippet/abstract is capped so a very long description
# doesn't dominate a spoken or short chat response.
MAX_SNIPPET_LENGTH = int(os.environ.get("SARVOS_RESEARCH_MAX_SNIPPET_LENGTH", "500"))

# Identifies this app to DuckDuckGo, per their own stated guidance for
# users of the Instant Answer API (t=nameofapp) -- costs nothing, and is
# the kind of small courtesy that distinguishes "using an API as
# intended" from "pretending to be a browser."
APP_IDENTIFIER = os.environ.get("SARVOS_APP_IDENTIFIER", "sarvos")
