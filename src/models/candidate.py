"""information-hub — candidate data contract (models layer).

Defines the shared ``Candidate`` class used by the collect phase to carry a
raw story from a source fetcher through selection, full-text extraction and
deep-dive generation.  It is the single "input record" type of the pipeline.

Role: phase collect — consumed by ``collect.fetchers`` (creation),
``collect.prompts`` (rendering into prompts) and ``main`` (selection).
"""

from __future__ import annotations

from typing import Any


class Candidate:
    """A raw candidate story fetched from a source, before deep-dive generation.

    Attributes:
        collection: name of the collection that sourced this candidate.
        source:     ``{"name", "url", "type"}`` — origin feed/API metadata.
        title:      story headline.
        url:        canonical story URL.
        summary:    snippet from the feed/API (may be empty for some sources).
        published:  ISO-8601 publish date, if known.
        key:        stable record key ``YYYY-MM-DD-NNN`` — assigned later.
        stable_id:  full item id ``info:item:<topic>:<region>:<key>``.
        date:       run date the candidate was published under (attribute,
                    never a folder).
    """

    __slots__ = ("collection", "source", "title", "url", "summary", "published",
                 "key", "stable_id", "date")

    def __init__(self, collection: str, source: dict[str, str], title: str,
                 url: str, summary: str = "", published: str | None = None,
                 key: str | None = None, stable_id: str | None = None,
                 date: str | None = None):
        self.collection = collection
        self.source = source
        self.title = title
        self.url = url
        self.summary = summary
        self.published = published
        self.key = key
        self.stable_id = stable_id
        self.date = date

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (used by logging and mock mode)."""
        return {
            "collection": self.collection,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "published": self.published,
        }
