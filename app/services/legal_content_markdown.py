from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from markdown_it import MarkdownIt
from markdown_it.token import Token
from pydantic import ValidationError

from app.schemas.legal_content import (
    MAX_LEGAL_BLOCK_JSON_BYTES,
    LegalDocumentContent,
    LegalInlineLink,
    LegalInlineRun,
    LegalInlineValue,
)


class LegalMarkdownError(ValueError):
    """Raised when Markdown cannot be represented by the legal-content contract."""


@dataclass(frozen=True)
class LegalMarkdownParseResult:
    content: LegalDocumentContent
    scrubbed_formatting: bool = False


_MARKDOWN = MarkdownIt(
    "js-default",
    {
        "html": False,
        "linkify": False,
        "typographer": False,
    },
)


def _line_number(token: Token) -> int | None:
    return token.map[0] + 1 if token.map else None


def _unsupported(token: Token, description: str) -> LegalMarkdownError:
    line = _line_number(token)
    suffix = f" on line {line}" if line is not None else ""
    verb = "are" if description.endswith("s") else "is"
    return LegalMarkdownError(f"{description} {verb} not supported{suffix}")


def _append_run(runs: list[dict[str, str]], run_type: str, text: str) -> None:
    if not text:
        return
    if runs and runs[-1]["type"] == run_type:
        runs[-1]["text"] += text
    else:
        runs.append({"type": run_type, "text": text})


def _validated_inline_link(*, text: str, url: str) -> dict[str, str]:
    try:
        link = LegalInlineLink(type="link", text=text, url=url)
    except ValidationError as exc:
        detail = str(exc.errors(include_input=False)[0].get("msg") or "Inline link is invalid")
        raise LegalMarkdownError(detail.removeprefix("Value error, ")) from exc
    return link.model_dump(mode="json")


def _inline_value(token: Token, *, allow_breaks: bool) -> tuple[LegalInlineValue, bool]:
    runs: list[dict[str, str]] = []
    italic_depth = 0
    bold_depth = 0
    active_link_url: str | None = None
    active_link_text: list[str] = []
    scrubbed = False

    def current_type() -> str:
        if italic_depth and bold_depth:
            return "bold_italic"
        if italic_depth:
            return "italic"
        if bold_depth:
            return "bold"
        return "text"

    for child in token.children or ():
        if active_link_url is not None and child.type not in {"text", "link_close"}:
            raise _unsupported(token, "Formatting inside links")
        if child.type == "text":
            if active_link_url is not None:
                active_link_text.append(child.content)
            else:
                _append_run(runs, current_type(), child.content)
        elif child.type in {"softbreak", "hardbreak"} and allow_breaks:
            _append_run(runs, current_type(), "\n")
        elif child.type == "em_open":
            italic_depth += 1
        elif child.type == "em_close" and italic_depth:
            italic_depth -= 1
        elif child.type == "strong_open":
            bold_depth += 1
        elif child.type == "strong_close" and bold_depth:
            bold_depth -= 1
        elif child.type in {"s_open", "s_close"}:
            scrubbed = True
        elif child.type == "code_inline":
            scrubbed = True
            _append_run(runs, current_type(), child.content)
        elif child.type == "image":
            scrubbed = True
            _append_run(runs, current_type(), child.content)
        elif child.type == "link_open":
            if active_link_url is not None or italic_depth or bold_depth or set(child.attrs) != {"href"}:
                raise _unsupported(token, "Nested, styled or titled links")
            active_link_url = child.attrs["href"]
            active_link_text = []
        elif child.type == "link_close" and active_link_url is not None:
            runs.append(
                _validated_inline_link(
                    text="".join(active_link_text),
                    url=active_link_url,
                )
            )
            active_link_url = None
            active_link_text = []
        else:
            raise _unsupported(token, "This Markdown structure")

    if italic_depth or bold_depth or active_link_url is not None:
        raise _unsupported(token, "Unclosed Markdown emphasis")
    if all(run["type"] == "text" for run in runs):
        return "".join(run["text"] for run in runs), scrubbed
    return runs, scrubbed


def _inline_plain_text(value: LegalInlineValue) -> str:
    if isinstance(value, str):
        return value
    return "".join(
        run.text if isinstance(run, (LegalInlineRun, LegalInlineLink)) else str(run["text"])
        for run in value
    )


