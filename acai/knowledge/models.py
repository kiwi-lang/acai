"""Data models for knowledge documents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


def slugify(text: str) -> str:
    """Turn a human string into a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-") or "untitled"


FACETS = ("personality", "matter", "energy", "space", "time")
"""The five PMEST classification facets (Ranganathan-style)."""


@dataclass
class Facets:
    """Faceted classification following PMEST.

    - personality: *what* — the essential subject/entity
    - matter: *material* — the substance, medium, or constituent
    - energy: *action* — the activity, operation, or process
    - space: *where* — geographic/logical location
    - time: *when* — temporal period or date
    """
    personality: str = ""
    matter: str = ""
    energy: str = ""
    space: str = ""
    time: str = ""

    def to_dict(self) -> dict[str, str]:
        return {k: v for k, v in self.__dict__.items() if v}

    @classmethod
    def from_dict(cls, data) -> Facets:
        if not data or not isinstance(data, dict):
            return cls()
        return cls(
            personality=str(data.get("personality", "") or ""),
            matter=str(data.get("matter", "") or ""),
            energy=str(data.get("energy", "") or ""),
            space=str(data.get("space", "") or ""),
            time=str(data.get("time", "") or ""),
        )

    def is_empty(self) -> bool:
        return not any(self.__dict__.values())


@dataclass
class KnowledgeDoc:
    subject: str
    subsubject: str
    title: str
    content: str
    updated_at: float
    tags: list[str] = field(default_factory=list)
    facets: Facets = field(default_factory=Facets)

    @property
    def path(self) -> str:
        """Logical path used as the document identifier."""
        return f"{self.subject}/{self.subsubject}/{self.title}"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "subject": self.subject,
            "subsubject": self.subsubject,
            "title": self.title,
            "content": self.content,
            "updated_at": self.updated_at,
            "tags": self.tags,
            "facets": self.facets.to_dict(),
        }

    def summary(self) -> dict:
        """Like to_dict but without the full content."""
        return {
            "path": self.path,
            "subject": self.subject,
            "subsubject": self.subsubject,
            "title": self.title,
            "updated_at": self.updated_at,
            "tags": self.tags,
            "facets": self.facets.to_dict(),
        }

    def matches(self, query: str) -> bool:
        """Case-insensitive substring match on path + content."""
        q = query.lower()
        return (
            q in self.path.lower()
            or q in self.content.lower()
        )
