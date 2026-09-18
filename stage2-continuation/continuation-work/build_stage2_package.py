#!/usr/bin/env python3
"""Build real PEP 517 artifacts with declared installed backend requirements.

This is a small build frontend, NOT an emulation of setuptools/hatchling and NOT
an importable replacement for the PyPA build module. Nothing is downloaded.
"""
from __future__ import annotations
import argparse
import importlib
import importlib.metadata
import json
from pathlib import Path
import sys
import tomllib
from packaging.requirements import Requirement

def require(specifications):
    for text in specifications:
        requirement=Requirement(text)
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try: version=importlib.metadata.version(requirement.name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(f"missing declared build requirement: {text}") from exc
        if requirement.specifier and version not in requirement.specifier:
            raise RuntimeError(f"build requirement {text}, installed {version}")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir',required=True,type=Path)
    args=parser.parse_args()
    project=Path.cwd().resolve();out=args.outdir.resolve();out.mkdir(parents=True,exist_ok=True)
    with (project/'pyproject.toml').open('rb') as stream: config=tomllib.load(stream)
    system=config.get('build-system')
    if not system or not system.get('build-backend'):
        raise RuntimeError('an explicit PEP 517 backend is required')
    require(system.get('requires',[]))
    for entry in reversed(system.get('backend-path',[])):
        path=(project/entry).resolve()
        if not path.is_relative_to(project):raise RuntimeError('backend-path escapes project')
        sys.path.insert(0,str(path))
    module,_,attribute=system['build-backend'].partition(':')
    backend=importlib.import_module(module)
    for part in attribute.split('.') if attribute else []:backend=getattr(backend,part)
    artifacts=[]
    for kind in ('sdist','wheel'):
        requirements=getattr(backend,'get_requires_for_build_'+kind,None)
        if requirements is not None:require(requirements(config_settings=None))
        hook=getattr(backend,'build_'+kind)
        filename=hook(str(out),config_settings=None)
        if not isinstance(filename,str) or Path(filename).name!=filename or not (out/filename).is_file():
            raise RuntimeError('backend did not produce a valid artifact filename')
        artifacts.append({'kind':kind,'filename':filename,'size':(out/filename).stat().st_size})
    print(json.dumps({'backend':system['build-backend'],'requirements':system.get('requires',[]),
                      'artifacts':artifacts},indent=2))
    return 0
if __name__=='__main__':raise SystemExit(main())
