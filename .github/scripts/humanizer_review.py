#!/usr/bin/env python3
"""Read-only, language-specific Humanizer PR review for Svesnost.

Only authored article prose is sent to language engines. The whole frontmatter,
code, JSX/MDX tags and props, expressions, URLs and Markdown syntax are excluded.
No source file is ever rewritten.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

MARKER = "<!-- svesnost-humanizer-review -->"
DOCS = "src/content/docs/"
CYRILLIC = re.compile(r"[\u0400-\u04ff]")
FENCE = re.compile(r"^ {0,3}(" + chr(96) + r"{3,}|~{3,})")
REFERENCE = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:[ \t]*(?=\S)")
MODULE = re.compile(r"^\s*(?:import|export)\s+")
RAW_ELEMENTS = frozenset({
    "script", "style", "codeblock", "pre", "code",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "smarttable", "structtable", "supabasetable", "sanitytable",
})
TABLE_ELEMENTS = frozenset({
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "smarttable", "structtable", "supabasetable", "sanitytable",
})


def language(path: str) -> str | None:
    if not path.startswith(DOCS) or not path.endswith((".md", ".mdx")):
        return None
    if path.startswith(DOCS + "ru/"):
        return "ru"
    if path.startswith(DOCS + "en/"):
        return None
    return "sr"


def body_without_frontmatter(raw: str) -> str:
    lines = raw.lstrip("\ufeff").splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n").rstrip(" \t") != "---":
        return "".join(lines)
    for index in range(1, len(lines)):
        # A frontmatter closing delimiter starts at column zero. Indented
        # --- and ... can be literal content of a YAML block scalar.
        delimiter = lines[index].rstrip("\r\n").rstrip(" \t")
        if delimiter in {"---", "..."}:
            return "".join(lines[index + 1:])
    return ""  # Fail closed: never treat unterminated YAML as prose.


def hide(chars: list[str], start: int, end: int) -> None:
    for pos in range(start, min(len(chars), end)):
        if chars[pos] != "\n":
            chars[pos] = " "


def is_markdown_escaped(text: str, pos: int) -> bool:
    """A delimiter is escaped iff an odd number of backslashes precedes it."""
    backslashes = 0
    pos -= 1
    while pos >= 0 and text[pos] == "\\":
        backslashes += 1
        pos -= 1
    return backslashes % 2 == 1


def skip_js_regex(text: str, start: int) -> int | None:
    """Scan a JS regex literal, respecting escapes and [...] character classes."""
    preceding = text[:start].rstrip()
    if (preceding and preceding[-1] not in "=([{,:;!?|&+-*%^~<>"
            and not re.search(r"\b(?:return|case|throw|yield|await|of|in)\s*$", preceding)):
        return None
    index = start + 1
    in_class = False
    while index < len(text):
        char = text[index]
        if char in "\r\n":
            return None
        if char == "\\":
            index += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]" and in_class:
            in_class = False
        elif char == "/" and not in_class:
            index += 1
            while index < len(text) and text[index].isalpha():
                index += 1
            return index
        index += 1
    return None


def end_brace(text: str, start: int) -> int:
    depth = 0
    quote = ""
    escaped = False
    pos = start
    while pos < len(text):
        char = text[pos]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif text.startswith("//", pos):
            newline = text.find("\n", pos + 2)
            pos = len(text) if newline < 0 else newline
            continue
        elif text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            pos = len(text) if end < 0 else end + 2
            continue
        elif char in ("'", '"', chr(96)):
            quote = char
        elif char == "/":
            regex_end = skip_js_regex(text, pos)
            if regex_end is not None:
                pos = regex_end
                continue
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    return len(text)


def end_tag(text: str, start: int) -> int:
    pos = start + 1
    quote = ""
    escaped = False
    while pos < len(text):
        char = text[pos]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in ("'", '"', chr(96)):
            quote = char
        elif char == "{":
            pos = end_brace(text, pos)
            continue
        elif char == ">":
            return pos + 1
        pos += 1
    return len(text)



def _table_cells(line: str) -> list[str] | None:
    """Split GFM cells on unescaped pipes outside matched backtick code spans."""
    row = line.strip()
    cells = []
    cell_start = 0
    delimiters = []
    i = 0
    while i < len(row):
        if row[i] == "\\" and i + 1 < len(row):
            i += 2
            continue
        if row[i] == chr(96):
            ticks = len(row[i:]) - len(row[i:].lstrip(chr(96)))
            j = i + ticks
            closing = None
            while j < len(row):
                if row[j] == "\\" and j + 1 < len(row):
                    j += 2
                elif row[j] == chr(96):
                    close_ticks = len(row[j:]) - len(row[j:].lstrip(chr(96)))
                    if close_ticks == ticks:
                        closing = j + close_ticks
                        break
                    j += close_ticks
                else:
                    j += 1
            if closing is not None:
                i = closing
                continue
            i += ticks
            continue
        if row[i] == "|":
            cells.append(row[cell_start:i].strip())
            delimiters.append(i)
            cell_start = i + 1
        i += 1
    if not delimiters:
        return None
    cells.append(row[cell_start:].strip())
    if delimiters[0] == 0:
        cells.pop(0)
    if delimiters[-1] == len(row) - 1:
        cells.pop()
    return cells



def _unquote_line(line: str) -> tuple[str, int]:
    """Remove Markdown blockquote container markers, preserving nested depth."""
    content = line.rstrip("\r\n")
    depth = 0
    while match := re.match(r"^ {0,3}>[ \t]?", content):
        content = content[match.end():]
        depth += 1
    return content, depth


def _fence_line(line: str, list_indents: list[tuple[int, int]]) -> tuple[str, int]:
    """The actual content line after blockquote or list indentation.

    A four-space standalone code block must not start a fenced block, but
    four-space indentation within a Markdown list is valid fence syntax.
    """
    visible, quote_depth = _unquote_line(line)
    if quote_depth:
        return visible, quote_depth
    indent = len(line) - len(line.lstrip(" "))
    if indent <= 3:
        return line.rstrip("\r\n"), 0
    if list_indents and indent >= list_indents[-1][1]:
        return line.lstrip(" ").rstrip("\r\n"), 0
    return "", 0


def mask_gfm_tables(text: str, chars: list[str]) -> None:
    """Mask complete GFM tables, not just separator rows and pipe syntax."""
    lines = text.splitlines(keepends=True)
    starts = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)

    for index in range(1, len(lines)):
        header_text, depth = _unquote_line(lines[index - 1])
        separator_text, separator_depth = _unquote_line(lines[index])
        if depth != separator_depth:
            continue
        header = _table_cells(header_text)
        separators = _table_cells(separator_text)
        if not header or not separators or len(header) != len(separators):
            continue
        if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separators):
            continue
        # A header fully hidden by fenced/indented code is not a table.
        start = starts[index - 1]
        if not any(char.strip() for char in chars[start:starts[index]]):
            continue
        end_index = index + 1
        while end_index < len(lines):
            row_text, row_depth = _unquote_line(lines[end_index])
            # A GFM row may omit pipes entirely (only its first cell then
            # contains text). The table ends at a blank line, a new block,
            # or when the surrounding blockquote changes depth.
            if row_depth != depth or not row_text.strip():
                break
            if re.match(
                r"^ {0,3}(?:#{1,6}(?:[ \t]|$)|"
                r"(?:[-*+]|\d+[.)])[ \t]+|"
                r"`{3,}|~{3,}|"
                r"(?:---|\*\*\*|___)[ \t]*$|"
                # Inline tags <em>, <strong>, <a> remain table cell data.
                r"<(?:!--|/?(?:address|article|aside|blockquote|body|"
                r"center|details|div|dl|figure|footer|form|h[1-6]|header|"
                r"hr|html|iframe|li|main|nav|ol|p|pre|script|section|"
                r"style|table|tbody|td|tfoot|th|thead|tr|ul)\b))",
                row_text
            ):
                break
            row_start = starts[end_index]
            if not any(c.strip() for c in chars[row_start:row_start + len(lines[end_index])]):
                break
            end_index += 1
        for line_index in range(index - 1, end_index):
            hide(chars, starts[line_index], starts[line_index] + len(lines[line_index]))


def mask_inline_dollar_math(chars: list[str]) -> None:
    """Mask $...$ math without consuming escaped dollars or unpaired currency."""
    text = "".join(chars)

    def escaped(pos: int) -> bool:
        backslashes = 0
        pos -= 1
        while pos >= 0 and text[pos] == "\\":
            backslashes += 1
            pos -= 1
        return backslashes % 2 == 1

    def starts_currency(pos: int) -> bool:
        """Protect a dollar-prefixed amount from being read as a math opener.

        In "$100–$200" and "$5/$10" both dollar signs begin new amounts.
        Arithmetic such as "$2+2$" and "$E=mc^2$" remains eligible as math.
        """
        match = re.match(r"\d+(?:[.,]\d+)*", text[pos + 1:])
        if match is None:
            return False
        end = pos + 1 + match.end()
        trailing = text[end:end + 1]
        if trailing in ("/", "–", "—", "-"):
            next_dollar = text.find("$", end + 1)
            return (next_dollar >= 0 and "\n" not in text[end:next_dollar]
                    and next_dollar + 1 < len(text)
                    and text[next_dollar + 1].isdigit() and not escaped(next_dollar))
        if trailing in ("+", "*", "^", "=", "$"):
            return False
        if trailing and trailing.isalpha():
            return False
        return not trailing or trailing.isspace() or trailing in ",.;:!?)]}"

    pos = 0
    while pos < len(text):
        if (text[pos] != "$" or escaped(pos) or starts_currency(pos)
                or (pos > 0 and text[pos - 1] == "$")
                or (pos + 1 < len(text) and (text[pos + 1] == "$" or text[pos + 1].isspace()))):
            pos += 1
            continue
        end = pos + 1
        while end < len(text) and text[end] != "\n":
            if text[end] == "$" and not escaped(end) and (end + 1 == len(text) or text[end + 1] != "$"):
                if not text[end - 1].isspace():
                    hide(chars, pos, end + 1)
                    pos = end + 1
                    break
            end += 1
        else:
            pos += 1


def valid_inline_link_payload(payload: str) -> bool:
    """Accept only a CommonMark destination and optional quoted title."""
    if "\n\n" in payload or "\r\n\r\n" in payload:
        return False
    value = payload.lstrip(" \t")
    if not value:
        return True
    if value[0] == "<":
        end = 1
        while end < len(value) and value[end] != ">":
            if value[end] == "\\" and end + 1 < len(value):
                end += 2
                continue
            if value[end] in "<\n\r":
                return False
            end += 1
        if end == len(value):
            return False
        end += 1
    else:
        end = 0
        nesting = 0
        while end < len(value) and not value[end].isspace():
            if value[end] == "\\" and end + 1 < len(value):
                end += 2
                continue
            if value[end] == "<":
                return False
            if value[end] == "(":
                nesting += 1
            elif value[end] == ")":
                nesting -= 1
                if nesting < 0:
                    return False
            end += 1
        if nesting:
            return False
    trailer = value[end:]
    if not trailer:
        return True
    if not trailer[0].isspace():
        return False
    title = trailer.strip()
    if not title:
        return True
    if title[0] not in ("'", '"', "("):
        return False
    closing = ")" if title[0] == "(" else title[0]
    index = 1
    while index < len(title):
        if title[index] == "\\" and index + 1 < len(title):
            index += 2
            continue
        if title[index] == closing:
            return not title[index + 1:].strip()
        index += 1
    return False


def markdown_inline_links(text: str) -> list[tuple[int, int, int, int, bool]]:
    """Find complete inline links/images, balancing destination parentheses.

    Return (start, end, label_start, label_end, is_image) for valid spans.
    The title may be quoted, so a ')' inside a title is not a link closer.
    """
    spans = []
    pos = 0
    while pos < len(text):
        image = text.startswith("![", pos)
        bracket = pos + 1 if image else pos
        if (bracket >= len(text) or text[bracket] != "["
                or is_markdown_escaped(text, bracket)):
            pos += 1
            continue
        label_start = bracket + 1
        cursor = label_start
        nested = 1
        while cursor < len(text):
            # Link labels may contain soft line breaks, but not blank lines
            # (which would end the surrounding Markdown paragraph).
            if text[cursor] == "\n" and (
                cursor + 1 >= len(text)
                or text[cursor + 1] == "\n"
                or text[cursor + 1] == "\r"
            ):
                break
            if text[cursor] == "\\" and cursor + 1 < len(text):
                cursor += 2
                continue
            if text[cursor] == "[":
                nested += 1
            elif text[cursor] == "]":
                nested -= 1
                if nested == 0:
                    break
            cursor += 1
        if nested or cursor + 1 >= len(text) or text[cursor + 1] != "(":
            pos += 1
            continue
        label_end = cursor
        cursor += 2
        parens = 1
        title_quote = None
        angle_destination = False
        destination_started = False
        while cursor < len(text):
            char = text[cursor]
            if char == "\n":
                # CommonMark allows a title on the following line (but not
                # a line break inside an unquoted link destination).
                next_line = text[cursor + 1:].split("\n", 1)[0]
                if not title_quote and not re.match(r"[ \t]*[\"']", next_line):
                    cursor = len(text)
                    continue
            if char == "\\" and cursor + 1 < len(text):
                cursor += 2
                continue
            if angle_destination:
                # A <destination> may contain unbalanced parentheses; only
                # the closing > ends it, not an intervening ).
                if char == ">":
                    angle_destination = False
                cursor += 1
                continue
            if char == "<" and not destination_started and not title_quote:
                angle_destination = True
                destination_started = True
                cursor += 1
                continue
            if not char.isspace():
                destination_started = True
            if title_quote:
                if char == title_quote:
                    title_quote = None
            elif char in ("'", '"') and cursor > label_end + 2 and text[cursor - 1].isspace():
                title_quote = char
            elif char == "(":
                parens += 1
            elif char == ")":
                parens -= 1
                if parens == 0:
                    if not valid_inline_link_payload(text[label_end + 2:cursor]):
                        pos += 1
                        break
                    spans.append((pos, cursor + 1, label_start, label_end, image))
                    # Inspect the label as well: [![alt](src)](href) contains
                    # an image whose alt must be excluded independently.
                    pos = label_start
                    break
            cursor += 1
        else:
            pos += 1
    return spans


def mask_inline_link_targets(text: str, chars: list[str]) -> None:
    """Keep visible link labels, mask the full balanced target and title."""
    for start, end, label_start, label_end, image in markdown_inline_links(text):
        if image:
            hide(chars, start, end)
        else:
            hide(chars, start, label_start)
            hide(chars, label_end, end)


def normalize_reference_label(label: str) -> str:
    return re.sub(r"\s+", " ", label).strip().casefold()


def mask_reference_definitions(text: str, chars: list[str]) -> set[str]:
    """Mask complete CommonMark link-reference definitions, not paragraph text.

    A definition cannot interrupt a paragraph. Its destination can follow
    the colon on the next line, and the optional title may span several lines.
    Blockquote prefixes belong to the container, not the reference value.
    """
    labels: set[str] = set()
    lines = "".join(chars).splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    def visible_line(index: int) -> tuple[str, int]:
        content, depth = _unquote_line(lines[index])
        return content.rstrip("\r\n"), depth

    def title_end(start_line: int, pos: int, depth: int) -> int | None:
        source, _ = visible_line(start_line)
        opening = source[pos]
        closing = ")" if opening == "(" else opening
        for number in range(start_line, len(lines)):
            content, actual_depth = visible_line(number)
            if actual_depth != depth or not content.strip():
                return None
            cursor = pos + 1 if number == start_line else 0
            while cursor < len(content):
                if content[cursor] == "\\" and cursor + 1 < len(content):
                    cursor += 2
                    continue
                if content[cursor] == closing:
                    return number if not content[cursor + 1:].strip() else None
                cursor += 1
        return None

    paragraph_depth: int | None = None
    index = 0
    while index < len(lines):
        line, depth = visible_line(index)
        stripped = line.strip()
        if not stripped:
            paragraph_depth = None
            index += 1
            continue
        if paragraph_depth is not None and paragraph_depth != depth:
            paragraph_depth = None
        # Markdown heading, list, thematic break and JSX block boundaries
        # interrupt the preceding paragraph; reference definitions do not.
        structural = re.match(
            r"^ {0,3}(?:#{1,6}[ \t]|[-*+][ \t]|\d+[.)][ \t]|"
            r"<(?:/?[A-Za-z]|!--)|(?:---|\*\*\*|___)[ \t]*$)",
            line,
        )
        if structural:
            paragraph_depth = None
        if paragraph_depth is not None:
            index += 1
            continue

        marker = re.match(r"^ {0,3}\[([^\]\n]+)\]:[ \t]*(.*)$", line)
        if not marker or marker.group(1).startswith("^"):
            paragraph_depth = None if structural else depth
            index += 1
            continue

        value = marker.group(2)
        end_index = index
        if not value.strip():
            if index + 1 >= len(lines):
                paragraph_depth = depth
                index += 1
                continue
            following, following_depth = visible_line(index + 1)
            if (following_depth != depth or not following.strip()
                    or len(following) - len(following.lstrip(" ")) > 3):
                paragraph_depth = depth
                index += 1
                continue
            end_index = index + 1
            value = following.lstrip(" \t")

        # Separate one destination token from the optional title. A quoted
        # destination <...> may contain spaces; a bare destination may not.
        if value.startswith("<"):
            match = re.match(r"^<[^<>\r\n]+>", value)
        else:
            match = re.match(r"^[^\s<>]+", value)
        if not match:
            paragraph_depth = depth
            index += 1
            continue
        tail = value[match.end():]
        if tail.strip():
            if not tail[0].isspace() or tail.lstrip()[0] not in ("'", '"', "("):
                paragraph_depth = depth
                index += 1
                continue
            # title_end needs the position in the original visible line.
            source, _ = visible_line(end_index)
            opener = source.find(tail.lstrip(), source.find(match.group(0)))
            if opener < 0:
                paragraph_depth = depth
                index += 1
                continue
            last_title = title_end(end_index, opener, depth)
            if last_title is None:
                paragraph_depth = depth
                index += 1
                continue
            end_index = last_title
        elif end_index + 1 < len(lines):
            continuation, continuation_depth = visible_line(end_index + 1)
            title_match = re.match(r"^ {1,3}([\"'(])", continuation)
            if continuation_depth == depth and title_match:
                last_title = title_end(end_index + 1, title_match.start(1), depth)
                if last_title is not None:
                    end_index = last_title

        for line_index in range(index, end_index + 1):
            hide(chars, offsets[line_index], offsets[line_index] + len(lines[line_index]))
        labels.add(normalize_reference_label(marker.group(1)))
        paragraph_depth = None
        index = end_index + 1
    return labels

def mask_shortcut_reference_images(chars: list[str], labels: set[str]) -> None:
    """Only resolved ![alt] shortcuts are images; unknown labels stay text."""
    if not labels:
        return
    source = "".join(chars)
    for match in re.finditer(r"!\[([^\]\n]+)\](?!\(|\[)", source):
        if normalize_reference_label(match.group(1)) in labels:
            hide(chars, *match.span())


def mask_bare_urls(chars: list[str]) -> None:
    """Mask bare URLs through balanced parentheses without swallowing prose."""
    text = "".join(chars)
    for match in re.finditer(r"https?://", text):
        start = match.start()
        end = match.end()
        parens = 0
        while end < len(text):
            char = text[end]
            if char.isspace() or char in '<>"' + "'[]":
                break
            if char == "(":
                parens += 1
            elif char == ")":
                if parens == 0:
                    break
                parens -= 1
            elif char == "\\" and end + 1 < len(text):
                end += 2
                continue
            end += 1
        hide(chars, start, end)


def mask_inline_paren_math(chars: list[str]) -> None:
    """Exclude balanced TeX \(...\), not escaped Markdown literal \\(."""
    text = "".join(chars)
    pos = 0
    while True:
        start = text.find(r"\(", pos)
        if start < 0:
            return
        if is_markdown_escaped(text, start):
            pos = start + 2
            continue
        close = text.find(r"\)", start + 2)
        while close >= 0 and is_markdown_escaped(text, close):
            close = text.find(r"\)", close + 2)
        if close < 0:
            pos = start + 2
            continue
        hide(chars, start, close + 2)
        pos = close + 2



def find_matching_raw_close(text: str, opening_end: int, tag: str) -> int | None:
    """Find the corresponding closing tag, respecting nested same-name tags."""
    tag_pattern = re.compile(r"</?([A-Za-z][\w.:-]*)(?=[\s/>])")
    depth = 1
    pos = opening_end
    while pos < len(text):
        start = text.find("<", pos)
        if start < 0:
            return None
        if text.startswith("<!--", start):
            comment_end = text.find("-->", start + 4)
            if comment_end < 0:
                return None
            pos = comment_end + 3
            continue
        match = tag_pattern.match(text, start)
        if not match:
            pos = start + 1
            continue
        # Consume a complete element tag before matching names, so
        # </table> and <!-- inside quoted attributes cannot act as tokens.
        end = end_tag(text, start)
        if end <= start or end == len(text) and ">" not in text[start:end]:
            return None
        if match.group(1).casefold() == tag.casefold():
            if text.startswith("</", start):
                depth -= 1
                if depth == 0:
                    return end
            elif not text[start:end].rstrip().endswith("/>"):
                depth += 1
        pos = end
    return None


def mask_fenced_blocks(text: str, chars: list[str]) -> None:
    """Mask code fences including list-indented and quoted Markdown fences.

    Existing masks (e.g. raw HTML code containers) are respected, so fences
    inside them cannot hide following reader-visible prose.
    """
    offset = 0
    in_fence = None
    in_comment = False
    list_indents: list[tuple[int, int]] = []
    # Comments are masked before code fences, but markers inside an already
    # open fence are code literals rather than HTML comments.
    comment_lines = iter(html_comment_scan_view("".join(chars)).splitlines(keepends=True))
    for original_line in "".join(chars).splitlines(keepends=True):
        comment_line = next(comment_lines)
        line = original_line
        if not in_fence:
            line, in_comment = mask_html_comments_in_line(
                comment_line, chars, offset, in_comment
            )
        trimmed = line.lstrip()
        indent = len(line) - len(trimmed)
        if not in_fence and not in_comment:
            marker = re.match(r"^( *)(?:[-*+]|\d+[.)])\s+", line)
            if marker:
                if indent < 4 or list_indents:
                    while list_indents and list_indents[-1][0] >= indent:
                        list_indents.pop()
                    list_indents.append((indent, len(marker.group(0))))
            elif trimmed.strip() and list_indents and indent < list_indents[0][1]:
                list_indents.clear()
        fence_text, quote_depth = _fence_line(line, list_indents)
        fence = FENCE.match(fence_text)
        if in_fence:
            hide(chars, offset, offset + len(line))
            if (quote_depth == in_fence[2] and fence
                    and fence.group(1)[0] == in_fence[0]
                    and len(fence.group(1)) >= in_fence[1]
                    and not fence_text[fence.end():].strip()):
                in_fence = None
        elif not in_comment and fence:
            token = fence.group(1)
            in_fence = (token[0], len(token), quote_depth)
            hide(chars, offset, offset + len(line))
        offset += len(line)


def mdx_container_prose_lines(text: str) -> set[int]:
    """Find reader-facing Markdown indented inside non-raw JSX containers.

    MDX components like Aside, Steps and Accordion nest Markdown children.
    Their formatting indentation is not an independent four-space code
    block. Preserve ordinary prose and list items up to six (or more
    relative) spaces, but leave substantially deeper indented code to the
    Markdown code scanner after a block boundary.
    """
    view_chars = list(text)
    mask_fenced_blocks(text, view_chars)
    view = "".join(view_chars)
    result: set[int] = set()
    containers: list[tuple[str, int]] = []
    offset = 0
    for line in view.splitlines(keepends=True):
        visible = line.rstrip("\r\n")
        indent = len(visible) - len(visible.lstrip(" "))
        match = re.match(r"^[ \t]*<(/?)([A-Z][\w.:-]*)(?=[\s/>])", visible)
        if match and match.group(2).lower() not in RAW_ELEMENTS:
            tag = match.group(2)
            if match.group(1):
                for index in range(len(containers) - 1, -1, -1):
                    if containers[index][0] == tag:
                        containers = containers[:index]
                        break
            elif containers or indent <= 3:
                end = end_tag(visible, visible.find("<", match.start(), match.end()))
                if not visible[:end].rstrip().endswith("/>"):
                    containers.append((tag, indent))
        elif (containers and visible.strip() and indent >= 4
              and indent < containers[-1][1] + 8):
            result.add(offset)
        offset += len(line)
    return result


def ordinary_paragraph_continuations(text: str) -> set[int]:
    """Offsets of indented lines continuing a paragraph without a blank line.

    CommonMark treats four leading spaces as ordinary continuation text when
    a paragraph is already open. A blank line resets this context, allowing
    subsequent four-space lines to start an indented code block.
    """
    offsets: set[int] = set()
    paragraph_open = False
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        _, quote_depth = _unquote_line(line)
        if not stripped or quote_depth:
            paragraph_open = False
        elif indent >= 4:
            if paragraph_open:
                offsets.add(offset)
            else:
                paragraph_open = False
        else:
            paragraph_open = not bool(re.match(
                r"^ {0,3}(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|"
                r"[" + chr(96) + r"~]{3,}|"
                r"<(?:/?[A-Za-z]|!--)|\[\^[^\]]+\]:|"
                r"(?:import|export)\s+|(?:---|\*\*\*|___)\s*$)",
                line
            ))
        offset += len(line)
    return offsets


def mask_indented_code_before_blocks(text: str, chars: list[str],
                                     paragraph_lines: set[int],
                                     mdx_prose_lines: set[int]) -> None:
    """Hide indented code before detecting ESM declarations or HTML raw tags.

    Preserve nested list prose and multiline footnote continuations. Code
    inside lists starts after a blank line at list-content-indent + 4 and
    continues until the next nonblank line dedents.
    """
    offset = 0
    list_indents: list[tuple[int, int]] = []
    list_code_indent: int | None = None
    after_blank = False
    in_footnote = False
    for line in "".join(chars).splitlines(keepends=True):
        trimmed = line.lstrip()
        indent = len(line) - len(trimmed)
        visible, quote_depth = _unquote_line(line)
        quote_indent = len(visible) - len(visible.lstrip(" "))
        if list_code_indent is not None:
            if not trimmed.strip() or indent >= list_code_indent:
                hide(chars, offset, offset + len(line))
                offset += len(line)
                continue
            list_code_indent = None

        footnote = re.match(r"^ {0,3}\[\^[^\]\n]+\]:[ \t]*", line)
        if footnote:
            in_footnote = True
        elif trimmed.strip() and indent < 4:
            in_footnote = False

        marker = re.match(r"^( *)(?:[-*+]|\d+[.)])\s+", line)
        if not trimmed.strip():
            after_blank = bool(list_indents)
        elif footnote:
            after_blank = False
        elif in_footnote and 4 <= indent < 8:
            after_blank = False
        elif marker:
            if indent >= 4 and not list_indents and offset not in mdx_prose_lines:
                hide(chars, offset, offset + len(line))
            else:
                while list_indents and list_indents[-1][0] >= indent:
                    list_indents.pop()
                list_indents.append((indent, len(marker.group(0))))
            after_blank = False
        elif quote_depth and quote_indent >= 4:
            hide(chars, offset, offset + len(line))
            after_blank = False
        elif indent >= 4:
            if not list_indents:
                if offset not in paragraph_lines and offset not in mdx_prose_lines:
                    hide(chars, offset, offset + len(line))
            elif after_blank and indent >= list_indents[-1][1] + 4:
                list_code_indent = list_indents[-1][1] + 4
                hide(chars, offset, offset + len(line))
            after_blank = False
        else:
            if list_indents and indent < list_indents[0][1]:
                list_indents.clear()
            after_blank = False
        offset += len(line)


def mask_mdx_esm(text: str, chars: list[str]) -> None:
    """Exclude whole top-level import/export declarations, including newlines.

    This is a conservative JS lexical boundary scan rather than an MDX AST:
    quoted strings, comments and balanced {}, [] and () cannot end a module
    declaration. Once balanced, the declaration ends at its line boundary.
    """
    active = False
    stack: list[str] = []
    quote = ""
    block_comment = False
    offset = 0
    lines = "".join(chars).splitlines(keepends=True)
    for line_index, line in enumerate(lines):
        if not active:
            if not MODULE.match(line):
                offset += len(line)
                continue
            active = True
        hide(chars, offset, offset + len(line))
        i = 0
        while i < len(line):
            char = line[i]
            if block_comment:
                if line.startswith("*/", i):
                    block_comment = False
                    i += 2
                    continue
            elif quote:
                if char == "\\" and i + 1 < len(line):
                    i += 2
                    continue
                if char == quote:
                    quote = ""
            elif line.startswith("//", i):
                break
            elif line.startswith("/*", i):
                block_comment = True
                i += 2
                continue
            elif char in ("'", '"', chr(96)):
                quote = char
            elif char in "([{":
                stack.append({"(": ")", "[": "]", "{": "}"}[char])
            elif char in ")]}" and stack and char == stack[-1]:
                stack.pop()
            i += 1
        trimmed = line.rstrip()
        continued = (trimmed.endswith(("=", "=>", ",", ".", "\\", "(", "[", "{"))
                     or bool(re.search(r"\b(?:from|as|default)\s*$", trimmed)))
        # 'import Widget\nfrom "./Widget.jsx"' and
        # 'export { Widget }\nfrom "./Widget.jsx"' are valid module
        # declarations. A balanced first line does not finish the statement
        # when the next line supplies its from-clause.
        if (not continued and line_index + 1 < len(lines)
                and re.match(r"^[ \t]*from[ \t]+", lines[line_index + 1])
                and re.match(r"^[ \t]*(?:import|export)\b", line)):
            continued = True
        if not stack and not quote and not block_comment and not continued:
            active = False
        offset += len(line)


def mask_raw_elements_before_blocks(text: str, chars: list[str]) -> None:
    """Hide raw HTML/JSX code and table containers before Markdown blocks.

    A shadow view protects fenced code, inline code, comments and link targets
    from being mistaken for raw element openers. All offsets remain unchanged.
    """
    view_chars = list("".join(chars))
    mask_inline_link_targets(text, view_chars)
    # Use the same list-aware fence rules as the main Markdown scanner.
    # This shadow mask prevents a literal <pre> within fenced code from being
    # mistaken for an opening raw element.
    mask_fenced_blocks(text, view_chars)
    # Raw-looking tags inside MDX expressions or ESM string/object content
    # are JavaScript literals, not HTML. Shield them in this *view* only:
    # real masks remain owned by prose_only's subsequent ESM/MDX passes.
    expr_pos = 0
    shadow = "".join(view_chars)
    while expr_pos < len(text):
        if (shadow[expr_pos] == "{" and not is_markdown_escaped(shadow, expr_pos)):
            close = end_brace(shadow, expr_pos)
            if close > expr_pos and shadow[close - 1] == "}":
                hide(view_chars, expr_pos, close)
                expr_pos = close
                continue
        expr_pos += 1
    mask_mdx_esm(text, view_chars)
    view = "".join(view_chars)
    pos = 0
    while pos < len(text):
        if view[pos] == chr(96):
            run = len(view[pos:]) - len(view[pos:].lstrip(chr(96)))
            token = chr(96) * run
            close = view.find(token, pos + run)
            if close >= 0:
                pos = close + run
                continue
        if view.startswith("<!--", pos) and not is_markdown_escaped(view, pos):
            close = view.find("-->", pos + 4)
            pos = len(view) if close < 0 else close + 3
            continue
        if view[pos] != "<" or is_markdown_escaped(view, pos):
            pos += 1
            continue
        match = re.match(r"</?([A-Za-z][\w.:-]*)(?=[\s/>])", view[pos:])
        if not match:
            pos += 1
            continue
        tag = match.group(1)
        end = end_tag(text, pos)
        if (tag.lower() in RAW_ELEMENTS and not view.startswith("</", pos)
                and not text[pos:end].rstrip().endswith("/>")):
            close_end = find_matching_raw_close(text, end, tag)
            stop = close_end if close_end is not None else len(text)
            hide(chars, pos, stop)
            pos = stop
        else:
            pos = end


def html_comment_scan_view(raw: str) -> str:
    """Shield comment openers within inline code, MDX expressions and JSX tags.

    Only the opener is shielded: a real HTML comment can still close at -->
    even if its contents happen to include backticks or other markup.
    """
    chars = list(raw)
    # A Markdown link's destination and optional title are metadata. A literal
    # <!-- in either cannot open an HTML comment or consume later paragraphs.
    for _, end, _, label_end, _ in markdown_inline_links(raw):
        for match in re.finditer(r"<!--", raw[label_end:end]):
            hide(chars, label_end + match.start(), label_end + match.end())
    pos = 0
    while pos < len(raw):
        end = None
        if raw[pos] == chr(96):
            run = len(raw[pos:]) - len(raw[pos:].lstrip(chr(96)))
            token = chr(96) * run
            close = raw.find(token, pos + run)
            if close >= 0:
                end = close + run
        elif raw[pos] == "{" and not is_markdown_escaped(raw, pos):
            end = end_brace(raw, pos)
        elif raw[pos] == "<" and not raw.startswith("<!--", pos):
            match = re.match(r"</?([A-Za-z][\w.:-]*)(?=[\s/>])", raw[pos:])
            if match or raw.startswith(("<>", "</>"), pos):
                end = end_tag(raw, pos)
        if end is not None and end > pos:
            for match in re.finditer(r"<!--", raw[pos:end]):
                hide(chars, pos + match.start(), pos + match.end())
            pos = end
        else:
            pos += 1
    return "".join(chars)


def mask_html_comments_in_line(
    line: str, chars: list[str], offset: int, in_comment: bool
) -> tuple[str, bool]:
    """Mask HTML comments before Markdown block-state detection.

    This helper is only called outside fenced code and block math. Keeping the
    original length and newlines preserves all offsets for downstream masking.
    """
    masked = list(line)
    pos = 0
    while pos < len(line):
        if in_comment:
            start = pos
        else:
            start = line.find("<!--", pos)
            # An escaped opener is visible Markdown text, not an HTML comment.
            while start >= 0 and is_markdown_escaped(line, start):
                start = line.find("<!--", start + 4)
        if start < 0:
            break
        close = line.find("-->", start)
        end = len(line) if close < 0 else close + 3
        hide(chars, offset + start, offset + end)
        for index in range(start, end):
            if masked[index] != "\n":
                masked[index] = " "
        in_comment = close < 0
        pos = end
    return "".join(masked), in_comment


def prose_only(raw: str) -> str:
    text = body_without_frontmatter(raw)
    chars = list(text)
    # Before detecting raw HTML and ESM, exclude indented Markdown code.
    # Otherwise an indented "<pre>" or incomplete "import {" can swallow
    # the next reader-visible paragraph. Fenced code is masked before ESM.
    paragraph_lines = ordinary_paragraph_continuations(text)
    mdx_prose_lines = mdx_container_prose_lines(text)
    mask_indented_code_before_blocks(text, chars, paragraph_lines, mdx_prose_lines)
    mask_raw_elements_before_blocks(text, chars)
    mask_fenced_blocks(text, chars)
    mask_mdx_esm(text, chars)
    reference_labels = mask_reference_definitions(text, chars)
    block_text = "".join(chars)
    offset = 0
    in_fence = None
    in_math = None
    list_indents: list[tuple[int, int]] = []
    list_code_indent: int | None = None
    after_blank = False
    in_html_comment = False
    in_footnote = False
    comment_lines = iter(html_comment_scan_view(block_text).splitlines(keepends=True))

    for original_line in block_text.splitlines(keepends=True):
        # Shield inline code/MDX attributes when searching for HTML comments;
        # preserve the original source for all subsequent Markdown work.
        comment_line = next(comment_lines)
        line = original_line
        if not in_fence and not in_math:
            scanned, in_html_comment = mask_html_comments_in_line(
                comment_line, chars, offset, in_html_comment
            )
            if scanned != comment_line:
                line = scanned
        trimmed = line.lstrip()
        indent = len(line) - len(trimmed)
        visible, quote_depth = _unquote_line(line)
        quote_indent = len(visible) - len(visible.lstrip(" "))
        # Code inside a blockquote uses the indentation AFTER the > marker.
        # Leave code-fence contents and mathematical blocks to their own
        # state machines, without updating the surrounding list context.
        # A list's indented code block continues over successive lines and
        # blank lines until a nonblank line dedents below its code threshold.
        list_code_line = False
        if list_code_indent is not None:
            if not trimmed.strip() or indent >= list_code_indent:
                list_code_line = True
                hide(chars, offset, offset + len(line))
            else:
                list_code_indent = None
        if not in_fence and not in_math and not in_html_comment and not list_code_line:
            footnote_marker = re.match(r"^ {0,3}\[\^[^\]\n]+\]:[ \t]*", line)
            if footnote_marker:
                in_footnote = True
            elif trimmed.strip() and indent < 4:
                in_footnote = False
            list_marker = re.match(r"^( *)(?:[-*+]|\d+[.)])\s+", line)
            if not trimmed.strip():
                after_blank = bool(list_indents)
            elif footnote_marker:
                after_blank = False
            elif in_footnote and indent >= 4 and indent < 8:
                # A four-space continuation belongs to the visible footnote,
                # not to an independent indented code block.
                after_blank = False
            elif list_marker:
                if indent >= 4 and not list_indents and offset not in mdx_prose_lines:
                    hide(chars, offset, offset + len(line))
                else:
                    while list_indents and list_indents[-1][0] >= indent:
                        list_indents.pop()
                    list_indents.append((indent, len(list_marker.group(0))))
                after_blank = False
            elif quote_depth and quote_indent >= 4:
                hide(chars, offset, offset + len(line))
                after_blank = False
            elif indent >= 4:
                if not list_indents:
                    if offset not in paragraph_lines and offset not in mdx_prose_lines:
                        hide(chars, offset, offset + len(line))
                elif after_blank and indent >= list_indents[-1][1] + 4:
                    list_code_indent = list_indents[-1][1] + 4
                    list_code_line = True
                    hide(chars, offset, offset + len(line))
                after_blank = False
            else:
                if list_indents and indent < list_indents[0][1]:
                    list_indents.clear()
                after_blank = False

        fence_text, quote_depth = _fence_line(line, list_indents)
        fence = FENCE.match(fence_text)
        # Display math can live inside a blockquote or a list. Strip the
        # container prefix before checking its opening/closing markers.
        math_text = fence_text.lstrip()
        math_list = re.match(r"^(?:[-*+]|\d+[.)])\s+", math_text)
        if math_list:
            math_text = math_text[math_list.end():]
        # The active block wins. A math delimiter inside fenced code is code,
        # and a code fence inside a math block must not affect block state.
        if in_fence:
            hide(chars, offset, offset + len(line))
            if (quote_depth == in_fence[2] and fence
                    and fence.group(1)[0] == in_fence[0]
                    and len(fence.group(1)) >= in_fence[1]
                    and not fence_text[fence.end():].strip()):
                in_fence = None
        elif in_math:
            hide(chars, offset, offset + len(line))
            if quote_depth == in_math[1] and in_math[0] in math_text:
                in_math = None
        elif in_html_comment:
            # A comment can contain fence or math delimiters on their own
            # lines; neither may change parser state.
            pass
        elif list_code_line:
            # Fences and math markers inside indented code are code, too.
            hide(chars, offset, offset + len(line))
        elif fence:
            marker = fence.group(1)
            in_fence = (marker[0], len(marker), quote_depth)
            hide(chars, offset, offset + len(line))
        elif math_text.startswith((chr(36) * 2, r"\[")):
            hide(chars, offset, offset + len(line))
            closing = chr(36) * 2 if math_text.startswith(chr(36) * 2) else r"\]"
            if closing not in math_text[2:]:
                in_math = (closing, quote_depth)
        elif (MODULE.match(line)
              or re.match(r"^ {0,3}(?:::{1}|!{3})", line)
              or re.match(r"^ {0,3}(?:-{3,}|\*{3,}|_{3,})\s*$", line)
              or re.match(r"^\s*\|?\s*:?[-]{3,}:?\s*(?:\|.*)?$", line)):
            hide(chars, offset, offset + len(line))
        offset += len(line)

    mask_gfm_tables(text, chars)
    # Hide link targets before the HTML/MDX pass: a comment-looking title or
    # expression inside a link must never be interpreted as real markup.
    mask_inline_link_targets(text, chars)

    pos = 0
    while pos < len(text):
        if chars[pos] == " " and text[pos] != " ":
            pos += 1
            continue
        if text.startswith("<!--", pos) and not is_markdown_escaped(text, pos):
            end = text.find("-->", pos + 4)
            end = len(text) if end < 0 else end + 3
            hide(chars, pos, end)
            pos = end
            continue
        if text[pos] == "<" and not is_markdown_escaped(text, pos):
            match = re.match(r"</?([A-Za-z][\w.:-]*)(?=[\s/>])", text[pos:])
            if match or text.startswith(("<>", "</>"), pos):
                tagname = match.group(1) if match else ""
                end = end_tag(text, pos)
                closing = text.startswith("</", pos)
                selfclosing = text[pos:end].rstrip().endswith("/>")
                hide(chars, pos, end)
                if tagname.lower() in RAW_ELEMENTS and not closing and not selfclosing:
                    close_end = find_matching_raw_close(text, end, tagname)
                    if close_end is not None:
                        hide(chars, end, close_end)
                        end = close_end
                    elif tagname.lower() in TABLE_ELEMENTS:
                        # Fail closed for malformed/unclosed table elements.
                        hide(chars, end, len(text))
                        end = len(text)
                pos = end
                continue
        if text[pos] == "{" and not is_markdown_escaped(text, pos):
            end = end_brace(text, pos)
            hide(chars, pos, end)
            pos = end
            continue
        if text[pos] == chr(96):
            run = len(text[pos:]) - len(text[pos:].lstrip(chr(96)))
            token = chr(96) * run
            end = text.find(token, pos + run)
            if end >= 0:
                end += run
                hide(chars, pos, end)
                pos = end
                continue
        pos += 1

    # Inline links/images were masked using balanced syntax before MDX parsing.
    # Reference-style links and resolved shortcut-reference images are masked
    # here; unknown ![literal] labels are left as reader-visible text.
    mask_shortcut_reference_images(chars, reference_labels)
    cleaned = "".join(chars)
    # Markdown reference links preserve their visible label, not [reference-id].
    # Images are not body prose, including reference-style image labels.
    for match in re.finditer(r"(!?)\[([^\]\n]+)\]\[([^\]\n]*)\]", cleaned):
        start, end = match.span()
        if match.group(1) == "!":
            hide(chars, start, end)
        else:
            hide(chars, start, start + 1)
            hide(chars, start + 1 + len(match.group(2)), end)
    cleaned = "".join(chars)
    mask_bare_urls(chars)
    for match in re.finditer(r"\[\^[^\]\n]+\](?=:?)", cleaned):
        hide(chars, *match.span())
    for match in re.finditer(r"(?m)^ {0,3}(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s+)", cleaned):
        hide(chars, *match.span())
    mask_inline_paren_math(chars)

    mask_inline_dollar_math(chars)
    cleaned = "".join(chars)
    cleaned = re.sub(r"(?m)^[ \t]*\|?[ \t]*:?-{3,}:?[ \t|:-]*$", "", cleaned)
    cleaned = re.sub(r"[*_~|]", " ", cleaned)
    return cleaned.strip() + "\n" if cleaned.strip() else ""


def read_article(root: Path, relative: str) -> str:
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or language(relative) is None:
        raise ValueError("Invalid content path")
    return target.read_text(encoding="utf-8") if target.is_file() else ""


def load_module(root: Path, module_path: str, module_name: str):
    path = root / module_path
    if not path.is_file():
        raise RuntimeError("Humanizer module missing: " + str(path))
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load " + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def normalized(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).casefold()[:180]


def russian_new(module, base: str, head: str) -> list[dict]:
    def run(s):
        return module.run_review(s, style_id="neutral", evidence_ids=None, register="general").get("findings", []) if s else []
    def signature(f):
        return (f.get("library_id"), f.get("rule_id"), f.get("phenomenon_id"), f.get("verdict"), normalized(f.get("excerpt")))
    counts = Counter(signature(f) for f in run(base))
    out = []
    for finding in run(head):
        key = signature(finding)
        if counts[key]:
            counts[key] -= 1
        else:
            out.append(finding)
    return out


def serbian_all_evidence(module, finding, text: str) -> list[str]:
    """Recover the complete evidence for each rule of the pinned Serbian seed.

    The upstream checker intentionally caps its public evidence at five examples
    while keeping the full count. Comparing only that preview misses replaced
    sixth-and-later matches, so we use its very same compiled patterns on the
    same input. Keep this adapter aligned with the pinned upstream revision.
    """
    if finding.rule_id == "sr_ai_negative_parallelism_density":
        normalized_text = module.normalize_script(text)
        pattern = module.NEGATIVE_PARALLELISM_RE
        return [module._compact_ws(m.group(0)) for m in pattern.finditer(normalized_text)]
    if finding.rule_id == "sr_ai_triplet_density":
        normalized_text = module.normalize_script(text)
        pattern = module.TRIPLET_RE
        return [module._compact_ws(m.group(0)) for m in pattern.finditer(normalized_text)]
    if finding.rule_id == "sr_ai_repeated_section_scaffold":
        headings = [module._compact_ws(h) for h in module.HEADING_RE.findall(text)]
        counts = Counter(module.normalize_script(h).casefold() for h in headings)
        return [f"{heading} × {count}" for heading, count in counts.most_common() if count >= 3]
    if finding.rule_id == "sr_ai_heading_fragmentation":
        return [module._compact_ws(h) for h in module.HEADING_RE.findall(text)]
    # Unknown future rules must not silently use a truncated five-item preview.
    if finding.count > len(finding.evidence):
        raise RuntimeError(f"Unrecognized Serbian Humanizer rule with truncated evidence: {finding.rule_id}")
    return list(finding.evidence)


def serbian_new(module, base: str, head: str) -> list[dict]:
    """Subtract the complete multiset of matched evidence, not summary counts."""
    if not head:
        return []
    before = {f.rule_id: f for f in module.review(base)} if base else {}
    out = []
    for finding in module.review(head):
        previous = before.get(finding.rule_id)
        old_evidence = serbian_all_evidence(module, previous, base) if previous else []
        new_evidence = serbian_all_evidence(module, finding, head)
        old_fragments = Counter(re.sub(r"\s+", " ", e).strip().casefold() for e in old_evidence)
        new_fragments = []
        for excerpt in new_evidence:
            key = re.sub(r"\s+", " ", excerpt).strip().casefold()
            if old_fragments[key]:
                old_fragments[key] -= 1
            else:
                new_fragments.append(excerpt)
        # Some rules report a count of affected instances rather than evidence
        # strings; retain their aggregate increase as an additional signal.
        extra_count = max(0, finding.count - (previous.count if previous else 0))
        if not new_fragments and extra_count == 0:
            continue
        out.append({
            "rule_id": finding.rule_id,
            "category": finding.category,
            "message": finding.message,
            "count": max(len(new_fragments), extra_count),
            "evidence": new_fragments,
        })
    return out


def inline(value: object, limit: int = 230) -> str:
    s = re.sub(r"\s+", " ", str(value or "")).strip().replace(chr(96), "'")
    s = s.replace("<", "‹").replace(">", "›")
    return s[: limit - 1] + "…" if len(s) > limit else s


def report(paths: list[str | dict[str, str]], base_root: Path, head_root: Path,
           ru_root: Path, sr_root: Path) -> str:
    groups: dict[str, list[tuple[str, list[dict]]]] = {"ru": [], "sr": []}
    ru = sr = None
    for entry in paths:
        # Keep the base identity for pure renames. If a file moves across
        # languages, review it as a new article in the destination language.
        file = entry if isinstance(entry, str) else entry["head"]
        previous = file if isinstance(entry, str) else entry.get("base", file)
        lang = language(file)
        if lang is None:
            continue
        before = (prose_only(read_article(base_root, previous))
                  if language(previous) == lang else "")
        after = prose_only(read_article(head_root, file))
        if not after or after == before:
            continue
        if lang == "ru":
            ru = ru or load_module(ru_root, "scripts/review.py", "svesnost_humanizer_ru")
            findings = russian_new(ru, before, after)
        else:
            sr = sr or load_module(sr_root, "scripts/check.py", "svesnost_humanizer_sr")
            findings = serbian_new(sr, before, after)
        groups[lang].append((file, findings))

    lines = [MARKER, "## Языковая редактура / Језичка редактура", "",
             "Проверяется только проза статей; frontmatter, код и служебная разметка исключены.",
             "Замечания не блокируют PR и не изменяют файлы автоматически.", ""]
    titles = {"ru": "Русский — humanizer_russian", "sr": "Српски — humanizer_serbian"}
    for lang in ("ru", "sr"):
        lines += ["### " + titles[lang], ""]
        if not groups[lang]:
            lines += ["Нет изменённых статей для проверки / Нема измењених чланака.", ""]
            continue
        for file, findings in groups[lang]:
            lines += ["**" + inline(file) + "**", ""]
            if not findings:
                lines += ["Новых замечаний нет / Нема нових напомена.", ""]
                continue
            for item in findings[:25]:
                if lang == "ru":
                    excerpt = inline(item.get("excerpt"))
                    reason = inline(item.get("reason") or item.get("rule_id"))
                    lines.append("- **" + excerpt + "** — " + reason)
                else:
                    evidence = ", ".join(inline(x, 100) for x in item.get("evidence", [])[:3])
                    lines.append("- **" + inline(item.get("rule_id")) + "**: " +
                                 inline(item.get("message")) +
                                 ((" Примери: " + evidence) if evidence else ""))
            if len(findings) > 25:
                lines += ["", f"_Ещё {len(findings)-25} замечаний не показано._"]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def self_test() -> dict:
    article = """---
title: УЖАСНОЕ НАЗВАНИЕ
description: НЕ ПРОВЕРЯТЬ
hero:
  title: НЕ ПОПАДАТЬ
---
# Заголовок статьи

Это обычный читательский текст со словами для проверки.[^1]

[Ссылка на издание](https://hidden.example/irrelevant)

<Aside title="СКРЫТАЯ РАЗМЕТКА" data={{ secret: "НЕЛЬЗЯ" }}>
Параграф внутри компонента виден читателю.
</Aside>

""" + chr(96) * 3 + """javascript
const priv = "КОД НЕ ПРОВЕРЯТЬ";
""" + chr(96) * 3 + """

<CodeBlock>Пример исходного кода НЕ ПРОВЕРЯТЬ</CodeBlock>

Здесь {someFunction("СКРЫТАЯ ПЕРЕМЕННАЯ")} остаётся текст.

[^1]: **Документ** — Примечание с читательским текстом.
"""
    result = prose_only(article)
    allowed = ["Заголовок статьи", "обычный читательский текст", "Ссылка на издание",
               "Параграф внутри компонента", "остаётся текст", "Примечание с читательским текстом"]
    forbidden = ["УЖАСНОЕ", "НЕ ПРОВЕРЯТЬ", "НЕ ПОПАДАТЬ", "СКРЫТАЯ",
                 "hidden.example", "javascript", "someFunction", "CodeBlock",
                 "Aside", "data=", "secret:", "const priv", "title:", "[^1]"]
    for value in allowed:
        assert value in result, ("missing prose", value, result)
    for value in forbidden:
        assert value not in result, ("leaked markup", value, result)

    # Markdown link destinations have nested parentheses and quoted titles.
    # None of their technical fragments may enter Humanizer; labels and prose
    # beside links remain reader-visible.
    link_cases = (
        ("[Текст](https://example.org/path_(foo)more) обычная проза.",
         ("Текст", "обычная проза"), ("example.org", "foo", "more")),
        ("См. https://example.org/path_(foo)more и далее.",
         ("См.", "и далее"), ("example.org", "foo", "more")),
        ('[Документ](https://example.org "подпись <!--")\nСледующий абзац.',
         ("Документ", "Следующий абзац"), ("example.org", "подпись", "<!--")),
        ('До [Файл](https://example.org/path_(foo)more "подпись <!-- (x)")'
         ' после ссылки.\nСледующий абзац.',
         ("До", "Файл", "после ссылки", "Следующий абзац"),
         ("example.org", "foo", "more", "подпись", "<!--")),
    )
    for sample, visible, hidden in link_cases:
        extracted = prose_only(sample)
        for fragment in visible:
            assert fragment in extracted, ("link lost prose", fragment, extracted)
        for fragment in hidden:
            assert fragment not in extracted, ("link leaked metadata", fragment, extracted)
    img = prose_only("Вводный текст ![СЕКРЕТНЫЙ ALT](https://example.org/x_(y).png) конец.")
    assert "Вводный текст" in img and "конец" in img, img
    assert "СЕКРЕТНЫЙ" not in img and "example.org" not in img, img

    # Clickable images are nested inside link labels; the image alt is still
    # non-prose even though the outer link is reader-visible.
    clickable_image = (
        "Проза до изображения. "
        "[![НЕ ПОСЫЛАТЬ ALT](https://example.org/image.png)]"
        "(https://example.org/page) "
        "Проза после изображения."
    )
    clickable_body = prose_only(clickable_image)
    for allowed_text in ("Проза до изображения", "Проза после изображения"):
        assert allowed_text in clickable_body, (allowed_text, clickable_body)
    for hidden_text in ("НЕ ПОСЫЛАТЬ ALT", "example.org", "image.png"):
        assert hidden_text not in clickable_body, (hidden_text, clickable_body)
    assert len(markdown_inline_links("[![alt](src)](href)")) == 2

    # CommonMark allows a link title to begin on the next line. The
    # destination and title remain metadata, while the label is visible.
    for title in ('СЕКРЕТНОЕ TITLE', 'СЕКРЕТНОЕ TITLE <!--'):
        multiline_link = (
            "До [Документ](/materials/page\n"
            + '  "' + title + '") после.\n'
            + "Следующий абзац.\n"
        )
        extracted = prose_only(multiline_link)
        for visible in ("До", "Документ", "после", "Следующий абзац"):
            assert visible in extracted, ("multiline link lost prose", visible, extracted)
        for hidden in ("/materials/page", "СЕКРЕТНОЕ TITLE", "<!--"):
            assert hidden not in extracted, ("multiline link metadata leaked", hidden, extracted)

    # CommonMark link text can span soft line breaks. The label remains
    # reader-visible but a relative target and title do not enter Humanizer.
    soft_break_link = (
        'См. [Нужный\nдокумент](/private/path "СЛУЖЕБНЫЙ TITLE") далее.\n'
        "Следующий читательский абзац.\n"
    )
    soft_link_prose = prose_only(soft_break_link)
    for fragment in ("См.", "Нужный", "документ", "далее", "Следующий читательский абзац"):
        assert fragment in soft_link_prose, ("multiline label lost prose", fragment, soft_link_prose)
    for hidden in ("/private/path", "СЛУЖЕБНЫЙ TITLE"):
        assert hidden not in soft_link_prose, ("multiline label leaked metadata", hidden, soft_link_prose)
    assert len(markdown_inline_links(soft_break_link)) == 1

    # An angle-bracket link destination may contain a literal unmatched ).
    # The complete destination including the technical tail must be hidden.
    for angle_link in (
        "До [Документ](<b)c>) после.",
        "До [Материал](<path_(foo)more>) после.",
    ):
        angle_prose = prose_only(angle_link)
        assert "До" in angle_prose and "после" in angle_prose, angle_prose
        assert "b)c>" not in angle_prose and "foo)more" not in angle_prose, angle_prose
        assert "<" not in angle_prose and ">" not in angle_prose, angle_prose
    assert "Документ" in prose_only("До [Документ](<b)c>) после.")
    assert "Материал" in prose_only("До [Материал](<path_(foo)more>) после.")

    # Malformed CommonMark must remain visible: whitespace after a bare
    # destination requires an explicit quoted/parenthesized title.
    malformed_link = "Видимый [пример](/служебный/путь СЛОВА ДЛЯ РЕДАКТУРЫ) после."
    malformed_result = prose_only(malformed_link)
    assert "СЛОВА ДЛЯ РЕДАКТУРЫ" in malformed_result, malformed_result
    assert "пример" in malformed_result and "после" in malformed_result, malformed_result
    assert not markdown_inline_links(malformed_link)
    assert "СЛОВА ДЛЯ РЕДАКТУРЫ" not in prose_only(
        'Видимый [пример](/служебный/путь "СЛОВА ДЛЯ РЕДАКТУРЫ") после.'
    )

    # An escaped Markdown literal \\( is not a TeX opener. It must not pair
    # with the closing delimiter of a later, real inline formula.
    quoted_tex = r"До \\( буквально. Важная проза после литерала. \(x+1\) Конец."
    quoted_tex_body = prose_only(quoted_tex)
    for fragment in ("До", "буквально", "Важная проза после литерала", "Конец"):
        assert fragment in quoted_tex_body, ("literal ate prose", fragment, quoted_tex_body)
    assert "x+1" not in quoted_tex_body, quoted_tex_body
    assert "После" in prose_only(r"После \\( литерала без формулы.")

    assert language(DOCS + "ru/story.md") == "ru"
    assert language(DOCS + "en/story.md") is None
    assert language(DOCS + "story.mdx") == "sr"
    assert language("docs/story.md") is None
    assert prose_only("---\ntitle: private\n") == ""

    # Multi-line MDX ESM declarations are not Markdown prose. Their nested
    # object keys and import specifiers must be hidden through the closing
    # brace/semicolon; ordinary paragraphs after them remain visible.
    mdx_modules = (
        "Проза перед экспортом.\n"
        "export const meta = {\n"
        '  title: "СЛУЖЕБНОЕ ПОЛЕ",\n'
        "  nested: { label: 'СЕКРЕТ ЭКСПОРТА' },\n"
        "};\n"
        "\n"
        "Видимый абзац после экспорта.\n"
        "import {\n"
        "  identifier as СЕКРЕТ_ИМПОРТА,\n"
        "} from './module.js';\n"
        "Обычный текст после импорта.\n"
    )
    modules_body = prose_only(mdx_modules)
    for visible_text in ("Проза перед экспортом", "Видимый абзац после экспорта",
                         "Обычный текст после импорта"):
        assert visible_text in modules_body, ("ESM swallowed prose", visible_text, modules_body)
    for hidden_text in ("СЛУЖЕБНОЕ ПОЛЕ", "СЕКРЕТ ЭКСПОРТА",
                        "СЕКРЕТ_ИМПОРТА", "module.js"):
        assert hidden_text not in modules_body, ("ESM leaked into prose", hidden_text, modules_body)

    # ESM 'from' is allowed on its own line after a balanced import/export.
    # The next reader-visible Markdown paragraph must not be consumed.
    for statement in (
        "import Widget\nfrom './Widget.jsx'\n",
        "export { Widget }\nfrom './Widget.jsx'\n",
    ):
        snippet = statement + "\nВидимая проза после импорта.\n"
        extracted = prose_only(snippet)
        assert "Видимая проза после импорта." in extracted, extracted
        for metadata in ("Widget", "from", ".jsx", "export", "import"):
            assert metadata not in extracted, ("split ESM leaked", metadata, extracted)

    # An incomplete import in standalone/list-indented Markdown code cannot
    # start module state and consume the paragraph after that code block.
    for indented_source, visible_after in (
        ("    import {\nВидимый читательский абзац.\n",
         "Видимый читательский абзац."),
        ("- Видимый пункт\n\n      import {\n"
         "      СЕКРЕТ В INDENTED CODE\n"
         "После списка видимая проза.\n",
         "После списка видимая проза."),
    ):
        indented_prose = prose_only(indented_source)
        assert visible_after in indented_prose, ("indented ESM swallowed prose", indented_prose)
        assert "import" not in indented_prose, indented_prose
        assert "СЕКРЕТ В INDENTED CODE" not in indented_prose, indented_prose

    # An unbalanced ESM-looking statement inside a fenced JS example must
    # not start top-level module handling or hide the paragraph after ~~~.
    fenced_esm = (
        "Проза до примера.\n"
        "~~~js\n"
        "import {\n"
        "~~~\n"
        "Видимый абзац после примера.\n"
    )
    fenced_esm_prose = prose_only(fenced_esm)
    assert "Проза до примера" in fenced_esm_prose, fenced_esm_prose
    assert "Видимый абзац после примера" in fenced_esm_prose, fenced_esm_prose
    assert "import" not in fenced_esm_prose, fenced_esm_prose
    quoted_esm = (
        "> ~~~js\n"
        "> export const config = {\n"
        "> ~~~\n"
        "Видимый текст после цитаты.\n"
    )
    assert "Видимый текст после цитаты" in prose_only(quoted_esm)

    # Indented YAML block scalar delimiters are data, not closing frontmatter.
    # Only a column-zero delimiter ends the metadata section.
    for closing in ("---", "..."):
        yaml_scalar = (
            "---\n"
            "description: |\n"
            "  ---\n"
            "  ...\n"
            "  СЛУЖЕБНАЯ ПРОЗА МЕТАДАННЫХ\n"
            + closing + "   \n"
            "# Видимый заголовок\n"
            "Следующий абзац.\n"
        )
        extracted = prose_only(yaml_scalar)
        assert "СЛУЖЕБНАЯ ПРОЗА МЕТАДАННЫХ" not in extracted, extracted
        assert "Видимый заголовок" in extracted, extracted
        assert "Следующий абзац." in extracted, extracted

    special = "Проза до формулы.\n---\n:::note\nСодержимое примечания — это проза.\n:::\n@MATH@\nx^2 + 1\n@MATH@\nИ проза после.\n".replace("@MATH@", chr(36) * 2).replace("@MATH@", chr(36) * 2)
    isolated = prose_only(special)
    assert "Проза до формулы." in isolated and "Содержимое примечания" in isolated
    assert "И проза после." in isolated and "x^2" not in isolated
    assert "---" not in isolated and ":::note" not in isolated and chr(36) * 2 not in isolated

    # Display math within quoted/list containers is code-like math, not
    # reader-visible text; the next paragraph must survive closing markers.
    math_samples = (
        ("> " + chr(36) * 2 + "\n> E=mc^2\n> " + chr(36) * 2
         + "\n> Видимый абзац.\n", "Видимый абзац"),
        ("> \\[\n> x^2+y^2\n> \\]\n> Проза цитаты.\n",
         "Проза цитаты"),
        ("- Видимый пункт\n  " + chr(36) * 2 + "\n  x^2+y^2\n  "
         + chr(36) * 2 + "\nПосле списка.\n", "После списка"),
        ("- " + chr(36) * 2 + "\n  E=mc^2\n  " + chr(36) * 2
         + "\nПосле формулы.\n", "После формулы"),
    )
    for sample, visible in math_samples:
        extracted = prose_only(sample)
        assert visible in extracted, ("display math swallowed prose", sample, extracted)
        assert "E=mc^2" not in extracted and "x^2+y^2" not in extracted, (
            "display math leaked", sample, extracted
        )

    # Fenced code is authoritative: math-looking lines inside must not open math
    # blocks that swallow the paragraph after the closing code fence.
    marker = chr(96) * 3
    for math_start in (chr(36) * 2 + " не закрыто", r"\[ не закрыто"):
        fenced = (
            "Обычный текст перед кодом.\n" + marker + "text\n"
            + math_start + "\n" + marker + "\n"
            + "Обычный текст после кода.\n"
        )
        after_fence = prose_only(fenced)
        assert "Обычный текст перед кодом." in after_fence, after_fence
        assert "Обычный текст после кода." in after_fence, after_fence
        assert "не закрыто" not in after_fence, after_fence

    # Fenced code inside blockquotes (including nested quotes) and Markdown
    # list items must be hidden as code, with following prose still visible.
    nested_fences = (
        ("> ~~~js\n> const secret = 'КОД В ЦИТАТЕ';\n> ~~~\n"
         "Обычная проза после цитаты.\n", "Обычная проза после цитаты"),
        ("> > ~~~js\n> > const secret = 'КОД ВО ВЛОЖЕННОЙ ЦИТАТЕ';\n"
         "> > ~~~\nОбычная проза после вложенной цитаты.\n",
         "Обычная проза после вложенной цитаты"),
        ("- Видимый пункт списка\n    ~~~js\n    const secret = 'КОД В СПИСКЕ';\n"
         "    ~~~\nОбычная проза после списка.\n", "Обычная проза после списка"),
    )
    for sample, expected_prose in nested_fences:
        extracted = prose_only(sample)
        assert expected_prose in extracted, ("prose lost after nested fence", extracted)
        assert "const secret" not in extracted and "КОД В" not in extracted, extracted
    assert "Видимый пункт списка" in prose_only(nested_fences[2][0])

    # A list marker inside a fenced block must not mutate the enclosing list:
    # otherwise the closing fence can be mistaken for indented prose.
    list_markers_inside_code = (
        "- Видимый пункт\n"
        "    ~~~\n"
        "    - КОД В СПИСКЕ\n"
        "    ~~~\n"
        "После списка.\n"
    )
    extracted = prose_only(list_markers_inside_code)
    assert "Видимый пункт" in extracted and "После списка." in extracted, extracted
    assert "КОД В СПИСКЕ" not in extracted, extracted

    # HTML comment contents are not Markdown block syntax. An embedded fence
    # or math opener must not consume the text after the comment closes.
    for fake_delimiter in ("~~~", chr(36) * 2, r"\["):
        comment = (
            "Проза до комментария.\n"
            "<!--\n" + fake_delimiter + "\n-->\n"
            "Проза после комментария.\n"
        )
        extracted = prose_only(comment)
        assert "Проза до комментария." in extracted, extracted
        assert "Проза после комментария." in extracted, extracted
        assert fake_delimiter not in extracted, extracted
    # HTML-style syntax *inside code* must not start a real comment either.
    in_code = (
        "Проза до блока.\n" + chr(96) * 3 + "html\n"
        "<!--\n" + chr(96) * 3 + "\n"
        "Проза после блока.\n"
    )
    extracted = prose_only(in_code)
    assert "Проза после блока." in extracted, extracted

    # Raw MDX/HTML code containers must be hidden before the Markdown block
    # scanner: internal ~~~ and $ cannot hide following reader-visible prose.
    raw_blocks = (
        ("pre", "~~~", "СЕКРЕТ В PRE"),
        ("pre", "$", "СЕКРЕТ МАТЕМАТИКИ В PRE"),
        ("CodeBlock", "~~~js", "СЕКРЕТ В CODEBLOCK"),
        ("CodeBlock", "$", "СЕКРЕТ В CODEBLOCK MATH"),
    )
    for tag, marker_line, secret in raw_blocks:
        sample = (
            "Проза перед raw-блоком.\n"
            f"<{tag}>\n"
            + marker_line + "\n"
            + secret + "\n"
            + f"</{tag}>\n"
            + "Обычный читательский абзац после блока.\n"
        )
        extracted = prose_only(sample)
        for wanted in ("Проза перед raw-блоком", "Обычный читательский абзац после блока"):
            assert wanted in extracted, ("raw block swallowed prose", tag, extracted)
        assert secret not in extracted, ("raw code leaked", tag, extracted)
    # A literal raw-looking tag inside fenced code is not an opening MDX tag.
    fenced_raw_literal = (
        "Текст до кода.\n"
        "~~~html\n"
        "<pre>\n"
        "~~~\n"
        "Текст после кода.\n"
    )
    assert "Текст после кода." in prose_only(fenced_raw_literal)

    # The raw-element pre-scanner must also recognize a fence indented under
    # a Markdown list: <pre> is code text, not a real opening container.
    nested_fenced_raw = (
        "- Видимый пункт\n"
        "    ~~~html\n"
        "    <pre>\n"
        "    ~~~\n"
        "Обычный абзац после списка.\n"
    )
    nested_fenced_raw_prose = prose_only(nested_fenced_raw)
    assert "Видимый пункт" in nested_fenced_raw_prose, nested_fenced_raw_prose
    assert "Обычный абзац после списка" in nested_fenced_raw_prose, nested_fenced_raw_prose

    # The outer HTML/MDX table remains hidden even after an inner </table>.
    nested_table = (
        "Проза до вложенной таблицы.\n"
        "<table><tr><td>\n"
        "  <table><tr><td>ВНУТРЕННИЕ ДАННЫЕ</td></tr></table>\n"
        "  СЕКРЕТ ВНЕ ВНУТРЕННЕЙ ТАБЛИЦЫ\n"
        "</td></tr></table>\n"
        "Видимый абзац после таблицы.\n"
    )
    nested_table_result = prose_only(nested_table)
    for expected in ("Проза до вложенной таблицы", "Видимый абзац после таблицы"):
        assert expected in nested_table_result, (expected, nested_table_result)
    for hidden in ("ВНУТРЕННИЕ ДАННЫЕ", "СЕКРЕТ ВНЕ ВНУТРЕННЕЙ ТАБЛИЦЫ"):
        assert hidden not in nested_table_result, (hidden, nested_table_result)

    # A raw-looking tag in standalone Markdown code is not a raw element.
    # The later non-indented prose must remain visible.
    for code_tag in ("pre", "table", "CodeBlock"):
        standalone_code = (
            f"    <{code_tag}>\n"
            "Обычный читательский абзац после примера.\n"
        )
        extracted = prose_only(standalone_code)
        assert "Обычный читательский абзац после примера." in extracted, (
            "indented raw tag swallowed prose", code_tag, extracted
        )

    # An escaped HTML tag is visible text, including a literal <table>.
    for escape_prefix in ("\\", "\\\\\\"):
        literal_tag = (
            "Буквально " + escape_prefix + "<table> означает имя тега.\n"
            "Видимый абзац после литерала.\n"
        )
        extracted = prose_only(literal_tag)
        assert "означает имя тега" in extracted, extracted
        assert "Видимый абзац после литерала" in extracted, extracted

    # A closing table tag inside an HTML comment does not terminate the
    # outer table; contents after the comment remain hidden as table data.
    table_with_comment = (
        "Проза перед таблицей.\n"
        "<table><tr><td>\n"
        "<!-- </table> -->\n"
        "СЕКРЕТ ВНУТРИ ЯЧЕЙКИ\n"
        "</td></tr></table>\n"
        "Видимый абзац после таблицы.\n"
    )
    extracted = prose_only(table_with_comment)
    assert "Проза перед таблицей" in extracted, extracted
    assert "Видимый абзац после таблицы" in extracted, extracted
    assert "СЕКРЕТ ВНУТРИ ЯЧЕЙКИ" not in extracted, extracted

    # Escaped Markdown delimiters remain readable: neither \{ nor \<!--
    # may start an MDX expression or HTML comment that eats later paragraphs.
    literal_delimiters = (
        r"Буквальная \{ скобка." + "\n" + "Следующий абзац.",
        r"Пишите \<!-- буквально." + "\n" + "Следующий абзац.",
        r"Буквальная \\\{ скобка." + "\n" + "Следующий абзац.",
        r"Пишите \\\<!-- буквально." + "\n" + "Следующий абзац.",
    )
    for sample in literal_delimiters:
        extracted = prose_only(sample)
        assert "Следующий абзац." in extracted, ("escaped delimiter consumed prose", sample, extracted)
        assert "Буквальная" in extracted or "Пишите" in extracted, extracted
    assert "Литерал перед выражением" in prose_only(
        r"Литерал перед выражением \{ и затем {handler('SECRET')} остаётся видимым."
    )
    assert "SECRET" not in prose_only(
        r"Литерал перед выражением \{ и затем {handler('SECRET')} остаётся видимым."
    )

    # JavaScript literals in MDX expressions/ESM are not raw HTML tags.
    # Unclosed pseudo-tags must never consume the following article paragraph.
    mdx_raw_literals = (
        '{ "<table>" }\nВидимый абзац после выражения.\n',
        'export const value = "<pre>";\nВидимый абзац после экспорта.\n',
        'export const metadata = {\n'
        '  title: "<table>",\n'
        '};\n'
        'Видимый абзац после объекта.\n',
    )
    for sample, visible in zip(
        mdx_raw_literals,
        ("Видимый абзац после выражения",
         "Видимый абзац после экспорта",
         "Видимый абзац после объекта")
    ):
        extracted = prose_only(sample)
        assert visible in extracted, ("JS pseudo-tag ate prose", sample, extracted)
        assert "<table>" not in extracted and "<pre>" not in extracted, extracted

    # Curly braces inside JS regex character classes are not MDX delimiters.
    for expression in (
        r"/[{]/.test('x') ? 'да' : 'нет'",
        r"/[}]/.test('x') ? 'да' : 'нет'",
    ):
        sample = "До {" + expression + "} после выражения.\nСледующий читательский абзац."
        extracted = prose_only(sample)
        for visible in ("До", "после выражения", "Следующий читательский абзац"):
            assert visible in extracted, ("regex swallowed reader prose", expression, extracted)
        for hidden in ("test(", "'да'", "'нет'"):
            assert hidden not in extracted, ("regex leaked JS", expression, extracted)
    assert "Деление рядом" in prose_only("Деление рядом {12 / 3} и затем проза.")

    # The literal <!-- inside inline code, an MDX attribute or a JS expression
    # cannot open an HTML comment and consume following reader-visible text.
    fake_openers = (
        "До " + chr(96) + "<!--" + chr(96)
        + " и видимая проза.\nСледующий абзац.\n",
        '<Aside title="<!--">Видимый текст компонента.</Aside>\nСледующий абзац.\n',
        'Проза с {flag === "<!--" ? "да" : "нет"} рядом.\nСледующий абзац.\n',
    )
    for sample in fake_openers:
        extracted = prose_only(sample)
        assert "Следующий абзац." in extracted, ("comment opener escaped", extracted)
    assert "видимая проза" in prose_only(fake_openers[0])
    assert "Видимый текст компонента" in prose_only(fake_openers[1])
    assert "Проза с" in prose_only(fake_openers[2])

    # Indented code in Markdown blockquotes is code even though the original
    # line begins with '>' instead of four spaces.
    for quoted_code in (
        ">     КОД В ЦИТАТЕ\n> Видимый текст.\n",
        "> >     КОД ВО ВЛОЖЕННОЙ ЦИТАТЕ\n> > Видимый текст.\n",
    ):
        extracted = prose_only(quoted_code)
        assert "КОД В" not in extracted, extracted
        assert "Видимый текст." in extracted, extracted

    # Reference-style Markdown links: keep reader-visible label only.
    reference = (
        "Читайте [проверенную публикацию][source] и [документ][].\n"
        "Не показывать ![изображение][cover].\n"
        "\n"
        "[source]: https://example.org/secret\n"
        "[cover]: https://example.org/image.png\n"
    )
    reference_body = prose_only(reference)
    assert "проверенную публикацию" in reference_body, reference_body
    assert "документ" in reference_body, reference_body
    for hidden in ("[source]", "[cover]", "изображение", "example.org", "[]"):
        assert hidden not in reference_body, (hidden, reference_body)
    # Shortcut image references are images only when their label resolves.
    shortcut = (
        "Видимый текст до.\n"
        "![СЕКРЕТНЫЙ ALT]\n"
        "\n"
        "[СЕКРЕТНЫЙ ALT]: /img.png\n"
        "\n"
        "![НЕОПРЕДЕЛЁННЫЙ ЛИТЕРАЛ]\n"
        "Видимый текст после.\n"
    )
    shortcut_text = prose_only(shortcut)
    for item in ("Видимый текст до", "Видимый текст после", "НЕОПРЕДЕЛЁННЫЙ ЛИТЕРАЛ"):
        assert item in shortcut_text, ("reference shortcut lost prose", item, shortcut_text)
    for item in ("СЕКРЕТНЫЙ ALT", "/img.png"):
        assert item not in shortcut_text, ("reference shortcut leaked metadata", item, shortcut_text)

    # Reference definitions, including inside blockquotes, hide the entire
    # destination and a continuation title on the next line.
    long_reference = (
        "[ref]: /page\n"
        '  "СЕКРЕТНОЕ TITLE"\n'
        "\n"
        "> [other]: /hidden\n"
        "\n"
        "Обычный абзац.\n"
    )
    long_ref_text = prose_only(long_reference)
    assert "Обычный абзац." in long_ref_text, long_ref_text
    for hidden in ("СЕКРЕТНОЕ TITLE", "/page", "/hidden", "[other]:"):
        assert hidden not in long_ref_text, ("definition leaked metadata", hidden, long_ref_text)

    # Definitions may omit whitespace entirely between ':' and destination.
    compact_reference = (
        "[ref]:/private/internal-path\n"
        "\n"
        "Текст со [ссылкой][ref].\n"
    )
    compact_body = prose_only(compact_reference)
    assert "Текст со" in compact_body and "ссылкой" in compact_body, compact_body
    assert "/private/internal-path" not in compact_body, compact_body
    assert "[ref]:" not in compact_body, compact_body

    # A reference definition cannot interrupt a paragraph (CommonMark
    # example 213); the entire second line remains reader-visible.
    visible_definition = (
        "Видимая читателю строка\n"
        "[ref]: /page\n"
        "\n"
        "Следующий абзац.\n"
    )
    visible_definition_prose = prose_only(visible_definition)
    for phrase in ("Видимая читателю строка", "[ref]: /page", "Следующий абзац"):
        assert phrase in visible_definition_prose, ("paragraph reference lost", phrase, visible_definition_prose)

    # CommonMark also permits a destination on the next line and a title
    # spanning multiple subsequent lines, including in blockquotes.
    multiline_reference = (
        "[ref]:\n"
        "/private/path\n"
        "\n"
        "[other]: /page '\n"
        "ТЕХНИЧЕСКИЙ TITLE\n"
        "ещё строка\n"
        "'\n"
        "\n"
        "> [third]:\n"
        "> /private/quoted\n"
        "\n"
        "См. [ссылку][ref] и [источник][other].\n"
    )
    multiline_reference_prose = prose_only(multiline_reference)
    for visible in ("См.", "ссылку", "источник"):
        assert visible in multiline_reference_prose, ("multiline ref lost prose", visible, multiline_reference_prose)
    for secret in ("[ref]:", "/private/path", "ТЕХНИЧЕСКИЙ TITLE",
                   "ещё строка", "/private/quoted", "[third]:"):
        assert secret not in multiline_reference_prose, (
            "multiline ref leaked", secret, multiline_reference_prose
        )

    # Entire GFM table blocks are structural data, with or without border pipes.
    table = """Текст до таблицы.

| Участник | Сумма |
| --- | ---: |
| Иван | 1000 |
| Ольга | 2000 |

Текст после таблицы.
"""
    table_result = prose_only(table)
    assert "Текст до таблицы" in table_result and "Текст после таблицы" in table_result
    for value in ("Участник", "Сумма", "Иван", "Ольга", "1000", "2000"):
        assert value not in table_result, ("table leaked", value, table_result)

    borderless = """До таблицы.
Имя | Значение
--- | ---
Мария | 900

После таблицы.
"""
    borderless_result = prose_only(borderless)
    assert "До таблицы" in borderless_result and "После таблицы" in borderless_result
    assert "Мария" not in borderless_result and "900" not in borderless_result
    assert "Обычная проза" in prose_only("Обычная проза А | Б без таблицы.")
    quoted_table = (
        "Обычная проза до цитаты.\n"
        "> | Участник | Сумма |\n"
        "> | --- | --- |\n"
        "> | Иван | 1000 |\n"
        ">\n"
        "> Видимая проза после таблицы внутри цитаты.\n"
        "Обычная проза после цитаты.\n"
    )
    quoted_prose = prose_only(quoted_table)
    for visible in ("Обычная проза до цитаты", "Видимая проза после таблицы",
                    "Обычная проза после цитаты"):
        assert visible in quoted_prose, ("prose lost near quoted table", visible, quoted_prose)
    for hidden in ("Участник", "Сумма", "Иван", "1000"):
        assert hidden not in quoted_prose, ("quoted GFM table leaked", hidden, quoted_prose)

    # A GFM table row with only its first cell populated can contain no
    # pipe. It remains table data up to the next blank line/new block.
    pipe_free_row = (
        "| Имя | Сумма |\n"
        "| --- | --- |\n"
        "СЕКРЕТНЫЕ ТАБЛИЧНЫЕ ДАННЫЕ\n"
        "\n"
        "Видимый абзац.\n"
    )
    pipe_free_result = prose_only(pipe_free_row)
    assert "СЕКРЕТНЫЕ ТАБЛИЧНЫЕ ДАННЫЕ" not in pipe_free_result, pipe_free_result
    assert "Видимый абзац." in pipe_free_result, pipe_free_result
    table_then_heading = (
        "| Имя | Сумма |\n"
        "| --- | --- |\n"
        "ДАННЫЕ БЕЗ PIPES\n"
        "# Видимый заголовок после таблицы\n"
    )
    heading_result = prose_only(table_then_heading)
    assert "ДАННЫЕ БЕЗ PIPES" not in heading_result, heading_result
    assert "Видимый заголовок после таблицы" in heading_result, heading_result

    # An inline HTML tag at the beginning of a GFM row is still a table
    # cell, and must not terminate the table as a new HTML block.
    inline_html_gfm = (
        "| Участник | Сумма |\n"
        "| --- | --- |\n"
        "<em>ТАБЛИЧНЫЕ ДАННЫЕ</em> | 500\n"
        "\n"
        "Видимый абзац.\n"
    )
    inline_html_result = prose_only(inline_html_gfm)
    assert "ТАБЛИЧНЫЕ ДАННЫЕ" not in inline_html_result, inline_html_result
    assert "500" not in inline_html_result, inline_html_result
    assert "Видимый абзац." in inline_html_result, inline_html_result

    # Pipes inside Markdown code spans and escaped pipes are not table columns.
    ticks = chr(96)
    headers = [
        f"| Оператор {ticks}a|b{ticks} | Значение |",
        f"| Оператор {ticks * 2}a|b{ticks * 2} | Значение |",
        r"| Оператор a\|b | Значение |",
    ]
    for header in headers:
        assert len(_table_cells(header)) == 2, (_table_cells(header), header)
        table_with_code = (
            "Проза до таблицы.\n" + header
            + "\n| --- | --- |\n| Пример | 1000 |\n\nПроза после таблицы.\n"
        )
        extracted = prose_only(table_with_code)
        for visible in ("Проза до таблицы", "Проза после таблицы"):
            assert visible in extracted, (visible, extracted)
        for hidden in ("Оператор", "Значение", "Пример", "1000"):
            assert hidden not in extracted, (hidden, extracted)

    # HTML/MDX table data is excluded, while surrounding prose survives.
    html_table = (
        "Проза перед таблицей.\n"
        "<table className=\"data\"><thead><tr><th>Участник</th></tr></thead>"
        "<tbody><tr><td>Иван</td><td>1000</td></tr></tbody></table>\n"
        "<p>Проза после таблицы.</p>\n"
    )
    jsx_table = (
        "Начальный абзац.\n"
        "<Table data={{ secret: 'СЕКРЕТНЫЕ ДАННЫЕ' }}>"
        "<tr><td>Мария</td><td>2000</td></tr></Table>\n"
        "Заключительный абзац.\n"
    )
    for sample, visible, hidden in (
        (html_table, ("Проза перед таблицей", "Проза после таблицы"),
         ("Участник", "Иван", "1000")),
        (jsx_table, ("Начальный абзац", "Заключительный абзац"),
         ("Мария", "2000", "СЕКРЕТНЫЕ ДАННЫЕ")),
    ):
        result = prose_only(sample)
        for value in visible:
            assert value in result, ("lost prose beside table", value, result)
        for value in hidden:
            assert value not in result, ("HTML/JSX table leaked", value, result)

    # Attribute values can contain tag-like text or <!-- without being
    # actual tag/comment tokens. Only the outer raw table may end its mask.
    raw_attrs = (
        '<table><tr><td title="</table>">СЕКРЕТНЫЕ ДАННЫЕ</td></tr></table>\n'
        "Видимая проза после первой таблицы.\n",
        '<table><td title="<!--">ДАННЫЕ ВТОРОЙ ТАБЛИЦЫ</td></table>\n'
        "Видимая проза после второй таблицы.\n",
    )
    for source, visible, hidden in (
        (raw_attrs[0], "Видимая проза после первой таблицы", "СЕКРЕТНЫЕ ДАННЫЕ"),
        (raw_attrs[1], "Видимая проза после второй таблицы", "ДАННЫЕ ВТОРОЙ ТАБЛИЦЫ"),
    ):
        extracted = prose_only(source)
        assert visible in extracted, ("tag attribute swallowed prose", extracted)
        assert hidden not in extracted, ("tag attribute leaked raw data", extracted)

    # CommonMark treats a four-space line as paragraph continuation when
    # there is no intervening blank line; after a blank, it is indented code.
    paragraph_continuations = (
        "Начало абзаца\n"
        "    ВАЖНОЕ ПРОДОЛЖЕНИЕ\n"
        "    ВТОРОЕ ПРОДОЛЖЕНИЕ\n"
        "Конец абзаца.\n"
        "\n"
        "    СЕКРЕТНЫЙ КОД ПОСЛЕ ПУСТОЙ СТРОКИ\n"
        "\n"
        "Обычная проза после кода.\n"
    )
    paragraph_prose = prose_only(paragraph_continuations)
    for fragment in ("Начало абзаца", "ВАЖНОЕ ПРОДОЛЖЕНИЕ",
                     "ВТОРОЕ ПРОДОЛЖЕНИЕ", "Конец абзаца",
                     "Обычная проза после кода"):
        assert fragment in paragraph_prose, ("paragraph continuation lost", fragment, paragraph_prose)
    assert "СЕКРЕТНЫЙ КОД ПОСЛЕ ПУСТОЙ СТРОКИ" not in paragraph_prose, paragraph_prose
    assert len(ordinary_paragraph_continuations(paragraph_continuations)) == 2

    # Multi-line list-indented code remains code until it dedents, including
    # after intervening blank lines and with Markdown-like syntax inside code.
    list_code = (
        "- Видимый пункт\n"
        "\n"
        "      КОД_1\n"
        "      КОД_2\n"
        "\n"
        "      - КОД_3\n"
        "      ~~~\n"
        "\n"
        "После списка.\n"
    )
    list_code_result = prose_only(list_code)
    assert "Видимый пункт" in list_code_result, list_code_result
    assert "После списка." in list_code_result, list_code_result
    for hidden in ("КОД_1", "КОД_2", "КОД_3", "~~~"):
        assert hidden not in list_code_result, (hidden, list_code_result)

    # Indentation in a JSX/MDX Markdown container is formatting, not
    # automatically an indented code block. Keep reader-visible text at
    # 0/2/4/6 spaces, including nested and multi-paragraph component bodies.
    for padding in (0, 2, 4, 6):
        nested_mdx = (
            "<Accordion>\n"
            '  <AccordionItem title="СЛУЖЕБНЫЙ ПРОП">\n'
            + " " * padding + "Видимая статья в аккордеоне.\n"
            + "\n"
            + " " * padding + "Второй видимый абзац.\n"
            + "  </AccordionItem>\n"
            + "</Accordion>\n"
            + "\n"
            + "<Aside>\n"
            + " " * padding + "Видимое пояснение Aside.\n"
            + "</Aside>\n"
            + "<Steps>\n"
            + " " * padding + "1. Видимое содержание шага.\n"
            + "</Steps>\n"
            + "\n"
            + "Внешняя проза после компонента.\n"
        )
        result_mdx = prose_only(nested_mdx)
        for visible in ("Видимая статья в аккордеоне", "Второй видимый абзац",
                        "Видимое пояснение Aside", "Видимое содержание шага",
                        "Внешняя проза после компонента"):
            assert visible in result_mdx, ("MDX lost reader text", padding, visible, result_mdx)
        assert "СЛУЖЕБНЫЙ ПРОП" not in result_mdx, result_mdx
    real_mdx_code = (
        "<Aside>\n"
        "\n"
        "        СЛУЖЕБНЫЙ КОД В ASIDE\n"
        "\n"
        "    Видимый текст после кода.\n"
        "</Aside>\n"
    )
    code_prose = prose_only(real_mdx_code)
    assert "СЛУЖЕБНЫЙ КОД В ASIDE" not in code_prose, code_prose
    assert "Видимый текст после кода" in code_prose, code_prose

    # Indented nested list items are prose, but standalone indented blocks are code.
    nested = """- Основной пункт списка
    - Вложенный пункт списка
      Продолжение вложенного пункта

После списка.

    СКРЫТЫЙ КОД
"""
    nested_result = prose_only(nested)
    for value in ("Основной пункт", "Вложенный пункт", "Продолжение вложенного пункта", "После списка"):
        assert value in nested_result, ("nested list lost", value, nested_result)
    assert "СКРЫТЫЙ КОД" not in nested_result

    # Inline TeX is markup; an escaped dollar or an unpaired currency sign is not.
    dollars = r"Формула $E=mc^2$ используется. Цена $5, другая цена $10. Экранировано \$5."
    dollars_result = prose_only(dollars)
    assert "$E=mc^2$" not in dollars_result and "E=mc" not in dollars_result
    for value in ("Формула", "используется", "$5", "$10"):
        assert value in dollars_result, ("currency or prose lost", value, dollars_result)
    assert "$E = mc^2$" not in prose_only("Формула $E = mc^2$ описана словами.")
    escaped_math = prose_only(r"Оставить литерал \$E=mc^2\$ как текст.")
    assert "E=mc^2" in escaped_math, ("escaped math incorrectly masked", escaped_math)
    # Price ranges and successive prices are prose, not mathematical pairs.
    monetary_examples = (
        ("Стоимость $100–$200.", ("$100", "$200")),
        ("Цена $5/$10 за набор.", ("$5", "$10")),
        ("Стоимость $100 - $200.", ("$100", "$200")),
    )
    for original, amounts in monetary_examples:
        extracted = prose_only(original)
        for amount in amounts:
            assert amount in extracted, ("currency masked as math", original, extracted)
    assert "2+2" not in prose_only("Математика $2+2$ остаётся формулой.")

    # Footnote definitions can contain indented, reader-visible continuation
    # lines, even after a blank line. A later independent indented code block
    # must still be suppressed after the definition's unindented prose ends.
    footnote = (
        "[^1]: Документ и источник.\n"
        "    Уточнение о статусе источника.\n"
        "\n"
        "    Продолжение сноски после пустой строки.\n"
        "Обычная проза после сноски.\n"
        "\n"
        "    КОД ВНЕ СНОСКИ\n"
    )
    footnote_prose = prose_only(footnote)
    for visible in ("Документ и источник", "Уточнение о статусе источника",
                    "Продолжение сноски после пустой строки",
                    "Обычная проза после сноски"):
        assert visible in footnote_prose, ("footnote prose lost", visible, footnote_prose)
    assert "КОД ВНЕ СНОСКИ" not in footnote_prose, footnote_prose

    # A pure rename must compare the new head path to the previous base path,
    # and must not load either Humanizer or repeat all existing findings.
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as folder:
        old_root = Path(folder) / "old"
        new_root = Path(folder) / "new"
        old_path = DOCS + "ru/old.md"
        new_path = DOCS + "ru/new.md"
        article_text = "---\ntitle: Hidden title\n---\nСтарая проверяемая проза.\n"
        for root, relative in ((old_root, old_path), (new_root, new_path)):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(article_text, encoding="utf-8")
        rename = json.loads(json.dumps({"head": new_path, "base": old_path}))
        summary = report([rename], old_root, new_root,
                         Path(folder) / "not-installed-ru",
                         Path(folder) / "not-installed-sr")
        assert MARKER in summary, summary
        assert new_path not in summary, ("pure rename reported as new", summary)
        assert "Новых замечаний нет" not in summary, summary

    # Exercise the full report path for both languages with nested,
    # multi-paragraph MDX content. Stubs keep the test deterministic and
    # require prose to reach both reviewer APIs before a report is generated.
    with TemporaryDirectory() as folder:
        root = Path(folder)
        base_dir = root / "base"
        head_dir = root / "head"
        ru_dir = root / "ru"
        sr_dir = root / "sr"
        for path in (ru_dir / "scripts", sr_dir / "scripts"):
            path.mkdir(parents=True, exist_ok=True)
        (ru_dir / "scripts/review.py").write_text(
            "def run_review(text, **kwargs):\n"
            "    assert 'видимый' in text.lower(), text\n"
            "    assert 'СЛУЖЕБНЫЙ' not in text, text\n"
            "    return {'findings': []}\n",
            encoding="utf-8",
        )
        (sr_dir / "scripts/check.py").write_text(
            "def review(text):\n"
            "    assert 'видљив' in text.lower(), text\n"
            "    assert 'СЛУЖБЕНИ' not in text, text\n"
            "    return []\n",
            encoding="utf-8",
        )
        pair = (
            (DOCS + "ru/mdx-review.mdx", "видимый текст для русского редактора", "СЛУЖЕБНЫЙ"),
            (DOCS + "mdx-review.mdx", "видљив текст за српског уредника", "СЛУЖБЕНИ"),
        )
        changed = []
        for document, phrase, secret in pair:
            target = head_dir / document
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                '<Accordion><AccordionItem title="' + secret + '">\n'
                '    ' + phrase + '.\n'
                '\n'
                '      ' + phrase + ' у другом пасусу.\n'
                '</AccordionItem></Accordion>\n'
                '<Steps>\n'
                '    1. ' + phrase + ' у кораку.\n'
                '</Steps>\n',
                encoding="utf-8",
            )
            changed.append(document)
        summary = report(changed, base_dir, head_dir, ru_dir, sr_dir)
        assert "humanizer_russian" in summary and "humanizer_serbian" in summary, summary
        for path in changed:
            assert path in summary, ("report skipped nested MDX", path, summary)

    # Serbian findings are subtracted by evidence multiset, not by total rule count.
    from types import SimpleNamespace

    def serbian_stub(text: str):
        if text == "before":
            return [SimpleNamespace(
                rule_id="sr_test", category="AI_STYLE_SIGNAL", message="Sample",
                count=2, evidence=["Стари образац", "Исти образац"])]
        if text == "after":
            return [SimpleNamespace(
                rule_id="sr_test", category="AI_STYLE_SIGNAL", message="Sample",
                count=2, evidence=["Исти образац", "Нови образац"])]
        if text == "same":
            return [SimpleNamespace(
                rule_id="sr_test", category="AI_STYLE_SIGNAL", message="Sample",
                count=2, evidence=["Исти образац", "Стари образац"])]
        if text == "twice":
            return [SimpleNamespace(
                rule_id="sr_test", category="AI_STYLE_SIGNAL", message="Sample",
                count=3, evidence=["Стари образац", "Исти образац", "Исти образац"])]
        return []

    module = SimpleNamespace(review=serbian_stub)
    findings = serbian_new(module, "before", "after")
    assert len(findings) == 1 and findings[0]["evidence"] == ["Нови образац"], findings
    assert findings[0]["count"] == 1
    assert serbian_new(module, "before", "same") == []
    repeated = serbian_new(module, "before", "twice")
    assert len(repeated) == 1 and repeated[0]["count"] == 1, repeated
    assert repeated[0]["evidence"] == ["Исти образац"], repeated

    return {"result": "OK", "frontmatter": "excluded", "mdx_jsx": "excluded",
            "gfm_tables": "excluded", "nested_list_prose": "retained",
            "inline_math": "excluded", "serbian_evidence_delta": "verified",
            "readable_prose": "retained", "languages": ["ru", "sr"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--files", type=Path)
    parser.add_argument("--base-root", type=Path)
    parser.add_argument("--head-root", type=Path)
    parser.add_argument("--russian-root", type=Path)
    parser.add_argument("--serbian-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False))
        return 0
    if not all((args.files, args.base_root, args.head_root, args.russian_root, args.serbian_root, args.output)):
        parser.error("Review requires all root, file list and output arguments")
    paths = [json.loads(row) for row in args.files.read_text(encoding="utf-8").splitlines()
             if row.strip()]
    result = report(paths, args.base_root, args.head_root, args.russian_root, args.serbian_root)
    args.output.write_text(result, encoding="utf-8")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
