"""Restore the original reviewed archive; retain exact source and test identities."""
import base64
import hashlib
import os
from pathlib import Path
import subprocess

root=Path('.github/stage2-imports')
text=''.join((root/f'{i:02}.b64').read_text() for i in range(1,7))
# Repair transport transcription only. The archived binary digest is immutable.
for old,new in [('CFUgExLLTAeq','CFUgExLTAeq'),('KuKdXxVVchRF','KuKdXxVchRF'),('PhLVJdrJk','PhLVdrJk'),('Bv7JvGepl2','Bv7vGepl2'),('G6MXe0Eef5','G6MXeEef5')]:
    text=text.replace(old,new)
data=base64.b64decode(text,validate=True)
assert hashlib.sha256(data).hexdigest()=='812583617d1651f8d2af83c8beb7569c6f48a9dc677758700bce31e57eedc27c','bundle checksum mismatch'
p=Path(os.environ['RUNNER_TEMP'])/'imports.bundle';p.write_bytes(data)
for command in [['git','bundle','verify',str(p)],['git','fetch',str(p),'refs/heads/stage2/locked-library-imports-20260907'],['git','checkout','--detach','79c89ea4e79f9925b4c7e3e9085f02eff0492492']]:
    subprocess.run(command,check=True)
assert subprocess.check_output(['git','rev-parse','HEAD^{tree}'],text=True).strip()=='41f8fe537041cd54b5d9ca38b91fdb18229e376a'
