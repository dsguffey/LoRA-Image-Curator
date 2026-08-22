"""Advanced catalog-search parsing and query construction.

The browser intentionally has one search language.  Both typed searches and
the Advanced Search dialog produce the same plain-text query, so a saved search
never depends on hidden GUI state.  This module contains no Tkinter code and is
therefore straightforward to test independently of the desktop interface.
"""

from __future__ import annotations

import shlex

from dataclasses import dataclass
from typing import Any, Iterable

from quality_analysis import (
    DEFAULT_BLUR_THRESHOLD,
    DEFAULT_DUPLICATE_SIMILARITY_PERCENT,
)


class SearchSyntaxError(ValueError):
    """Raised when a query cannot be interpreted predictably."""


FIELD_ALIASES = {
    "any_field": "all",
    "any_tag": "tag",
    "manual_tag": "manual",
    "active_ai_tag": "ai",
    "excluded_ai_tag": "excluded",
    "keyword": "trigger",
    "trigger_keyword": "trigger",
    "status": "review",
    "review_state": "review",
    "available": "file",
    "file_availability": "file",
    "filename_or_path": "filename",
    "image_quality": "quality",
    "sharpness": "blur",
    "duplicates": "duplicate",
    "image_set": "set",
    "dataset": "set",
    "image_id": "id",
    "visible_text": "ocr",
    "text_overlay": "ocr",
}

KNOWN_FIELDS = {
    "all",
    "tag",
    "manual",
    "ai",
    "excluded",
    "trigger",
    "review",
    "identity",
    "file",
    "caption",
    "ocr",
    "filename",
    "resolution",
    "quality",
    "blur",
    "duplicate",
    "set",
    "id",
}


@dataclass(slots=True, frozen=True)
class SearchClause:
    """One row from the Advanced Search dialog."""

    field: str
    value: str
    excluded: bool = False


def build_search_query(
    clauses: Iterable[SearchClause],
    *,
    match_any: bool = False,
) -> str:
    """Build readable query text from explicit advanced-search rows.

    Values are quoted only when necessary.  Negation wraps an OR expression so
    the generated query retains the meaning displayed by the dialog.
    """
    rendered: list[str] = []
    for clause in clauses:
        field = _normalize_field(clause.field)
        value = " ".join(clause.value.split()).strip()
        if not value:
            continue
        token = f"{field}:{_quote_value(value)}"
        rendered.append(f"NOT {token}" if clause.excluded else token)

    if not rendered:
        return ""
    operator = " OR " if match_any else " AND "
    if match_any and any(item.startswith("NOT ") for item in rendered):
        rendered = [f"({item})" if item.startswith("NOT ") else item for item in rendered]
    return operator.join(rendered)


def record_matches_query(record: Any, query: str) -> bool:
    """Evaluate one browser record against the documented search language.

    Operators use conventional precedence: ``NOT`` before ``AND`` before
    ``OR``. Adjacent terms imply ``AND`` for compatibility with the original
    compact search box. Parentheses may be used for explicit grouping.
    Unqualified terms search tags and the Trigger Keyword through
    ``record.search_blob``; named fields handle other review metadata.
    """
    if not query.strip():
        return True
    rpn = _to_reverse_polish(_tokenize(query))
    stack: list[bool] = []
    for token in rpn:
        operator = token.upper()
        if operator == "NOT":
            if not stack:
                raise SearchSyntaxError("NOT must be followed by a search condition.")
            stack.append(not stack.pop())
        elif operator in {"AND", "OR"}:
            if len(stack) < 2:
                raise SearchSyntaxError(f"{operator} must join two search conditions.")
            right = stack.pop()
            left = stack.pop()
            stack.append(left and right if operator == "AND" else left or right)
        else:
            stack.append(_matches_predicate(record, token))
    if len(stack) != 1:
        raise SearchSyntaxError("The search expression is incomplete.")
    return stack[0]


