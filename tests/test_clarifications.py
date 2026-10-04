import copy
import json
import unittest

from workos.cancellation import CancelledError, OperationConflict
from workos.clarifications import (
    ClarificationRequired, financial_clarification, financial_validation_clarification,
    needs_input, normalize_assumptions, resolve_method, task_clarification,
)
from workos.valuation import calculate_valuation


def dcf_fixture(**changes):
    return {
        'currency': 'RMB', 'unit': '百万元', 'valuation_date': '2025-12-31',
        'wacc': .10, 'discount_timing': 'year_end', 'terminal_method': 'perpetuity',
        'terminal_growth': .02, 'net_debt': 0, 'minority_interest': 0,
        'forecasts': [{'year': '2026E', 'ebit': 100, 'da': 10, 'capex': 20,
                       'delta_nwc': 5, 'tax_rate': .25}], **changes,
    }


class ClarificationTests(unittest.TestCase):
    def test_unambiguous_method_aliases_and_natural_tasks(self):
        cases = [('按20倍利润算估值', 'net_income'), ('P/E', 'net_income'),
                 ('按3倍收入估算股权价值', 'ps'), ('市销率', 'ps'),
                 ('请做现金流折现', 'dcf'), ('杠杆收购回报', 'lbo')]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(resolve_method(text=text), expected)
        self.assertIsNone(resolve_method(text='随便做个模型'))
        self.assertIsNone(resolve_method(text='比较DCF和LBO'))
        self.assertEqual(resolve_method('ps', '讨论DCF和LBO'), 'ps')
        report = financial_clarification('magic', {'revenue': 500, 'currency': 'USD'})
        self.assertEqual([option['value'] for option in report['questions'][0]['options']],
                         ['investor_return', 'net_income', 'ps', 'dcf', 'lbo'])
        self.assertEqual(report['assumptions']['revenue'], 500)

    def test_missing_units_and_period_keep_known_numbers_without_zero_defaults(self):
        given = {'net_income': '100', 'pe_multiple': '12倍'}
        report = financial_clarification('net_income', given)
        self.assertEqual(report['status'], 'needs_input')
        self.assertEqual(report['method'], 'net_income')
        self.assertEqual(report['assumptions'], {'net_income': 100, 'pe_multiple': 12})
        self.assertEqual(set(report['missing']), {'currency', 'unit', 'period'})
        self.assertEqual([q['id'] for q in report['questions']], ['currency_unit', 'period'])
        self.assertIn('实际值', report['questions'][1]['label'])
        self.assertNotIn('currency', given)
        completed = {**report['assumptions'], 'currency': 'RMB', 'unit': '百万元', 'period': 'FY2025A'}
        self.assertIsNone(financial_clarification('net_income', completed))
        self.assertEqual(calculate_valuation('net_income', completed)['equity_value'], 1200)

    def test_ambiguous_units_relative_period_and_date_prompt_instead_of_guess(self):
        base = {'currency': 'RMB', 'unit': '钱', 'period': '今年', 'net_income': 100, 'pe_multiple': 12}
        for unit in ('钱', '人民币', '大数', '亿', '默认'):
            with self.subTest(unit=unit):
                report = financial_clarification('net_income', {**base, 'unit': unit})
                self.assertIn('unit', report['missing'])
                self.assertIn('period', report['missing'])
                self.assertEqual(report['assumptions']['unit'], unit)
        for unit in ('百万元', 'millions', 'USD millions', '亿元'):
            self.assertIsNone(financial_clarification('net_income', {**base, 'unit': unit, 'period': 'FY2025A'}))
        report = financial_clarification('dcf', dcf_fixture(valuation_date='年底'))
        self.assertEqual(report['missing'], ['valuation_date'])
        self.assertEqual(report['assumptions']['valuation_date'], '年底')

    def test_explicit_numeric_percent_multiple_normalization_does_not_change_scale(self):
        source = dcf_fixture(wacc='10%', terminal_growth='2％', net_debt='1,000',
                             forecasts=[{'year': '2026E', 'revenue': '500', 'ebit_margin': '20%',
                                         'da': '10', 'capex': '20', 'delta_nwc': '5', 'tax_rate': '25%'}])
        untouched = copy.deepcopy(source)
        normalized = normalize_assumptions('dcf', source)
        self.assertEqual(normalized['wacc'], .10)
        self.assertEqual(normalized['terminal_growth'], .02)
        self.assertEqual(normalized['net_debt'], 1000)
        self.assertEqual(normalized['forecasts'][0]['ebit_margin'], .20)
        self.assertEqual(source, untouched)
        self.assertIsNone(financial_clarification('dcf', normalized))
        self.assertEqual(calculate_valuation('dcf', normalized)['forecast'][0]['ebit'], 100)
        report = financial_clarification('dcf', {**source, 'wacc': '10'})
        self.assertIn('wacc', report['missing'])
        self.assertEqual(report['assumptions']['wacc'], 10)
        self.assertIn('不会把10自动改成10%', next(q['hint'] for q in report['questions'] if q['id'] == 'wacc'))
        pe = normalize_assumptions('net_income', {'pe_multiple': '12x', 'net_income': '100亿元'})
        self.assertEqual(pe['pe_multiple'], 12)
        self.assertEqual(pe['net_income'], '100亿元')
        self.assertEqual(normalize_assumptions('ps', {'revenue': '1,5'})['revenue'], '1,5')

    def test_missing_forecast_rows_and_global_rates_are_grouped_and_zero_retained(self):
        report = financial_clarification('dcf', dcf_fixture(forecasts=[]))
        self.assertIn('forecasts', report['missing'])
        self.assertEqual(report['questions'][0]['id'], 'forecasts')
        source = dcf_fixture(forecasts=[{'year': '2026E', 'ebit': 100, 'da': 0, 'capex': 0}])
        report = financial_clarification('dcf', source)
        self.assertIn('forecasts[1].delta_nwc', report['missing'])
        self.assertIn('forecasts[1].tax_rate', report['missing'])
        self.assertEqual(len(report['questions']), 1)
        self.assertEqual(report['assumptions']['forecasts'][0]['capex'], 0)
        self.assertNotIn('tax_rate', report['assumptions'])
        valid = dcf_fixture(tax_rate=.25, forecasts=[{'year': '2026E', 'ebit': 100, 'da': 0, 'capex': 0, 'delta_nwc': 0}])
        self.assertIsNone(financial_clarification('dcf', valid))

    def test_conditional_terminal_inputs_no_unselected_assumptions_required(self):
        source = dcf_fixture(terminal_method='exit_multiple', terminal_multiple='8倍')
        source.pop('terminal_growth')
        self.assertIsNone(financial_clarification('dcf', source))
        normalized = normalize_assumptions('dcf', source)
        self.assertEqual(normalized['terminal_multiple'], 8)
        source.pop('terminal_multiple')
        report = financial_clarification('dcf', source)
        self.assertEqual(report['missing'], ['terminal_multiple'])
        report = financial_clarification('dcf', dcf_fixture(terminal_method='随意'))
        self.assertIn('terminal_method', report['missing'])
        self.assertEqual({o['value'] for o in report['questions'][0]['options']}, {'perpetuity', 'exit_multiple'})

    def test_invalid_number_type_range_nonfinite_require_input_no_secret_echo(self):
        for value in (True, float('nan'), float('inf'), '一百', '1e9999', 10 ** 1000):
            with self.subTest(value=value):
                report = financial_clarification('net_income', {
                    'currency': 'RMB', 'unit': '百万元', 'period': 'FY2025A',
                    'net_income': value, 'pe_multiple': 12, 'api_key': 'not-for-report',
                    'notes': {'authorization': 'also-not-for-report', 'why': 'user assumption'},
                })
                self.assertIn('net_income', report['missing'])
                encoded = json.dumps(report, ensure_ascii=False, allow_nan=False)
                self.assertNotIn('not-for-report', encoded)
        report = financial_clarification('dcf', dcf_fixture(forecasts=[
            {'year': False, 'ebit': 100, 'da': -1, 'capex': 0, 'delta_nwc': 0, 'tax_rate': 25}]))
        self.assertIn('forecasts[1].year', report['missing'])
        self.assertIn('forecasts[1].da', report['missing'])
        self.assertIn('forecasts[1].tax_rate', report['missing'])

    def test_known_calculation_relationship_errors_are_actionable_not_autofixed(self):
        source = dcf_fixture(wacc=.02, terminal_growth=.02)
        with self.assertRaises(ValueError) as raised:
            calculate_valuation('dcf', source)
        report = financial_validation_clarification('dcf', source, raised.exception)
        self.assertEqual(report['questions'][0]['id'], 'wacc_terminal_growth')
        self.assertEqual(report['assumptions']['wacc'], .02)
        self.assertEqual(report['assumptions']['terminal_growth'], .02)
        negative = {'currency': 'RMB', 'unit': '百万元', 'period': 'FY2025A', 'net_income': -100, 'pe_multiple': 12}
        with self.assertRaises(ValueError) as raised:
            calculate_valuation('net_income', negative)
        report = financial_validation_clarification('net_income', negative, raised.exception)
        self.assertIn('其他估值方法', report['questions'][0]['label'])
        self.assertEqual(report['assumptions']['net_income'], -100)

    def test_validation_missing_driver_and_forecast_horizon_use_exact_known_errors(self):
        source = dcf_fixture(wacc='unknown')
        with self.assertRaises(ValueError) as raised:
            calculate_valuation('dcf', source)
        report = financial_validation_clarification('dcf', source, raised.exception)
        self.assertIn('wacc', report['missing'])
        report = financial_validation_clarification('lbo', {}, ValueError(
            'LBO预测年度数量与进入/退出日期不一致；每行须覆盖一个完整年度，暂不支持未明确计算的不足一年期间'))
        self.assertEqual(report['questions'][0]['id'], 'forecast_horizon')
        self.assertIn('完整年度', report['questions'][0]['hint'])

    def test_auth_cancellation_provider_json_scope_and_unknown_schema_are_not_masked(self):
        errors = [CancelledError(), OperationConflict('请求已存在'), PermissionError('鉴权失败'),
                  RuntimeError('provider failed'), ValueError('项目不存在'),
                  ValueError('资料不属于当前项目'), ValueError('模型返回的假设JSON无法解析；未运行测算'),
                  ValueError('以下假设字段未映射，未用于计算：api_key'),
                  ValueError('假设 api_key 必须是有限数值'), ValueError('请刷新会话')]
        for error in errors:
            with self.subTest(error=str(error)):
                self.assertIsNone(financial_validation_clarification('dcf', dcf_fixture(), error))
        source = {**dcf_fixture(), 'made_up': 10}
        self.assertIn('made_up', normalize_assumptions('dcf', source))
        with self.assertRaisesRegex(ValueError, '未映射'):
            calculate_valuation('dcf', normalize_assumptions('dcf', source))

    def test_unmapped_drivers_require_explicit_mapping_and_remain_visible(self):
        for source in ({**dcf_fixture(), 'cost_of_debt': .05},
                       dcf_fixture(forecasts=[{**dcf_fixture()['forecasts'][0], 'adjusted_ebit': 120}])):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, '未映射') as raised:
                calculate_valuation('dcf', source)
            report = financial_validation_clarification('dcf', source, raised.exception)
            self.assertEqual(report['questions'][0]['id'], 'assumption_mapping')
            self.assertEqual(report['assumptions'], source)
            with self.assertRaisesRegex(ValueError, '未映射'):
                calculate_valuation('dcf', report['assumptions'])
        self.assertIsNone(financial_validation_clarification('dcf', dcf_fixture(),
                          ValueError('以下假设字段未映射，未用于计算：not_in_inputs')))

    def test_lbo_zero_entry_value_gets_guidance_matching_engine_bound(self):
        source = {'currency': 'RMB', 'unit': '百万元', 'entry_date': '2026-01-01',
                  'exit_date': '2026-12-31', 'entry_ev': 0, 'entry_debt': 0, 'entry_fees': 0,
                  'minimum_cash': 0, 'initial_cash': 0, 'seller_rollover': 0, 'exit_fees': 0,
                  'exit_multiple': 10, 'tax_rate': .25, 'interest_rate': .05,
                  'mandatory_amortization': 0, 'cash_sweep_pct': 1,
                  'forecasts': [{'year': '2026', 'ebitda': 100, 'da': 0, 'capex': 0, 'delta_nwc': 0}]}
        with self.assertRaisesRegex(ValueError, 'entry_ev') as raised:
            calculate_valuation('lbo', source)
        report = financial_validation_clarification('lbo', source, raised.exception)
        self.assertEqual(report['missing'], ['entry_ev'])
        self.assertEqual(report['assumptions']['entry_ev'], 0)

    def test_task_preflight_questions_do_not_accept_malformed_scope_or_guess_project(self):
        report = task_clarification('actions', {'message': '整理项目', 'document_ids': []}, requires_project=True)
        self.assertEqual(report['missing'], ['project_id'])
        self.assertEqual(report['questions'][0]['id'], 'project_id')
        self.assertIsNone(task_clarification('actions', {'message': '整理项目', 'project_id': 'selected'}, requires_project=True))
        report = task_clarification('meeting', {'transcript': '', 'project_id': 'selected'})
        self.assertEqual(report['missing'], ['transcript'])
        report = task_clarification('workflow', {'message': '比较版本', 'document_ids': ['one', 'one']}, requires_sources=True, min_sources=2)
        self.assertEqual(report['questions'][0]['label'], '请至少选择两份需要比较的材料。')
        self.assertIsNone(task_clarification('workflow', {'message': '项目周报'}, requires_sources=True, has_project_context=True))
        for body in ({'message': []}, {'message': '任务', 'document_ids': 'one'}, {'transcript': []}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                task_clarification('meeting' if 'transcript' in body else 'workflow', body)

    def test_reports_and_typed_exception_bounded_and_independent(self):
        report = needs_input('x' * 2000, [{'id': str(i), 'label': 'q' * 500, 'hint': 'h' * 1000}
                                         for i in range(12)], purpose='workflow',
                             known_conditions=[{'label': 'known', 'value': 0}],
                             missing=['a', 'a', 'b'])
        self.assertEqual(len(report['questions']), 3)
        self.assertEqual(len(report['message']), 1000)
        self.assertEqual(len(report['questions'][0]['label']), 240)
        self.assertEqual(report['known_conditions'][0]['value'], 0)
        self.assertEqual(report['missing'], ['a', 'b'])
        error = ClarificationRequired(report)
        report['questions'][0]['label'] = 'changed'
        self.assertNotEqual(error.report['questions'][0]['label'], 'changed')
        with self.assertRaises(ValueError):
            needs_input('bad', [])
        with self.assertRaises(ValueError):
            normalize_assumptions([], {})
        options = needs_input('choose', [{'id': 'scope', 'label': '请选择范围',
                                         'options': ['先给一般解释', {'value': 'selected', 'label': '结合所选资料'}]}])
        self.assertEqual(options['questions'][0]['options'], [
            {'value': '先给一般解释', 'label': '先给一般解释'},
            {'value': 'selected', 'label': '结合所选资料'},
        ])


if __name__ == '__main__':
    unittest.main()
