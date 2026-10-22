"""The single application path, independent of CLI/HTTP/process transport."""

import platform
from dataclasses import asdict

from jsonschema.exceptions import ValidationError

from packages.compiler.semantic.specification import SpecError
from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.application import CompileRequest, RunRequest
from packages.execution.session import Session
from packages.execution.artifacts import export_program
from packages.runtime.engine import program_hash


def rejection(error):
    if isinstance(error, OpenPineError):
        diagnostic = error.diagnostic
    elif isinstance(error, (SpecError, ValidationError)):
        code = getattr(error, "code", "SPEC_INVALID")
        category = code if code in ("SPEC_UNRESOLVED", "PROFILE_UNCOVERED", "IMPLEMENTATION_MISSING") else "ARTIFACT_INCOMPATIBLE"
        diagnostic = Diagnostic(category, code, str(error))
    elif isinstance(error, (OSError, UnicodeError)):
        diagnostic = Diagnostic("INPUT_UNAVAILABLE", "INPUT_IO", str(error))
    else:
        raise error
    return {"status": "rejected", "diagnostics": [asdict(diagnostic)]}


def response(program, result=None):
    from packages.contracts.inputs import definitions,input_schema
    return {"status": "compiled" if result is None else "executed", "python": platform.python_version(),
            "identity": asdict(program.identity), "program_sha256": program_hash(program),
            "ir": asdict(program), "result": asdict(result) if result else None,
            **({"artifact": export_program(program)} if result is None else {}),
            **({'inputs':input_schema(program)} if definitions(program) else {})}


class Application:
    def __init__(self):
        self.sessions = {}

    def session(self, profile_id):
        if profile_id not in self.sessions:
            self.sessions[profile_id] = Session(profile_id=profile_id)
        return self.sessions[profile_id]

    def handle(self, request: CompileRequest | RunRequest):
        try:
            compilation = request.compilation if isinstance(request, RunRequest) else request
            if type(compilation) is not CompileRequest or type(compilation.source) is not str or type(compilation.source_name) is not str:
                raise OpenPineError(Diagnostic("INPUT_INVALID", "REQUEST_INVALID", "Invalid compile request."))
            if compilation.profile_id not in ("openpine.historical-bars.1", "openpine.literal-harness.1"):
                raise OpenPineError(Diagnostic("INPUT_INVALID", "REQUEST_INVALID", "Unknown implementation profile."))
            session = self.session(compilation.profile_id)
            if isinstance(request, RunRequest):
                program, result = session.run(compilation.source, compilation.source_name, data=request.data,inputs=request.inputs)
                return response(program, result)
            return response(session.compile(compilation.source, compilation.source_name))
        except (OpenPineError, SpecError, ValidationError, OSError, UnicodeError) as error:
            return rejection(error)

    def execute(self, program, *, data=None, progress=None, cancelled=None,inputs=None):
        try:
            if program.identity.implementation_profile_id not in ("openpine.historical-bars.1", "openpine.literal-harness.1"):
                raise OpenPineError(Diagnostic("ARTIFACT_INCOMPATIBLE", "ARTIFACT_IDENTITY", "Unknown artifact implementation profile."))
            session = self.session(program.identity.implementation_profile_id)
            result = session.execute(program, data=data, progress=progress, cancelled=cancelled,inputs=inputs)
            return response(program, result)
        except (OpenPineError, SpecError, ValidationError, OSError, UnicodeError) as error:
            return rejection(error)
