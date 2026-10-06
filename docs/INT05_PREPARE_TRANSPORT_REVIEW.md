# INT05 prepare transport review

This is a bounded application environment patch on frozen candidate
`9eaae28ef583f72fd7213ad37709c30e378602cd`. It requires review before another
prepare attempt. It is not a frozen or installed-qualified candidate: applying
it requires a package identity refresh, fresh preparation/environment verification,
and new source/collection/plan anchors. The existing 21 manifests at `9eaae28`
remain unchanged and all NOT_RUN.

The first actual stock prepare failed before library checkout or builds:
`git clone https://github.com/s7cret/ast2python.git` returned 128 with
`Could not resolve host: github.com`. Ambient `git ls-remote` to the same repo
succeeded. The existing `clean_environment` allowlist discards the configured
proxy and CA context. Package harness already allows `SSL_CERT_FILE`; frontend
owner already accepts an explicit proxy. No generic transport binding or
environment-extra API was found in the current eight source trees.

`clean_environment(..., inherit_transport=True)` now adds only already configured
HTTP/HTTPS proxy aliases, NO_PROXY aliases, and four standard CA file settings:
SSL_CERT_FILE, REQUESTS_CA_BUNDLE, CURL_CA_BUNDLE and GIT_SSL_CAINFO. The flag
defaults to false. Only prepare's Git clones, two hashed dependency installations
and hashed wheel download opt in. Offline installations, snapshots, preflights,
collections and pytest shards keep their original environment handling.

Proxy userinfo, password, query, non-root path, malformed endpoints and control
characters are rejected. CA paths must be absolute, readable regular files.
Diagnostics expose the recognized setting name without its value or parser
exception. Credentials, index URLs, private keys, debug key logs and TLS bypass
flags are excluded. No proxy value becomes semantic policy or frozen plan
authority. No OS, network, credential, certificate or publication configuration
is changed, and TLS verification is not disabled.

The existing `verification/ci-runtime-requirements.txt` gains exact tested pins:

| Distribution | Version | Reason |
| --- | --- | --- |
| black | 26.10.0 | Declared dev extras |
| pytest-cov | 7.1.0 | Declared dev extras and coverage plugin |
| socksio | 1.0.0 | Declared marketdata stream extra |
| zstandard | 0.25.0 | Declared marketdata zstd extra |
| platformdirs | 4.12.3 | Black dependency |
| pytokens | 0.4.1 | Black dependency |

All 53 original runtime stanzas, pins, hashes and relative order are retained.
The six new pins admit 168 available wheel hashes from exact PyPI release JSON.
Six ordinary CPython3.13-compatible wheels were downloaded (7,921,067 bytes),
checked against those digests and retained as raw inputs. No dependency was
installed and no project was built. Existing require-hashes/only-binary/no-deps
owner paths remain intact; no supplemental installation mechanism is introduced.

The separate transport contract has 28 exact nodes, zero deselected, locked in
`verification/int05-prepare-transport-inventory.json` under suite
`int05-prepare-transport-contract`. It is additional to the 115 INT05 reader/owner
contract nodes and does not change the normative 11998 host selectors. Negative
cases include credential and parser error leaks, TLS bypass environment leakage,
bad CA paths, unsafe proxy forms and rejection before child launch. Real local
child commands prove opt-in/default handling and absence of synthetic secrets
in raw receipts/logs; these tests do not qualify real network access or builds.

The bounded regression passed 208 nodes in 36.861 seconds with zero failures,
errors, skips or deselected nodes. An initial attempt failed 15 cases because the
new isolated review worktree lacked seven sibling source directories; exact-pin
sibling worktrees were then added. Both attempts and primaries are retained.
Ruff passed for both changed Python owners and the new test module.

A single read-only native `git ls-remote` to the failed clone's repository
through the patched stock Commands owner completed with exit 0 in 1.068 seconds.
It inherited only the configured transport context, excluded credential and
TLS-bypass environment settings, and retained the raw command receipt. It did
not clone, install, build or repeat prepare. This verifies transport access;
the prepared/installed candidate and control calibration still need review,
package identity refresh and actual execution.

Raw evidence lives under `/workspace/int05/evidence/prepare-transport-*`,
including exact release metadata, wheel inputs, pre/post tests, strict owner gate
and regression JUnit. Reserved stage_gate.py, execution_cli.py, execution policy,
shared schemas and all seven library pins are unchanged. No prepare retry,
baseline calibration, hosted CI dispatch, merge, release or paid operation is
authorized by this review receipt.