def duplicate_review_threshold(query: str) -> float | None:
    """Return the positive similarity threshold that enables grouped review.

    Grouped review is intentionally narrower than ordinary search matching. It
    activates for a direct similarity search, or for additional constraints
    joined to that search with AND (for example an image-set-scoped readiness
    link).  OR expressions stay in the normal grid because they may include
    records that are not duplicate candidates, and negated/exact-copy queries
    are not side-by-side perceptual comparisons.

    The helper recognizes the same user-facing aliases and defaults as the
    search evaluator.  Invalid/incomplete text returns ``None`` so typing in the
    search box cannot unexpectedly switch layouts.
    """
    try:
        tokens = _tokenize(query)
    except SearchSyntaxError:
        return None
    if any(token.upper() == "OR" for token in tokens):
        return None

    # Each stack item contains thresholds used positively and negatively in
    # that subexpression. NOT swaps the sets, which correctly handles both
    # ``NOT duplicate:96`` and ``NOT (duplicate:96)`` without a second parser.
    stack: list[tuple[set[float], set[float]]] = []
    try:
        rpn = _to_reverse_polish(tokens)
    except SearchSyntaxError:
        return None
    for token in rpn:
        operator = token.upper()
        if operator == "NOT":
            if not stack:
                return None
            positive, negative = stack.pop()
            stack.append((negative, positive))
        elif operator == "AND":
            if len(stack) < 2:
                return None
            right_positive, right_negative = stack.pop()
            left_positive, left_negative = stack.pop()
            stack.append(
                (
                    left_positive | right_positive,
                    left_negative | right_negative,
                )
            )
        else:
            threshold: float | None = None
            if ":" in token:
                field, raw_value = token.split(":", 1)
                if _normalize_field(field) == "duplicate":
                    needle = _normalize_value(raw_value)
                    if needle in {"possible", "probable", "similar"}:
                        threshold = float(DEFAULT_DUPLICATE_SIMILARITY_PERCENT)
                    elif needle not in {"exact", "sha256", "copy", "copies"}:
                        try:
                            threshold = max(
                                0.0,
                                min(100.0, float(raw_value.rstrip("%"))),
                            )
                        except ValueError:
                            pass
            stack.append(({threshold} if threshold is not None else set(), set()))

    if len(stack) != 1:
        return None
    positive_thresholds, _negative_thresholds = stack[0]
    return max(positive_thresholds) if positive_thresholds else None


