"""Closed, typed record codec. JSON never selects a Python module or constructor."""

import math
import types
from dataclasses import fields, is_dataclass
from functools import cache
from typing import Literal, TypeAliasType, get_args, get_origin, get_type_hints


def encode(value):
    if is_dataclass(value) and not isinstance(value, type):
        return {"$type": type(value).__name__, **{f.name: encode(getattr(value, f.name)) for f in fields(value)}}
    if type(value) is tuple:
        return [encode(v) for v in value]
    if type(value) is float:
        return {"$float": value.hex()}
    if type(value) is int and abs(value) > 2**53 - 1:
        return {"$int": str(value)}
    if value is None or type(value) in (str, int, bool):
        return value
    raise ValueError("Unsupported record value.")


@cache
def hints(cls):
    return get_type_hints(cls)


@cache
def union_members(annotation):
    """Flatten closed aliases, including NodeIR inside an optional union."""
    if isinstance(annotation, TypeAliasType):return union_members(annotation.__value__)
    if get_origin(annotation) is types.UnionType:
        return tuple(child for member in get_args(annotation) for child in union_members(member))
    return (annotation,)


def decode(payload, expected):
    remaining = 250000

    def visit(value, annotation, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 384:
            raise ValueError("Record size/depth exceeds implementation limits.")
        if isinstance(annotation, TypeAliasType):
            annotation = annotation.__value__
        origin, args = get_origin(annotation), get_args(annotation)
        if origin is types.UnionType:
            for member in union_members(annotation):
                if is_dataclass(member) and type(value) is dict and value.get("$type") == member.__name__:
                    return visit(value, member, depth + 1)
                if member is float and type(value) is dict and value.keys() == {"$float"}:
                    return visit(value, member, depth + 1)
                if member is int and type(value) is dict and value.keys() == {"$int"}:
                    return visit(value, member, depth + 1)
                if member is type(None) and value is None or member in (int, bool, str) and type(value) is member:
                    return visit(value, member, depth + 1)
            raise ValueError("Value does not match its declared union.")
        if origin is Literal:
            if not any(type(value) is type(item) and value == item for item in args):
                raise ValueError("Invalid literal record field.")
            return value
        if origin is tuple:
            if type(value) is not list:
                raise ValueError("Tuple field requires a JSON array.")
            if len(args) == 2 and args[1] is Ellipsis:
                return tuple(visit(v, args[0], depth + 1) for v in value)
            if len(value) != len(args):
                raise ValueError("Tuple field has the wrong length.")
            return tuple(visit(v, a, depth + 1) for v, a in zip(value, args, strict=True))
        if is_dataclass(annotation):
            members = fields(annotation)
            if type(value) is not dict or value.keys() != {"$type", *(f.name for f in members)} or value["$type"] != annotation.__name__:
                raise ValueError("Unknown record type or missing/extra fields.")
            kwargs = {}
            for f in members:
                decoded = visit(value[f.name], hints(annotation)[f.name], depth + 1)
                if f.init:
                    kwargs[f.name] = decoded
                elif type(decoded) is not type(f.default) or decoded != f.default:
                    raise ValueError("Record discriminant/schema mismatch.")
            instance = annotation(**kwargs)
            if annotation.__name__ == "SourceSpan":
                if not 0 <= instance.start <= instance.end <= 65536 or not 1 <= instance.line <= instance.end_line <= 65537 or instance.column < 1 or instance.end_column < 1 or len(instance.source) > 4096:
                    raise ValueError("Invalid source span.")
            return instance
        if annotation is type(None) and value is None:
            return None
        if annotation is float:
            if type(value) is not dict or value.keys() != {"$float"} or type(value["$float"]) is not str or len(value["$float"]) > 64:
                raise ValueError("Float requires a lossless hexadecimal record.")
            decoded = float.fromhex(value["$float"])
            if not math.isfinite(decoded) or decoded.hex() != value["$float"]:
                raise ValueError("Noncanonical float record.")
            return decoded
        if annotation is int and type(value) is dict and value.keys() == {"$int"}:
            text = value["$int"]
            if type(text) is not str or len(text) > 21:
                raise ValueError("Invalid exact integer record.")
            decoded = int(text)
            if str(decoded) != text or not 2**53 <= abs(decoded) <= 2**63:
                raise ValueError("Noncanonical exact integer record.")
            return decoded
        if annotation not in (int, bool, str) or type(value) is not annotation:
            raise ValueError("Invalid primitive record field.")
        if type(value) is int and abs(value) > 2**53 - 1:
            raise ValueError("Large integer requires an exact integer record.")
        if type(value) is float and not math.isfinite(value) or type(value) is str and len(value) > 65536:
            raise ValueError("Primitive record exceeds limits.")
        return value

    return visit(payload, expected)
