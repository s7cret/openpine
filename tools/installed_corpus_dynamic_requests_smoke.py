"""Run the owned multi-market scripts through the installed CLI and HTTP service."""
from tools.installed_corpus_smoke import verify as verify_corpus


def verify(http, cli, work):
    return verify_corpus(http, cli, work, shard=9)
