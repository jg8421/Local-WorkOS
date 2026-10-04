"""Actual HTTP return dialogue, source scope and cancellation; synthetic data."""
import json
import threading
from unittest.mock import patch
import unittest
from tests import test_jobs_http as fixture
from tests.test_investor_returns import inputs
from workos.investor_returns import calculate
from workos.cancellation import check_cancelled
from workos.return_excel import ExcelUnavailable


class ReturnFlowHttpTests(unittest.TestCase):
    setUp=fixture.AsyncJobsHttpTests.setUp
    tearDown=fixture.AsyncJobsHttpTests.tearDown
    request=fixture.AsyncJobsHttpTests.request
    normal_model=fixture.AsyncJobsHttpTests.normal_model

    def body(self, **changes):
        return {'method':'investor_return','text':'Synthetic投资MOC和IRR','mode':'deepseek',
                'project_id':self.projects['personal']['id'],'document_ids':[self.docs['personal']['id']],
                'use_project_sources':True,**changes}

    def set_response(self,a):
        self.model.return_value=(json.dumps({'assumptions':a,'clarifications':['Optional fees?']},ensure_ascii=False),'Synthetic model')
        self.model.side_effect=None

    def test_complete_inputs_call_excel_once_and_persist_result_with_source_context(self):
        self.set_response(inputs())
        computed={**calculate(inputs()),'excel_verified':True,'answer':'MOC 1.80×；IRR 15.8%。'}
        with patch('workos.return_excel.calculate_excel',return_value=computed) as excel:
            code,r=self.request('POST','/api/model/parse-assumptions',self.body())
        self.assertEqual(code,200,r);excel.assert_called_once_with(inputs())
        self.assertAlmostEqual(r['calculation']['moic'],1.8);self.assertEqual(r['clarifications'],[])
        self.assertIn('Selected synthetic evidence',self.model.call_args.args[2])
        chat=self.app.conversations.get('personal',r['conversation_id'])
        self.assertEqual(chat['source_ids'],[self.docs['personal']['id']])
        self.assertTrue(chat['turns'][0]['output_snapshot']['calculation']['excel_verified'])

    def test_missing_basis_one_question_then_latest_supplement_with_same_context(self):
        a=inputs();del a['entry_valuation_basis'];self.set_response(a)
        with patch('workos.return_excel.calculate_excel') as excel:
            code,r=self.request('POST','/api/model/parse-assumptions',self.body())
            self.assertEqual(code,200,r);self.assertEqual(len(r['questions']),1);excel.assert_not_called()
            self.set_response(inputs());excel.return_value={**calculate(inputs()),'answer':'MOC 1.80×；IRR 15.8%。','excel_verified':True}
            code,second=self.request('POST','/api/model/parse-assumptions',self.body(text='投后，保持美元口径',conversation_id=r['conversation_id']))
        self.assertEqual(code,200,second);self.assertIn('已有用户假设',self.model.call_args.args[2])
        self.assertEqual(second['conversation_id'],r['conversation_id']);self.assertIn('calculation',second)

    def test_literal_post_money_and_explicit_scenario_override_do_not_repeat_old_source_questions(self):
        a=inputs(entry_valuation_basis=None,source_conflicts=['Old source had a different entry valuation'])
        self.set_response(a)
        with patch('workos.return_excel.calculate_excel',return_value={**calculate(inputs()),'answer':'Synthetic native Excel result'}) as excel:
            code,r=self.request('POST','/api/model/parse-assumptions',self.body(text='本轮假设优先，进入是投后，直接计算'))
        self.assertEqual(code,200,r);self.assertNotEqual(r.get('status'),'needs_input');self.assertEqual(r['assumptions']['entry_valuation_basis'],'post_money')
        self.assertEqual(r['assumptions']['source_conflicts'],[]);excel.assert_called_once();self.assertTrue(r['source_notes'])

    def test_extractor_computed_proceeds_cannot_freeze_later_profit_or_dilution_edits(self):
        a=inputs(entry_ownership=.05,exit_ownership=.04,exit_equity_value=1800,exit_proceeds=72)
        self.set_response(a)
        with patch('workos.return_excel.calculate_excel',side_effect=lambda values:{**calculate(values),'answer':'Synthetic Excel result'}):
            code,first=self.request('POST','/api/model/parse-assumptions',self.body(text='按退出净利润及P/E测MOC和IRR'))
            self.assertEqual(code,200,first)
            for key in ('entry_ownership','exit_ownership','exit_equity_value','exit_proceeds'):self.assertNotIn(key,first['assumptions'])
            self.set_response({**a,'exit_net_income':240})
            code,second=self.request('POST','/api/model/parse-assumptions',self.body(text='退出净利润改成240，其他沿用',conversation_id=first['conversation_id']))
        self.assertEqual(code,200,second);self.assertAlmostEqual(second['calculation']['moic'],3.6)

    def test_readonly_optout_and_foreign_or_memory_sources_fail_before_model(self):
        self.set_response(inputs());self.model.reset_mock()
        with patch('workos.return_excel.calculate_excel',return_value={**calculate(inputs()),'answer':'Synthetic result'}):
            code,r=self.request('POST','/api/model/parse-assumptions',self.body(document_ids=[],use_project_sources=False))
        self.assertEqual(code,200,r);self.assertEqual(r['sources'],[])
        self.assertNotIn('Selected synthetic evidence',self.model.call_args.args[2])
        for doc in (self.docs['demo'],self.app.stores['personal'].create('documents',{'title':'Synthetic private memory','content':'MEMORY NEVER SEND','kind':'memory'})):
            self.model.reset_mock();code,r=self.request('POST','/api/model/parse-assumptions',self.body(document_ids=[doc['id']]))
            self.assertIn(code,(400,404));self.model.assert_not_called()

    def test_excel_unavailable_keeps_conditions_without_fake_success(self):
        self.set_response(inputs())
        with patch('workos.return_excel.calculate_excel',side_effect=ExcelUnavailable('Synthetic Excel unavailable')):
            code,r=self.request('POST','/api/model/parse-assumptions',self.body())
        self.assertEqual(code,200,r);self.assertEqual(r['status'],'excel_unavailable');self.assertNotIn('calculation',r)
        self.assertEqual(r['assumptions'],inputs())

    def test_ready_project_return_autosaves_record_and_archive_without_second_confirmation(self):
        self.set_response(inputs());computed={**calculate(inputs()),'excel_verified':True,'answer':'Synthetic MOC 1.80×'}
        with patch('workos.return_excel.calculate_excel',return_value=computed),patch.object(self.app,'archive_record',return_value={'status':'saved_local'}) as archive:
            code,r=self.request('POST','/api/model/parse-assumptions',self.body(auto_save=True))
        self.assertEqual(code,200,r);self.assertTrue(r['saved']);archive.assert_called_once()
        saved=self.app.stores['personal'].get('deliverables',r['deliverable_id'])
        self.assertEqual(saved['method'],'investor_return');self.assertIn('MOC / MOIC',saved['body']);self.assertNotIn('JSON:',saved['body'])
        self.assertEqual(self.app.conversations.get('personal',r['conversation_id'])['turns'][0]['current_artifact']['id'],r['deliverable_id'])

    def test_changed_source_during_extraction_blocks_excel_and_autosave(self):
        a=inputs()
        def response(*args,**kwargs):
            self.app.stores['personal'].update('documents',self.docs['personal']['id'],{'content':'Changed source while generating'})
            return json.dumps({'assumptions':a}), 'Synthetic model'
        self.model.side_effect=response
        with patch('workos.return_excel.calculate_excel') as excel:
            code,r=self.request('POST','/api/model/parse-assumptions',self.body(auto_save=True))
        self.assertEqual(code,400,r);self.assertIn('已改变',r['error']);excel.assert_not_called()
        self.assertEqual(self.app.stores['personal'].list('deliverables'),[])

    def test_stop_during_excel_does_not_convert_cancellation_to_input_question(self):
        entered,release=threading.Event(),threading.Event();self.releases.append(release)
        def pending(a):entered.set();release.wait(3);check_cancelled();return calculate(a)
        responses=[]
        with patch('workos.return_excel.calculate_excel',side_effect=pending):
            worker=threading.Thread(target=lambda:responses.append(self.request('POST','/api/model/valuation',{'method':'investor_return','assumptions':inputs()},headers={'X-WorkOS-Request-ID':'synthetic-excel-stop'})))
            worker.start();self.assertTrue(entered.wait(1))
            code,r=self.request('POST','/api/operations/synthetic-excel-stop/cancel',{})
            release.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(code,200,r)
        self.assertEqual(responses[0][0],409,responses);self.assertEqual(responses[0][1]['code'],'request_cancelled')
        self.assertEqual(self.app.stores['personal'].list('deliverables'),[])


if __name__=='__main__':unittest.main()
