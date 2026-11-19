"""Lossless helpers for a State File's leading YAML frontmatter."""
from __future__ import annotations

import re
from dataclasses import dataclass


class FrontmatterError(ValueError):
    pass


@dataclass(frozen=True)
class FrontmatterParts:
    opening: str
    metadata: str
    closing: str
    body: str

    @property
    def newline(self) -> str:
        return "\r\n" if self.opening.endswith("\r\n") else "\n"

    def render(self, metadata: str | None = None) -> str:
        return self.opening + (self.metadata if metadata is None else metadata) + self.closing + self.body


def split_frontmatter(text: str) -> FrontmatterParts | None:
    """Split an initial frontmatter block without reserializing metadata or body."""
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        return None
    lines = text.splitlines(keepends=True)
    if not lines:
        return None
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n").strip() == "---":
            return FrontmatterParts(
                opening=lines[0],
                metadata="".join(lines[1:index]),
                closing=line,
                body="".join(lines[index + 1:]),
            )
    raise FrontmatterError("frontmatter has no closing delimiter")


def field_value(metadata: str, key: str) -> str | None:
    """Return the sole top-level scalar value, or ``None`` when absent."""
    pattern = re.compile(rf"^{re.escape(key)}\s*:\s*(.*?)\s*(?:\r?\n|$)", re.MULTILINE)
    matches = pattern.findall(metadata)
    if len(matches) > 1:
        raise FrontmatterError(f"duplicate frontmatter field: {key}")
    if not matches:
        return None
    return matches[0].strip().strip("'\"")


def set_field(metadata: str, key: str, value: str, *, newline: str = "\n") -> str:
    """Replace or append one top-level field while preserving all other text."""
    pattern = re.compile(rf"^{re.escape(key)}\s*:.*?(?P<eol>\r?\n|$)", re.MULTILINE)
    matches = list(pattern.finditer(metadata))
    if len(matches) > 1:
        raise FrontmatterError(f"duplicate frontmatter field: {key}")
    replacement_line = f"{key}: {value}"
    if matches:
        match = matches[0]
        replacement = replacement_line + match.group("eol")
        return metadata[:match.start()] + replacement + metadata[match.end():]
    separator = "" if not metadata or metadata.endswith(("\n", "\r")) else newline
    return metadata + separator + replacement_line + newline


def with_frontmatter_fields(text: str, fields: dict[str, str]) -> str:
    """Losslessly add or replace fields in an initial frontmatter block."""
    parts = split_frontmatter(text)
    if parts is None:
        metadata = ""
        newline = "\n"
        for key, value in fields.items():
            metadata = set_field(metadata, key, value, newline=newline)
        return f"---{newline}{metadata}---{newline}{text}"
    metadata = parts.metadata
    for key, value in fields.items():
        metadata = set_field(metadata, key, value, newline=parts.newline)
    return parts.render(metadata)
