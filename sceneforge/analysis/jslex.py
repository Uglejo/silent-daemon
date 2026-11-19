from __future__ import annotations

import re
from dataclasses import dataclass

_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_NUMBER = re.compile(r"0[xX][0-9a-fA-F_]+n?|0[bB][01_]+n?|0[oO][0-7_]+n?|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)"
                     r"(?:[eE][+-]?\d+)?n?")
_PUNCT3 = (">>>=", "...", "===", "!==", "**=", "<<=", ">>=", ">>>", "&&=", "||=", "??=")
_PUNCT2 = ("=>", "==", "!=", "<=", ">=", "&&", "||", "??", "?.", "++", "--", "+=", "-=", "*=", "/=", "%=", "&=",
           "|=", "^=", "**", "<<", ">>")
_REGEX_AFTER_WORDS = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case", "do",
                      "else", "yield", "await"}
_CONDITION_WORDS = {"if", "while", "for", "with"}
_UNARY = ("-", "+", "!", "~")


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    start: int
    end: int


class LexError(ValueError):
    pass


def _keyword(toks: list[Token], k: int, words: set[str]) -> bool:
    return (toks[k].kind == "ident" and toks[k].text in words
            and not (k > 0 and toks[k - 1].kind == "punct" and toks[k - 1].text in (".", "?.")))


def tokens(source: str) -> list[Token]:
    out: list[Token] = []
    i, n = 0, len(source)
    braces: list[str] = []
    parens: list[bool] = []
    conditions: set[int] = set()

    def regex_allowed() -> bool:
        if not out:
            return True
        last = out[-1]
        if last.kind == "tmpl":
            return last.text.endswith("${")
        if last.kind in ("num", "str", "regex"):
            return False
        if last.kind == "ident":
            return _keyword(out, len(out) - 1, _REGEX_AFTER_WORDS)
        if last.text == ")":
            return len(out) - 1 in conditions
        return last.text not in ("]", "}")

    def read_template(j: int) -> int:
        while j < n:
            c = source[j]
            if c == "\\":
                j += 2
                continue
            if c == "`":
                return j + 1
            if c == "$" and j + 1 < n and source[j + 1] == "{":
                return j + 2
            j += 1
        raise LexError("unterminated template literal")

    while i < n:
        c = source[i]
        if c in " \t\r\n﻿ ":
            i += 1
            continue
        if source.startswith("//", i):
            j = source.find("\n", i)
            i = n if j < 0 else j
            continue
        if source.startswith("/*", i):
            j = source.find("*/", i + 2)
            if j < 0:
                raise LexError("unterminated comment")
            i = j + 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and source[j] != c:
                if source[j] == "\\":
                    j += 1
                elif source[j] == "\n":
                    raise LexError("newline in string")
                j += 1
            if j >= n:
                raise LexError("unterminated string")
            out.append(Token("str", source[i:j + 1], i, j + 1))
            i = j + 1
            continue
        if c == "`" or (c == "}" and braces and braces[-1] == "${"):
            if c == "}":
                braces.pop()
            j = read_template(i + 1)
            if source[j - 2:j] == "${":
                braces.append("${")
            out.append(Token("tmpl", source[i:j], i, j))
            i = j
            continue
        if c == "/" and regex_allowed():
            j, in_class = i + 1, False
            while j < n:
                d = source[j]
                if d == "\\":
                    j += 2
                    continue
                if d == "[":
                    in_class = True
                elif d == "]":
                    in_class = False
                elif d == "/" and not in_class:
                    break
                elif d == "\n":
                    raise LexError("newline in regex")
                j += 1
            j += 1
            while j < n and (source[j].isalnum() or source[j] in "_$"):
                j += 1
            out.append(Token("regex", source[i:j], i, j))
            i = j
            continue
        found = _IDENT.match(source, i)
        if found:
            out.append(Token("ident", found.group(), i, found.end()))
            i = found.end()
            continue
        if c.isdigit() or (c == "." and i + 1 < n and source[i + 1].isdigit()):
            found = _NUMBER.match(source, i)
            out.append(Token("num", found.group(), i, found.end()))
            i = found.end()
            continue
        punct = next((p for p in (*_PUNCT3, *_PUNCT2) if source.startswith(p, i)), c)
        if punct == "{":
            braces.append("{")
        elif punct == "}" and braces:
            braces.pop()
        elif punct == "(":
            parens.append(bool(out) and _keyword(out, len(out) - 1, _CONDITION_WORDS))
        elif punct == ")" and parens and parens.pop():
            conditions.add(len(out))
        out.append(Token("punct", punct, i, i + len(punct)))
        i += len(punct)
    return out


