"""Small, deterministic FASTEXPR syntax tree used for feature extraction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(.))")


@dataclass(frozen=True)
class AstNode:
    kind: str
    value: str
    children: tuple["AstNode", ...] = ()

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "value": self.value,
            "children": [child.as_dict() for child in self.children],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )


class ExpressionSyntaxError(ValueError):
    pass


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = [
            (
                number and "number" or ident and "ident" or "symbol",
                number or ident or symbol,
            )
            for number, ident, symbol in _TOKEN.findall(text)
        ]
        self.index = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self, value: str | None = None) -> tuple[str, str]:
        token = self.peek()
        if token is None or (value is not None and token[1] != value):
            raise ExpressionSyntaxError(f"expected {value or 'token'}")
        self.index += 1
        return token

    def expression(self, minimum: int = 0) -> AstNode:
        token = self.take()
        if token[1] in {"+", "-"}:
            left = AstNode("unary", token[1], (self.expression(30),))
        elif token[1] == "(":
            left = self.expression()
            self.take(")")
        elif (
            token[0] == "ident"
            and (lookahead := self.peek()) is not None
            and lookahead[1] == "("
        ):
            self.take("(")
            args: list[AstNode] = []
            lookahead = self.peek()
            if lookahead is None or lookahead[1] != ")":
                while True:
                    args.append(self.expression())
                    lookahead = self.peek()
                    if lookahead is None or lookahead[1] != ",":
                        break
                    self.take(",")
            self.take(")")
            left = AstNode("call", token[1].lower(), tuple(args))
        else:
            left = AstNode(token[0], token[1].lower())
        precedence = {"+": 10, "-": 10, "*": 20, "/": 20, "^": 25}
        while (
            (lookahead := self.peek()) is not None
            and lookahead[1] in precedence
            and precedence[lookahead[1]] >= minimum
        ):
            operator = self.take()[1]
            left = AstNode(
                "binary", operator, (left, self.expression(precedence[operator] + 1))
            )
        return left


def parse_expression(expression: str) -> AstNode:
    parser = _Parser(str(expression or ""))
    if not parser.tokens:
        raise ExpressionSyntaxError("empty expression")
    node = parser.expression()
    if (unexpected := parser.peek()) is not None:
        raise ExpressionSyntaxError(f"unexpected token {unexpected[1]}")
    return node


def depth(node: AstNode) -> int:
    return 1 + max((depth(child) for child in node.children), default=0)
