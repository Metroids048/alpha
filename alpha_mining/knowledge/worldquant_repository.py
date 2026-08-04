"""Deterministic, read-only retrieval over checked-in WorldQuant knowledge."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class KnowledgeIntent(str, Enum):
    IDEA_GENERATION = "IDEA_GENERATION"
    QUALITY_RULE = "QUALITY_RULE"
    TUNING_RULE = "TUNING_RULE"


class KnowledgeDocType(str, Enum):
    IDEA_BODY = "IDEA_BODY"
    RULE = "RULE"
    INDEX = "INDEX"
    AUTH = "AUTH"
    ENGINEERING = "ENGINEERING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class KnowledgeSnippet:
    ref_id: str
    path: str
    heading: str
    content_hash: str
    text: str
    tags: tuple[str, ...]
    document_type: KnowledgeDocType = KnowledgeDocType.UNKNOWN


@dataclass(frozen=True)
class KnowledgeContext:
    snippets: tuple[KnowledgeSnippet, ...]
    completeness_status: str
    missing_refs: tuple[str, ...] = ()
    context_hash: str = ""
    intent: KnowledgeIntent = KnowledgeIntent.IDEA_GENERATION


class WorldQuantKnowledgeRepository:
    """Only use local, relevant Markdown as attributable knowledge evidence."""

    def __init__(self, root: str | Path = "World quant") -> None:
        self.root = Path(root)

    def retrieve(
        self,
        *,
        dataset: str,
        fields: tuple[str, ...],
        mechanism: str,
        failure_category: str = "",
        intent: KnowledgeIntent = KnowledgeIntent.IDEA_GENERATION,
    ) -> KnowledgeContext:
        snippets = self._snippets()
        if not snippets:
            return self._context((), "MISSING", ("WORLDQUANT_MARKDOWN",), intent)
        admissible = [item for item in snippets if _admissible(item.document_type, intent)]
        if not admissible:
            return self._context((), "INCOMPLETE", ("NO_ADMISSIBLE_WORLDQUANT_BODY",), intent)
        terms = _terms(dataset, mechanism, failure_category, *fields)
        ranked = sorted(
            ((item, _score(item, terms)) for item in admissible),
            key=lambda item: (-item[1], item[0].ref_id),
        )
        # A ref is evidence only when it matches the actual request.  Never
        # silently fill a context budget with unrelated directory material.
        relevant = [item for item, score in ranked if score > 0]
        if not relevant:
            return self._context((), "NO_RELEVANT_MATCH", ("NO_RELEVANT_WORLDQUANT_BODY",), intent)
        selected: list[KnowledgeSnippet] = []
        per_source: dict[str, int] = {}
        remaining = 6000
        for item in relevant:
            if len(selected) >= 5 or remaining <= 0:
                break
            source_count = per_source.get(item.path, 0)
            if source_count >= 2:
                continue
            text = item.text[: min(1200, remaining)]
            if not text:
                continue
            selected.append(
                item if text == item.text else KnowledgeSnippet(
                    item.ref_id, item.path, item.heading, item.content_hash,
                    text, item.tags, item.document_type,
                )
            )
            per_source[item.path] = source_count + 1
            remaining -= len(text)
        return self._context(tuple(selected), "COMPLETE", (), intent)

    def _context(
        self,
        snippets: tuple[KnowledgeSnippet, ...],
        status: str,
        missing_refs: tuple[str, ...],
        intent: KnowledgeIntent,
    ) -> KnowledgeContext:
        digest = hashlib.sha256()
        digest.update(intent.value.encode("utf-8"))
        digest.update(status.encode("utf-8"))
        for item in snippets:
            digest.update(item.ref_id.encode("utf-8"))
            digest.update(item.content_hash.encode("ascii"))
        return KnowledgeContext(snippets, status, missing_refs, digest.hexdigest(), intent)

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
            document_type = _classify_document(relative, source)
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
                    tags=tuple(sorted(_terms(relative, heading, normalized))),
                    document_type=document_type,
                )
        return tuple(sorted(unique.values(), key=lambda item: item.ref_id))


def _classify_document(path: str, source: str) -> KnowledgeDocType:
    frontmatter = source.split("---", 2)
    declared = ""
    if len(frontmatter) >= 3 and source.lstrip().startswith("---"):
        match = re.search(r"(?im)^\s*(?:document_type|type|kind)\s*:\s*([^\r\n#]+)", frontmatter[1])
        declared = match.group(1).strip().upper() if match else ""
    aliases = {item.value: item for item in KnowledgeDocType}
    if declared in aliases:
        return aliases[declared]
    value = path.lower().replace("\\", "/")
    if any(token in value for token in ("index", "directory", "catalog", "目录", "索引", "readme")):
        return KnowledgeDocType.INDEX
    if any(token in value for token in ("auth", "login", "cookie", "credential", "认证", "登录")):
        return KnowledgeDocType.AUTH
    if "alpha灵感启示录" in value:
        return KnowledgeDocType.INDEX
    if any(token in value for token in ("engineering", "runtime", "deploy", "implementation", "代码", "工程")):
        return KnowledgeDocType.ENGINEERING
    if any(token in value for token in ("优质alpha挖掘", "高质量alpha", "高质量工作流")):
        return KnowledgeDocType.IDEA_BODY
    if any(token in value for token in ("完整agent工作流", "failed ra", "自相关", "算子多样性", "假说优先")):
        return KnowledgeDocType.RULE
    if any(token in value for token in (
        "alpha_inspiration/posts/", "inspiration", "idea", "alpha_idea",
        "guide", "tutorial", "example", "灵感", "指南", "教程",
    )):
        return KnowledgeDocType.IDEA_BODY
    if any(token in value for token in ("operator", "rule", "constraint", "quality", "tuning", "settings", "规则")):
        return KnowledgeDocType.RULE
    return KnowledgeDocType.UNKNOWN


def _admissible(document_type: KnowledgeDocType, intent: KnowledgeIntent) -> bool:
    if intent is KnowledgeIntent.IDEA_GENERATION:
        return document_type in {KnowledgeDocType.IDEA_BODY, KnowledgeDocType.RULE}
    if intent is KnowledgeIntent.QUALITY_RULE:
        return document_type is KnowledgeDocType.RULE
    return document_type is KnowledgeDocType.RULE


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
    """Return English identifiers plus Chinese words and stable bigrams.

    Markdown in this repository is bilingual.  A regex that only sees ASCII
    silently makes all Chinese operational rules unreachable, so aliases are
    expanded into a shared canonical vocabulary before ranking.
    """

    text = " ".join(str(value or "").lower() for value in values)
    terms: set[str] = set()
    for token in re.findall(r"[a-z0-9_]+", text):
        if len(token) > 1:
            terms.add(token)
            terms.update(part for part in token.split("_") if len(part) > 1)
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(chunk) >= 2:
            terms.add(chunk)
            terms.update(chunk[index:index + 2] for index in range(len(chunk) - 1))
    for canonical, aliases in _TERM_ALIASES.items():
        if any(alias in text for alias in aliases):
            terms.add(canonical)
            for alias in aliases:
                if len(alias) > 1:
                    terms.add(alias)
    return terms


_TERM_ALIASES: dict[str, tuple[str, ...]] = {
    "self_correlation": ("self correlation", "self_correlation", "自相关"),
    "turnover": ("turnover", "换手率", "换手"),
    "momentum": ("momentum", "动量"),
    "reversal": ("reversal", "反转"),
    "fundamental": ("fundamental", "基本面"),
    "neutralization": ("neutralization", "neutralize", "中性化"),
    "low_sharpe": ("low sharpe", "low_sharpe", "低夏普"),
    "prod_correlation": ("prod correlation", "prod_correlation", "相关性", "生产相关"),
    "operator_diversity": ("operator diversity", "算子多样性"),
    "hypothesis_first": ("hypothesis first", "假说优先"),
}


def _score(item: KnowledgeSnippet, terms: set[str]) -> int:
    if not terms:
        return 0
    haystack = set(item.tags) | _terms(item.path)
    return len(haystack & terms)
