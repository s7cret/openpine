"""Version selection, reconstructed-policy disclosure and reviewed-data integrity."""

import shutil
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from packages.compiler.semantic.specification import ROOT, SpecError, coverage, encode, file_map, load_snapshot, read_json, sha
from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.execution.session import Session
from packages.marketdata import from_payload
from packages.runtime.engine import Runtime
from tools.conformance import VERSION_CASES, run_cases


def source(version, body, *, annotation=True):
    declaration = "study" if version < 5 else "indicator"
    return (f'//@version={version}\n' if annotation else '') + f'{declaration}("versions")\n{body}\n'


def bars(values):
    return from_payload({"schema_version": "openpine.historical-bars.1", "context_id": "versions.test",
        "symbol": "TEST:VERSIONS", "timeframe": "1",
        "bars": [{"time": index * 60000, "close": value} for index, value in enumerate(values)]})


class VersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.snapshot = cls.session.snapshot

    def copied_root(self):
        parent = ROOT / ".runtime/version-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="case-", dir=parent)
        if not Path(temporary.name).resolve().is_relative_to(parent.resolve()):
            raise RuntimeError("Unexpected temporary path")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        shutil.copytree(ROOT / "spec", root / "spec")
        return root

    def repin_review(self, root):
        lock = read_json(root / "spec/lock.json")
        directory = root / lock["version_review"]["path"]
        review = read_json(directory / "review.json")
        for row in review["versions"].values():
            row["semantic_sha256"] = sha((directory / row["registry"]).read_bytes())
        (directory / "review.json").write_bytes(encode(review))
        manifest = read_json(directory / "manifest.json")
        manifest["files"] = {key: value for key, value in file_map(directory).items() if key != "manifest.json"}
        (directory / "manifest.json").write_bytes(encode(manifest))
        lock["version_review"]["manifest_sha256"] = sha((directory / "manifest.json").read_bytes())
        if "language_release" in lock:
            language_directory = root / lock["language_release"]["path"]
            policy = read_json(language_directory / "policy.json")
            policy["version_review_manifest_sha256"] = lock["version_review"]["manifest_sha256"]
            (language_directory / "policy.json").write_bytes(encode(policy))
            language_manifest = read_json(language_directory / "manifest.json")
            language_manifest["files"]["policy.json"] = sha((language_directory / "policy.json").read_bytes())
            (language_directory / "manifest.json").write_bytes(encode(language_manifest))
            lock["language_release"]["manifest_sha256"] = sha((language_directory / "manifest.json").read_bytes())
        if "series_release" in lock:
            directory_series = root / lock["series_release"]["path"]
            series = read_json(directory_series / "series.json")
            series.update(version_review_manifest_sha256=lock["version_review"]["manifest_sha256"],
                          language_manifest_sha256=lock["language_release"]["manifest_sha256"])
            (directory_series / "series.json").write_bytes(encode(series))
            manifest_series = read_json(directory_series / "manifest.json")
            manifest_series["files"]["series.json"] = sha((directory_series / "series.json").read_bytes())
            (directory_series / "manifest.json").write_bytes(encode(manifest_series))
            lock["series_release"]["manifest_sha256"] = sha((directory_series / "manifest.json").read_bytes())
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
        (root / "spec/lock.json").write_bytes(encode(lock))
        return directory

    def test_all_versions_run_independent_cases(self):
        report = run_cases(self.session, cases_path=VERSION_CASES)
        self.assertEqual((report["executed_programs"], report["expected_rejections"]), (36, 26))
        for version in range(1, 7):
            rows = [row for row in report["results"] if row["language_version"] == version]
            self.assertEqual(sum(row["observed"]["status"] == "executed" for row in rows), 6)
            self.assertTrue(all(row["semantic_profile_id"] == self.snapshot.semantic_identity(version)[0] for row in rows))

    def test_profiles_have_unique_identities_and_disclose_evidence(self):
        identities = [self.snapshot.semantic_identity(v) for v in range(1, 7)]
        self.assertEqual(len({row[0] for row in identities}), 6)
        self.assertEqual(len({row[1] for row in identities}), 6)
        technical_note = ('Technical-analysis overloads are a bounded artifact-derived model; '
                          'missing historical signatures are explicitly reconstructed.')
        loop_note = ('Loops use versioned documented availability and bounded artifact-defined execution; '
                     'historical reconstructions and platform equivalence remain unverified.')
        environment_note = ('UTC environment and bar phases follow pinned artifact-defined rules; '
                            'external platform equivalence is unverified.')
        signal_note = 'Signal/event and weighted-window functions follow a bounded artifact-defined model; external platform equivalence is unverified.'
        partial_note = 'Partial exits use an explicit bounded historical OHLC model; incomplete v1-v3 quantity signatures are reconstructed and external equivalence is unverified.'
        entry_note = 'Pending entries use a bounded historical OHLC model; incomplete v1-v2 signatures are reconstructed and intrabar activation never implies a known tick path.'
        from packages.contracts.lots import NOTE as lot_note
        from packages.contracts.order_groups import NOTE as group_note
        for version in range(1, 7):
            program = self.session.compile(source(version, 'plot(close)'))
            self.assertTrue(program.identity.compatibility_notes)
            self.assertIn(technical_note, program.identity.compatibility_notes)
            self.assertIn(loop_note, program.identity.compatibility_notes)
            self.assertIn(environment_note, program.identity.compatibility_notes)
            self.assertIn(signal_note, program.identity.compatibility_notes)
            self.assertIn(partial_note, program.identity.compatibility_notes)
            self.assertIn(entry_note, program.identity.compatibility_notes)
            if version < 3:
                self.assertTrue(any('reconstruction' in note for note in program.identity.compatibility_notes))
        composite_note='Composite results and statistical windows use the bounded artifact-defined model; legacy BB v1-v3 is reconstructed and external equivalence is unverified.'
        array_note='Arrays use a bounded primitive-reference heap in v4-v6; historical reconstruction and external equivalence are unverified.'
        self.assertEqual(self.session.compile(source(6, 'plot(close)')).identity.compatibility_notes, (composite_note,technical_note,loop_note,array_note,environment_note,signal_note,partial_note,entry_note,lot_note,group_note))

    def test_legacy_sma_seed_matches_independent_closed_form(self):
        values = (2, -3, 17, 0, 21, -11, 5, 39)
        for version in range(1, 5):
            for length in (1, 2, 3, 7, 20):
                alpha = Fraction(2, length + 1)
                ratio = 1 - alpha
                seed = Fraction(sum(values[:length]), length) if len(values) >= length else None
                expected = []
                for i in range(len(values)):
                    if i + 1 < length:
                        expected.append(None)
                    else:
                        expected.append(ratio**(i-length+1) * seed + alpha * sum(
                            ratio**(i-j) * values[j] for j in range(length, i+1)))
                _, result = self.session.run(source(version, f'plot(ema(close,{length}))'), data=bars(values))
                for actual, target in zip(result.outputs[0].values, expected, strict=True):
                    if target is None:
                        self.assertIsNone(actual)
                    else:
                        self.assertAlmostEqual(actual, float(target), delta=1e-12)

    def test_default_v1_and_correct_historical_parameter_names(self):
        program, result = self.session.run(source(1, 'plot(ema(close,3))', annotation=False), data=bars((10,20,30,40)))
        self.assertEqual(program.identity.language_version, 1)
        call = program.statements[1].arguments[0].expression
        self.assertTrue(call.arguments[0].parameter_id.endswith('.x'))
        self.assertTrue(call.arguments[1].parameter_id.endswith('.y'))
        self.assertEqual(result.outputs[0].values, (None,None,20.0,30.0))
        for version in (3,4,5,6):
            name = 'ema' if version < 5 else 'ta.ema'
            _, result = self.session.run(source(version, f'plot({name}(length=1, source=close))'), data=bars((9,5)))
            self.assertEqual(result.outputs[0].values, (9.0,5.0))

    def test_wrong_era_names_and_unverified_named_calls_are_rejected(self):
        for version in range(1,7):
            wrong = 'ta.ema' if version < 5 else 'ema'
            with self.assertRaises(OpenPineError) as caught:
                self.session.compile(source(version, f'plot({wrong}(close,3))'))
            self.assertEqual((caught.exception.diagnostic.code,caught.exception.diagnostic.language_version), ('SIGNATURE_UNAVAILABLE',version))
        for version in (1,2):
            with self.assertRaises(OpenPineError) as caught:
                self.session.compile(source(version,'plot(ema(x=close,y=3))'))
            self.assertEqual(caught.exception.diagnostic.code,'NAMED_ARGUMENT_UNVERIFIED')

    def test_mixed_version_sessions_do_not_share_state_or_fall_back(self):
        for version in (1,6,2,5,3,4,1):
            name = 'ema' if version < 5 else 'ta.ema'
            text = source(version, f'plot({name}(close,3))')
            _, first = self.session.run(text,data=bars((10,20,30,40)))
            _, other = self.session.run(text,data=bars((100,200,300,400)))
            _, repeated = self.session.run(text,data=bars((10,20,30,40)))
            self.assertEqual(first,repeated)
            self.assertEqual(other.outputs[0].values[-1],300.0 if version < 5 else 312.5)

    def test_artifacts_are_rejected_by_a_different_language_session(self):
        first = self.session.compile(source(1,'plot(close)'))
        second = self.session.compile(source(2,'plot(close)'))
        catalog = self.session.catalog_for(2)
        runtime = Runtime(second.identity,catalog.contracts.values(),catalog.references.values())
        with self.assertRaises(OpenPineError) as caught:
            runtime.admit(first)
        self.assertEqual(caught.exception.diagnostic.code,'IR_IDENTITY')

    def test_references_remain_inventory_and_unresolved_defaults_remain_visible(self):
        self.assertTrue(all(row['review_status']=='reference_only' and row['executable_contract'] is False
                            for reference in self.snapshot.references.values() for row in reference['entries']))
        report = coverage(self.snapshot)
        self.assertEqual(len(report['versions'][4]['unresolved_assertion_refs']),3)
        with self.assertRaises(SpecError) as caught:
            self.snapshot.resolve('assertion.overload.ta.ema.source_length.signature',4)
        self.assertEqual(caught.exception.code,'PROFILE_UNCOVERED')

    def test_registry_hash_and_decision_pin_are_both_required(self):
        root=self.copied_root(); directory=root/self.snapshot.lock['version_review']['path'];path=directory/'v4.json'
        registry=read_json(path)
        fact=next(row for row in registry['assertions'] if row['subject_ref']=='rule.ema')
        fact['value']['seed']='unreviewed_seed';path.write_bytes(encode(registry))
        with self.assertRaises(SpecError): load_snapshot(root)
        self.repin_review(root)
        with self.assertRaises(SpecError) as caught: load_snapshot(root)
        self.assertEqual(caught.exception.code,'SPEC_REVIEW')

    def test_cross_version_scopes_and_catalog_evidence_cannot_be_reassigned(self):
        root=self.copied_root();directory=root/self.snapshot.lock['version_review']['path'];path=directory/'v2.json'
        registry=read_json(path)
        for fact in registry['assertions']:fact['scope']['language_versions']=[1,2]
        path.write_bytes(encode(registry));self.repin_review(root)
        with self.assertRaises(SpecError) as caught:load_snapshot(root)
        self.assertEqual(caught.exception.code,'SPEC_VERSION')
        root=self.copied_root();directory=root/self.snapshot.lock['version_review']['path'];path=directory/'evidence.json'
        evidence=read_json(path);next(row for row in evidence['sources']['sources'] if row['kind']=='pinned_reference_review')['source_record_sha256']='0'*64
        path.write_bytes(encode(evidence));self.repin_review(root)
        with self.assertRaises(SpecError) as caught:load_snapshot(root)
        self.assertEqual(caught.exception.code,'SPEC_PROVENANCE')

    def test_unlisted_review_files_and_wrong_parent_are_rejected(self):
        root=self.copied_root();directory=root/self.snapshot.lock['version_review']['path']
        (directory/'extra.json').write_bytes(b'{}')
        with self.assertRaises(SpecError):load_snapshot(root)
        root=self.copied_root();directory=root/self.snapshot.lock['version_review']['path'];path=directory/'review.json'
        review=read_json(path);review['base_manifest_sha256']='0'*64;path.write_bytes(encode(review));self.repin_review(root)
        with self.assertRaises(SpecError) as caught:load_snapshot(root)
        self.assertEqual(caught.exception.code,'SPEC_PROVENANCE')


if __name__ == '__main__':
    unittest.main()
