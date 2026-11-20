"""Behavioral invariants for the ledger. All science/tool content is synthetic."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skill' / 'scripts' / 'research_state.py'


class StateWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.work = self.base / 'work'
        self.sequence = 0
        self.case = self.write({'case_id': 'SYNTHETIC-UNIT', 'output_contract': {'type': 'object', 'required': ['case_id']}})
        self.cli('init', '--case', self.case, '--work', self.work)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value):
        self.sequence += 1
        path = self.base / ('record-%d.json' % self.sequence)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True,
                                text=True, timeout=20)
        output = json.loads(result.stdout)
        if success:
            self.assertEqual(result.returncode, 0, output)
        else:
            self.assertNotEqual(result.returncode, 0, output)
        return output

    def state(self):
        return json.loads((self.work / 'state.json').read_text(encoding='utf-8'))

    def put(self, kind, records):
        return self.cli('put', '--work', self.work, '--kind', kind, '--file', self.write(records))

    def question(self, id, status='planned', dependencies=None):
        return {'id': id, 'text': 'Synthetic question ' + id, 'status': status,
                'requirement_ids': [], 'acceptance_criteria': 'Check the supplied synthetic source',
                'dependencies': dependencies or []}

    def register(self):
        source = self.base / 'synthetic.txt'
        source.write_text('Synthetic test data, not a paper.\nThe outcome is 12 units at day 3.\n', encoding='utf-8')
        result = self.cli('artifact', '--work', self.work, '--id', 'ART1', '--path', source, '--source-id', 'S1')
        return self.work / result['artifact']['path']

    def evidence_chain(self):
        self.register()
        self.put('questions', self.question('Q1'))
        self.put('sources', {'id': 'S1', 'title': 'Synthetic source', 'artifact_id': 'ART1', 'access_level': 'full_text'})
        self.put('evidence', {'id': 'E1', 'source_id': 'S1', 'question_ids': ['Q1'], 'artifact_id': 'ART1',
                              'quote': 'The outcome is 12 units at day 3.', 'locator': {'line_start': 2, 'line_end': 2}})
        self.put('claims', {'id': 'C1', 'question_ids': ['Q1'], 'text': 'The outcome is 12 units at day 3.',
                            'kind': 'fact', 'supporting_evidence_ids': ['E1'], 'opposing_evidence_ids': [], 'review_status': 'supported'})
        self.put('reviews', {'id': 'V1', 'claim_id': 'C1', 'verdict': 'supported', 'reviewer': 'synthetic-unit-fixture',
                             'evidence_ids': ['E1'], 'rationale': 'Synthetic test fixture only.'})

    def test_init_keeps_contract_and_does_not_invent_formal_output(self):
        state = self.state()
        self.assertEqual(state['case_id'], 'SYNTHETIC-UNIT')
        self.assertEqual(state['input_contract']['required'], ['case_id'])
        self.assertEqual(json.loads((self.work / 'progress.json').read_text())['status'], 'partial')
        self.assertFalse((self.work / 'result.json').exists())

    def test_reinitialization_cannot_erase_history(self):
        self.put('questions', self.question('Q1'))
        self.cli('init', '--case', self.case, '--work', self.work, success=False)
        self.assertEqual(len(self.state()['questions']), 1)

    def test_hard_dependency_controls_ready_frontier(self):
        q1 = self.question('Q1')
        q2 = self.question('Q2', dependencies=[{'question_id': 'Q1', 'type': 'hard', 'required_artifacts': []}])
        self.put('questions', [q1, q2])
        self.assertEqual(self.cli('ready', '--work', self.work)['ready'], ['Q1'])
        q1['status'] = 'completed'
        self.put('questions', q1)
        self.assertEqual(self.cli('ready', '--work', self.work)['ready'], ['Q2'])

    def test_missing_dependency_is_rejected(self):
        self.put('questions', self.question('Q2', dependencies=[{'question_id': 'MISSING', 'type': 'hard'}]))
        self.cli('ready', '--work', self.work, success=False)

    def test_dependency_cycle_is_rejected(self):
        self.put('questions', [self.question('Q1', dependencies=[{'question_id': 'Q2', 'type': 'hard'}]),
                               self.question('Q2', dependencies=[{'question_id': 'Q1', 'type': 'hard'}])])
        self.cli('ready', '--work', self.work, success=False)

    def test_soft_dependency_does_not_block(self):
        self.put('questions', [self.question('Q1'), self.question('Q2', dependencies=[{'question_id': 'Q1', 'type': 'soft'}])])
        self.assertEqual(set(self.cli('ready', '--work', self.work)['ready']), {'Q1', 'Q2'})

    def test_missing_required_artifact_blocks_even_completed_predecessor(self):
        self.put('questions', [self.question('Q1', 'completed'), self.question('Q2', dependencies=[
            {'question_id': 'Q1', 'type': 'hard', 'required_artifacts': ['MISSING']}])])
        self.assertEqual(self.cli('ready', '--work', self.work)['ready'], [])

    def test_changed_artifact_cannot_reuse_id(self):
        self.register()
        changed = self.base / 'changed.txt'
        changed.write_text('Different source bytes', encoding='utf-8')
        self.cli('artifact', '--work', self.work, '--id', 'ART1', '--path', changed, success=False)

    def test_cache_hit_requires_intact_artifact(self):
        retained = self.register()
        cache = self.write({'status': 'success', 'artifact_ids': ['ART1'], 'value': 'synthetic'})
        self.cli('cache-put', '--work', self.work, '--key', 'test-key', '--file', cache)
        self.assertTrue(self.cli('cache-get', '--work', self.work, '--key', 'test-key')['hit'])
        retained.write_text('tampered', encoding='utf-8')
        self.assertFalse(self.cli('cache-get', '--work', self.work, '--key', 'test-key')['hit'])

    def test_failed_result_cannot_enter_cache(self):
        self.register()
        cache = self.write({'status': 'failed', 'artifact_ids': ['ART1']})
        self.cli('cache-put', '--work', self.work, '--key', 'test-key', '--file', cache, success=False)

    def test_query_key_preserves_scientific_sign_and_scope(self):
        params1 = self.write({'query': '1e-3 dose', 'page': 1})
        params2 = self.write({'query': '1e3 dose', 'page': 1})
        scope1 = self.write({'corpus_version': 'v1'})
        scope2 = self.write({'corpus_version': 'v2'})
        def key(params, scope):
            return self.cli('cache-key', '--tool', 'search', '--parameters', params, '--scope', scope)['key']
        self.assertNotEqual(key(params1, scope1), key(params2, scope1))
        self.assertNotEqual(key(params1, scope1), key(params1, scope2))

    def test_resume_detects_original_input_change(self):
        original = self.state()['metadata']['budget']['started_at']
        self.case.write_text('{"case_id":"CHANGED"}', encoding='utf-8')
        self.cli('resume', '--work', self.work, success=False)
        self.assertEqual(self.state()['metadata']['budget']['started_at'], original)

    def test_resume_invalidates_dependent_evidence(self):
        self.evidence_chain()
        artifact = self.work / self.state()['artifacts']['ART1']['path']
        artifact.write_text('tampered', encoding='utf-8')
        self.cli('resume', '--work', self.work, success=False)
        self.assertEqual(self.state()['evidence'][0].get('validity'), 'invalid')
        self.assertEqual(self.state()['claims'][0].get('validity'), 'invalid')

    def test_claim_edit_invalidates_previous_review(self):
        self.evidence_chain()
        claim = self.state()['claims'][0]
        claim['text'] = 'The outcome is 120 units.'
        self.put('claims', claim)
        self.assertEqual(self.state()['reviews'][0].get('validity'), 'invalid')

    def test_evidence_edit_invalidates_previous_review(self):
        self.evidence_chain()
        evidence = self.state()['evidence'][0]
        evidence['quote'] = 'Changed underlying evidence.'
        self.put('evidence', evidence)
        self.assertEqual(self.state()['reviews'][0].get('validity'), 'invalid')

    def test_event_history_and_credential_redaction(self):
        event = {'id': 'T1', 'action_id': 'A1', 'tool': 'synthetic-unit-tool', 'status': 'failed', 'attempt': 1,
                 'parameters': {'query': 'test', 'api_key': 'FAKE_TEST_SECRET'},
                 'input_artifacts': [], 'output_artifacts': [], 'error': 'Synthetic failure'}
        self.cli('event', '--work', self.work, '--file', self.write(event))
        event.update(id='T2', status='success', attempt=2, error=None)
        self.cli('event', '--work', self.work, '--file', self.write(event))
        state = self.state()
        self.assertEqual(len(state['metadata']['events']), 2)
        self.assertEqual(state['metadata']['events'][0]['status'], 'failed')
        self.assertNotIn('FAKE_TEST_SECRET', (self.work / 'state.json').read_text())
        self.assertNotIn('FAKE_TEST_SECRET', (self.work / 'events.jsonl').read_text())

    def test_observed_request_count_cannot_move_backwards(self):
        self.cli('budget', '--work', self.work, '--observed-requests', '8')
        self.cli('budget', '--work', self.work, '--observed-requests', '7', success=False)
        self.assertFalse(self.state()['metadata']['budget']['request_count_is_global_total'])

    def test_checkpoint_fallback_does_not_reset_budget(self):
        started = self.state()['metadata']['budget']['started_at']
        self.cli('budget', '--work', self.work, '--observed-requests', '5')
        self.cli('checkpoint', '--work', self.work)
        self.cli('budget', '--work', self.work, '--observed-requests', '12')
        (self.work / 'state.json').write_text('{broken', encoding='utf-8')
        result = subprocess.run([sys.executable, str(SCRIPT), 'resume', '--work', str(self.work)],
                                capture_output=True, text=True, timeout=20)
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, payload)
        observed = self.state()['metadata']['budget']['observed_requests']
        self.assertIsNotNone(observed, payload)
        self.assertGreaterEqual(observed, 12)
        self.assertEqual(self.state()['metadata']['budget']['started_at'], started)
        self.assertTrue(payload['post_checkpoint_state_may_be_missing'])
        archived = payload['recovery_archive']['files']
        self.assertTrue(any(item['original'] == 'state.json' for item in archived), archived)
        for item in archived:
            if item['original'] == 'state.json':
                self.assertEqual((self.work / item['path']).read_text(), '{broken')

    def test_missing_budget_receipt_blocks_new_actions(self):
        self.put('questions', self.question('Q1'))
        (self.work / 'budget_receipt.json').unlink()
        ready = self.cli('ready', '--work', self.work)
        self.assertEqual(ready['ready'], [])

    def test_damaged_budget_receipt_blocks_new_actions(self):
        self.put('questions', self.question('Q1'))
        (self.work / 'budget_receipt.json').write_text('{broken', encoding='utf-8')
        ready = self.cli('ready', '--work', self.work)
        self.assertEqual(ready['ready'], [])

    def test_question_change_invalidates_old_scientific_context(self):
        self.evidence_chain()
        question = self.state()['questions'][0]
        question['time_range'] = 'different follow-up period'
        self.put('questions', question)
        state = self.state()
        self.assertEqual(state['reviews'][0].get('validity'), 'invalid')
        self.assertNotEqual(state['claims'][0].get('review_status'), 'supported')

    def test_action_parameter_change_invalidates_previous_support(self):
        self.evidence_chain()
        action = {'id': 'A1', 'question_ids': ['Q1'], 'tool': 'synthetic-read',
                  'parameters': {'source_version': 'v1'}, 'artifact_ids': ['ART1'],
                  'status': 'completed'}
        self.put('actions', action)
        action['parameters'] = {'source_version': 'v2'}
        self.put('actions', action)
        self.assertEqual(self.state()['evidence'][0].get('validity'), 'invalid')
        self.assertNotEqual(self.state()['claims'][0].get('review_status'), 'supported')

    def test_source_revision_invalidates_previous_extraction(self):
        self.evidence_chain()
        source = self.state()['sources'][0]
        source['version'] = 'new revision'
        self.put('sources', source)
        self.assertEqual(self.state()['evidence'][0].get('validity'), 'invalid')
        self.assertEqual(self.state()['reviews'][0].get('validity'), 'invalid')

    def validate_state(self):
        proc = subprocess.run([sys.executable, str(SCRIPT.parent / 'validate.py'), 'state', '--work', str(self.work)],
                              capture_output=True, text=True, timeout=20)
        return proc.returncode, json.loads(proc.stdout)

    def planned_chain(self):
        self.evidence_chain()
        self.put('requirements', {'id': 'R1', 'text': 'State the supplied synthetic outcome.', 'answerability': 'answerable'})
        question = self.state()['questions'][0]
        question['requirement_ids'] = ['R1']
        self.put('questions', question)
        # The plan change correctly invalidates previous work: explicitly rebuild/review it.
        self.evidence_chain()
        question = self.state()['questions'][0]
        question['requirement_ids'] = ['R1']
        # Set the final fixture in one write: this is test setup, not a production update path.
        state = self.state()
        state['questions'] = [question]
        for kind in ['questions', 'sources', 'evidence', 'claims', 'reviews']:
            for record in state[kind]:
                record.pop('validity', None)
                record.pop('invalid_reasons', None)
        state['claims'][0]['review_status'] = 'supported'
        (self.work / 'state.json').write_text(json.dumps(state), encoding='utf-8')

    def test_located_quote_does_not_certify_semantic_truth(self):
        self.planned_chain()
        code, report = self.validate_state()
        self.assertEqual(code, 0, report)
        self.assertTrue(report['semantic_review_required'])
        self.assertIsNone(report['metrics']['semantic_success_count'])

    def test_false_quote_is_rejected(self):
        self.planned_chain()
        state = self.state()
        state['evidence'][0]['quote'] = 'The outcome is 1200 units.'
        (self.work / 'state.json').write_text(json.dumps(state), encoding='utf-8')
        code, report = self.validate_state()
        self.assertNotEqual(code, 0, report)

    def test_wrong_source_line_is_rejected(self):
        self.planned_chain()
        state = self.state()
        state['evidence'][0]['locator'] = {'line_start': 99, 'line_end': 99}
        (self.work / 'state.json').write_text(json.dumps(state), encoding='utf-8')
        code, report = self.validate_state()
        self.assertNotEqual(code, 0, report)

    def test_broken_source_reference_is_rejected(self):
        self.planned_chain()
        state = self.state()
        state['evidence'][0]['source_id'] = 'MISSING'
        (self.work / 'state.json').write_text(json.dumps(state), encoding='utf-8')
        code, report = self.validate_state()
        self.assertNotEqual(code, 0, report)

    def test_unknown_does_not_count_as_supported_answer(self):
        self.planned_chain()
        state = self.state()
        state['claims'][0].update(kind='unknown', text='Not established.', supporting_evidence_ids=[], review_status='unreviewed')
        state['reviews'] = []
        (self.work / 'state.json').write_text(json.dumps(state), encoding='utf-8')
        code, report = self.validate_state()
        self.assertEqual(code, 0, report)
        self.assertEqual(report['metrics']['requirements_linked_to_located_claims_with_recorded_supported_review'], 0)

    def test_stale_review_is_not_counted(self):
        self.planned_chain()
        state = self.state()
        state['reviews'][0]['validity'] = 'invalid'
        state['claims'][0]['review_status'] = 'unreviewed'
        (self.work / 'state.json').write_text(json.dumps(state), encoding='utf-8')
        _, report = self.validate_state()
        self.assertEqual(report['metrics']['claims_with_located_support_and_recorded_supported_review'], 0)


if __name__ == '__main__':
    unittest.main()
