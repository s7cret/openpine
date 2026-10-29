from tools.installed_corpus_smoke import verify as _verify
def verify(http,cli,work):return _verify(http,cli,work,shard=4)