def closing(toks: list[Token], k: int) -> int:
    depth = 0
    for j in range(k, len(toks)):
        token = toks[j]
        if token.kind == "punct" and token.text in "([{":
            depth += 1
        elif token.kind == "punct" and token.text in ")]}":
            depth -= 1
            if depth == 0:
                return j
    raise LexError("unbalanced brackets")


def new_calls(source: str, classes: tuple[str, ...]) -> list[tuple[int, int, str]]:
    toks = tokens(source)
    found = []
    for k in range(len(toks) - 4):
        if (toks[k].text == "new" and toks[k + 1].text == "THREE" and toks[k + 2].text == "."
                and toks[k + 3].text in classes and toks[k + 4].text == "("):
            found.append((toks[k].start, toks[closing(toks, k + 4)].end, toks[k + 3].text))
    return found


def _operand_end(toks: list[Token], k: int) -> int | None:
    if k < len(toks) and toks[k].kind == "ident" and toks[k].text == "new":
        k += 1
    if k >= len(toks) or _keyword(toks, k, _REGEX_AFTER_WORDS):
        return None
    if toks[k].kind == "punct":
        if toks[k].text not in ("(", "["):
            return None
        k = closing(toks, k) + 1
    elif toks[k].kind in ("ident", "num", "str"):
        k += 1
    else:
        return None
    while k < len(toks) and toks[k].kind == "punct":
        if toks[k].text in ("(", "["):
            k = closing(toks, k) + 1
        elif toks[k].text in (".", "?.") and k + 1 < len(toks) and toks[k + 1].kind == "ident":
            k += 2
        elif toks[k].text == "?." and k + 1 < len(toks) and toks[k + 1].text in ("(", "["):
            k = closing(toks, k + 1) + 1
        else:
            break
    return k


def _is_prefix(toks: list[Token], k: int) -> bool:
    if k == 0:
        return True
    last = toks[k - 1]
    if last.kind == "tmpl":
        return last.text.endswith("${")
    if last.kind == "ident":
        return _keyword(toks, k - 1, _REGEX_AFTER_WORDS)
    return last.kind == "punct" and last.text not in (")", "]", "}", "++", "--")


def exponent_safe(source: str) -> str:
    try:
        toks = tokens(source)
        spans: set[tuple[int, int]] = set()
        for k, token in enumerate(toks):
            if token.kind != "punct" or token.text not in _UNARY or not _is_prefix(toks, k):
                continue
            start = k + 1
            while start < len(toks) and toks[start].kind == "punct" and toks[start].text in _UNARY:
                start += 1
            end = _operand_end(toks, start)
            if end is None or end >= len(toks) or toks[end].text != "**":
                continue
            while end is not None and end < len(toks) and toks[end].kind == "punct" and toks[end].text == "**":
                end += 1
                while end < len(toks) and toks[end].kind == "punct" and toks[end].text in _UNARY:
                    end += 1
                end = _operand_end(toks, end)
            if end is not None and not (end < len(toks) and toks[end].kind == "punct" and toks[end].text in ("++", "--")):
                spans.add((toks[start].start, toks[end - 1].end))
    except LexError:
        return source
    if not spans:
        return source
    cuts = sorted([(start, 1, "(") for start, _ in spans] + [(end, 0, ")") for _, end in spans])
    out, last = [], 0
    for offset, _, bracket in cuts:
        out += [source[last:offset], bracket]
        last = offset
    return "".join(out) + source[last:]


def line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1
