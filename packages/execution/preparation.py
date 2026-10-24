"""Input normalization and migration policy using supplied persistence ports."""

import re
import time
from dataclasses import asdict

from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.application import TERMINAL_STATUSES
from packages.contracts.storage import RunPlan,ResourcePolicy
from packages.contracts.wire import canonical, read_json, digest
from packages.marketdata.providers import snapshot_from_payload
from packages.execution.identity import plan_identity


def invalid(message, code="JOB_INVALID"):
    raise OpenPineError(Diagnostic("INPUT_INVALID", code, message))


def identifier(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Invalid content identifier.")


def validate_input(content):
    if type(content) is not dict or content.keys() not in ({"source", "source_name"}, {"artifact"}, {"artifact_ref"}, {"artifact_ref", "source_ref"}):
        invalid("Job requires source/source_name or an artifact.")
    for key in ("artifact_ref", "source_ref"):
        if key in content:
            identifier(content[key])
    if "source" in content and (type(content["source"]) is not str or len(content["source"].encode("utf-8")) > 65536
            or type(content["source_name"]) is not str or len(content["source_name"]) > 4096):
        invalid("Invalid source text/name or source exceeds 64 KiB.")


def validate_request(payload):
    if type(payload) is dict and payload.get('schema_version')=='openpine.job-request.7':
        from packages.contracts.optimization import evaluation_window
        evaluation_window(payload.get('evaluation_window'))
        configured={k:v for k,v in payload.items() if k!='evaluation_window'}
        if configured.get('settings','absent') is None:
            if 'inputs' in configured:invalid('Configured request requires exactly one of inputs or frozen settings.')
            configured.pop('settings');configured['inputs']={}
        validate_request({**configured,'schema_version':'openpine.job-request.6'})
        return
    if type(payload) is dict and payload.get('schema_version')=='openpine.job-request.6':
        base={'schema_version','input_ref','snapshot_id','timeout_seconds'}
        extra={'inputs','settings','primary_context','contexts','broker_settings'}
        if not base<=payload.keys()<=base|extra or ('inputs' in payload)==('settings' in payload):invalid('Configured request requires exactly one of inputs or frozen settings.')
        if 'inputs' in payload and (type(payload['inputs']) is not dict or len(payload['inputs'])>64):invalid('Inputs require at most 64 overrides.')
        if 'settings' in payload:
            from packages.contracts.inputs import validate_settings
            validate_settings(payload['settings'])
        legacy={k:payload[k] for k in base};legacy['schema_version']='openpine.job-request.2'
        if payload.get('primary_context') is not None or payload.get('contexts') or payload.get('broker_settings') is not None:
            legacy.update(schema_version='openpine.job-request.5' if payload.get('broker_settings') is not None else 'openpine.job-request.4',
                primary_context=payload.get('primary_context'),contexts=payload.get('contexts',[]))
            if payload.get('broker_settings') is not None:legacy['broker_settings']=payload['broker_settings']
        validate_request(legacy)
        return
    if type(payload) is not dict or payload.get("schema_version") not in ("openpine.job-request.1", "openpine.job-request.2","openpine.job-request.4","openpine.job-request.5"):
        invalid("Unknown job request schema.")
    timeout = payload.get("timeout_seconds")
    if type(timeout) not in (float, int) or not 0 < timeout <= 3600:
        invalid("Job timeout must be greater than zero and at most 3600 seconds.")
    if payload["schema_version"]!="openpine.job-request.1":
        extra={"primary_context","contexts"} if payload["schema_version"] in ("openpine.job-request.4","openpine.job-request.5") else set()
        if payload['schema_version']=='openpine.job-request.5':extra.add('broker_settings')
        if payload.keys() != {"schema_version", "input_ref", "snapshot_id", "timeout_seconds",*extra}:
            invalid("Unknown reference request fields.")
        identifier(payload["input_ref"]); identifier(payload["snapshot_id"])
        if extra:
            from packages.contracts.contexts import MarketContext,ContextBinding
            if payload['primary_context'] is not None:MarketContext.from_payload(payload["primary_context"])
            elif payload['schema_version']!='openpine.job-request.5' or payload['contexts']:invalid('Additional contexts require a primary context.')
            if type(payload["contexts"]) is not list or len(payload["contexts"])>7:invalid("Contexts require at most seven additional snapshot bindings.")
            for row in payload["contexts"]:ContextBinding.from_payload(row)
            if 'broker_settings' in extra:
                from packages.contracts.trading import BrokerSettings
                BrokerSettings.from_payload(payload['broker_settings'])
    else:
        if payload.keys() != {"schema_version", "input", "snapshot", "timeout_seconds"}:
            invalid("Unknown legacy request fields.")
        validate_input(payload["input"])
        snapshot_from_payload(payload["snapshot"])
        if len(canonical(payload)) > 16 * 1024 * 1024:
            invalid("Legacy job request exceeds 16 MiB.")


def make_plan(request, policy, simulation_policy=None):
    execution_sha, spec_sha = plan_identity()
    from packages.contracts.contexts import MarketContext,ContextBinding
    primary=MarketContext.from_payload(request["primary_context"]) if request.get('primary_context') is not None else None
    return RunPlan(request["input_ref"], request["snapshot_id"], request["timeout_seconds"], execution_sha, spec_sha,
        threads=policy.threads_per_worker, memory_mb=policy.memory_mb_per_worker,
        max_attempts=policy.max_attempts, heartbeat_timeout=policy.heartbeat_timeout,primary_context=primary,
        contexts=tuple(ContextBinding.from_payload(c) for c in request.get("contexts",[])),
        simulation_policy=simulation_policy,settings=request.get('settings'),evaluation_window=request.get('evaluation_window'),
        schema_version='openpine.run-plan.6' if request.get('evaluation_window') else 'openpine.run-plan.5' if request.get('settings') else 'openpine.run-plan.4',
        output_schema='openpine.run-summary.7' if request.get('evaluation_window') else 'openpine.run-summary.6' if request.get('settings') else 'openpine.run-summary.5' if simulation_policy else "openpine.run-summary.4" if primary else "openpine.run-summary.3")


class Preparation:
    def __init__(self, objects, market, repository, pin, limiter):
        self.objects, self.market, self.repository, self.pin = objects, market, repository, pin
        self.limiter = limiter

    def build_plan(self,request,policy):
        from packages.execution.artifacts import import_program
        from packages.contracts.trading import BrokerSettings
        content=self.objects.get(request['input_ref'])
        if 'source' in content:
            if 'broker_settings' in request:invalid('Broker settings require a successfully compiled strategy declaration.')
            return make_plan(request,policy)
        try:
            program=import_program(self.objects.get(content['artifact_ref']))
        except (OpenPineError,ValueError,KeyError,TypeError):
            # Existing transports report source/artifact errors as worker results.
            return make_plan(request,policy)
        settings=BrokerSettings.from_payload(request['broker_settings']) if 'broker_settings' in request else BrokerSettings()
        from packages.contracts.inputs import admitted_settings
        if admitted_settings(program,request.get('settings'))!=request.get('settings'):invalid('Configured programs require pinned execution settings before submission.')
        from packages.execution.environment import source_policy
        simulation=source_policy(program,self.market.source(request['snapshot_id']),settings)
        if simulation is None and 'broker_settings' in request:invalid('Broker settings require a strategy declaration.')
        return make_plan(request,policy,simulation)

    def freeze_input(self, content):
        with self.limiter.slot("prepare"):
            return self._freeze_input(content)

    def _freeze_input(self, content):
        validate_input(content)
        # A conservative routing check avoids loading the compiler for ordinary
        # indicator inputs. Candidate strategies still go through the real
        # parser, binder and IR validation; this does not accept any syntax.
        if 'source' in content and any(term in content['source'] for term in ('strategy','input')):
            from packages.execution.session import Session
            from packages.execution.artifacts import export_program
            from packages.contracts import HISTORICAL_PROFILE_ID
            from packages.compiler.semantic.specification import SpecError
            try:
                session=Session(profile_id=HISTORICAL_PROFILE_ID)
                program=session.compile(content['source'],content['source_name']);session.validate_program(program)
            except (OpenPineError,SpecError):
                pass  # Preserve asynchronous worker diagnostics for invalid input.
            else:
                from packages.contracts.inputs import definitions
                if program.statements[0].operation=='simulation.strategy' or definitions(program):
                    content={'artifact_ref':self.objects.put(export_program(program)),'source_ref':self.objects.put(content)}
        if "artifact" in content:
            content = {"artifact_ref": self.objects.put(content["artifact"])}
        if "artifact_ref" in content:
            self.objects.verify(content["artifact_ref"])
        if "source_ref" in content:
            original = self.objects.get(content["source_ref"])
            if type(original) is not dict or original.keys() != {"source", "source_name"}:
                invalid("Invalid source reference.")
            validate_input(original)
        ref = self.objects.put(content)
        self.pin("input:" + ref, ref)
        return ref

    def freeze(self, request):
        with self.limiter.slot("prepare"):
            return self._freeze(request)

    def _freeze(self, request):
        validate_request(request)
        if request.get('schema_version')=='openpine.job-request.7':
            window=request['evaluation_window']
            configured={k:v for k,v in request.items() if k!='evaluation_window'}
            if configured.get('settings','absent') is None:
                configured.pop('settings');configured['inputs']={}
            frozen=self._freeze({**configured,'schema_version':'openpine.job-request.6'})
            if request.get('settings','absent') is None and frozen.get('settings') is not None:
                invalid('Null execution settings require a program without configurable inputs.')
            frozen.setdefault('settings',None)
            identity=self.market.manifest(frozen['snapshot_id'])['identity']
            if not window['warmup_start_ms']<=identity['first_ms']<=window['score_start_ms']<=identity['last_ms']<window['end_ms']:
                invalid('Snapshot does not fit its research window or contains no scored bars.')
            return {**frozen,'schema_version':'openpine.job-request.7','evaluation_window':dict(window)}
        if request["schema_version"].endswith(".1"):
            snapshot_id = self.market.save(snapshot_from_payload(request["snapshot"]))
            content = request["input"]
        else:
            snapshot_id = request["snapshot_id"]
            self.market.manifest(snapshot_id)
            content = self.objects.get(request["input_ref"])
        normalized={"schema_version": "openpine.job-request.2", "input_ref": self.freeze_input(content),
            "snapshot_id": snapshot_id, "timeout_seconds": request["timeout_seconds"]}
        if request["schema_version"] in ("openpine.job-request.4","openpine.job-request.5","openpine.job-request.6"):
            normalized.update(schema_version=request["schema_version"],primary_context=request.get("primary_context"),contexts=request.get("contexts",[]))
            if request.get('broker_settings') is not None:normalized['broker_settings']=request['broker_settings']
            plan=make_plan(normalized,ResourcePolicy())
            for context,sha in ([ (plan.primary_context,snapshot_id)] if plan.primary_context else [])+[(b.context,b.snapshot_id) for b in plan.contexts]:
                manifest=self.market.manifest(sha);identity=manifest["identity"]
                if ((context.context_id,context.symbol,context.timeframe)!=(identity["context_id"],identity["symbol"],identity["timeframe"])
                        or context.source!=manifest["provenance"]["provider"]):
                    invalid("Context metadata/source differs from its pinned snapshot.","CONTEXT_INPUT")
        frozen=self.objects.get(normalized['input_ref'])
        if 'artifact_ref' in frozen:
            from packages.execution.artifacts import import_program
            from packages.contracts.inputs import admitted_settings,settings_for
            try:
                program=import_program(self.objects.get(frozen['artifact_ref']))
                settings=admitted_settings(program,request['settings']) if 'settings' in request else settings_for(program,request.get('inputs'))
            except (OpenPineError,ValueError,KeyError,TypeError):
                if request['schema_version']=='openpine.job-request.6':raise
                # Legacy transports keep reporting incompatible IR as worker results.
                # Only explicitly configured submissions require early input admission.
                settings=None
            if settings is not None:normalized.update(schema_version='openpine.job-request.6',settings=settings)
            elif normalized['schema_version']=='openpine.job-request.6':
                normalized['schema_version']='openpine.job-request.5' if 'broker_settings' in normalized else 'openpine.job-request.4' if normalized.get('primary_context') is not None else 'openpine.job-request.2'
                if normalized['schema_version']=='openpine.job-request.2':
                    normalized.pop('primary_context',None);normalized.pop('contexts',None)
        elif request['schema_version']=='openpine.job-request.6':
            invalid('Input overrides require a successfully compiled program.')
        return normalized

    def migrate(self, directory, policy):
        repo = self.repository
        if not repo.scanned("__legacy_scan_v1__"):
            for path in sorted(directory.glob("*/status.json")):
                job_id = path.parent.name
                if not re.fullmatch(r"[0-9a-f]{32}", job_id):
                    continue
                try:
                    state = read_json(path)
                    if state["job_id"] != job_id or state["status"] not in {*TERMINAL_STATUSES, "queued", "running"}:
                        raise ValueError("Invalid legacy state.")
                    request = self.freeze(read_json(path.parent / "request.json"))
                    state.update(attempt_id=None, attempt_count=0, result_id=None)
                    if state["status"] not in TERMINAL_STATUSES:
                        state.update(status="failed", finished_at=time.time(), result_sha256=None,
                            error={"code": "HOST_RESTARTED", "message": "Imported interrupted legacy job."})
                    elif state.get("result_sha256"):
                        result = read_json(path.parent / "result.json", max_bytes=32*1024*1024)
                        if digest(result) != state["result_sha256"]:
                            raise ValueError("Legacy result hash mismatch.")
                        state["result_id"] = self.objects.put(result)
                    repo.insert(state, request, imported_from=str(path), plan=make_plan(request, policy))
                except (OSError, ValueError, KeyError, TypeError) as error:
                    repo.record_import(str(path), error=str(error))
            repo.record_import("__legacy_scan_v1__")
        # Upgrade existing catalog v1 jobs exactly once. Interrupted plans thereafter
        # retain their submitted policy, regardless of current coordinator defaults.
        if not repo.scanned("__run_plans_v1__"):
            for state in repo.list(limit=2**63-1):
                try:
                    repo.plan(state["job_id"])
                except KeyError:
                    repo.attach_plan(state["job_id"], make_plan(repo.request(state["job_id"]), policy))
            repo.record_import("__run_plans_v1__")
