"""Admission tests, not claims of executing Pine programs."""

import copy
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from packages.compiler.semantic.specification import (
    _DocumentSchemas, validate_document, validate_schema,
)
from tools.specification import (
    ROOT, SpecError, coverage, encode, file_map, load_snapshot, loads,
    read_json, require_python, safe_path, sha,
)


class DocumentSchemaTests(unittest.TestCase):
    def definitions(self):
        return {"$defs": {"count": {"type": "integer", "minimum": 0},
                          "name": {"type": "string", "minLength": 1}}}

    def test_one_schema_check_per_context_still_validates_every_document(self):
        with patch.object(Draft202012Validator, "check_schema",
                          wraps=Draft202012Validator.check_schema) as check:
            context = _DocumentSchemas(self.definitions())
            for value in (1, 2):
                validate_document(value, context, "count")
            validate_document("Pine", context, "name")
            for value, kind in ((-1, "count"), ("1", "count"), ("", "name")):
                with self.subTest(value=value, kind=kind), self.assertRaises(ValidationError):
                    validate_document(value, context, kind)
            with self.assertRaisesRegex(SpecError, "unknown document schema"):
                validate_document({}, context, "missing")
            self.assertEqual(check.call_count, 1)

    def test_entire_schema_is_checked_including_unused_definitions(self):
        definitions = self.definitions()
        definitions["$defs"]["unused"] = {"type": "invalid-type"}
        with self.assertRaises(SchemaError):
            validate_document(1, definitions, "count")
        definitions["$defs"]["unused"] = {"$ref": "https://example.invalid/schema"}
        with self.assertRaisesRegex(SpecError, "schema must use local references"):
            validate_document(1, definitions, "count")

    def test_context_is_private_and_new_context_checks_mutated_contents(self):
        definitions = self.definitions()
        context = _DocumentSchemas(definitions)
        definitions["$defs"]["count"] = {"type": "string"}
        validate_document(1, context, "count")
        with self.assertRaises(ValidationError):
            validate_document("changed", context, "count")
        validate_document("changed", definitions, "count")
        definitions["$defs"]["count"]["type"] = "invalid-type"
        with self.assertRaises(SchemaError):
            validate_document(1, definitions, "count")
        with self.assertRaises(SchemaError):
            _DocumentSchemas(definitions)

    def test_generic_schema_is_checked_again_after_caller_mutation(self):
        schema = {"type": "integer"}
        with patch.object(Draft202012Validator, "check_schema",
                          wraps=Draft202012Validator.check_schema) as check:
            validate_schema(1, schema)
            schema["type"] = "invalid-type"
            with self.assertRaises(SchemaError):
                validate_schema(1, schema)
            self.assertEqual(check.call_count, 2)


class SpecificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.version_info[:3] != (3, 14, 8):
            raise RuntimeError("Tests must run on Python 3.14.8")
        cls.temporary_root = ROOT / ".runtime/spec-tests"
        cls.temporary_root.mkdir(parents=True, exist_ok=True)
        cls.snapshot = load_snapshot()

    def copied_root(self):
        temporary = tempfile.TemporaryDirectory(prefix="case-", dir=self.temporary_root)
        if not Path(temporary.name).resolve().is_relative_to(self.temporary_root.resolve()):
            raise RuntimeError("Unexpected test temporary path")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        shutil.copytree(ROOT / "spec", root / "spec")
        return root

    def repin(self, root, *, source_bundle=False):
        """Simulate a newly proposed release with consistent byte hashes."""
        lock = read_json(root / "spec/lock.json")
        bundle = root / lock["snapshot"]
        manifest = read_json(bundle / "manifest.json")
        source = bundle / manifest["semantic_bundle"]
        source_manifest = read_json(source / "manifest.json")
        if source_bundle:
            files = file_map(source)
            files.pop("manifest.json")
            source_manifest["files"] = files
            for name in ("semantic", "documentation", "evidence"):
                source_manifest[f"{name}_hash"] = files[f"{name}.json"]
            (source / "manifest.json").write_bytes(encode(source_manifest))
            lock["semantic_hash"] = source_manifest["semantic_hash"]
        files = file_map(bundle)
        files.pop("manifest.json")
        manifest["files"] = files
        (bundle / "manifest.json").write_bytes(encode(manifest))
        lock["manifest_sha256"] = sha((bundle / "manifest.json").read_bytes())
        if "version_review" in lock:
            directory = root / lock["version_review"]["path"]
            review = read_json(directory / "review.json")
            review["base_manifest_sha256"] = lock["manifest_sha256"]
            (directory / "review.json").write_bytes(encode(review))
            version_manifest = read_json(directory / "manifest.json")
            version_manifest["base_manifest_sha256"] = lock["manifest_sha256"]
            version_manifest["files"]["review.json"] = sha((directory / "review.json").read_bytes())
            (directory / "manifest.json").write_bytes(encode(version_manifest))
            lock["version_review"]["manifest_sha256"] = sha((directory / "manifest.json").read_bytes())
        for path in lock["profiles"]:
            lock["profiles"][path] = sha((root / path).read_bytes())
        if "language_release" in lock:
            directory = root / lock["language_release"]["path"]
            policy = read_json(directory / "policy.json")
            policy.update(base_manifest_sha256=lock["manifest_sha256"], version_review_manifest_sha256=lock["version_review"]["manifest_sha256"], grammar_sha256=sha((bundle / "review/grammar.json").read_bytes()))
            (directory / "policy.json").write_bytes(encode(policy))
            language_manifest = read_json(directory / "manifest.json")
            language_manifest["files"]["policy.json"] = sha((directory / "policy.json").read_bytes())
            (directory / "manifest.json").write_bytes(encode(language_manifest))
            lock["language_release"]["manifest_sha256"] = sha((directory / "manifest.json").read_bytes())
        if "series_release" in lock:
            directory = root / lock["series_release"]["path"]
            policy = read_json(directory / "series.json")
            policy.update(base_manifest_sha256=lock["manifest_sha256"],
                version_review_manifest_sha256=lock["version_review"]["manifest_sha256"],
                language_manifest_sha256=lock["language_release"]["manifest_sha256"])
            (directory / "series.json").write_bytes(encode(policy))
            series_manifest = read_json(directory / "manifest.json")
            series_manifest["files"]["series.json"] = sha((directory / "series.json").read_bytes())
            (directory / "manifest.json").write_bytes(encode(series_manifest))
            lock["series_release"]["manifest_sha256"] = sha((directory / "manifest.json").read_bytes())
        if "function_release" in lock:
            function_directory = root / lock["function_release"]["path"]
            function_data = read_json(function_directory / "functions.json")
            function_data.update(base_manifest_sha256=lock["manifest_sha256"],
                version_review_manifest_sha256=lock["version_review"]["manifest_sha256"],
                language_manifest_sha256=lock["language_release"]["manifest_sha256"],
                series_manifest_sha256=lock["series_release"]["manifest_sha256"])
            (function_directory / "functions.json").write_bytes(encode(function_data))
            function_manifest = read_json(function_directory / "manifest.json")
            function_manifest["files"]["functions.json"] = sha((function_directory / "functions.json").read_bytes())
            (function_directory / "manifest.json").write_bytes(encode(function_manifest))
            lock["function_release"]["manifest_sha256"] = sha((function_directory / "manifest.json").read_bytes())
        if "request_release" in lock:
            directory=root/lock["request_release"]["path"]
            data=read_json(directory/"requests.json")
            data.update(base_manifest_sha256=lock["manifest_sha256"],
                version_review_manifest_sha256=lock["version_review"]["manifest_sha256"])
            (directory/"requests.json").write_bytes(encode(data))
            request_manifest=read_json(directory/"manifest.json")
            request_manifest["files"]["requests.json"]=sha((directory/"requests.json").read_bytes())
            (directory/"manifest.json").write_bytes(encode(request_manifest))
            lock["request_release"]["manifest_sha256"]=sha((directory/"manifest.json").read_bytes())
        if 'strategy_release' in lock:
            directory=root/lock['strategy_release']['path'];data=read_json(directory/'strategies.json')
            data.update(base_manifest_sha256=lock['manifest_sha256'],version_review_manifest_sha256=lock['version_review']['manifest_sha256'])
            (directory/'strategies.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
            manifest['files']['strategies.json']=sha((directory/'strategies.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
            lock['strategy_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
        if 'input_release' in lock:
            directory=root/lock['input_release']['path'];data=read_json(directory/'inputs.json')
            data.update(base_manifest_sha256=lock['manifest_sha256'],version_review_manifest_sha256=lock['version_review']['manifest_sha256'])
            (directory/'inputs.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
            manifest['files']['inputs.json']=sha((directory/'inputs.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
            lock['input_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
        if 'exit_release' in lock:
            directory=root/lock['exit_release']['path'];data=read_json(directory/'exits.json')
            data.update(base_manifest_sha256=lock['manifest_sha256'],version_review_manifest_sha256=lock['version_review']['manifest_sha256'])
            (directory/'exits.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
            manifest['files']['exits.json']=sha((directory/'exits.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
            lock['exit_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
        if 'technical_release' in lock:
            directory=root/lock['technical_release']['path'];data=read_json(directory/'technical.json')
            data.update(base_manifest_sha256=lock['manifest_sha256'],version_review_manifest_sha256=lock['version_review']['manifest_sha256'])
            (directory/'technical.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
            manifest['files']['technical.json']=sha((directory/'technical.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
            lock['technical_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
        if 'composite_release' in lock:
            directory=root/lock['composite_release']['path'];data=read_json(directory/'composite.json')
            data.update(base_manifest_sha256=lock['manifest_sha256'],version_review_manifest_sha256=lock['version_review']['manifest_sha256'])
            (directory/'composite.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
            manifest['files']['composite.json']=sha((directory/'composite.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
            lock['composite_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
        if 'loop_release' in lock:
            directory=root/lock['loop_release']['path'];data=read_json(directory/'loops.json')
            data.update(base_manifest_sha256=lock['manifest_sha256'],version_review_manifest_sha256=lock['version_review']['manifest_sha256'])
            (directory/'loops.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
            manifest['files']['loops.json']=sha((directory/'loops.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
            lock['loop_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
        if 'array_release' in lock:
            directory=root/lock['array_release']['path'];data=read_json(directory/'arrays.json')
            data.update(base_manifest_sha256=lock['manifest_sha256'],version_review_manifest_sha256=lock['version_review']['manifest_sha256'])
            (directory/'arrays.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
            manifest['files']['arrays.json']=sha((directory/'arrays.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
            lock['array_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
        if 'pending_entry_release' in lock:
            directory=root/lock['pending_entry_release']['path'];data=read_json(directory/'pending-entries.json')
            data.update(base_manifest_sha256=lock['manifest_sha256'],version_review_manifest_sha256=lock['version_review']['manifest_sha256'])
            (directory/'pending-entries.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
            manifest['files']['pending-entries.json']=sha((directory/'pending-entries.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
            lock['pending_entry_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
        if 'partial_exit_release' in lock:
            directory=root/lock['partial_exit_release']['path'];data=read_json(directory/'partial-exits.json')
            data.update(base_manifest_sha256=lock['manifest_sha256'],version_review_manifest_sha256=lock['version_review']['manifest_sha256'])
            (directory/'partial-exits.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
            manifest['files']['partial-exits.json']=sha((directory/'partial-exits.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
            lock['partial_exit_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
        if 'signal_release' in lock:
            directory=root/lock['signal_release']['path'];data=read_json(directory/'signals.json')
            data.update(base_manifest_sha256=lock['manifest_sha256'],version_review_manifest_sha256=lock['version_review']['manifest_sha256'])
            (directory/'signals.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
            manifest['files']['signals.json']=sha((directory/'signals.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
            lock['signal_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
        if 'environment_release' in lock:
            directory=root/lock['environment_release']['path'];data=read_json(directory/'environment.json')
            data.update(base_manifest_sha256=lock['manifest_sha256'],version_review_manifest_sha256=lock['version_review']['manifest_sha256'])
            (directory/'environment.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
            manifest['files']['environment.json']=sha((directory/'environment.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
            lock['environment_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
        (root / "spec/lock.json").write_bytes(encode(lock))

    def expect_error(self, code, operation):
        with self.assertRaises(SpecError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def test_interpreter_and_supported_python_boundary(self):
        self.assertEqual(sys.version_info[:3], (3, 14, 8))
        for version in ((3, 13, 9), (3, 15, 0)):
            with self.subTest(version=version), patch("tools.specification.sys.version_info", version):
                self.expect_error("PYTHON_VERSION", require_python)

    def test_all_versions_keep_independent_profiles_and_zero_execution_claims(self):
        report = coverage(self.snapshot)
        self.assertEqual(report["reference_entries"], 4631)
        self.assertEqual([row["reference_entries"] for row in report["versions"]],
                         [315, 365, 361, 626, 1444, 1520])
        for version in range(1, 7):
            profile = self.snapshot.profile(version)
            self.assertEqual(profile["language_version"], version)
            self.assertNotIn("execution_support", profile)
            self.assertEqual(profile["inheritance"], "explicit_only")
            row = report["versions"][version - 1]
            self.assertNotIn("openpine_implementation", row)
            if version < 6:
                self.assertEqual(row["admitted_assertions"], 15)
                self.assertEqual(row["evidence_level"], "reconstructed" if version < 3 else "documentation_derived")

    def test_profiles_and_resolved_values_are_returned_as_copies(self):
        profile = self.snapshot.profile(6)
        profile["semantic_profile_ids"].clear()
        self.assertTrue(self.snapshot.profile(6)["semantic_profile_ids"])
        fact = next(row for row in self.snapshot.registry["assertions"]
                    if row["property_path"] == "signature" and row["value"]["operation"] == "numeric.abs")
        resolved = self.snapshot.resolve(fact["assertion_id"], 6)
        self.assertEqual(resolved["operation"], "numeric.abs")
        resolved["operation"] = "changed"
        self.assertEqual(self.snapshot.resolve(fact["assertion_id"], 6)["operation"], "numeric.abs")
        self.expect_error("PROFILE_UNCOVERED", lambda: self.snapshot.resolve(fact["assertion_id"], 5))
        self.expect_error("PROFILE_UNCOVERED", lambda: self.snapshot.resolve(fact["assertion_id"], 6, "realtime"))

    def test_unsupported_version_never_falls_back_to_v6(self):
        self.assertEqual(self.snapshot.profile()["language_version"], 1)
        for version in (0, 7, True, "6"):
            with self.subTest(version=version):
                self.expect_error("LANGUAGE_VERSION_UNSUPPORTED", lambda: self.snapshot.profile(version))

    def test_unresolved_v5_defaults_remain_unusable(self):
        facts = coverage(self.snapshot)["versions"][4]["unresolved_assertion_refs"]
        self.assertEqual(len(facts), 3)
        for fact in facts:
            self.expect_error("SPEC_UNRESOLVED", lambda: self.snapshot.resolve(fact, 5))
        self.expect_error("SPEC_UNRESOLVED", lambda: self.snapshot.resolve("missing.fact", 6))

    def test_reference_spellings_never_become_active_defaults(self):
        study = next(entry for entry in self.snapshot.references[1]["entries"] if entry["name"] == "study")
        overlay = next(parameter for parameter in study["parameters"] if parameter["name"] == "overlay")
        self.assertEqual(overlay["default_claim"]["spellings"], ["false"])
        self.assertFalse(study["executable_contract"])
        self.assertEqual(study["review_status"], "reference_only")

    def test_reader_works_with_network_disabled(self):
        with patch.object(socket, "create_connection", side_effect=AssertionError("unexpected network")), \
             patch.object(socket, "getaddrinfo", side_effect=AssertionError("unexpected DNS")):
            self.assertEqual(load_snapshot().lock, self.snapshot.lock)

    def test_changed_or_missing_file_is_rejected(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                root = self.copied_root()
                path = root / self.snapshot.lock["snapshot"] / "reference/v1.json"
                if missing:
                    path.unlink()
                else:
                    path.write_bytes(path.read_bytes() + b" ")
                self.expect_error("SPEC_HASH", lambda: load_snapshot(root))

    def test_extra_snapshot_file_is_rejected(self):
        root = self.copied_root()
        (root / self.snapshot.lock["snapshot"] / "extra.json").write_bytes(b"{}")
        self.expect_error("SPEC_FILES", lambda: load_snapshot(root))

    def test_manifest_rehash_without_external_pin_is_rejected(self):
        root = self.copied_root()
        path = root / self.snapshot.lock["snapshot"] / "manifest.json"
        value = read_json(path)
        value["source_commit"] = "0" * 40
        path.write_bytes(encode(value))
        self.expect_error("SPEC_HASH", lambda: load_snapshot(root))

    def test_review_cannot_be_bypassed_by_rehashing_semantic_files(self):
        root = self.copied_root()
        bundle = root / self.snapshot.lock["snapshot"]
        registry_path = bundle / "review/registry.json"
        registry = read_json(registry_path)
        fact = next(row for row in registry["assertions"]
                    if row["property_path"] == "signature" and row["value"]["operation"] == "numeric.abs")
        fact["value"]["parameters"][0]["requiredness"]["state"] = "optional"
        registry_path.write_bytes(encode(registry))
        semantic_path = bundle / read_json(bundle / "manifest.json")["semantic_bundle"] / "semantic.json"
        semantic = read_json(semantic_path)
        next(row for row in semantic["registry"]["assertions"]
             if row["assertion_id"] == fact["assertion_id"])["value"] = fact["value"]
        semantic_path.write_bytes(encode(semantic))
        self.repin(root, source_bundle=True)
        self.expect_error("SPEC_REVIEW", lambda: load_snapshot(root))

    def test_cross_version_profile_inheritance_is_rejected_even_if_repinned(self):
        root = self.copied_root()
        path = root / "spec/profiles/pine-v5.json"
        profile = read_json(path)
        profile["semantic_profile_ids"] = self.snapshot.profile(6)["semantic_profile_ids"]
        path.write_bytes(encode(profile))
        self.repin(root)
        self.expect_error("SPEC_VERSION", lambda: load_snapshot(root))

    def test_duplicate_identity_is_rejected_even_if_repinned(self):
        root = self.copied_root()
        bundle = root / self.snapshot.lock["snapshot"]
        path = bundle / read_json(bundle / "manifest.json")["semantic_bundle"] / "identities.json"
        identities = read_json(path)
        identities["aliases"].append(copy.deepcopy(identities["aliases"][0]))
        path.write_bytes(encode(identities))
        self.repin(root, source_bundle=True)
        self.expect_error("SPEC_IDENTITY", lambda: load_snapshot(root))

    def test_request_release_foreign_base_is_rejected_even_with_valid_file_hashes(self):
        root=self.copied_root();lock=read_json(root/"spec/lock.json")
        directory=root/lock["request_release"]["path"]
        data=read_json(directory/"requests.json");data["base_manifest_sha256"]="0"*64
        (directory/"requests.json").write_bytes(encode(data))
        manifest=read_json(directory/"manifest.json")
        manifest["files"]["requests.json"]=sha((directory/"requests.json").read_bytes())
        (directory/"manifest.json").write_bytes(encode(manifest))
        lock["request_release"]["manifest_sha256"]=sha((directory/"manifest.json").read_bytes())
        (root/"spec/lock.json").write_bytes(encode(lock))
        self.expect_error("SPEC_PROVENANCE",lambda:load_snapshot(root))

    def test_strategy_release_foreign_base_is_rejected_even_with_valid_file_hashes(self):
        root=self.copied_root();lock=read_json(root/'spec/lock.json');directory=root/lock['strategy_release']['path']
        data=read_json(directory/'strategies.json');data['base_manifest_sha256']='0'*64
        (directory/'strategies.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
        manifest['files']['strategies.json']=sha((directory/'strategies.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
        lock['strategy_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
        self.expect_error('SPEC_PROVENANCE',lambda:load_snapshot(root))

    def test_input_release_foreign_base_is_rejected_even_with_valid_file_hashes(self):
        root=self.copied_root();lock=read_json(root/'spec/lock.json');directory=root/lock['input_release']['path']
        data=read_json(directory/'inputs.json');data['base_manifest_sha256']='0'*64
        (directory/'inputs.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
        manifest['files']['inputs.json']=sha((directory/'inputs.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
        lock['input_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
        self.expect_error('SPEC_PROVENANCE',lambda:load_snapshot(root))

    def test_exit_release_foreign_base_is_rejected_even_with_valid_file_hashes(self):
        root=self.copied_root();lock=read_json(root/'spec/lock.json');directory=root/lock['exit_release']['path']
        data=read_json(directory/'exits.json');data['base_manifest_sha256']='0'*64
        (directory/'exits.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
        manifest['files']['exits.json']=sha((directory/'exits.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
        lock['exit_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
        self.expect_error('SPEC_PROVENANCE',lambda:load_snapshot(root))

    def test_unknown_bundle_schema_is_rejected_even_if_repinned(self):
        root = self.copied_root()
        bundle = root / self.snapshot.lock["snapshot"]
        path = bundle / read_json(bundle / "manifest.json")["semantic_bundle"] / "manifest.json"
        manifest = read_json(path)
        manifest["schema_version"] = "2.0.0"
        path.write_bytes(encode(manifest))
        self.repin(root)
        self.expect_error("SPEC_SCHEMA", lambda: load_snapshot(root))

    def test_catalog_provenance_mismatch_is_rejected_even_if_repinned(self):
        root = self.copied_root()
        path = root / self.snapshot.lock["snapshot"] / "reference/v1.json"
        reference = read_json(path)
        reference["source_catalog_sha256"] = "0" * 64
        path.write_bytes(encode(reference))
        self.repin(root)
        self.expect_error("SPEC_PROVENANCE", lambda: load_snapshot(root))

    def test_strict_json_and_safe_paths(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}'):
            self.expect_error("SPEC_JSON", lambda: loads(text))
        for path in ("../outside", "/absolute", "C:/outside", "x\\y", "a/../b"):
            self.expect_error("SPEC_PATH", lambda: safe_path(ROOT, path))

    def test_local_schema_rejects_remote_references(self):
        from tools.specification import validate_schema
        self.expect_error("SPEC_SCHEMA", lambda: validate_schema({}, {"$ref": "https://example.invalid/schema"}))

    def test_coverage_is_derived_from_admitted_data(self):
        self.assertEqual((ROOT / "spec/coverage.json").read_bytes(), encode(coverage(self.snapshot)))
        self.assertEqual(coverage(self.snapshot)["versions"][5]["admitted_assertions"], 55)


if __name__ == "__main__":
    unittest.main()
