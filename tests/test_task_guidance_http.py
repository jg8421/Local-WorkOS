"""Missing work information is a scoped conversation, not a failed task.

Real loopback HTTP, temporary stores and folders, and mocked model adapters only.
No provider, production record, or private project folder is contacted.
"""
import json
import time
import unittest

from tests import test_ai_experience_http as experience_fixture


class TaskGuidanceHttpTests(unittest.TestCase):
    setUp = experience_fixture.AiExperienceHttpTests.setUp
    stop_server = experience_fixture.AiExperienceHttpTests.stop_server
    request = experience_fixture.AiExperienceHttpTests.request
    post = experience_fixture.AiExperienceHttpTests.post
    ask_body = experience_fixture.AiExperienceHttpTests.ask_body
    conversation = experience_fixture.AiExperienceHttpTests.conversation

    def guidance(self, status, result, *, purpose=None):
        self.assertEqual(status, 200, result)
        self.assertEqual(result.get('status'), 'needs_input', result)
        self.assertTrue(result.get('message'), result)
        self.assertNotIn('error', result)
        questions = result.get('questions', [])
        self.assertTrue(1 <= len(questions) <= 3, result)
        self.assertEqual(len({item['id'] for item in questions}), len(questions))
        self.assertTrue(all(item.get('label') for item in questions), result)
        if purpose:
            self.assertEqual(result.get('purpose'), purpose)
        return result

    def no_generated_work(self):
        self.app.local_chat.assert_not_called()
        self.app.dsh_answer.assert_not_called()
        self.assertEqual(self.store.list('deliverables'), [])
        self.assertEqual(self.app.artifacts.status('personal', self.project)['archives'], [])

    def workflow_body(self, **changes):
        return {'workflow_key': 'brief', 'message': 'Explain the selected company evidence briefly',
                'project_id': self.project['id'], 'document_ids': [self.doc['id']],
                'mode': 'deepseek', 'model_id': 'deepseek-v4.1-flash', 'quality_mode': 'fast',
                'request_id': 'synthetic-guidance-workflow', **changes}

    def test_missing_research_materials_asks_before_creating_a_result(self):
        status, result = self.post('/api/workflows/run', self.workflow_body(document_ids=[]))
        self.guidance(status, result, purpose='workflow')
        self.no_generated_work()

    def test_compare_with_only_one_version_asks_for_another_explicit_source(self):
        status, result = self.post('/api/workflows/run', self.workflow_body(workflow_key='compare'))
        self.guidance(status, result, purpose='workflow')
        self.no_generated_work()
        self.assertEqual(len(self.store.list('documents')), 2)

    def test_project_update_without_records_or_selected_materials_asks_for_evidence(self):
        status, result = self.post('/api/workflows/run', self.workflow_body(workflow_key='weekly', document_ids=[]))
        self.guidance(status, result, purpose='workflow')
        self.no_generated_work()

    def test_plain_moic_irr_request_uses_return_method_and_asks_only_for_missing_conditions(self):
        self.app.local_chat.side_effect=None
        self.app.local_chat.return_value=(json.dumps({'assumptions':{},'clarifications':[]}), 'Synthetic selected model')
        status, result = self.post('/api/model/parse-assumptions', {'text': 'Calculate synthetic MOIC and IRR.',
            'mode': 'deepseek', 'model_id': 'deepseek-v4.1-flash', 'project_id': self.project['id']})
        self.guidance(status, result, purpose='valuation')
        self.assertNotIn('irr', result)
        self.assertNotIn('moic', result)
        self.assertEqual(result['method'],'investor_return')
        self.assertEqual(len(result['questions']),1)
        self.app.local_chat.assert_called_once()
        self.assertEqual(self.app.local_chat.call_args.args[1],'deepseek-v4.1-flash')
        self.app.dsh_answer.assert_not_called()
        self.assertEqual(self.store.list('deliverables'),[])
        self.assertEqual(self.app.artifacts.status('personal',self.project)['archives'],[])

    def test_empty_ask_actions_and_meeting_transcript_are_questions(self):
        cases = [('/api/ask', self.ask_body(question=''), 'ask'),
                 ('/api/agent', {'message': '', 'project_id': self.project['id'],
                                'document_ids': [], 'mode': 'deepseek'}, 'actions'),
                 ('/api/meeting-draft', {'transcript': '', 'project_id': self.project['id'],
                                        'provider': 'deepseek'}, 'meeting')]
        for path, body, purpose in cases:
            with self.subTest(path=path):
                status, result = self.post(path, body)
                self.guidance(status, result, purpose=purpose)
        self.no_generated_work()
        self.assertEqual(self.store.list('meetings'), [])

    def test_incomplete_valuation_preserves_supplied_numbers_as_known_conditions(self):
        assumptions = {'net_income': 100, 'pe_multiple': 12}
        status, result = self.post('/api/model/valuation', {'method': 'net_income', 'assumptions': assumptions})
        self.guidance(status, result, purpose='valuation')
        self.assertEqual(result['method'], 'net_income')
        self.assertEqual(result['assumptions']['net_income'], 100)
        self.assertEqual(result['assumptions']['pe_multiple'], 12)
        self.assertTrue(result.get('known_conditions'), result)
        self.assertNotIn('equity_value', result)
        self.no_generated_work()

    def test_valuation_followup_recovers_extracted_conditions_from_the_pending_conversation(self):
        partial = {'net_income': 100, 'pe_multiple': 12}
        complete = {**partial, 'currency': 'CNY', 'unit': 'million', 'period': 'FY2025A'}
        self.app.dsh_answer.side_effect = [json.dumps({'assumptions': partial, 'clarifications': []}),
                                           json.dumps({'assumptions': complete, 'clarifications': []})]
        body = {'method': 'net_income', 'text': 'Synthetic net income100 and PE12; other conditions unknown.',
                'mode': 'dsh', 'model_id': 'gpt-6-luna', 'project_id': self.project['id']}
        status, first = self.post('/api/model/parse-assumptions', body)
        self.guidance(status, first, purpose='valuation')
        conversation_id = first['conversation_id']
        self.assertEqual(self.conversation(conversation_id)['turns'][0]['status'], 'needs_input')
        status, second = self.post('/api/model/parse-assumptions', {**body,
            'text': 'The amounts are CNY million and the period is FY2025A.', 'conversation_id': conversation_id})
        self.assertEqual(status, 200, second)
        self.assertNotEqual(second.get('status'), 'needs_input', second)
        self.assertEqual(second['assumptions'], complete)
        prompt = self.app.dsh_answer.call_args.args[0]
        self.assertIn('"net_income": 100', prompt)
        self.assertIn('"pe_multiple": 12', prompt)
        self.assertEqual(second['conversation_id'], conversation_id)
        self.assertEqual(self.conversation(conversation_id)['turns'][-1]['status'], 'completed')
        status, rejected = self.post('/api/model/parse-assumptions', {**body,
            'method': 'dcf', 'conversation_id': conversation_id})
        self.assertEqual(status, 400, rejected)
        self.assertEqual(self.app.dsh_answer.call_count, 2)
        self.app.local_chat.assert_not_called()
        self.assertEqual(self.store.list('deliverables'), [])
        self.assertEqual(self.app.artifacts.status('personal', self.project)['archives'], [])

    def test_clear_numeric_strings_and_multiple_suffix_calculate_without_model(self):
        body = {'method': 'net_income', 'assumptions': {'currency': 'CNY', 'unit': 'million',
            'period': 'FY2025A', 'net_income': '100', 'pe_multiple': '12x'}}
        status, result = self.post('/api/model/valuation', body)
        self.assertEqual(status, 200, result)
        self.assertNotEqual(result.get('status'), 'needs_input', result)
        self.assertEqual(result['equity_value'], 1200)
        self.no_generated_work()

    def test_inconsistent_dcf_rates_need_confirmation_without_automatically_changing_values(self):
        assumptions = {'currency': 'CNY', 'unit': 'million', 'valuation_date': '2026-12-31',
            'wacc': '3%', 'discount_timing': 'year_end', 'terminal_method': 'perpetuity',
            'terminal_growth': '4%', 'net_debt': 0, 'minority_interest': 0, 'tax_rate': '25%',
            'forecasts': [{'year': 'FY2027E', 'ebit': 50, 'da': 5, 'capex': 5, 'delta_nwc': 0}]}
        status, result = self.post('/api/model/valuation', {'method': 'dcf', 'assumptions': assumptions})
        self.guidance(status, result, purpose='valuation')
        self.assertEqual(result['assumptions']['wacc'], .03)
        self.assertEqual(result['assumptions']['terminal_growth'], .04)
        self.assertEqual(result['assumptions']['tax_rate'], .25)
        self.assertNotIn('enterprise_value', result)
        self.no_generated_work()

    def test_unknown_financial_input_needs_explicit_mapping_instead_of_silent_omission(self):
        assumptions = {'currency': 'CNY', 'unit': 'million', 'period': 'FY2025A',
                       'net_income': 100, 'pe_multiple': 12, 'investment_horizon': 3}
        status, result = self.post('/api/model/valuation', {'method': 'net_income', 'assumptions': assumptions})
        self.guidance(status, result, purpose='valuation')
        self.assertNotIn('equity_value', result)
        self.assertEqual(result['assumptions']['net_income'], 100)
        self.assertTrue(any('investment_horizon' in str(question) for question in result['questions']), result)
        self.no_generated_work()

    def test_foreign_or_memory_sources_remain_hard_errors_even_when_task_is_empty(self):
        foreign = self.store.create('documents', {'title': 'Synthetic foreign source',
            'project_id': self.other['id'], 'content': 'Foreign project scope.'})
        memory = self.store.create('documents', {'title': 'Synthetic personal memory',
            'kind': 'memory', 'content': 'Synthetic memory must not leave local scope.'})
        for source in (foreign, memory):
            with self.subTest(source=source['kind']):
                status, result = self.post('/api/ask', self.ask_body(question='', document_ids=[source['id']]))
                self.assertEqual(status, 400, result)
                self.assertIn('error', result)
                self.assertNotEqual(result.get('status'), 'needs_input', result)
        self.no_generated_work()

    def test_auth_and_malformed_selection_do_not_become_questions(self):
        status, result = self.request('POST', '/api/ask', csrf=False)
        self.assertEqual(status, 403, result)
        self.assertIn('error', result)
        for changes in ({'document_ids': 'not-a-list'}, {'project_id': {'invalid': True}},
                        {'mode': 'dsh', 'model_id': 'not-a-reviewed-model'}):
            with self.subTest(changes=changes):
                status, result = self.post('/api/ask', self.ask_body(question='', **changes))
                self.assertEqual(status, 400, result)
                self.assertIn('error', result)
                self.assertNotEqual(result.get('status'), 'needs_input', result)
        self.no_generated_work()

    def test_provider_malformed_json_is_a_retryable_failure_not_missing_user_information(self):
        self.app.dsh_answer.side_effect = None
        self.app.dsh_answer.return_value = 'not a JSON response'
        status, result = self.post('/api/model/parse-assumptions', {'method': 'net_income',
            'text': 'Synthetic CNY million FY2025A net income 100 and PE 12',
            'mode': 'dsh', 'model_id': 'gpt-6-luna', 'project_id': self.project['id']})
        self.assertEqual(status, 400, result)
        self.assertIn('error', result)
        self.assertNotEqual(result.get('status'), 'needs_input', result)
        self.app.dsh_answer.assert_called_once()
        self.app.local_chat.assert_not_called()
        self.assertEqual(self.store.list('deliverables'), [])

    def test_assumption_extraction_without_model_choice_uses_default_deepseek_flash(self):
        assumptions = {'currency': 'CNY', 'unit': 'million', 'period': 'FY2025A',
                       'net_income': 100, 'pe_multiple': 12}
        self.app.local_chat.side_effect = None
        self.app.local_chat.return_value = (json.dumps({'assumptions': assumptions, 'clarifications': []}),
                                           'Synthetic default model')
        status, result = self.post('/api/model/parse-assumptions', {'method': 'net_income',
            'text': 'Synthetic CNY million FY2025A net income100 PE12.', 'project_id': self.project['id']})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['assumptions'], assumptions)
        self.assertNotEqual(result.get('status'), 'needs_input', result)
        self.app.local_chat.assert_called_once()
        self.assertEqual(self.app.local_chat.call_args.args[1], 'deepseek-v4.1-flash')
        self.app.dsh_answer.assert_not_called()
        self.assertEqual(self.store.list('deliverables'), [])

    def test_extracted_unsupported_assumption_remains_pending_without_silently_losing_it(self):
        assumptions = {'currency': 'CNY', 'unit': 'million', 'period': 'FY2025A',
                       'net_income': 100, 'pe_multiple': 12, 'investment_horizon': 3}
        self.app.dsh_answer.side_effect = None
        self.app.dsh_answer.return_value = json.dumps({'assumptions': assumptions, 'clarifications': []})
        status, result = self.post('/api/model/parse-assumptions', {'method': 'net_income',
            'text': 'Synthetic complete assumptions with three-year investment horizon.',
            'mode': 'dsh', 'model_id': 'gpt-6-luna', 'project_id': self.project['id']})
        self.guidance(status, result, purpose='valuation')
        self.assertEqual(result['assumptions']['investment_horizon'], 3)
        self.assertEqual(result['assumptions']['net_income'], 100)
        self.assertNotIn('equity_value', result)
        self.assertTrue(any('investment_horizon' in str(item) for item in result['questions']), result)
        self.app.dsh_answer.assert_called_once()
        self.app.local_chat.assert_not_called()
        self.assertEqual(self.store.list('deliverables'), [])

    def test_incomplete_background_work_returns_guidance_without_a_durable_job(self):
        status, result = self.post('/api/workflows/jobs', self.workflow_body(document_ids=[]))
        self.guidance(status, result, purpose='workflow')
        self.assertNotIn('job', result)
        if self.app._jobs is not None:
            self.assertEqual(self.app._jobs.list('personal'), [])
        self.no_generated_work()

    def test_background_email_model_clarification_finishes_waiting_without_empty_saved_body(self):
        guidance = {'status': 'needs_input', 'message': 'Who receives this email?',
                    'questions': [{'id': 'recipient', 'label': 'Who receives this email?'}]}
        self.app.local_chat.side_effect = None
        self.app.local_chat.return_value = (json.dumps(guidance), 'Synthetic selected model')
        body = self.workflow_body(workflow_key='email', document_ids=[],
            message='Draft the synthetic request email; ask for a necessary detail if unclear.',
            request_id='synthetic-email-runtime-clarification')
        status, accepted = self.post('/api/workflows/jobs', body)
        self.assertEqual(status, 202, accepted)
        job_id = accepted['job']['id']
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            status, response = self.request('GET', '/api/workflows/jobs/' + job_id)
            self.assertEqual(status, 200, response)
            job = response['job']
            if job['status'] not in ('queued', 'running'):
                break
            time.sleep(.01)
        else:
            self.fail('Synthetic clarification job did not finish within four seconds.')
        self.assertEqual(job['status'], 'needs_input', job)
        self.assertEqual(job['result']['status'], 'needs_input', job)
        self.assertEqual(job['result']['questions'][0]['id'], 'recipient')
        self.assertNotIn('deliverable_id', job['result'])
        self.assertNotIn('archive', job['result'])
        self.assertEqual(self.store.list('deliverables'), [])
        self.assertEqual(self.app.artifacts.status('personal', self.project)['archives'], [])
        self.app.local_chat.assert_called_once()
        self.app.dsh_answer.assert_not_called()

    def test_semantic_plan_continuation_keeps_original_limits_and_exact_selected_model(self):
        first_message = 'Handle this synthetic material; no more than three rows and no other sources.'
        clarification = {'route': 'research', 'workflow_key': '', 'status': 'needs_input',
            'message': 'Choose the useful result before starting.', 'questions': [
                {'id': 'purpose', 'label': 'Would a brief or a comparison be useful?', 'hint': 'Choose one.'}]}
        completed = {'route': 'research', 'workflow_key': 'brief',
                     'message': 'Synthetic classified task; do not replace the user request.'}
        self.app.dsh_answer.side_effect = [json.dumps(clarification), json.dumps(completed)]
        body = {'message': first_message, 'project_id': self.project['id'],
                'document_ids': [self.doc['id']], 'mode': 'dsh', 'model_id': 'gpt-6-sol'}
        status, first = self.post('/api/workflows/plan', body)
        self.guidance(status, first, purpose='plan')
        conversation_id = first['conversation_id']
        conversation = self.conversation(conversation_id)
        self.assertEqual(conversation['purpose'], 'plan')
        self.assertEqual(conversation['turns'][0]['status'], 'needs_input')
        self.assertEqual(conversation['turns'][0]['current_artifact'], {})
        answer = 'Just a brief summary.'
        status, second = self.post('/api/workflows/plan', {**body, 'message': answer,
                                                        'conversation_id': conversation_id})
        self.assertEqual(status, 200, second)
        self.assertEqual(second['workflow_key'], 'brief')
        self.assertEqual(second['route'], 'research')
        self.assertEqual(second['question'], answer)
        self.assertEqual(second['conversation_id'], conversation_id)
        prompt = self.app.dsh_answer.call_args.args[0]
        self.assertIn(first_message, prompt)
        self.assertIn(answer, prompt)
        self.assertNotIn(self.doc['content'], prompt)  # Classifier needs task/scope metadata, not business evidence.
        self.assertEqual([call.args[1] for call in self.app.dsh_answer.call_args_list], ['gpt-6-sol'] * 2)
        for changes in ({'project_id': self.other['id']}, {'document_ids': [self.doc2['id']]}):
            with self.subTest(changes=changes):
                status, rejected = self.post('/api/workflows/plan', {**body, 'message': answer,
                    'conversation_id': conversation_id, **changes})
                self.assertEqual(status, 400, rejected)
                self.assertIn('error', rejected)
        self.assertEqual(self.request('GET', '/api/conversations/' + conversation_id, workspace='demo')[0], 404)
        self.assertEqual(self.app.dsh_answer.call_count, 2)
        self.app.local_chat.assert_not_called()
        self.assertEqual(self.store.list('deliverables'), [])

    def test_classifier_cannot_invent_a_tool_route_or_change_the_original_request(self):
        self.app.local_chat.side_effect = None
        self.app.local_chat.return_value = (json.dumps({'route': 'shell', 'workflow_key': 'anything',
            'message': 'Run an arbitrary command.'}), 'Synthetic classifier')
        status, result = self.post('/api/workflows/plan', {'message': 'Handle a synthetic task.',
            'mode': 'deepseek', 'model_id': 'deepseek-v4.1-flash', 'project_id': self.project['id']})
        self.assertEqual(status, 400, result)
        self.assertIn('error', result)
        self.assertNotEqual(result.get('status'), 'needs_input', result)
        self.app.local_chat.assert_called_once()
        self.app.dsh_answer.assert_not_called()
        self.assertEqual(self.store.list('deliverables'), [])

    def test_public_usage_guidance_contains_only_the_synthetic_example(self):
        status, result = self.request('GET', '/api/guidance')
        self.assertEqual(status, 200, result)
        self.assertTrue(result.get('title'))
        self.assertIn('Antigravity（合成示例）', result['content'])
        self.assertIn('单层债务', result['content'])
        self.assertNotIn(self.temp.name, result['content'])
        self.no_generated_work()


if __name__ == '__main__':
    unittest.main()
