"""The explicitly chosen statement-profile policies, independent of parsing."""

from packages.contracts import LanguagePolicy


def language_policy(version):
    return LanguagePolicy(version, "float" if version == 6 else "truncate",
                          "lazy" if version == 6 else "strict", version < 6)
