"""Bounded statements and Pratt expressions from the pinned grammar."""

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


@dataclass(frozen=True)
class Binary:
    operator: str
    left: Expression
    right: Expression
    span: SourceSpan


@dataclass(frozen=True)
class Conditional:
    condition: Expression
    when_true: Expression
    when_false: Expression
    span: SourceSpan


@dataclass(frozen=True)
class History:
    operand: Expression
    offset: Expression
    span: SourceSpan


@dataclass(frozen=True)
class Declaration:
    name: str
    annotation: str | None
    mode: str
    expression: Expression
    span: SourceSpan


@dataclass(frozen=True)
class Reassignment:
    name: str
    expression: Expression
    span: SourceSpan


@dataclass(frozen=True)
class If:
    condition: Expression
    body: tuple[Statement, ...]
    otherwise: tuple[Statement, ...]
    span: SourceSpan


@dataclass(frozen=True)
class ValueIf:
    condition: Expression
    body: tuple[Statement, ...]
    otherwise: tuple[Statement, ...]
    span: SourceSpan


@dataclass(frozen=True)
class ExpressionStatement:
    expression: Expression
    span: SourceSpan


@dataclass(frozen=True)
class Parameter:
    name: str
    annotation: str | None
    qualifier: str | None
    span: SourceSpan


@dataclass(frozen=True)
class Function:
    name: str
    parameters: tuple[Parameter, ...]
    body: tuple[Statement, ...]
    span: SourceSpan


type Expression = Literal | Reference | Unary | Call | Binary | Conditional | History | ValueIf
type Statement = Call | Declaration | Reassignment | If | ExpressionStatement | Function


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
    matcher = re.compile(pattern.replace("(?P<OP>", "(?P<OP>=>|"))
    tokens = []
    position = depth = 0
    line_start = True
    indentation = [0]
    while position < len(source.text):
        if line_start and depth == 0:
            start = position
            while position < len(source.text) and source.text[position] in " \t":
                position += 1
            spaces = source.text[start:position]
            if position == len(source.text):
                break
            if source.text[position] not in "\r\n" and not source.text.startswith("//", position):
                if "\t" in spaces or len(spaces) % 4:
                    source.fail("PROFILE_UNCOVERED", "INDENTATION", "Blocks require multiples of four spaces.", start, position)
                if len(spaces) > indentation[-1]:
                    if len(spaces) != indentation[-1] + 4:
                        source.fail("LANGUAGE_INVALID", "INDENTATION", "A block must increase indentation by four spaces.", start, position)
                    indentation.append(len(spaces))
                    tokens.append(Token("INDENT", "", position, position))
                else:
                    while len(spaces) < indentation[-1]:
                        indentation.pop()
                        tokens.append(Token("DEDENT", "", position, position))
            line_start = False
        character = source.text[position]
        if source.text.startswith("//", position):
            end = source.text.find("\n", position)
            position = len(source.text) if end < 0 else end
            continue
        if character in "\r\n":
            end = position + (2 if source.text.startswith("\r\n", position) else 1)
            if depth == 0:
                tokens.append(Token("NEWLINE", source.text[position:end], position, end))
            line_start = True
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
            line_start = False
            if text in ("(", "["):
                depth += 1
            elif text in (")", "]"):
                depth -= 1
                if depth < 0:
                    source.fail("LANGUAGE_INVALID", "UNMATCHED_PARENTHESIS", "Unexpected closing parenthesis.", position, end)
            tokens.append(Token(kind, text, position, end))
            if len(tokens) > 4096:
                source.fail("PROFILE_UNCOVERED", "TOKEN_LIMIT", "Literal slice token limit exceeded.", position, end)
        position = end
    while len(indentation) > 1:
        indentation.pop()
        tokens.append(Token("DEDENT", "", len(source.text), len(source.text)))
    if len(tokens) > 4096:
        source.fail("PROFILE_UNCOVERED", "TOKEN_LIMIT", "Token limit exceeded.", 0, 0)
    tokens.append(Token("EOF", "", len(source.text), len(source.text)))
    return tuple(tokens)


