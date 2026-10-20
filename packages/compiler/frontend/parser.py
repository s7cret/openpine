"""Literal/call subset of the pinned Pine grammar; no Python evaluation."""

from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass

from packages.contracts import Diagnostic, OpenPineError, SourceSpan


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class Literal:
    value: int | float | bool | str
    span: SourceSpan


@dataclass(frozen=True)
class Reference:
    name: str
    span: SourceSpan


@dataclass(frozen=True)
class Unary:
    operator: str
    operand: Expression
    span: SourceSpan


@dataclass(frozen=True)
class Argument:
    name: str | None
    expression: Expression
    span: SourceSpan


@dataclass(frozen=True)
class Call:
    name: str
    arguments: tuple[Argument, ...]
    span: SourceSpan


type Expression = Literal | Reference | Unary | Call


class Source:
    def __init__(self, text: str, name: str):
        self.text, self.name = text, name
        self.starts = [0] + [match.end() for match in re.finditer("\n", text)]

    def span(self, start: int, end: int) -> SourceSpan:
        line = bisect.bisect_right(self.starts, start) - 1
        end_line = bisect.bisect_right(self.starts, end) - 1
        return SourceSpan(self.name, start, end, line + 1, start - self.starts[line] + 1,
                          end_line + 1, end - self.starts[end_line] + 1)

    def fail(self, category: str, code: str, message: str, start: int, end: int,
             rule: str = "rule.grammar"):
        raise OpenPineError(Diagnostic(category, code, message, self.span(start, end), rule))


def language_version(source: Source, pattern: str) -> int:
    """Read version comments while skipping quoted string contents."""
    versions = []
    quote = None
    escaped = False
    position = 0
    while position < len(source.text):
        character = source.text[position]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            position += 1
        elif character in "\"'":
            quote = character
            position += 1
        elif source.text.startswith("//", position):
            end = source.text.find("\n", position)
            end = len(source.text) if end < 0 else end
            comment = source.text[position:end].rstrip("\r")
            match = re.fullmatch(pattern, comment)
            if match:
                number = match.group(1).lstrip("0") or "0"
                if number not in ("1", "2", "3", "4", "5", "6"):
                    source.fail("LANGUAGE_INVALID", "LANGUAGE_VERSION", "Pine version must be 1..6.", position, end)
                versions.append((int(number), position, end))
            elif comment.startswith("//@version"):
                source.fail("LANGUAGE_INVALID", "VERSION_ANNOTATION", "Malformed version annotation.", position, end)
            position = end
        else:
            position += 1
    if len(versions) > 1:
        source.fail("PROFILE_UNCOVERED", "MULTIPLE_VERSION_ANNOTATIONS",
                    "This slice accepts one version annotation.", versions[1][1], versions[1][2])
    if not versions:
        return 1
    version, start, end = versions[0]
    if version not in range(1, 7):
        source.fail("LANGUAGE_INVALID", "LANGUAGE_VERSION", "Pine version must be 1..6.", start, end)
    return version


def tokenize(source: Source, pattern: str) -> tuple[Token, ...]:
    matcher = re.compile(pattern)
    tokens = []
    position = depth = 0
    while position < len(source.text):
        character = source.text[position]
        if source.text.startswith("//", position):
            end = source.text.find("\n", position)
            position = len(source.text) if end < 0 else end
            continue
        if character in "\r\n":
            end = position + (2 if source.text.startswith("\r\n", position) else 1)
            if depth == 0:
                tokens.append(Token("NEWLINE", source.text[position:end], position, end))
            position = end
            continue
        if character in "\"'" and source.text.startswith(character * 3, position):
            source.fail("PROFILE_UNCOVERED", "MULTILINE_STRING", "Multiline strings are outside this slice.", position, position + 3)
        match = matcher.match(source.text, position)
        if not match:
            if character in "\"'":
                source.fail("LANGUAGE_INVALID", "UNTERMINATED_STRING", "Unterminated string literal.", position, position + 1)
            source.fail("PROFILE_UNCOVERED", "TOKEN_UNCOVERED", "Token is outside the literal/call slice.", position, position + 1)
        kind, text, end = match.lastgroup, match.group(), match.end()
        if kind != "SPACE":
            if text == "(":
                depth += 1
            elif text == ")":
                depth -= 1
                if depth < 0:
                    source.fail("LANGUAGE_INVALID", "UNMATCHED_PARENTHESIS", "Unexpected closing parenthesis.", position, end)
            tokens.append(Token(kind, text, position, end))
            if len(tokens) > 4096:
                source.fail("PROFILE_UNCOVERED", "TOKEN_LIMIT", "Literal slice token limit exceeded.", position, end)
        position = end
    tokens.append(Token("EOF", "", len(source.text), len(source.text)))
    return tuple(tokens)


