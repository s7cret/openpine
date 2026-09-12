"""Independent rational derivation. Standard library only; never imports the SUT.

--check compares the committed fixtures byte for byte without updating them.
The TSI observation window starts after a constant-increment conditioning segment;
its unknown startup policy is expressly NOT given an invented expected sequence.
"""

from __future__ import annotations

import argparse
from fractions import Fraction as Q
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "verification/builtin-numeric-closure-v1"


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def seal(body):
    return {**body, "content_hash": "sha256:" + hashlib.sha256(canonical(body)).hexdigest()}


def value(number):
    if isinstance(number, (list, tuple)):
        return [value(item) for item in number]
    if number is None:
        return {"kind": "na"}
    return {"kind": "value", "value": int(number) if type(number) is int else float(number)}


def smoothed(samples, length, alpha):
    """SMA bootstrap followed by rational IIR, independently derived from v3/v4 docs."""
    history, result, state = [], [], None
    for sample in samples:
        if sample is None:
            result.append(None)
            continue
        history.append(Q(sample))
        if state is None and len(history) >= length:
            state = sum(history[-length:], Q(0)) / length
        elif state is not None:
            state = alpha * Q(sample) + (1 - alpha) * state
        result.append(state)
    return result


def rsi(prices, length):
    changes = [None, *(Q(b) - Q(a) for a, b in zip(prices, prices[1:]))]
    gains = smoothed([None if x is None else max(x, 0) for x in changes], length, Q(1, length))
    losses = smoothed([None if x is None else max(-x, 0) for x in changes], length, Q(1, length))
    # Only mixed gain/loss trajectories: undefined all-flat/zero-loss startup is outside this derivation.
    assert all(loss is None or loss > 0 for loss in losses)
    return [
        None if gain is None else 100 * gain / (gain + loss) for gain, loss in zip(gains, losses)
    ]


def macd(prices, fast, slow, signal):
    left = smoothed(prices, fast, Q(2, fast + 1))
    right = smoothed(prices, slow, Q(2, slow + 1))
    line = [None if a is None or b is None else a - b for a, b in zip(left, right)]
    sig = smoothed(line, signal, Q(2, signal + 1))
    return [(a, b, None if a is None or b is None else a - b) for a, b in zip(line, sig)]


def conditioned_tsi(changes, short, long, constant, start):
    """EMA-of-EMA(momentum) / EMA-of-EMA(abs(momentum)) from an exact steady state."""
    numerator1 = numerator2 = Q(constant)
    denominator1 = denominator2 = abs(Q(constant))
    result = [None] * start + [Q(1 if constant > 0 else -1)]
    for change in changes:
        a, b = Q(2, long + 1), Q(2, short + 1)
        numerator1 = a * change + (1 - a) * numerator1
        denominator1 = a * abs(change) + (1 - a) * denominator1
        numerator2 = b * numerator1 + (1 - b) * numerator2
        denominator2 = b * denominator1 + (1 - b) * denominator2
        assert denominator2 > 0
        result.append(numerator2 / denominator2)
    return result