def _standalone_link(token: Token) -> tuple[dict[str, str] | None, bool]:
    children = token.children or ()
    if len(children) < 3 or children[0].type != "link_open" or children[-1].type != "link_close":
        return None, False
    if any(child.type in {"link_open", "link_close"} for child in children[1:-1]):
        raise _unsupported(token, "Nested links")
    attributes = children[0].attrs
    if set(attributes) != {"href"}:
        raise _unsupported(token, "Link titles and other link attributes")
    if not attributes["href"].lower().startswith("https://"):
        return None, False
    label_token = Token("inline", "", 0)
    label_token.children = list(children[1:-1])
    label, scrubbed = _inline_value(label_token, allow_breaks=False)
    return {
        "type": "labelled_https_link",
        "label": _inline_plain_text(label),
        "url": attributes["href"],
    }, scrubbed or not isinstance(label, str)


def _expect(tokens: Sequence[Token], index: int, token_type: str) -> Token:
    if index >= len(tokens) or tokens[index].type != token_type:
        raise LegalMarkdownError("The Markdown document has an unsupported structure")
    return tokens[index]


def _table_cell(tokens: Sequence[Token], index: int, cell_type: str) -> tuple[LegalInlineValue, int, bool]:
    open_token = _expect(tokens, index, f"{cell_type}_open")
    inline = _expect(tokens, index + 1, "inline")
    _expect(tokens, index + 2, f"{cell_type}_close")
    value, scrubbed = _inline_value(inline, allow_breaks=False)
    return value, index + 3, scrubbed or bool(open_token.attrs)


def _table_row(tokens: Sequence[Token], index: int, cell_type: str) -> tuple[list[LegalInlineValue], int, bool]:
    _expect(tokens, index, "tr_open")
    index += 1
    cells: list[LegalInlineValue] = []
    scrubbed = False
    while index < len(tokens) and tokens[index].type != "tr_close":
        cell, index, cell_scrubbed = _table_cell(tokens, index, cell_type)
        cells.append(cell)
        scrubbed = scrubbed or cell_scrubbed
    _expect(tokens, index, "tr_close")
    return cells, index + 1, scrubbed


def _table_block(tokens: Sequence[Token], index: int) -> tuple[dict[str, object], int, bool]:
    _expect(tokens, index, "table_open")
    _expect(tokens, index + 1, "thead_open")
    headers, index, scrubbed = _table_row(tokens, index + 2, "th")
    _expect(tokens, index, "thead_close")
    _expect(tokens, index + 1, "tbody_open")
    index += 2
    rows: list[list[LegalInlineValue]] = []
    while index < len(tokens) and tokens[index].type != "tbody_close":
        row, index, row_scrubbed = _table_row(tokens, index, "td")
        rows.append(row)
        scrubbed = scrubbed or row_scrubbed
    _expect(tokens, index, "tbody_close")
    _expect(tokens, index + 1, "table_close")
    return {"type": "table", "headers": headers, "rows": rows}, index + 2, scrubbed


