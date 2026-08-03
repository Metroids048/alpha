"""Deterministic, read-only retrieval over the checked-in WorldQuant notes."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KnowledgeSnippet:
    ref_id: str
    path: str
    heading: str
    content_hash: str
    text: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class KnowledgeContext:
    snippets: tuple[KnowledgeSnippet, ...]
    completeness_status: str
    missing_refs: tuple[str, ...] = ()


class WorldQuantKnowledgeRepository:
    """Only scans Markdown that exists below the configured knowledge root."""

    def __init__(self, root: str | Path = "World quant") -> None:
        self.root = Path(root)

    def retrieve(
        self,
        *,
        dataset: str,
        fields: tuple[str, ...],
        mechanism: str,
        failure_category: str = "",
    ) -> KnowledgeContext:
        snippets = self._snippets()
        if not snippets:
            return KnowledgeContext((), "MISSING", ("WORLDQUANT_MARKDOWN",))
        terms = _terms(dataset, mechanism, failure_category, *fields)
        ranked = sorted(
            snippets,
            key=lambda item: (-_score(item, terms), item.ref_id),
        )
        selected: list[KnowledgeSnippet] = []
        remaining = 6000
        for item in ranked:
            if len(selected) >= 5 or remaining <= 0:
                break
            text = item.text[:remaining]
            if not text:
                continue
            selected.append(
                item if text == item.text else KnowledgeSnippet(
                    item.ref_id, item.path, item.heading, item.content_hash, text, item.tags
                )
            )
            remaining -= len(text)
        return KnowledgeContext(tuple(selected), "COMPLETE")

    def _snippets(self) -> tuple[KnowledgeSnippet, ...]:
        if not self.root.is_dir():
            return ()
        unique: dict[str, KnowledgeSnippet] = {}
        for path in sorted(self.root.rglob("*.md"), key=lambda item: item.as_posix().lower()):
            if not path.is_file():
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                continue
            relative = path.relative_to(self.root).as_posix()
            for index, (heading, text) in enumerate(_sections(source), start=1):
                normalized = " ".join(text.split())
                if not normalized:
                    continue
                content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                if content_hash in unique:
                    continue
                ref_id = f"worldquant:{relative}#{index}"
                unique[content_hash] = KnowledgeSnippet(
                    ref_id=ref_id,
                    path=relative,
                    heading=heading,
                    content_hash=content_hash,
                    text=normalized,
                    tags=tuple(sorted(_terms(heading, normalized))),
                )
        return tuple(sorted(unique.values(), key=lambda item: item.ref_id))


def _sections(source: str) -> list[tuple[str, str]]:
    heading = ""
    content: list[str] = []
    result: list[tuple[str, str]] = []
    for line in source.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if match:
            if content:
                result.append((heading, "\n".join(content)))
            heading = match.group(1)
            content = []
        else:
            content.append(line)
    if content:
        result.append((heading, "\n".join(content)))
    return result


def _terms(*values: object) -> set[str]:
    return {
        token
        for value in values
        for token in re.findall(r"[a-z0-9_]+", str(value or "").lower().replace("_", " "))
        if len(token) > 1
    }


def _score(item: KnowledgeSnippet, terms: set[str]) -> int:
    if not terms:
        return 0
    haystack = set(item.tags) | _terms(item.path)
    return len(haystack & terms)
