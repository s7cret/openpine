"""Private rebuildable indexes; reject links and escapes before creation or deletion."""
from pathlib import Path
from .diagnostics import identifier

def directory(root,key):
    identifier(key);root=Path(root).resolve();parent=root/'comparison-work';target=parent/key
    for path in (parent,target,target/'ids.sqlite3'):
        if path.is_symlink() or path.exists() and (path.is_junction() or not path.resolve().is_relative_to(root)):raise ValueError('Unsafe comparison scratch path.')
    return target

def clear(root,key):
    import shutil
    target=directory(root,key)
    if target.exists():
        for child in target.iterdir():
            if child.is_symlink() or child.is_junction() or not child.is_file():raise ValueError('Unexpected comparison scratch entry.')
        shutil.rmtree(target)
