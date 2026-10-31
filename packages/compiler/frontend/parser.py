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
class MemberCall(Call):
    receiver: Expression
    member: str
    qualified: bool = False


@dataclass(frozen=True)
class FieldAccess:
    receiver: Expression
    member: str
    span: SourceSpan
    history_parenthesized: bool = False


@dataclass(frozen=True)
class FieldReassignment:
    target: Expression
    expression: Expression
    span: SourceSpan


@dataclass(frozen=True)
class InputOptions:
    values: tuple[Expression, ...]
    span: SourceSpan


@dataclass(frozen=True)
class TupleExpression:
    elements: tuple[Expression, ...]
    span: SourceSpan


@dataclass(frozen=True)
class TupleDeclaration:
    targets: tuple[Declaration, ...]
    expression: Expression
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
    default: Expression | None = None


@dataclass(frozen=True)
class Switch:
    body: tuple[Statement, ...]
    span: SourceSpan


@dataclass(frozen=True)
class ValueSwitch:
    body: tuple[Statement, ...]
    span: SourceSpan


@dataclass(frozen=True)
class Function:
    name: str
    parameters: tuple[Parameter, ...]
    body: tuple[Statement, ...]
    span: SourceSpan


@dataclass(frozen=True)
class Method(Function):
    pass


@dataclass(frozen=True)
class ObjectField:
    name: str
    annotation: str
    intrabar: bool
    default: Expression | None
    span: SourceSpan


@dataclass(frozen=True)
class ObjectType:
    name: str
    fields: tuple[ObjectField, ...]
    span: SourceSpan


@dataclass(frozen=True)
class EnumMember:
    name: str
    title: str
    span: SourceSpan


@dataclass(frozen=True)
class EnumType:
    name: str
    members: tuple[EnumMember, ...]
    span: SourceSpan


@dataclass(frozen=True)
class For:
    counter: Declaration
    initial: Expression
    final: Expression
    step: Expression
    body: tuple[Statement, ...]
    span: SourceSpan


@dataclass(frozen=True)
class While:
    condition: Expression
    body: tuple[Statement, ...]
    span: SourceSpan


@dataclass(frozen=True)
class ForIn:
    targets: tuple[Declaration, ...]
    collection: Expression
    body: tuple[Statement, ...]
    span: SourceSpan


@dataclass(frozen=True)
class ValueLoop:
    loop: For | While | ForIn
    span: SourceSpan


@dataclass(frozen=True)
class LoopControl:
    action: str
    span: SourceSpan


