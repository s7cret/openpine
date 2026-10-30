"""Read-only descriptions of the pinned policies; never a second admission table."""
import hashlib
import re
from packages.contracts.wire import read_json,digest,canonical
from packages.compiler.semantic.specification import ROOT,safe_path

def profile_inventory(session):
    rows={}
    def add(identifier,origin,value,versions):
        if not isinstance(identifier,str) or not identifier.startswith(('openpine.','pine')):return
        row=rows.setdefault(identifier,{'profile_id':identifier,'versions':set(),'sources':[]})
        row['versions'].update(versions)
        evidence={'origin':origin,'sha256':digest(value)}
        if evidence not in row['sources']:row['sources'].append(evidence)
    def walk(value,origin,versions=()):
        if type(value) is dict:
            selected=value.get('language_version')
            if type(selected) is int and selected in range(1,7):versions=(selected,)
            for key in ('language_versions','versions'):
                if type(value.get(key)) is list and all(type(v) is int and v in range(1,7) for v in value[key]):versions=tuple(value[key])
            for key,item in value.items():
                if key in ('profile_id','profile','historical_profile','event_profile') or isinstance(key,str) and key.endswith('_profile_id'):
                    add(item,origin,value,versions)
                elif isinstance(key,str) and key.endswith('_profile_ids') and type(item) is list:
                    for identifier in item:add(identifier,origin,value,versions)
                if key=='profiles' and type(item) is dict:
                    for v,identifier in item.items():
                        if str(v) in tuple(map(str,range(1,7))):add(identifier,origin,value,(int(v),))
                if key=='versions' and type(item) is dict:
                    for v,child in item.items():walk(child,origin+'#/versions/'+str(v),(int(v),) if str(v) in tuple(map(str,range(1,7))) else versions)
                elif type(item) in (dict,list):walk(item,origin,versions)
        elif type(value) is list:
            for child in value:walk(child,origin,versions)
    for name,value in vars(session.snapshot).items():
        if name not in ('references','registry','version_registries') and type(value) is dict:walk(value,'snapshot.'+name)
    for path in sorted((ROOT/'spec').glob('*-profile.json')):
        value=read_json(path);origin=path.relative_to(ROOT).as_posix();walk(value,origin)
        declared=value.get('versions',{})
        versions=tuple(int(v) for v,row in declared.items() if str(v) in tuple(map(str,range(1,7)))
            and type(row) is dict and row.get('policy',{}).get('available') is not False) if type(declared) is dict else ()
        add(value.get('schema_version'),origin,value,versions)
    for row in rows.values():
        if not row['versions']:
            match=re.search(r'\.v([1-6])(?:\.|$)',row['profile_id'])
            if match:row['versions'].add(int(match[1]))
        row['versions']=sorted(row['versions']) or None
        row['availability_basis']='explicit_policy_or_versioned_id' if row['versions'] else 'not_inferred'
        row['execution_verified']=False
    return sorted(rows.values(),key=lambda row:row['profile_id'])

def overlay_profiles(program):
    """Use suffix/operation identities owned by existing contract modules."""
    from packages.contracts.context_ir import ir_nodes
    from packages.contracts import (dynamic_windows,dynamic_signals,prepared_requests,computed_targets,
        primary_targets,function_targets,scalar_targets,numeric_targets,lower_tf)
    nodes=list(ir_nodes(program.statements));ids=[getattr(n,'overload_id','') for n in nodes]
    result=set()
    from packages.contracts.objects import uses_objects,PROFILE as OBJECTS
    if uses_objects(program):result.add(OBJECTS)
    for module in (dynamic_windows,dynamic_signals,computed_targets,primary_targets,function_targets,scalar_targets,numeric_targets):
        if any(identity.endswith(module.SUFFIX) for identity in ids):result.add(module.PROFILE)
    if prepared_requests.uses_prepared(program):result.add(prepared_requests.PROFILE)
    if any(getattr(n,'operation',None)==lower_tf.OPERATION for n in nodes):
        result.add(read_json(ROOT/'spec/lower-tf-profile.json')['schema_version'])
    return result

def fixture_evidence(version,operations,implementation):
    """Only matching recorded observations count as execution evidence."""
    operations={o for o in operations if not o.startswith(('visual.','declaration.','simulation.strategy'))}
    result=[]
    for path in sorted((ROOT/'conformance').glob('*/cases.json')):
        corpus=read_json(path);cases=[c for c in corpus['cases'] if c['language_version']==version]
        if not cases:continue
        matches=[]
        for report_path in sorted((ROOT/'conformance/results').glob(path.parent.name+'*.json')):
            report=read_json(report_path)
            if (report.get('cases_sha256')==hashlib.sha256(path.read_bytes()).hexdigest()
                    and report.get('implementation_sha256')==implementation):
                matches.append((report_path,report))
        if not matches:continue
        report_path,report=matches[0]
        overlap=sorted(set(report.get('operations_by_version',{}).get(str(version),[]))&set(operations))
        if not overlap:continue
        recorded={r['case_id']:r for r in report['results']}
        passed=[]
        for case in cases:
            row=recorded.get(case['case_id'],{})
            if (row.get('passed') is True and case['expected']['status']=='executed'
                    and row.get('expected_sha256')==hashlib.sha256(canonical(case['expected'])+b'\n').hexdigest()
                    and row.get('source_sha256')==hashlib.sha256(safe_path(path.parent,case['source']).read_bytes()).hexdigest()):passed.append(case['case_id'])
        if not passed:continue
        result.append({'cases':path.relative_to(ROOT).as_posix(),'report':report_path.relative_to(ROOT).as_posix(),
            'report_sha256':hashlib.sha256(report_path.read_bytes()).hexdigest(),'case_ids':passed,
            'operation_overlap':overlap,'scope':'matching_recorded_corpus_observations; not proof of this whole program'})
    return result
