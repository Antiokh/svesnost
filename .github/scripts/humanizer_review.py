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
REFERENCE = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:\s")
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
    if not lines or lines[0].strip() != "---":
        return "".join(lines)
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            return "".join(lines[index + 1:])
    return ""  # Fail closed: never treat unterminated YAML as prose.


def hide(chars: list[str], start: int, end: int) -> None:
    for pos in range(start, min(len(chars), end)):
        if chars[pos] != "\n":
            chars[pos] = " "


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
            if row_depth != depth or not _table_cells(row_text):
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

def prose_only(raw: str) -> str:
    text = body_without_frontmatter(raw)
    chars = list(text)
    offset = 0
    in_fence = None
    in_math = None
    list_indents: list[tuple[int, int]] = []
    after_blank = False

    for line in text.splitlines(keepends=True):
        trimmed = line.lstrip()
        indent = len(line) - len(trimmed)
        list_marker = re.match(r"^( *)(?:[-*+]|\d+[.)])\s+", line)
        if not trimmed.strip():
            after_blank = bool(list_indents)
        elif list_marker:
            if indent >= 4 and not list_indents:
                hide(chars, offset, offset + len(line))
            else:
                while list_indents and list_indents[-1][0] >= indent:
                    list_indents.pop()
                list_indents.append((indent, len(list_marker.group(0))))
            after_blank = False
        elif indent >= 4:
            if (not list_indents or
                    (after_blank and indent >= list_indents[-1][1] + 4)):
                hide(chars, offset, offset + len(line))
            after_blank = False
        else:
            if list_indents and indent < list_indents[0][1]:
                list_indents.clear()
            after_blank = False

        fence_text, quote_depth = _fence_line(line, list_indents)
        fence = FENCE.match(fence_text)
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
            if in_math in line:
                in_math = None
        elif fence:
            marker = fence.group(1)
            in_fence = (marker[0], len(marker), quote_depth)
            hide(chars, offset, offset + len(line))
        elif trimmed.startswith((chr(36) * 2, r"\[")):
            hide(chars, offset, offset + len(line))
            closing = chr(36) * 2 if trimmed.startswith(chr(36) * 2) else r"\]"
            if closing not in trimmed[2:]:
                in_math = closing
        elif (MODULE.match(line)
              or REFERENCE.match(line)
              or re.match(r"^ {0,3}(?:::{1}|!{3})", line)
              or re.match(r"^ {0,3}(?:-{3,}|\*{3,}|_{3,})\s*$", line)
              or re.match(r"^\s*\|?\s*:?[-]{3,}:?\s*(?:\|.*)?$", line)):
            hide(chars, offset, offset + len(line))
        offset += len(line)

    mask_gfm_tables(text, chars)

    pos = 0
    while pos < len(text):
        if chars[pos] == " " and text[pos] != " ":
            pos += 1
            continue
        if text.startswith("<!--", pos):
            end = text.find("-->", pos + 4)
            end = len(text) if end < 0 else end + 3
            hide(chars, pos, end)
            pos = end
            continue
        if text[pos] == "<":
            match = re.match(r"</?([A-Za-z][\w.:-]*)(?=[\s/>])", text[pos:])
            if match or text.startswith(("<>", "</>"), pos):
                tagname = match.group(1) if match else ""
                end = end_tag(text, pos)
                closing = text.startswith("</", pos)
                selfclosing = text[pos:end].rstrip().endswith("/>")
                hide(chars, pos, end)
                if tagname.lower() in RAW_ELEMENTS and not closing and not selfclosing:
                    close = re.search(r"</" + re.escape(tagname) + r"\s*>", text[end:], re.I)
                    if close:
                        stop = end + close.end()
                        hide(chars, end, stop)
                        end = stop
                    elif tagname.lower() in TABLE_ELEMENTS:
                        # Fail closed for malformed/unclosed table elements.
                        hide(chars, end, len(text))
                        end = len(text)
                pos = end
                continue
        if text[pos] == "{":
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

    # Keep the visible link label, not its target/attributes. Footnote prose stays.
    cleaned = "".join(chars)
    for match in re.finditer(r"!?\[([^\]\n]*)\]\(([^)\n]*)\)", cleaned):
        start, end = match.span()
        if cleaned[start] == "!":
            hide(chars, start, end)  # Image alt is not article prose.
        else:
            hide(chars, start, start + 1)
            label_end = start + 1 + len(match.group(1))
            hide(chars, label_end, end)
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
    for match in re.finditer(r"https?://[^\s)>\]]+", cleaned):
        hide(chars, *match.span())
    for match in re.finditer(r"\[\^[^\]\n]+\](?=:?)", cleaned):
        hide(chars, *match.span())
    for match in re.finditer(r"(?m)^ {0,3}(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s+)", cleaned):
        hide(chars, *match.span())
    for match in re.finditer(r"\\\([\s\S]*?\\\)", cleaned):
        hide(chars, *match.span())

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


def report(paths: list[str], base_root: Path, head_root: Path, ru_root: Path, sr_root: Path) -> str:
    groups: dict[str, list[tuple[str, list[dict]]]] = {"ru": [], "sr": []}
    ru = sr = None
    for file in paths:
        lang = language(file)
        if lang is None:
            continue
        before = prose_only(read_article(base_root, file))
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
    assert language(DOCS + "ru/story.md") == "ru"
    assert language(DOCS + "en/story.md") is None
    assert language(DOCS + "story.mdx") == "sr"
    assert language("docs/story.md") is None
    assert prose_only("---\ntitle: private\n") == ""
    special = "Проза до формулы.\n---\n:::note\nСодержимое примечания — это проза.\n:::\n@MATH@\nx^2 + 1\n@MATH@\nИ проза после.\n".replace("@MATH@", chr(36) * 2).replace("@MATH@", chr(36) * 2)
    isolated = prose_only(special)
    assert "Проза до формулы." in isolated and "Содержимое примечания" in isolated
    assert "И проза после." in isolated and "x^2" not in isolated
    assert "---" not in isolated and ":::note" not in isolated and chr(36) * 2 not in isolated

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

    # Reference-style Markdown links: keep reader-visible label only.
    reference = (
        "Читайте [проверенную публикацию][source] и [документ][].\n"
        "Не показывать ![изображение][cover].\n"
        "[source]: https://example.org/secret\n"
        "[cover]: https://example.org/image.png\n"
    )
    reference_body = prose_only(reference)
    assert "проверенную публикацию" in reference_body, reference_body
    assert "документ" in reference_body, reference_body
    for hidden in ("[source]", "[cover]", "изображение", "example.org", "[]"):
        assert hidden not in reference_body, (hidden, reference_body)
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
    paths = [p.strip() for p in args.files.read_text(encoding="utf-8").splitlines() if p.strip()]
    result = report(paths, args.base_root, args.head_root, args.russian_root, args.serbian_root)
    args.output.write_text(result, encoding="utf-8")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