type Expression = Literal | Reference | Unary | Call | Binary | Conditional | History | ValueIf | TupleExpression | ValueLoop | ValueSwitch | FieldAccess
type Statement = Call | Declaration | Reassignment | If | ExpressionStatement | Function | TupleDeclaration | For | While | LoopControl | ForIn | Switch | ObjectType | EnumType | FieldReassignment


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
    matcher = re.compile(pattern.replace("(?P<OP>", r"(?P<OP>=>|\.|"))
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
    def __init__(self, source: Source, grammar: dict, *, allow_strategy=False, version=1):
        self.source = source
        self.tokens = tokenize(source, grammar["token_pattern"])
        self.position = 0
        self.binary_operators = {row["token"] for row in grammar["operators"]}
        self.precedence = {row["token"]: row["precedence"] for row in grammar["operators"]}
        self.uncovered = set(grammar["uncovered_keywords"]) | {"varip", "once"}
        self.uncovered -= {"for", "while", "break", "continue"}
        self.uncovered.discard('array')
        if version>=4 and allow_strategy:self.uncovered.discard('varip')
        if allow_strategy:self.uncovered.discard('strategy')
        self.levels = {}
        self.block_depth = 0
        self.value_depth = 0
        self.version = version
        self.uncovered.discard('switch')
        self.object_names=set()
        self.enum_names=set()
        self.parenthesized=set()

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
            # A block expression already consumed its terminating newline/dedent.
            # A tuple declaration on the next line is not a history postfix.
            if self.position and self.tokens[self.position - 1].kind == "DEDENT":return left
            token = self.current
            if token.text == '.' and minimum <= 80:
                self.take();name=self.take()
                if name.kind!='NAME' or not re.fullmatch(r'[A-Za-z_]\w*',name.text):
                    self.source.fail('LANGUAGE_INVALID','METHOD_NAME','Expected an unqualified method name.',name.start,name.end)
                if self.current.text!='(':
                    left=self.mark(FieldAccess(left,name.text,self.source.span(left.span.start,name.end),id(left) in self.parenthesized),left)
                    continue
                self.expect('(');arguments=[]
                while self.current.text!=')':
                    start=self.current.start;label=None
                    if self.current.kind=='NAME' and self.peek().text=='=':label=self.take().text;self.take()
                    value=self.expression(0,depth+1);arguments.append(Argument(label,value,self.source.span(start,value.span.end)))
                    if self.current.text!=',':break
                    self.take()
                    if self.current.text==')':self.source.fail('PROFILE_UNCOVERED','TRAILING_COMMA','Trailing commas are outside this slice.',self.current.start,self.current.end)
                end=self.expect(')')
                left=self.mark(MemberCall(name.text,tuple(arguments),self.source.span(left.span.start,end.end),left,name.text),left,*(a.expression for a in arguments))
            elif token.text == "[" and minimum <= 80:
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
        if token.text == 'switch':
            node = self.switch()
            return self.mark(ValueSwitch(node.body, node.span))
        if token.text in ("for", "while"):
            node = self.loop()
            return self.mark(ValueLoop(node, node.span))
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
            self.parenthesized.add(id(result))
            return result
        if token.kind == "NAME":
            if token.text in self.uncovered or token.text in ("if", "else"):
                self.source.fail("PROFILE_UNCOVERED", "CONSTRUCT_UNCOVERED", "Construct is outside this statement profile.", token.start, token.end)
            self.take()
            call_name = token.text
            if token.text == 'array.new' and self.current.text == '<':
                self.take()
                element = self.take()
                if element.text not in ('int', 'float', 'bool', 'string') and element.text not in self.object_names|self.enum_names:
                    self.source.fail('PROFILE_UNCOVERED', 'ARRAY_ELEMENT', 'Only primitive scalar array elements are admitted.', element.start, element.end)
                self.expect('>')
                call_name += '<' + element.text + '>'
            if self.current.text != "(":
                if call_name != token.text:self.source.fail('LANGUAGE_INVALID', 'EXPECTED_TOKEN', 'Expected array constructor call.', token.start, self.current.end)
                return self.mark(Reference(token.text, self.source.span(token.start, token.end)))
            self.take()
            arguments = []
            while self.current.text != ")":
                start = self.current.start
                name = None
                if self.current.kind == "NAME" and self.tokens[self.position + 1].text == "=":
                    name = self.take().text
                    self.take()
                if (token.text=='input' or token.text.startswith('input.')) and name=='options' and self.current.text=='[':
                    opening=self.take();values=[]
                    while self.current.text!=']':
                        values.append(self.expression(0,depth+1))
                        if len(values)>64:self.source.fail('PROFILE_UNCOVERED','INPUT_OPTIONS','At most 64 input options.',opening.start,self.current.end)
                        if self.current.text!=',':break
                        self.take()
                    closing=self.expect(']')
                    expression=self.mark(InputOptions(tuple(values),self.source.span(opening.start,closing.end)),*values)
                else:expression = self.expression(0, depth + 1)
                arguments.append(Argument(name, expression, self.source.span(start, expression.span.end)))
                if self.current.text != ",":
                    break
                self.take()
                if self.current.text == ")":
                    self.source.fail("PROFILE_UNCOVERED", "TRAILING_COMMA", "Trailing commas are outside this slice.", self.current.start, self.current.end)
            closing = self.expect(")")
            span=self.source.span(token.start,closing.end)
            if '.' in call_name:
                prefix,member=call_name.rsplit('.',1)
                receiver_span=self.source.span(token.start,token.start+len(prefix))
                receiver=self.mark(Literal({'true':True,'false':False,'na':None}[prefix],receiver_span) if prefix in ('true','false','na') else Reference(prefix,receiver_span))
                node=MemberCall(call_name,tuple(arguments),span,receiver,member,isinstance(receiver,Reference))
            else:node=Call(call_name,tuple(arguments),span)
            return self.mark(node,*(argument.expression for argument in arguments))
        if token.text == "[":
            self.take(); elements=[]
            while self.current.text != ']':
                elements.append(self.expression(0, depth+1))
                if len(elements)>16:
                    self.source.fail('PROFILE_UNCOVERED','TUPLE_ARITY','At most 16 tuple results.',token.start,self.current.end)
                if self.current.text != ',':break
                self.take()
            end=self.expect(']')
            if len(elements)<2:
                self.source.fail('PROFILE_UNCOVERED','TUPLE_ARITY','Tuples require 2..16 scalar results.',token.start,end.end)
            return self.mark(TupleExpression(tuple(elements),self.source.span(token.start,end.end)),*elements)
        self.source.fail("LANGUAGE_INVALID", "EXPECTED_EXPRESSION", "Expected a literal or call expression.", token.start, token.end)

    def newlines(self):
        while self.current.kind == "NEWLINE":
            self.take()

    def loop(self):
        start = self.take()
        if start.text == "for":
            if self.current.text == '[' or self.peek().text == 'in':
                paired = self.current.text == '['
                if paired:self.take()
                targets = []
                for index in range(2 if paired else 1):
                    name = self.take()
                    if name.kind != 'NAME' or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name.text) or name.text in {'for','in','var','na','true','false','if','else','while','break','continue','and','or','not'}:
                        self.source.fail('LANGUAGE_INVALID', 'FOR_IN_TARGET', 'Expected a local iteration variable.', name.start, name.end)
                    targets.append(Declaration(name.text, None, 'counter', None, self.source.span(name.start, name.end)))
                    if paired and index == 0:self.expect(',')
                if paired:self.expect(']')
                self.expect('in')
                collection = self.expression()
                self.value_depth += 1
                body = self.block()
                self.value_depth -= 1
                return ForIn(tuple(targets), collection, body, self.source.span(start.start, body[-1].span.end))
            name = self.take()
            if name.kind != "NAME" or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name.text) or name.text in {"for", "while", "if", "else", "var", "break", "continue", "to", "by", "na", "true", "false", "and", "or", "not"}:
                self.source.fail("LANGUAGE_INVALID", "LOOP_COUNTER", "Expected an unqualified loop counter.", name.start, name.end)
            if self.current.text == 'in':
                self.source.fail('PROFILE_UNCOVERED', 'FOR_IN_UNCOVERED', 'Collection iteration requires the collection profile.', start.start, self.current.end)
            self.expect("=")
            initial = self.expression()
            self.expect("to")
            final = self.expression()
            step = self.mark(Literal(1, self.source.span(start.start, start.end)))
            if self.current.text == "by":
                self.take()
                step = self.expression()
            counter = Declaration(name.text, "int", "counter", initial, self.source.span(name.start, name.end))
        else:
            condition = self.expression()
        self.value_depth += 1
        body = self.block()
        self.value_depth -= 1
        span = self.source.span(start.start, body[-1].span.end)
        return For(counter, initial, final, step, body, span) if start.text == "for" else While(condition, body, span)

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

    def switch(self):
        start = self.expect('switch')
        if self.version < 5:
            self.source.fail('LANGUAGE_INVALID', 'SWITCH_VERSION', 'switch requires Pine v5 or v6.', start.start, start.end)
        selector = None if self.current.kind == 'NEWLINE' else self.expression()
        if self.current.kind != 'NEWLINE':
            self.source.fail('LANGUAGE_INVALID', 'BLOCK_NEWLINE', 'switch requires indented cases.', start.start, self.current.end)
        self.newlines()
        if self.current.kind != 'INDENT':
            self.source.fail('LANGUAGE_INVALID', 'SWITCH_CASE', 'switch requires a nonempty case block.', start.start, self.current.end)
        self.take()
        self.block_depth += 1
        if self.block_depth > 24:
            self.source.fail('PROFILE_UNCOVERED', 'BLOCK_DEPTH', 'Block nesting limit exceeded.', start.start, start.end)
        cases, fallback = [], ()
        used = {token.text for token in self.tokens if token.kind == 'NAME'}
        name = '_openpine_switch'
        while name in used:name += '_'
        self.value_depth += 1
        while self.current.kind not in ('DEDENT', 'EOF'):
            beginning = self.current.start
            if fallback:
                self.source.fail('LANGUAGE_INVALID', 'SWITCH_DEFAULT_LAST', 'The default case must be last.', beginning, self.current.end)
            condition = None if self.current.text == '=>' else self.expression()
            self.expect('=>')
            if self.current.kind == 'NEWLINE':
                body = self.block()
            else:
                body = (self.statement(),)
            self.newlines()
            end = body[-1].span.end
            if condition is None:
                fallback = body
            else:
                if selector is not None:
                    reference = Reference(name, self.source.span(start.start, start.end))
                    condition = Binary('==', reference, condition, self.source.span(beginning, condition.span.end))
                cases.append((condition, body, self.source.span(beginning, end)))
                if len(cases) > 32:
                    self.source.fail('PROFILE_UNCOVERED', 'SWITCH_LIMIT', 'At most 32 switch cases.', start.start, end)
        if not cases:
            self.source.fail('LANGUAGE_INVALID', 'SWITCH_CASE', 'At least one conditional case is required.', start.start, self.current.end)
        self.expect_kind_dedent()
        self.value_depth -= 1
        self.block_depth -= 1
        for condition, body, span in reversed(cases):
            fallback = (If(condition, body, fallback, span),)
        prefix = () if selector is None else (Declaration(name, None, 'bar', selector, self.source.span(start.start, selector.span.end)),)
        return Switch((*prefix, *fallback), self.source.span(start.start, end))

    def expect_kind_dedent(self):
        if self.current.kind != 'DEDENT':
            self.source.fail('LANGUAGE_INVALID', 'SWITCH_CASE', 'Incomplete switch block.', self.current.start, self.current.end)
        self.take()

    def function(self,method=False):
        opening=self.take() if method else None
        start = self.take()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", start.text):
            self.source.fail("LANGUAGE_INVALID", "FUNCTION_NAME", "Function name must be an unqualified identifier.", start.start, start.end)
        self.expect("(")
        parameters = []
        while self.current.text != ")":
            beginning = self.current.start
            qualifier = self.take().text if self.current.text in ("simple", "series") else None
            annotation = self.annotation()
            name = self.take()
            if name.kind != "NAME" or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name.text) or name.text in {"if", "else", "var", "na", "true", "false", "and", "or", "not", "for", "while", "break", "continue", "to", "by"} or (qualifier and annotation is None):
                self.source.fail("LANGUAGE_INVALID", "FUNCTION_PARAMETER", "Expected a parameter with an optional scalar type/qualifier.", beginning, name.end)
            default = None
            if self.current.text == "=":
                if self.version < 5:
                    self.source.fail('LANGUAGE_INVALID', 'FUNCTION_DEFAULT_VERSION', 'Parameter defaults require Pine v5 or v6.', self.current.start, self.current.end)
                self.take()
                default = self.expression()
            parameters.append(Parameter(name.text, annotation, qualifier, self.source.span(beginning, name.end), default))
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
        return (Method if method else Function)(start.text, tuple(parameters), body, self.source.span(opening.start if opening else start.start, body[-1].span.end))

    def object_type(self):
        start=self.take()
        if self.version<5:self.source.fail('LANGUAGE_INVALID','UDT_VERSION','User-defined types require Pine v5 or v6.',start.start,start.end)
        name=self.take()
        if name.kind!='NAME' or not re.fullmatch(r'[A-Za-z_]\w*',name.text):
            self.source.fail('LANGUAGE_INVALID','UDT_NAME','Expected an unqualified type name.',name.start,name.end)
        self.object_names.add(name.text)
        if self.current.kind!='NEWLINE':self.source.fail('LANGUAGE_INVALID','UDT_FIELDS','Expected indented fields.',start.start,self.current.end)
        self.newlines()
        if self.current.kind!='INDENT':self.source.fail('LANGUAGE_INVALID','UDT_FIELDS','A type requires fields.',start.start,self.current.end)
        self.take();members=[]
        while self.current.kind not in ('DEDENT','EOF'):
            beginning=self.current.start;intrabar=False
            if self.current.text=='varip':self.take();intrabar=True
            typ=self.take();field=self.take()
            if typ.text not in ('int','float','bool','string') and typ.text not in self.enum_names:
                self.source.fail('PROFILE_UNCOVERED','UDT_FIELD_TYPE','Only scalar UDT fields are admitted.',typ.start,typ.end)
            if field.kind!='NAME' or not re.fullmatch(r'[A-Za-z_]\w*',field.text):
                self.source.fail('LANGUAGE_INVALID','UDT_FIELD','Expected an unqualified field name.',field.start,field.end)
            default=None
            if self.current.text=='=':self.take();default=self.expression()
            end=default.span.end if default else field.end
            members.append(ObjectField(field.text,typ.text,intrabar,default,self.source.span(beginning,end)))
            if len(members)>32:self.source.fail('PROFILE_UNCOVERED','UDT_LIMIT','At most 32 fields per type.',start.start,end)
            if self.current.kind!='NEWLINE':self.source.fail('LANGUAGE_INVALID','UDT_FIELDS','Expected the end of a field declaration.',self.current.start,self.current.end)
            self.newlines()
        self.expect_kind_dedent()
        if not members:self.source.fail('LANGUAGE_INVALID','UDT_FIELDS','A type requires at least one field.',start.start,name.end)
        return ObjectType(name.text,tuple(members),self.source.span(start.start,members[-1].span.end))

    def enum_type(self):
        start=self.take()
        if self.version<5:self.source.fail('LANGUAGE_INVALID','ENUM_VERSION','Enums require Pine v5 or v6.',start.start,start.end)
        name=self.take()
        if name.kind!='NAME' or not re.fullmatch(r'[A-Za-z_]\w*',name.text):self.source.fail('LANGUAGE_INVALID','ENUM_NAME','Expected an unqualified enum name.',name.start,name.end)
        self.enum_names.add(name.text)
        if self.current.kind!='NEWLINE':self.source.fail('LANGUAGE_INVALID','ENUM_MEMBERS','Expected indented members.',start.start,self.current.end)
        self.newlines()
        if self.current.kind!='INDENT':self.source.fail('LANGUAGE_INVALID','ENUM_MEMBERS','An enum requires members.',start.start,self.current.end)
        self.take();members=[]
        while self.current.kind not in ('DEDENT','EOF'):
            member=self.take();title=member.text;end=member.end
            if member.kind!='NAME' or not re.fullmatch(r'[A-Za-z_]\w*',member.text):self.source.fail('LANGUAGE_INVALID','ENUM_MEMBER','Expected an unqualified member name.',member.start,member.end)
            if self.current.text=='=':
                self.take();value=self.expression();end=value.span.end
                if not isinstance(value,Literal) or type(value.value) is not str:self.source.fail('PROFILE_UNCOVERED','ENUM_TITLE','Enum titles require string literals.',value.span.start,value.span.end)
                title=value.value
            members.append(EnumMember(member.text,title,self.source.span(member.start,end)))
            if len(members)>64:self.source.fail('PROFILE_UNCOVERED','ENUM_LIMIT','At most 64 enum members.',start.start,end)
            if self.current.kind!='NEWLINE':self.source.fail('LANGUAGE_INVALID','ENUM_MEMBERS','Expected the end of an enum member.',self.current.start,self.current.end)
            self.newlines()
        self.expect_kind_dedent()
        if not members:self.source.fail('LANGUAGE_INVALID','ENUM_MEMBERS','An enum requires members.',start.start,name.end)
        return EnumType(name.text,tuple(members),self.source.span(start.start,members[-1].span.end))

    def statement(self) -> Statement:
        token = self.current
        if token.text=='enum':return self.enum_type()
        if token.text=='type':return self.object_type()
        if token.text=='method':
            if self.version<5:self.source.fail('LANGUAGE_INVALID','METHOD_VERSION','Methods require Pine v5 or v6.',token.start,token.end)
            return self.function(method=True)
        if token.text == 'switch':
            return self.switch()
        if token.text in ("for", "while"):
            return self.loop()
        if token.text in ("break", "continue"):
            self.take()
            if self.current.kind not in ("NEWLINE", "DEDENT", "EOF"):
                self.source.fail("LANGUAGE_INVALID", "LOOP_CONTROL", "Loop control takes no arguments.", token.start, self.current.end)
            self.newlines()
            return LoopControl(token.text, self.source.span(token.start, token.end))
        if token.text=='[':
            closing=next((i for i in range(self.position+1,len(self.tokens)) if self.tokens[i].text==']'),None)
            if closing is not None and self.tokens[min(closing+1,len(self.tokens)-1)].text=='=':
                self.take(); targets=[]
                while self.current.text!=']':
                    name=self.take()
                    if name.kind!='NAME' or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',name.text) or name.text in {'var','if','else','true','false','na','and','or','not','for','while','break','continue','to','by'}:
                        self.source.fail('LANGUAGE_INVALID','TUPLE_TARGET','Expected an unqualified tuple target.',name.start,name.end)
                    targets.append(Declaration(name.text,None,'bar',None,self.source.span(name.start,name.end)))
                    if len(targets)>16:self.source.fail('PROFILE_UNCOVERED','TUPLE_ARITY','At most 16 tuple targets.',token.start,name.end)
                    if self.current.text!=',':break
                    self.take()
                self.expect(']'); self.expect('='); value=self.expression()
                if len(targets)<2:self.source.fail('PROFILE_UNCOVERED','TUPLE_ARITY','Tuples require 2..16 targets.',token.start,value.span.end)
                if self.current.kind not in ('NEWLINE','DEDENT','EOF') and self.tokens[self.position-1].kind not in ('DEDENT','NEWLINE'):
                    self.source.fail('LANGUAGE_INVALID','TUPLE_TARGET','Expected the end of tuple declaration.',self.current.start,self.current.end)
                self.newlines()
                return TupleDeclaration(tuple(targets),value,self.source.span(token.start,value.span.end))
        if self.function_ahead():
            return self.function()
        if token.text == "if":
            return self.if_statement()
        if token.text in self.uncovered:
            self.source.fail("PROFILE_UNCOVERED", "CONSTRUCT_UNCOVERED", "Construct is outside this statement profile.", token.start, token.end)
        mode = "bar"
        annotation = None
        if token.text in ('var','varip'):
            self.take()
            mode = token.text
        annotation = self.annotation()
        is_declaration = mode in ('var','varip') or annotation is not None or (self.current.kind == "NAME" and self.peek().text == "=")
        if is_declaration:
            name = self.take()
            if name.kind != "NAME" or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name.text) or name.text in {"if", "else", "var", "varip", "true", "false", "na", "not", "and", "or", "for", "while", "break", "continue", "to", "by"}:
                self.source.fail("LANGUAGE_INVALID", "VARIABLE_NAME", "Invalid variable name.", name.start, name.end)
            self.expect("=")
            value = self.expression()
            statement = Declaration(name.text, annotation, mode, value, self.source.span(token.start, value.span.end))
        elif self.current.kind == "NAME" and self.peek().text == ":=":
            name = self.take()
            self.take()
            value = self.expression()
            statement = (FieldReassignment(self.mark(Reference(name.text,self.source.span(name.start,name.end))),value,self.source.span(token.start,value.span.end))
                if '.' in name.text else Reassignment(name.text, value, self.source.span(token.start, value.span.end)))
        else:
            statement = self.expression()
            if self.current.text==':=' and isinstance(statement,FieldAccess):
                self.take();value=self.expression();statement=FieldReassignment(statement,value,self.source.span(token.start,value.span.end))
                self.newlines();return statement
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

    def annotation(self):
        if self.current.text in self.object_names|self.enum_names and self.peek().kind=='NAME':return self.take().text
        if self.current.text == 'array' and self.peek().text == '<':
            opening = self.take();self.take();element = self.take()
            if element.text not in ('int', 'float', 'bool', 'string') and element.text not in self.object_names|self.enum_names:
                self.source.fail('PROFILE_UNCOVERED', 'ARRAY_ELEMENT', 'Only scalar array elements are admitted.', opening.start, element.end)
            self.expect('>')
            return 'array<' + element.text + '>'
        if self.current.text in ('int', 'float', 'bool', 'string'):
            if self.peek().text == '[' and self.peek(2).text == ']':
                element = self.take().text;self.take();self.take()
                return 'array.' + element
            if self.peek().kind == 'NAME':return self.take().text
        return None

    def parse(self) -> tuple[Statement, ...]:
        statements = []
        self.newlines()
        while self.current.kind != "EOF":
            if self.current.kind in ("INDENT", "DEDENT"):
                self.source.fail("LANGUAGE_INVALID", "INDENTATION", "Unexpected indentation.", self.current.start, self.current.end)
            statements.append(self.statement())
        return tuple(statements)
