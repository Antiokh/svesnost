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
RAW_ELEMENTS = ("script", "style", "CodeBlock", "pre", "code")


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


def prose_only(raw: str) -> str:
    text = body_without_frontmatter(raw)
    chars = list(text)
    offset = 0
    in_fence = None

    for line in text.splitlines(keepends=True):
        trimmed = line.lstrip()
        fence = FENCE.match(line)
        if in_fence:
            hide(chars, offset, offset + len(line))
            if trimmed.startswith(in_fence[0] * len(in_fence)):
                in_fence = None
        elif fence:
            marker = fence.group(1)
            in_fence = (marker[0], len(marker))
            hide(chars, offset, offset + len(line))
        elif (line.startswith(("    ", "\t")) and trimmed.strip()
              or MODULE.match(line)
              or REFERENCE.match(line)
              or re.match(r"^ {0,3}(?:::|:::|!!!)\s", line)
              or re.match(r"^\s*\|?\s*:?[-]{3,}:?\s*(?:\|.*)?$", line)):
            hide(chars, offset, offset + len(line))
        offset += len(line)

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
                if tagname in RAW_ELEMENTS and not closing and not selfclosing:
                    close = re.search(r"</" + re.escape(tagname) + r"\s*>", text[end:], re.I)
                    if close:
                        stop = end + close.end()
                        hide(chars, end, stop)
                        end = stop
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
    for match in re.finditer(r"https?://[^\s)>\]]+", cleaned):
        hide(chars, *match.span())
    for match in re.finditer(r"\[\^[^\]\n]+\](?=:?)", cleaned):
        hide(chars, *match.span())
    for match in re.finditer(r"(?m)^ {0,3}(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s+)", cleaned):
        hide(chars, *match.span())

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


def serbian_new(module, base: str, head: str) -> list[dict]:
    original = {f.rule_id: f.count for f in module.review(base)} if base else {}
    return [
        {"rule_id": f.rule_id, "category": f.category, "message": f.message,
         "count": f.count, "evidence": f.evidence}
        for f in module.review(head)
        if f.count > original.get(f.rule_id, 0)
    ] if head else []


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
            if len(CYRILLIC.findall(after)) < 25:
                continue
            ru = ru or load_module(ru_root, "scripts/review.py", "svesnost_humanizer_ru")
            findings = russian_new(ru, before, after)
        else:
            if len(CYRILLIC.findall(after)) < 25:
                continue
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
    return {"result": "OK", "frontmatter": "excluded", "mdx_jsx": "excluded",
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