def parse_legal_markdown_result(source: str) -> LegalMarkdownParseResult:
    """Parse Markdown into canonical legal blocks and report harmless formatting removal."""

    if len(source.encode("utf-8")) > MAX_LEGAL_BLOCK_JSON_BYTES:
        raise LegalMarkdownError("Legal document Markdown exceeds the 64 KiB limit")

    tokens = _MARKDOWN.parse(source)
    blocks: list[dict[str, object]] = []
    scrubbed = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == "heading_open":
            inline = _expect(tokens, index + 1, "inline")
            _expect(tokens, index + 2, "heading_close")
            value, inline_scrubbed = _inline_value(inline, allow_breaks=False)
            blocks.append({"type": "heading", "text": value})
            scrubbed = scrubbed or inline_scrubbed
            index += 3
            continue

        if token.type == "paragraph_open":
            inline = _expect(tokens, index + 1, "inline")
            _expect(tokens, index + 2, "paragraph_close")
            link, link_scrubbed = _standalone_link(inline)
            if link is not None:
                blocks.append(link)
                scrubbed = scrubbed or link_scrubbed
            else:
                value, inline_scrubbed = _inline_value(inline, allow_breaks=True)
                blocks.append({"type": "paragraph", "text": value})
                scrubbed = scrubbed or inline_scrubbed
            index += 3
            continue

        if token.type == "bullet_list_open":
            items: list[LegalInlineValue] = []
            index += 1
            while index < len(tokens) and tokens[index].type != "bullet_list_close":
                item_open = _expect(tokens, index, "list_item_open")
                _expect(tokens, index + 1, "paragraph_open")
                inline = _expect(tokens, index + 2, "inline")
                _expect(tokens, index + 3, "paragraph_close")
                _expect(tokens, index + 4, "list_item_close")
                value, inline_scrubbed = _inline_value(inline, allow_breaks=True)
                items.append(value)
                scrubbed = scrubbed or inline_scrubbed
                index += 5
                if index < len(tokens) and tokens[index].type == "bullet_list_open":
                    raise _unsupported(item_open, "Nested lists")
            _expect(tokens, index, "bullet_list_close")
            blocks.append({"type": "bullet_list", "items": items})
            index += 1
            continue

        if token.type == "table_open":
            block, index, table_scrubbed = _table_block(tokens, index)
            blocks.append(block)
            scrubbed = scrubbed or table_scrubbed
            continue

        if token.type == "hr":
            scrubbed = True
            index += 1
            continue

        descriptions = {
            "ordered_list_open": "Numbered lists",
            "blockquote_open": "Block quotes",
            "fence": "Code blocks",
            "code_block": "Code blocks",
        }
        raise _unsupported(token, descriptions.get(token.type, "This Markdown block"))

    try:
        content = LegalDocumentContent.model_validate({"blocks": blocks})
    except ValidationError as exc:
        detail = str(exc.errors(include_input=False)[0].get("msg") or "Legal Markdown is invalid")
        raise LegalMarkdownError(detail.removeprefix("Value error, ")) from exc
    return LegalMarkdownParseResult(content=content, scrubbed_formatting=scrubbed)


def parse_legal_markdown(source: str) -> LegalDocumentContent:
    """Parse Markdown into canonical legal-content blocks."""

    return parse_legal_markdown_result(source).content


_MARKDOWN_PUNCTUATION_RE = re.compile(r"([\\`*_[\]{}<>#+\-.!|])")


def _escape_plain_text(value: str) -> str:
    lines = value.split("\n")
    last_index = len(lines) - 1
    return "\n".join(
        "\\"
        if line == "" and 0 < index < last_index
        else _MARKDOWN_PUNCTUATION_RE.sub(r"\\\1", line)
        for index, line in enumerate(lines)
    )


def _inline_to_markdown(value: LegalInlineValue) -> str:
    if isinstance(value, str):
        return _escape_plain_text(value)
    markers = {
        "text": ("", ""),
        "italic": ("_", "_"),
        "bold": ("**", "**"),
        "bold_italic": ("***", "***"),
    }
    parts: list[str] = []
    for run in value:
        if run.type == "link":
            parts.append(f"[{_escape_link_label(run.text)}]({_escape_link_url(run.url)})")
            continue
        prefix, suffix = markers[run.type]
        parts.append(f"{prefix}{_escape_plain_text(run.text)}{suffix}")
    return "".join(parts)


def _escape_link_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _escape_link_url(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def legal_content_to_markdown(content: LegalDocumentContent) -> str:
    """Return a stable Markdown editing representation of validated blocks."""

    sections: list[str] = []
    for block in content.blocks:
        if block.type == "heading":
            sections.append(f"## {_inline_to_markdown(block.text)}")
        elif block.type == "paragraph":
            sections.append(_inline_to_markdown(block.text))
        elif block.type == "bullet_list":
            bullet_lines: list[str] = []
            for item in block.items:
                item_lines = _inline_to_markdown(item).split("\n")
                bullet_lines.append(f"- {item_lines[0]}")
                bullet_lines.extend(f"  {line}" for line in item_lines[1:])
            sections.append("\n".join(bullet_lines))
        elif block.type == "table":
            header = "| " + " | ".join(_inline_to_markdown(value) for value in block.headers) + " |"
            separator = "| " + " | ".join("---" for _ in block.headers) + " |"
            rows = [
                "| " + " | ".join(_inline_to_markdown(value) for value in row) + " |"
                for row in block.rows
            ]
            sections.append("\n".join([header, separator, *rows]))
        elif block.type == "labelled_https_link":
            sections.append(f"[{_escape_link_label(block.label)}]({_escape_link_url(block.url)})")
    return "\n\n".join(sections)