class Parser:
    def __init__(self, source: Source, grammar: dict):
        self.source = source
        self.tokens = tokenize(source, grammar["token_pattern"])
        self.position = 0
        self.binary_operators = {row["token"] for row in grammar["operators"]}

    @property
    def current(self) -> Token:
        return self.tokens[self.position]

    def take(self) -> Token:
        token = self.current
        self.position += 1
        return token

    def expect(self, text: str) -> Token:
        if self.current.text != text:
            token = self.current
            if text == ")" and token.text in self.binary_operators | {"[", "?"}:
                self.source.fail("PROFILE_UNCOVERED", "EXPRESSION_UNCOVERED", "Expression syntax is outside the literal/call slice.", token.start, token.end)
            self.source.fail("LANGUAGE_INVALID", "EXPECTED_TOKEN", f"Expected {text!r}.", token.start, token.end)
        return self.take()

    def string(self, token: Token) -> str:
        result, position = [], 1
        escapes = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "\"": "\"", "'": "'"}
        while position < len(token.text) - 1:
            character = token.text[position]
            if character in "\r\n":
                self.source.fail("PROFILE_UNCOVERED", "MULTILINE_STRING", "Multiline strings are outside this slice.", token.start, token.end)
            if character == "\\":
                position += 1
                character = token.text[position]
                if character not in escapes:
                    self.source.fail("PROFILE_UNCOVERED", "STRING_ESCAPE", "String escape is outside this slice.", token.start, token.end)
                character = escapes[character]
            result.append(character)
            position += 1
        return "".join(result)

    def expression(self, depth: int = 0) -> Expression:
        token = self.current
        if depth > 48:
            self.source.fail("PROFILE_UNCOVERED", "EXPRESSION_DEPTH", "Literal slice nesting limit exceeded.", token.start, token.end)
        if token.text in ("+", "-"):
            self.take()
            operand = self.expression(depth + 1)
            return Unary(token.text, operand, self.source.span(token.start, operand.span.end))
        if token.kind == "NUMBER":
            self.take()
            if len(token.text) > 256:
                self.source.fail("PROFILE_UNCOVERED", "NUMERIC_RANGE", "Numeric spelling exceeds this slice's limit.", token.start, token.end)
            value = float(token.text) if any(marker in token.text for marker in ".eE") else int(token.text)
            if (type(value) is float and not math.isfinite(value)) or (type(value) is int and value > 2**63 - 1):
                self.source.fail("PROFILE_UNCOVERED", "NUMERIC_RANGE", "Literal is outside this implementation's finite numeric range.", token.start, token.end)
            return Literal(value, self.source.span(token.start, token.end))
        if token.kind == "STRING":
            self.take()
            return Literal(self.string(token), self.source.span(token.start, token.end))
        if token.text in ("true", "false"):
            self.take()
            return Literal(token.text == "true", self.source.span(token.start, token.end))
        if token.text == "(":
            self.take()
            result = self.expression(depth + 1)
            self.expect(")")
            return result
        if token.kind == "NAME":
            self.take()
            if self.current.text != "(":
                return Reference(token.text, self.source.span(token.start, token.end))
            self.take()
            arguments = []
            while self.current.text != ")":
                start = self.current.start
                name = None
                if self.current.kind == "NAME" and self.tokens[self.position + 1].text == "=":
                    name = self.take().text
                    self.take()
                expression = self.expression(depth + 1)
                arguments.append(Argument(name, expression, self.source.span(start, expression.span.end)))
                if self.current.text != ",":
                    break
                self.take()
                if self.current.text == ")":
                    self.source.fail("PROFILE_UNCOVERED", "TRAILING_COMMA", "Trailing commas are outside this slice.", self.current.start, self.current.end)
            closing = self.expect(")")
            return Call(token.text, tuple(arguments), self.source.span(token.start, closing.end))
        if token.text == "[":
            self.source.fail("PROFILE_UNCOVERED", "EXPRESSION_UNCOVERED", "Tuple/collection expressions are outside this slice.", token.start, token.end)
        self.source.fail("LANGUAGE_INVALID", "EXPECTED_EXPRESSION", "Expected a literal or call expression.", token.start, token.end)

    def parse(self) -> tuple[Call, ...]:
        statements = []
        while self.current.kind != "EOF":
            if self.current.kind == "NEWLINE":
                self.take()
                continue
            token = self.current
            if token.start != self.source.starts[bisect.bisect_right(self.source.starts, token.start) - 1]:
                self.source.fail("PROFILE_UNCOVERED", "INDENTED_STATEMENT", "Only unindented global calls are implemented.", token.start, token.end)
            statement = self.expression()
            if not isinstance(statement, Call):
                self.source.fail("PROFILE_UNCOVERED", "STATEMENT_UNCOVERED", "Only global call statements are implemented.", statement.span.start, statement.span.end)
            if self.current.kind not in ("NEWLINE", "EOF"):
                token = self.current
                self.source.fail("PROFILE_UNCOVERED", "EXPRESSION_UNCOVERED", "Expression syntax is outside the literal/call slice.", token.start, token.end)
            statements.append(statement)
        return tuple(statements)