def _tokenize(query: str) -> list[str]:
    """Tokenize quotes and parentheses without treating underscores specially."""
    try:
        lexer = shlex.shlex(query, posix=True, punctuation_chars="()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError as error:
        raise SearchSyntaxError(str(error)) from error

    expanded: list[str] = []
    for token in tokens:
        if token and set(token) == {"("}:
            expanded.extend("(" for _ in token)
        elif token and set(token) == {")"}:
            expanded.extend(")" for _ in token)
        else:
            expanded.append(token)
    return _insert_implicit_and(expanded)


def _insert_implicit_and(tokens: list[str]) -> list[str]:
    result: list[str] = []
    previous_kind = "start"
    for token in tokens:
        upper = token.upper()
        if token == "(":
            kind = "open"
        elif token == ")":
            kind = "close"
        elif upper in {"AND", "OR"}:
            kind = "binary"
        elif upper == "NOT" or token.startswith("-"):
            kind = "not"
        else:
            kind = "term"

        if previous_kind in {"term", "close"} and kind in {"term", "open", "not"}:
            result.append("AND")
        result.append("NOT" if token == "-" else token)
        previous_kind = kind
    return result


def _to_reverse_polish(tokens: list[str]) -> list[str]:
    output: list[str] = []
    operators: list[str] = []
    precedence = {"OR": 1, "AND": 2, "NOT": 3}

    for original in tokens:
        token = original
        if token.startswith("-") and len(token) > 1:
            # The long-standing ``-tag:hat`` shorthand remains supported.
            token = token[1:]
            original = token
            while operators and operators[-1] == "NOT":
                output.append(operators.pop())
            operators.append("NOT")

        upper = original.upper()
        if original == "(":
            operators.append(original)
        elif original == ")":
            while operators and operators[-1] != "(":
                output.append(operators.pop())
            if not operators:
                raise SearchSyntaxError("A closing parenthesis has no matching opening parenthesis.")
            operators.pop()
        elif upper in precedence:
            right_associative = upper == "NOT"
            while operators and operators[-1] in precedence:
                top = operators[-1]
                if precedence[top] > precedence[upper] or (
                    precedence[top] == precedence[upper] and not right_associative
                ):
                    output.append(operators.pop())
                else:
                    break
            operators.append(upper)
        else:
            output.append(original)

    while operators:
        operator = operators.pop()
        if operator == "(":
            raise SearchSyntaxError("An opening parenthesis has no matching closing parenthesis.")
        output.append(operator)
    return output


def _matches_predicate(record: Any, token: str) -> bool:
    field = "all"
    value = token
    if ":" in token:
        candidate, remainder = token.split(":", 1)
        normalized = _normalize_field(candidate)
        if normalized in KNOWN_FIELDS and remainder:
            field = normalized
            value = remainder

    needle = _normalize_value(value)
    if field == "review":
        aliases = {
            "needs_review": "review",
            "needs_follow_up": "review",
            "needs-follow-up": "review",
            "follow_up": "review",
            "follow-up": "review",
        }
        return str(record.review_status).casefold() == aliases.get(needle, needle)
    if field == "file":
        if needle in {"missing", "unavailable"}:
            return str(record.file_status).casefold() != "present"
        if needle in {"available", "present"}:
            return str(record.file_status).casefold() == "present"
        return needle == str(record.file_status).casefold()
    if field == "identity":
        status = str(record.identity_review_status or "").casefold()
        if needle in {"unconfirmed", "needs_review", "suggested"}:
            return bool(record.suggested_identity) and status not in {"confirmed", "rejected"}
        if needle == "confirmed":
            return status == "confirmed"
        if needle == "rejected":
            return status == "rejected"
        if needle in {"none", "missing"}:
            return not bool(record.suggested_identity)
        if needle in {"multiple_faces", "multiple-faces", "multiple"}:
            return int(record.face_count or 0) > 1
        if needle in {"no_face", "no_faces"}:
            return int(record.face_count or 0) == 0
        return _contains(str(record.suggested_identity), needle)
    if field == "resolution":
        short_side = min(int(record.width or 0), int(record.height or 0))
        if needle in {"low", "below_512"}:
            return short_side == 0 or short_side < 512
        if needle in {"preferred", "768_plus", "at_least_768"}:
            return short_side >= 768
        if needle.startswith("below_"):
            try:
                threshold = int(needle.removeprefix("below_"))
            except ValueError:
                return False
            return short_side == 0 or short_side < threshold
        return False
    if field == "quality":
        status = str(getattr(record, "quality_status", "") or "").casefold()
        if needle in {"missing", "none", "not_analyzed", "unanalyzed"}:
            return not status
        if needle in {"analyzed", "complete", "success"}:
            return status == "success"
        if needle in {"error", "failed"}:
            return status == "error"
        if needle == "blur":
            score = getattr(record, "sharpness_score", None)
            return score is not None and float(score) < DEFAULT_BLUR_THRESHOLD
        return False
    if field == "blur":
        score = getattr(record, "sharpness_score", None)
        if score is None:
            return False
        try:
            threshold = float(value)
        except ValueError:
            threshold = DEFAULT_BLUR_THRESHOLD
        return float(score) < threshold
    if field == "duplicate":
        if needle in {"exact", "sha256", "copy", "copies"}:
            return int(getattr(record, "file_location_count", 0) or 0) > 1
        similarity = getattr(record, "nearest_duplicate_similarity", None)
        if similarity is None:
            return False
        if needle in {"possible", "probable", "similar"}:
            threshold = float(DEFAULT_DUPLICATE_SIMILARITY_PERCENT)
        else:
            try:
                threshold = float(value.rstrip("%"))
            except ValueError:
                return False
        return float(similarity) >= threshold
    if field == "set":
        names = str(getattr(record, "image_set_names", "") or "").split("\x1f")
        if needle in {"missing", "none"}:
            return not any(name.strip() for name in names)
        if needle in {"any", "present"}:
            return any(name.strip() for name in names)
        return any(_normalize_value(name) == needle for name in names if name.strip())
    if field == "id":
        try:
            return int(getattr(record, "image_id")) == int(value)
        except (TypeError, ValueError):
            return False

    text_by_field = {
        "all": str(record.search_blob),
        "tag": "\n".join((record.manual_tags, record.ai_tags_active, record.ai_tags_excluded)),
        "manual": str(record.manual_tags),
        "ai": str(record.ai_tags_active),
        "excluded": str(record.ai_tags_excluded),
        "trigger": str(record.manual_keyword),
        "caption": str(record.caption),
        "ocr": str(getattr(record, "ocr_text", "") or ""),
        "filename": "\n".join((record.filename, record.relative_path, record.absolute_path)),
        "set": str(getattr(record, "image_set_names", "") or "").replace("\x1f", "\n"),
    }
    haystack = text_by_field[field]
    if needle in {"missing", "none", "blank"} and field != "all":
        return not haystack.strip()
    if needle in {"any", "present"} and field != "all":
        return bool(haystack.strip())
    return _contains(haystack, needle)


def _contains(haystack: str, normalized_needle: str) -> bool:
    normalized_haystack = haystack.casefold()
    normalized_haystack += "\n" + normalized_haystack.replace(" ", "_")
    return normalized_needle in normalized_haystack


def _normalize_field(field: str) -> str:
    normalized = field.strip().casefold().replace(" ", "_")
    return FIELD_ALIASES.get(normalized, normalized)


def _normalize_value(value: str) -> str:
    return "_".join(value.strip().casefold().split())


def _quote_value(value: str) -> str:
    if any(character.isspace() or character in "()" for character in value):
        return '"' + value.replace('"', '\\"') + '"'
    return value