def make_files():
    files, cases = {}, []
    derivation = {
        "schema_id": "openpine.numeric_derivation.v1",
        "method": "stdlib Fraction rational recurrence and linear order statistics; no OpenPine imports",
        "primary_references": {
            "legacy": "https://fr.tradingview.com/pine-script-reference/v3/",
            "legacy4": "https://in.tradingview.com/pine-script-reference/v4/",
            "rsi_overloads": "https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-5/",
            "math": "https://in.tradingview.com/pine-script-reference/v5/",
            "arrays": "https://www.tradingview.com/pine-script-docs/language/arrays/",
            "tsi_formula": "https://www.tradingview.com/support/solutions/43000592290-true-strength-index/",
        },
        "limits": [
            "v4 binary-search availability and absence results are not backported from v5/v6; see original failed diagnostic run.",
            "No TradingView execution export. Finite bounded trajectories do not prove full overload semantics.",
            "v1/v2 RSI and MACD bootstrap authority remains unresolved; no backward extrapolated oracle credit.",
            "TSI compares only the explicit conditioned observation window. Startup/NA policy stays unresolved.",
            "Binary plain-search duplicates have no asserted tie-breaking; neighbor searches use documented first/last matches and interior gaps.",
            "No allocation, request, broker or visual semantics are inferred from numeric cases.",
        ],
    }
    files["derivation.json"] = canonical(derivation) + b"\n"

    def put(path, payload):
        data = payload.encode() if isinstance(payload, str) else canonical(payload) + b"\n"
        files[path] = data
        return {"path": path, "sha256": hashlib.sha256(data).hexdigest()}

    def add(ident, version, operation, form, source_lines, data, expected, **settings):
        namespace = (
            operation
            if operation.startswith("array.") or operation.startswith("math.")
            else "ta." + operation
        )
        method = form == "METHOD"
        symbol = ("pine:method:" if method else "pine:function:") + namespace
        overload = symbol + ("#overload:0" if operation == "rsi" else "#canonical")
        call_line = next(i for i, line in enumerate(source_lines, 1) if "PRIMARY" in line)
        source = (
            f"//@version={version}\n"
            + ("study" if version < 5 else "indicator")
            + '("Independent numeric closure")\n'
            + "\n".join(source_lines)
            + "\n"
        )
        abi = "pinelib.abi." + (
            "reference." + operation.replace(".", "_") + "_v1"
            if operation.startswith("array.")
            else (
                "math." + operation.split(".")[1] + "_v1"
                if operation.startswith("math.")
                else "ta." + operation + "_v1"
            )
        )
        config = {
            "operation": operation,
            "declared_binding": [symbol, overload, form],
            "source_name": ident + ".pine",
            "primary_source_line": call_line + 2,
            "abi_callable": abi,
            "observed_from_bar": 0,
            "scope": "finite_bounded_full_trace",
            **settings,
            "derivation_sha256": hashlib.sha256(files["derivation.json"]).hexdigest(),
        }
        case = {
            "id": ident,
            "pine_version": version,
            "layer": "runtime",
            "weight": 1,
            "critical": True,
            "source": put(ident + ".pine", source),
            "data": put(ident + ".data.json", data),
            "settings": put(ident + ".settings.json", config),
            "expected": put(
                ident + ".expected.json",
                {
                    "compile": True,
                    "events": [
                        {"bar": bar, "value": value(result)}
                        for bar, result in enumerate(expected)
                        if bar >= config["observed_from_bar"]
                    ],
                },
            ),
            "oracle": {
                "kind": "manual_fixture",
                "provenance": "Independent rational/order derivation in derive_numeric_closure.py; "
                + config["scope"]
                + "; no TradingView execution used.",
            },
            "tolerance": {"absolute": 1e-11, "relative": 1e-12},
        }
        cases.append(case)

    prices = [100, 103, 101, 106, 104, 109, 107, 112, 110, 115, 111, 118, 113, 112, 117, 120]
    for v in (3, 4):
        for length in (2, 3, 5):
            add(
                f"rsi-l{length}-v{v}",
                v,
                "rsi",
                "FUNCTION",
                [f"result = rsi(close, {length}) // PRIMARY", "plot(result)"],
                {"prices": prices},
                rsi(prices, length),
                parameters=[length],
                arity=1,
            )
        for params in ((2, 3, 2), (3, 5, 3), (5, 3, 2)):
            add(
                f"macd-{'-'.join(map(str, params))}-v{v}",
                v,
                "macd",
                "FUNCTION",
                [
                    f"[line, signal, histogram] = macd(close, {', '.join(map(str, params))}) // PRIMARY",
                    "plot(line)",
                    "plot(signal)",
                    "plot(histogram)",
                ],
                {"prices": prices},
                macd(prices, *params),
                parameters=list(params),
                arity=3,
            )
    # Exact-match and insertion-neighbor expected indices from linear comparisons, not binary search.
    for v in (5,):
        for form in ("NAMESPACE_FUNCTION", "METHOD"):
            for operation in (
                "array.binary_search",
                "array.binary_search_leftmost",
                "array.binary_search_rightmost",
            ):
                for variant, numbers, probes in (
                    (
                        "unique",
                        [-10.0, -2.0, 0.0, 4.0, 9.0],
                        [-10.0, -2.0, -1.0, 0.0, 2.0, 4.0, 8.0, 9.0],
                    ),
                    (
                        "fractional",
                        [-3.5, -1.25, 0.125, 2.25, 8.5],
                        [-3.5, -1.25, -0.5, 0.125, 1.5, 2.25, 4.5, 8.5],
                    ),
                    (
                        "duplicates",
                        [-10.0, -2.0, -2.0, 0.0, 4.0, 4.0, 9.0],
                        [-10.0, -2.0, -1.0, 0.0, 2.0, 4.0, 8.0, 9.0],
                    ),
                    (
                        "large",
                        [-1.0e12, -4.0e6, 0.0, 4.0e6, 1.0e12],
                        [-1.0e12, -4.0e6, -2.0, 0.0, 2.0, 4.0e6, 8.0e6, 1.0e12],
                    ),
                ):
                    if operation == "array.binary_search" and variant == "duplicates":
                        continue
                    expected = []
                    for probe in probes:
                        matches = [i for i, item in enumerate(numbers) if item == probe]
                        if operation.endswith("leftmost"):
                            expected.append(
                                matches[0]
                                if matches
                                else max(i for i, item in enumerate(numbers) if item < probe)
                            )
                        elif operation.endswith("rightmost"):
                            expected.append(
                                matches[-1]
                                if matches
                                else min(i for i, item in enumerate(numbers) if item > probe)
                            )
                        else:
                            expected.append(matches[0] if matches else -1)
                    lines = [
                        "a = array.new_float(0)",
                        *(f"array.push(a, {number!r})" for number in numbers),
                    ]
                    expr = (
                        operation + "(a, close)"
                        if form != "METHOD"
                        else "a." + operation.split(".")[1] + "(close)"
                    )
                    lines.extend([f"result = {expr} // PRIMARY", "plot(result)"])
                    add(
                        f"{operation.split('.')[-1]}-{variant}-v{v}-{'method' if form == 'METHOD' else 'namespace'}",
                        v,
                        operation,
                        form,
                        lines,
                        {"prices": probes, "numbers": numbers},
                        expected,
                        parameters=[],
                        arity=1,
                    )
    for v in (5, 6):
        for operation in ("math.min", "math.max"):
            for ident, expressions in (
                ("pair", ["close", "-close"]),
                ("three", ["close", "-close", "2.5"]),
                ("six", ["close", "-close", "2.5", "-7.5", "1.25", "0.0"]),
                ("na", ["sample", "-close", "2.5"]),
            ):
                samples = [-100.0, -7.5, -0.125, 0.0, 0.5, 2.5, 100.0, -1.0e6]
                expected = []
                for i, sample in enumerate(samples):
                    operands = [
                        sample
                        if e in {"close", "sample"}
                        else -sample
                        if e == "-close"
                        else float(e)
                        for e in expressions
                    ]
                    expected.append(
                        None
                        if ident == "na" and i in (2, 5)
                        else (min(operands) if operation == "math.min" else max(operands))
                    )
                lines = (
                    ["sample = bar_index == 2 or bar_index == 5 ? na : close"]
                    if ident == "na"
                    else []
                )
                lines += [
                    f"result = {operation}({', '.join(expressions)}) // PRIMARY",
                    "plot(result)",
                ]
                add(
                    f"{operation.replace('.', '-')}-{ident}-v{v}",
                    v,
                    operation,
                    "NAMESPACE_FUNCTION",
                    lines,
                    {"prices": samples, "operands": expressions},
                    expected,
                    parameters=[],
                    arity=1,
                    missing_bars=[2, 5] if ident == "na" else [],
                )
        for short, long, constant in ((2, 3, 2), (3, 2, 2), (2, 3, -2), (1, 3, 2)):
            start = 12
            changes = [Q(v) for v in (-3, 5, -2, 0, 4, -6, 3, 1, -4, 2)]
            samples = [Q(100) + constant * i for i in range(start + 1)]
            for change in changes:
                samples.append(samples[-1] + change)
            expected = conditioned_tsi(changes, short, long, constant, start)
            add(
                f"tsi-conditioned-{short}-{long}-{constant}-v{v}",
                v,
                "tsi",
                "NAMESPACE_FUNCTION",
                [f"result = ta.tsi(close, {short}, {long}) // PRIMARY", "plot(result)"],
                {"prices": [float(p) for p in samples]},
                expected,
                parameters=[short, long],
                arity=1,
                observed_from_bar=start,
                scope="conditioned_steady_state_continuation_NOT_startup_or_missing_values",
            )
    files["manifest.json"] = (
        canonical(
            seal(
                {
                    "schema_id": "openpine.conformance_corpus.v1",
                    "revision": 1,
                    "profile": "engineering",
                    "cases": cases,
                }
            )
        )
        + b"\n"
    )
    files["lock.json"] = (
        canonical(
            {
                "schema_id": "openpine.numeric_closure_lock.v1",
                "case_count": len(cases),
                "content_hash": json.loads(files["manifest.json"])["content_hash"],
                "derivation_sha256": hashlib.sha256(files["derivation.json"]).hexdigest(),
                "full_builtin_expected_accepted": False,
                "unresolved": [
                    "v1_v2_rsi_macd_primary_bootstrap",
                    "v4_binary_search_availability_and_missing_neighbors",
                    "tsi_startup_missing_values",
                    "all_signature_boundaries",
                ],
            }
        )
        + b"\n"
    )
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = make_files()
    if args.check:
        mismatches = [
            name
            for name, data in files.items()
            if not (ROOT / name).is_file() or (ROOT / name).read_bytes() != data
        ]
        extras = [
            str(p.relative_to(ROOT))
            for p in ROOT.rglob("*")
            if p.is_file() and str(p.relative_to(ROOT)) not in files
        ]
        if mismatches or extras:
            raise SystemExit(f"Independent derivation differs: {mismatches}; unexpected: {extras}")
    else:
        ROOT.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (ROOT / name).write_bytes(data)
    print(
        f"{'Checked' if args.check else 'Generated'} {len(files)} files and {json.loads(files['lock.json'])['case_count']} cases"
    )


if __name__ == "__main__":
    main()