class Parser:
    def __init__(self, source: Source, grammar: dict):
        self.source = source
        self.tokens = tokenize(source, grammar["token_pattern"])
        self.position = 0
        self.binary_operators = {row["token"] for row in grammar["operators"]}
        self.precedence = {row["token"]: row["precedence"] for row in grammar["operators"]}
        self.uncovered = set(grammar["uncovered_keywords"]) | {"varip", "once"}
        self.levels = {}
        self.block_depth = 0
        self.value_depth = 0

    def mark(self, node, *children):
        height = 1 + max((self.levels[id(child)] for child in children), default=0)
        if height > 48:
            self.source.fail("PROFILE_UNCOVERED", "EXPRESSION_DEPTH", "Expression nesting limit exceeded.", node.span.start, node.span.end)
        self.levels[id(node)] = height
        return node

    def peek(self, offset=1):
        return self.tokens[min(self.position + offset, len(self.tokens) - 1)]

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

    def expression(self, minimum=0, depth: int = 0) -> Expression:
        left = self.primary(depth)
        while True:
            token = self.current
            if token.text == "[" and minimum <= 80:
                self.take()
                offset = self.expression(0, depth + 1)
                closing = self.expect("]")
                left = self.mark(History(left, offset, self.source.span(left.span.start, closing.end)), left, offset)
            elif token.text == "?" and minimum <= 5:
                self.take()
                when_true = self.expression(0, depth + 1)
                self.expect(":")
                when_false = self.expression(5, depth + 1)
                left = self.mark(Conditional(left, when_true, when_false, self.source.span(left.span.start, when_false.span.end)), left, when_true, when_false)
            elif token.text in self.precedence and self.precedence[token.text] >= minimum:
                self.take()
                right = self.expression(self.precedence[token.text] + 1, depth + 1)
                left = self.mark(Binary(token.text, left, right, self.source.span(left.span.start, right.span.end)), left, right)
            else:
                return left

    def primary(self, depth: int) -> Expression:
        token = self.current
        if depth > 48:
            self.source.fail("PROFILE_UNCOVERED", "EXPRESSION_DEPTH", "Literal slice nesting limit exceeded.", token.start, token.end)
        if token.text == "if":
            self.value_depth += 1
            node = self.if_statement()
            self.value_depth -= 1
            return self.mark(ValueIf(node.condition, node.body, node.otherwise, node.span), node.condition)
        if token.text in ("+", "-", "not"):
            self.take()
            operand = self.expression(70, depth + 1)
            return self.mark(Unary(token.text, operand, self.source.span(token.start, operand.span.end)), operand)
        if token.kind == "NUMBER":
            self.take()
            if len(token.text) > 256:
                self.source.fail("PROFILE_UNCOVERED", "NUMERIC_RANGE", "Numeric spelling exceeds this slice's limit.", token.start, token.end)
            value = float(token.text) if any(marker in token.text for marker in ".eE") else int(token.text)
            if (type(value) is float and not math.isfinite(value)) or (type(value) is int and value > 2**63 - 1):
                self.source.fail("PROFILE_UNCOVERED", "NUMERIC_RANGE", "Literal is outside this implementation's finite numeric range.", token.start, token.end)
            return self.mark(Literal(value, self.source.span(token.start, token.end)))
        if token.kind == "STRING":
            self.take()
            return self.mark(Literal(self.string(token), self.source.span(token.start, token.end)))
        if token.text in ("true", "false") or (token.text == "na" and self.peek().text != "("):
            self.take()
            return self.mark(Literal(None if token.text == "na" else token.text == "true", self.source.span(token.start, token.end)))
        if token.text == "(":
            self.take()
            result = self.expression(0, depth + 1)
            self.expect(")")
            return result
        if token.kind == "NAME":
            if token.text in self.uncovered or token.text in ("if", "else"):
                self.source.fail("PROFILE_UNCOVERED", "CONSTRUCT_UNCOVERED", "Construct is outside this statement profile.", token.start, token.end)
            self.take()
            if self.current.text != "(":
                return self.mark(Reference(token.text, self.source.span(token.start, token.end)))
            self.take()
            arguments = []
            while self.current.text != ")":
                start = self.current.start
                name = None
                if self.current.kind == "NAME" and self.tokens[self.position + 1].text == "=":
                    name = self.take().text
                    self.take()
                expression = self.expression(0, depth + 1)
                arguments.append(Argument(name, expression, self.source.span(start, expression.span.end)))
                if self.current.text != ",":
                    break
                self.take()
                if self.current.text == ")":
                    self.source.fail("PROFILE_UNCOVERED", "TRAILING_COMMA", "Trailing commas are outside this slice.", self.current.start, self.current.end)
            closing = self.expect(")")
            return self.mark(Call(token.text, tuple(arguments), self.source.span(token.start, closing.end)), *(argument.expression for argument in arguments))
        if token.text == "[":
            self.source.fail("PROFILE_UNCOVERED", "EXPRESSION_UNCOVERED", "Tuple/collection expressions are outside this slice.", token.start, token.end)
        self.source.fail("LANGUAGE_INVALID", "EXPECTED_EXPRESSION", "Expected a literal or call expression.", token.start, token.end)

    def newlines(self):
        while self.current.kind == "NEWLINE":
            self.take()

    def block(self) -> tuple[Statement, ...]:
        if self.current.kind != "NEWLINE":
            self.source.fail("LANGUAGE_INVALID", "BLOCK_NEWLINE", "A block header requires a newline.", self.current.start, self.current.end)
        self.newlines()
        if self.current.kind != "INDENT":
            self.source.fail("LANGUAGE_INVALID", "BLOCK_REQUIRED", "Expected an indented nonempty block.", self.current.start, self.current.end)
        self.take()
        self.block_depth += 1
        if self.block_depth > 24:
            self.source.fail("PROFILE_UNCOVERED", "BLOCK_DEPTH", "Block nesting limit exceeded.", self.current.start, self.current.end)
        statements = []
        self.newlines()
        while self.current.kind not in ("DEDENT", "EOF"):
            statements.append(self.statement())
            self.newlines()
        if self.current.kind != "DEDENT" or not statements:
            self.source.fail("LANGUAGE_INVALID", "BLOCK_REQUIRED", "Expected a complete nonempty block.", self.current.start, self.current.end)
        self.take()
        self.block_depth -= 1
        return tuple(statements)

    def if_statement(self) -> If:
        start = self.expect("if")
        condition = self.expression()
        body = self.block()
        self.newlines()
        otherwise = ()
        if self.current.text == "else":
            self.take()
            otherwise = (self.if_statement(),) if self.current.text == "if" else self.block()
        end = (otherwise or body)[-1].span.end
        return If(condition, body, otherwise, self.source.span(start.start, end))

    def function_ahead(self):
        if self.current.kind != "NAME" or self.peek().text != "(":
            return False
        depth = 0
        for token in self.tokens[self.position + 1:]:
            if token.text == "(":
                depth += 1
            elif token.text == ")":
                depth -= 1
                if depth == 0:
                    index = self.tokens.index(token, self.position + 1)
                    return self.tokens[min(index + 1, len(self.tokens) - 1)].text == "=>"
            elif token.kind in ("NEWLINE", "EOF"):
                return False
        return False

    def function(self):
        start = self.take()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", start.text):
            self.source.fail("LANGUAGE_INVALID", "FUNCTION_NAME", "Function name must be an unqualified identifier.", start.start, start.end)
        self.expect("(")
        parameters = []
        while self.current.text != ")":
            beginning = self.current.start
            qualifier = self.take().text if self.current.text in ("simple", "series") else None
            annotation = self.take().text if self.current.text in ("int", "float", "bool", "string") and self.peek().kind == "NAME" else None
            name = self.take()
            if name.kind != "NAME" or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name.text) or name.text in {"if", "else", "var", "na", "true", "false", "and", "or", "not"} or (qualifier and annotation is None):
                self.source.fail("LANGUAGE_INVALID", "FUNCTION_PARAMETER", "Expected a parameter with an optional scalar type/qualifier.", beginning, name.end)
            if self.current.text == "=":
                self.source.fail("PROFILE_UNCOVERED", "FUNCTION_DEFAULT_UNCOVERED", "Parameter defaults are outside this function profile.", self.current.start, self.current.end)
            parameters.append(Parameter(name.text, annotation, qualifier, self.source.span(beginning, name.end)))
            if self.current.text != ",":
                break
            self.take()
        self.expect(")")
        self.expect("=>")
        self.value_depth += 1
        if self.current.kind == "NEWLINE":
            body = self.block()
        else:
            expression = self.expression()
            body = (ExpressionStatement(expression, expression.span),)
            if self.current.kind not in ("NEWLINE", "DEDENT", "EOF"):
                self.source.fail("PROFILE_UNCOVERED", "EXPRESSION_UNCOVERED", "Expected the end of the function body.", self.current.start, self.current.end)
            self.newlines()
        self.value_depth -= 1
        return Function(start.text, tuple(parameters), body, self.source.span(start.start, body[-1].span.end))

    def statement(self) -> Statement:
        token = self.current
        if self.function_ahead():
            return self.function()
        if token.text == "if":
            return self.if_statement()
        if token.text in self.uncovered:
            self.source.fail("PROFILE_UNCOVERED", "CONSTRUCT_UNCOVERED", "Construct is outside this statement profile.", token.start, token.end)
        mode = "bar"
        annotation = None
        if token.text == "var":
            self.take()
            mode = "var"
        if self.current.text in ("int", "float", "bool", "string") and self.peek().kind == "NAME":
            annotation = self.take().text
        is_declaration = mode == "var" or annotation is not None or (self.current.kind == "NAME" and self.peek().text == "=")
        if is_declaration:
            name = self.take()
            if name.kind != "NAME" or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name.text) or name.text in {"if", "else", "var", "true", "false", "na", "not", "and", "or"}:
                self.source.fail("LANGUAGE_INVALID", "VARIABLE_NAME", "Invalid variable name.", name.start, name.end)
            self.expect("=")
            value = self.expression()
            statement = Declaration(name.text, annotation, mode, value, self.source.span(token.start, value.span.end))
        elif self.current.kind == "NAME" and self.peek().text == ":=":
            name = self.take()
            self.take()
            value = self.expression()
            statement = Reassignment(name.text, value, self.source.span(token.start, value.span.end))
        else:
            statement = self.expression()
            if not isinstance(statement, Call):
                if self.value_depth:
                    statement = ExpressionStatement(statement, statement.span)
                else:
                    self.source.fail("PROFILE_UNCOVERED", "STATEMENT_UNCOVERED", "Expected declaration, assignment, block or call.", statement.span.start, statement.span.end)
        # A value-if consumes its closing dedent and is already at the next line.
        ended_block = self.position and self.tokens[self.position - 1].kind in ("DEDENT", "NEWLINE")
        if self.current.kind not in ("NEWLINE", "DEDENT", "EOF") and not ended_block:
            self.source.fail("PROFILE_UNCOVERED", "EXPRESSION_UNCOVERED", "Syntax is outside this statement profile.", self.current.start, self.current.end)
        self.newlines()
        return statement

    def parse(self) -> tuple[Statement, ...]:
        statements = []
        self.newlines()
        while self.current.kind != "EOF":
            if self.current.kind in ("INDENT", "DEDENT"):
                self.source.fail("LANGUAGE_INVALID", "INDENTATION", "Unexpected indentation.", self.current.start, self.current.end)
            statements.append(self.statement())
        return tuple(statements)
