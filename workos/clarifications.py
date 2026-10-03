"""Bounded follow-up questions for ordinary missing inputs, never error masking.

Call these helpers at explicit input preflights or around deterministic valuation
validation. Authentication, scope, provider and cancellation exceptions retain
their existing paths. No model calls, storage writes or financial defaults live
in this module.
"""
from __future__ import annotations

import copy
from datetime import date
import math
import re

from .valuation import ASSUMPTION_SCHEMAS, missing_assumptions


METHOD_OPTIONS = [
    {'value': 'net_income', 'label': '净利润 × 市盈率（P/E）'},
    {'value': 'ps', 'label': '收入 × 市销率（P/S）'},
    {'value': 'dcf', 'label': '现金流折现（DCF）'},
    {'value': 'lbo', 'label': '杠杆收购回报（LBO）'},
]
LABELS = {
    'currency': '币种', 'unit': '金额单位', 'period': '利润或收入对应期间',
    'net_income': '净利润', 'pe_multiple': '市盈率（P/E）', 'revenue': '收入',
    'ps_multiple': '市销率（P/S）', 'diluted_shares': '稀释后股数',
    'valuation_date': '估值日', 'wacc': '资本成本（WACC）',
    'discount_timing': '现金流折现时点', 'terminal_method': '终值方法',
    'terminal_growth': '永续增长率', 'terminal_multiple': '终值退出倍数',
    'net_debt': '净债务', 'minority_interest': '少数股东权益',
    'entry_date': '投资进入日期', 'exit_date': '退出日期', 'entry_ev': '进入企业价值',
    'entry_debt': '进入债务', 'entry_fees': '进入费用', 'minimum_cash': '最低现金',
    'initial_cash': '初始现金', 'seller_rollover': '卖方滚存股权金额',
    'exit_fees': '退出费用', 'exit_multiple': '退出 EBITDA 倍数',
    'tax_rate': '税率', 'interest_rate': '债务利率',
    'mandatory_amortization': '每年强制还本金额', 'cash_sweep_pct': '剩余现金还债比例',
    'ebit': '经营利润（EBIT）', 'ebitda': 'EBITDA', 'ebit_margin': '经营利润率',
    'da': '折旧摊销', 'capex': '资本开支', 'delta_nwc': '营运资金增加额', 'year': '预测年度',
}
RATIOS = {'wacc', 'tax_rate', 'interest_rate', 'cash_sweep_pct', 'terminal_growth', 'ebit_margin'}
MULTIPLES = {'pe_multiple', 'ps_multiple', 'exit_multiple', 'terminal_multiple'}
NUMERIC = (set(LABELS) - {'currency', 'unit', 'period', 'valuation_date', 'entry_date',
                         'exit_date', 'discount_timing', 'terminal_method', 'year'})
RANGES = {
    'pe_multiple': (.01, 200), 'ps_multiple': (0, 100), 'terminal_multiple': (0, 100),
    'exit_multiple': (0, 100), 'diluted_shares': (.0000001, None),
    'wacc': (0, 1), 'tax_rate': (0, 1), 'interest_rate': (0, 1),
    'cash_sweep_pct': (0, 1), 'terminal_growth': (-.5, .25), 'ebit_margin': (-1, 1),
    **{key: (0, None) for key in ('revenue', 'da', 'capex', 'minority_interest',
       'entry_ev', 'entry_debt', 'entry_fees', 'minimum_cash', 'initial_cash',
       'seller_rollover', 'exit_fees', 'mandatory_amortization')},
    'entry_ev': (.000001, None),
}
_NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
_SECRET = re.compile(r'(?:api[_-]?key|password|secret|authorization|credential|access[_-]?token)', re.I)


def _text(value, limit=600):
    return value.strip()[:limit] if isinstance(value, str) else ''


def _finite(value):
    try:
        return math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _safe_json(value, depth=0):
    if depth > 6:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if _finite(value) else str(value)[:4000]
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, list):
        return [_safe_json(item, depth + 1) for item in value[:60]]
    if isinstance(value, dict):
        return {key[:100]: _safe_json(item, depth + 1) for key, item in list(value.items())[:64]
                if isinstance(key, str) and not _SECRET.search(key)}
    return None


def needs_input(message, questions, *, purpose='', known_conditions=None, missing=None,
                assumptions=None, method=None):
    """Return a serializable report; only 1–3 questions are shown per round."""
    if not isinstance(questions, (list, tuple)):
        raise ValueError('补充问题结构不正确')
    cleaned = []
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError('补充问题结构不正确')
        identifier, label = _text(question.get('id'), 120), _text(question.get('label'), 240)
        if not identifier or not label:
            raise ValueError('补充问题缺少编号或内容')
        item = {'id': identifier, 'label': label, 'hint': _text(question.get('hint'), 600)}
        if isinstance(question.get('options'), (list, tuple)):
            options = []
            for option in question['options'][:8]:
                if isinstance(option, str):
                    options.append({'value': _text(option, 100), 'label': _text(option, 160)})
                elif isinstance(option, dict):
                    options.append({'value': _text(option.get('value'), 100), 'label': _text(option.get('label'), 160)})
            item['options'] = [option for option in options if option['value'] and option['label']]
        if identifier not in {entry['id'] for entry in cleaned}:
            cleaned.append(item)
    if not cleaned:
        raise ValueError('补充问题不能为空')
    result = {'status': 'needs_input', 'message': _text(message, 1000),
              'questions': cleaned[:3], 'purpose': _text(purpose, 60),
              'known_conditions': [], 'missing': []}
    for condition in (known_conditions or [])[:20]:
        if isinstance(condition, dict) and _text(condition.get('label'), 100):
            result['known_conditions'].append({'label': _text(condition['label'], 100),
                                                'value': _safe_json(condition.get('value'))})
    result['missing'] = list(dict.fromkeys(_text(item, 160) for item in (missing or [])
                                          if isinstance(item, str)))[:80]
    if assumptions is not None:
        result['assumptions'] = _safe_json(assumptions)
    if isinstance(method, str) and method in ASSUMPTION_SCHEMAS:
        result['method'] = method
    return result


class ClarificationRequired(ValueError):
    """Explicit continuation outcome; ordinary ValueErrors are never converted."""
    def __init__(self, report):
        if not isinstance(report, dict) or report.get('status') != 'needs_input':
            raise ValueError('补充条件结果不正确')
        self.report = copy.deepcopy(report)
        super().__init__(report.get('message') or '请补充条件后继续')


def resolve_method(method=None, text=''):
    """Resolve only an explicit alias or unambiguous financial language."""
    if isinstance(method, str) and method.strip().lower() in ASSUMPTION_SCHEMAS:
        return method.strip().lower()
    content = ' '.join(value for value in (method, text) if isinstance(value, str)).lower()
    matches = []
    patterns = {
        'net_income': r'\bnet[_\s-]?income\b|\bp\s*[/\-]?\s*e\b|市盈率|净利润\s*[×x*]|(?:\d+(?:\.\d+)?\s*倍\s*(?:净)?利润)|利润倍数',
        'ps': r'\bp\s*[/\-]?\s*s\b|市销率|(?:收入|营收)\s*[×x*]|(?:\d+(?:\.\d+)?\s*倍\s*(?:收入|营收))|(?:收入|营收)倍数',
        'dcf': r'\bdcf\b|现金流(?:量)?(?:折现|贴现)',
        'lbo': r'\blbo\b|杠杆收购',
    }
    for key, pattern in patterns.items():
        if re.search(pattern, content, re.I):
            matches.append(key)
    return matches[0] if len(matches) == 1 else None


def _numeric(value, key):
    if not isinstance(value, str):
        return copy.deepcopy(value)
    candidate = value.strip().replace('％', '%')
    # Accept only correctly grouped thousands, never a comma decimal guess.
    if re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?', candidate):
        candidate = candidate.replace(',', '')
    suffix = ''
    if key in RATIOS and candidate.endswith('%'):
        candidate, suffix = candidate[:-1].strip(), '%'
    elif key in MULTIPLES:
        candidate = re.sub(r'\s*(?:倍|[x×]|times)\s*$', '', candidate, flags=re.I)
    if not re.fullmatch(_NUMBER, candidate):
        return value
    number = float(candidate)
    if suffix == '%':
        number /= 100
    return number if _finite(number) else value


def normalize_assumptions(method, assumptions):
    """Copy known numeric fields; no currency, scale, dates or missing defaults.

    Unknown keys are retained so the deterministic calculator can reject them;
    this helper must never silently omit a requested assumption from calculation.
    """
    if not isinstance(method, str) or method not in ASSUMPTION_SCHEMAS or not isinstance(assumptions, dict):
        raise ValueError('估值方法或假设结构不正确')
    result = copy.deepcopy(assumptions)
    allowed = set(ASSUMPTION_SCHEMAS[method]['required'] + ASSUMPTION_SCHEMAS[method]['optional'])
    for key in allowed & NUMERIC:
        if key in result:
            result[key] = _numeric(result[key], key)
    if isinstance(result.get('forecasts'), list):
        for row in result['forecasts']:
            if isinstance(row, dict):
                for key in NUMERIC & row.keys():
                    row[key] = _numeric(row[key], key)
    return result


def _public_assumptions(method, assumptions):
    # Retain unsupported user drivers for an explicit mapping decision. The
    # calculator still rejects them; hiding them here could silently drop input
    # on the next round. Credential keys never belong in a financial report.
    return _safe_json(assumptions)


def _known(assumptions):
    return [{'label': LABELS[key], 'value': _safe_json(value)} for key, value in assumptions.items()
            if key in LABELS and value is not None and value != ''][:20]


def _question(key):
    if key == 'currency_unit':
        return {'id': key, 'label': '这些金额使用什么币种和金额单位？',
                'hint': '例如人民币、百万元；币种和单位不会替你猜，也不会默认补金额。'}
    if key == 'method':
        return {'id': key, 'label': '你想按什么方法做估值或回报测算？',
                'hint': '也可以直接描述目标，例如“按20倍利润估值”或“算杠杆收购回报”。',
                'options': METHOD_OPTIONS}
    if key == 'period':
        return {'id': key, 'label': '利润或收入对应哪个期间，是实际值还是预测值？',
                'hint': '例如2025年实际、2026年预测，或截至2026年9月的过去12个月。'}
    if key == 'forecasts':
        return {'id': key, 'label': '请补充预测年度，以及各年的经营和现金流假设。',
                'hint': '可以直接粘贴表格或自然语言，包含年份、经营利润或EBITDA、折旧、资本开支、营运资金变化及税率；LBO还需利率、还本和现金还债比例。'}
    if key == 'discount_timing':
        return {'id': key, 'label': '预测现金流按年末还是年中折现？', 'hint': '这会影响折现价值，请按你的业务口径选择。',
                'options': [{'value': 'year_end', 'label': '年末'}, {'value': 'mid_year', 'label': '年中'}]}
    if key == 'terminal_method':
        return {'id': key, 'label': '预测期之后的终值按永续增长还是退出倍数估算？', 'hint': '选永续增长需增长率；选退出倍数需末年EBITDA倍数。',
                'options': [{'value': 'perpetuity', 'label': '永续增长'}, {'value': 'exit_multiple', 'label': '退出倍数'}]}
    if key in ('entry_date', 'exit_date', 'valuation_date'):
        return {'id': key, 'label': f'请确认{LABELS[key]}是哪一天。', 'hint': '例如2026年12月31日；需要明确日期，不能代选今天或持有年限。'}
    label = LABELS.get(key, key)
    hint = ('可以写“10%”；直接写数字时0.10代表10%，不会把10自动改成10%。' if key in RATIOS
            else '可以用自然语言回答；请说明数值的口径，未知值不会自动填零。')
    return {'id': key, 'label': f'{label}采用什么数值或口径？', 'hint': hint}


def financial_clarification(method, assumptions, model_questions=()):
    """Identify absent/ambiguous inputs without invoking or changing the engine."""
    if not isinstance(assumptions, dict):
        raise ValueError('估值假设必须是对象')
    resolved = resolve_method(method)
    if resolved is None:
        allowed = set().union(*(set(schema['required'] + schema['optional']) for schema in ASSUMPTION_SCHEMAS.values()))
        retained = {key: value for key, value in assumptions.items() if key in allowed}
        return needs_input('先确认测算方法，再继续整理已有条件。', [_question('method')],
                           purpose='valuation', missing=['method'], assumptions=retained,
                           known_conditions=_known(retained))
    values = normalize_assumptions(resolved, assumptions)
    missing = missing_assumptions(resolved, values)
    invalid = []
    for key in ('currency', 'unit', 'period'):
        if key not in ASSUMPTION_SCHEMAS[resolved]['required']:
            continue
        value = values.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > (40 if key == 'period' else 32):
            invalid.append(key)
        elif re.fullmatch(r'(?:未知|不详|默认|随便|按默认|金额|钱|当地|最近|今年|明年|当期|待定|unknown|default|tbd)', value.strip(), re.I):
            invalid.append(key)
        elif key == 'unit' and re.fullmatch(r'(?:人民币|美元|rmb|cny|usd)', value.strip(), re.I):
            invalid.append(key)
        elif key == 'unit' and not re.fullmatch(
                r'(?:(?:人民币|美元|港元|港币|欧元|rmb|cny|usd|hkd|eur)\s*)?'
                r'(?:元|千元|万元|百万元|亿元|万亿元|units?|thousands?|millions?|billions?)'
                r'(?:\s*(?:人民币|美元|港元|港币|欧元|rmb|cny|usd|hkd|eur))?', value.strip(), re.I):
            invalid.append(key)
    for key in ('valuation_date', 'entry_date', 'exit_date'):
        if key in ASSUMPTION_SCHEMAS[resolved]['required']:
            try:
                date.fromisoformat(values.get(key))
            except (TypeError, ValueError):
                invalid.append(key)
    for key, choices in [('discount_timing', ('year_end', 'mid_year')), ('terminal_method', ('perpetuity', 'exit_multiple'))]:
        if key in ASSUMPTION_SCHEMAS[resolved]['required'] and values.get(key) not in choices:
            invalid.append(key)
    if resolved in ('dcf', 'lbo') and (not isinstance(values.get('forecasts'), list) or not 1 <= len(values['forecasts']) <= 15):
        invalid.append('forecasts')
    allowed = set(ASSUMPTION_SCHEMAS[resolved]['required'] + ASSUMPTION_SCHEMAS[resolved]['optional'])
    for key in allowed & NUMERIC:
        if key in values and values[key] is not None:
            if _invalid_number(key, values[key]):
                invalid.append(key)
    if isinstance(values.get('forecasts'), list):
        for index, row in enumerate(values['forecasts'], 1):
            if not isinstance(row, dict):
                continue
            for key in NUMERIC & row.keys():
                if row[key] is not None and _invalid_number(key, row[key]):
                    invalid.append(f'forecasts[{index}].{key}')
            year = row.get('year')
            if isinstance(year, bool) or not isinstance(year, (int, str)) or not str(year).strip() or len(str(year)) > 16:
                invalid.append(f'forecasts[{index}].year')
    missing = list(dict.fromkeys(missing + invalid))
    questions = []
    if any(key in missing for key in ('currency', 'unit')):
        questions.append(_question('currency_unit'))
    if 'period' in missing:
        questions.append(_question('period'))
    for key in missing:
        if key in ('currency', 'unit', 'period'):
            continue
        key = 'forecasts' if key.startswith('forecasts') else key
        if key not in {question['id'] for question in questions}:
            questions.append(_question(key))
    if isinstance(model_questions, (list, tuple)):
        for index, question in enumerate(model_questions[:3], 1):
            if isinstance(question, str) and question.strip():
                questions.append({'id': f'model_question_{index}', 'label': _text(question, 240),
                                  'hint': '可以直接用自然语言补充，已有条件会保留。'})
    if not questions:
        return None
    return needs_input('已有条件已保留，请补充下面的问题后继续测算。', questions,
                       purpose='valuation', missing=missing, method=resolved,
                       assumptions=_public_assumptions(resolved, values), known_conditions=_known(values))


def _invalid_number(key, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not _finite(value):
        return True
    minimum, maximum = RANGES.get(key, (None, None))
    return (minimum is not None and value < minimum) or (maximum is not None and value > maximum)


def financial_validation_clarification(method, assumptions, error):
    """Convert ONLY recognized calculation input errors, not arbitrary failures."""
    if type(error) is not ValueError or not isinstance(assumptions, dict):
        return None
    resolved = resolve_method(method)
    if resolved is None:
        return None
    text = str(error)
    mapping = re.fullmatch(r'(以下假设字段未映射，未用于计算：|DCF预测含未映射字段：|LBO预测含未映射字段：)(.+)', text)
    if mapping:
        names = mapping.group(2).split('、')
        if len(names) > 40 or any(not name.strip() or len(name) > 100 or _SECRET.search(name) or '\n' in name for name in names):
            return None
        # Require the reported unmapped names to exist in these actual inputs,
        # rather than turning arbitrary ValueError text into a continuation.
        available = set(assumptions)
        forecast_mapping = mapping.group(1).startswith(('DCF', 'LBO'))
        if forecast_mapping:
            available = {key for row in assumptions.get('forecasts', []) if isinstance(row, dict) for key in row}
        if not set(names) <= available:
            return None
        values = normalize_assumptions(resolved, assumptions)
        return needs_input('这些假设还没有对应到计算字段，需要先确认口径。',
                           [{'id': 'assumption_mapping', 'label': '未映射的假设应该对应哪个计算口径，或是否明确移除？',
                             'hint': '待确认字段：' + '、'.join(names[:6]) + '。不会自动忽略这些假设；可以直接说明它们的含义。'}],
                           purpose='valuation', method=resolved, missing=names,
                           assumptions=_public_assumptions(resolved, values), known_conditions=_known(values))
    numeric = re.fullmatch(r'(?:缺少假设：|假设 )([a-z_]+)(?: 必须是有限数值| 低于允许范围| 高于允许范围)?', text)
    preflight_messages = {
        '请明确模型币种，例如 RMB 或 USD', '请明确金额单位，例如 元、千元或百万元',
        '请标注净利润期间，例如 FY2025、LTM 2026-09', '请标注收入期间，例如 FY2025、LTM 2026-09',
        '请提供估值日 YYYY-MM-DD', '请提供1至15个预测年度',
        '请明确现金流折现时点：year_end 或 mid_year', '请明确终值方法：perpetuity 或 exit_multiple',
        'LBO 必须提供 entry_date 和 exit_date（YYYY-MM-DD）',
        '请明确每年利率，或提供全期统一 interest_rate',
    }
    if numeric or text in preflight_messages or re.fullmatch(r'(?:尚缺少明确假设：.+|第\d+年(?:期间标识不正确|预测必须是对象))', text):
        if numeric and numeric.group(1) not in NUMERIC:
            return None
        report = financial_clarification(resolved, assumptions)
        if report is not None:
            return report
        # A normalized numeric string may be ready now; let the caller retry
        # deterministic validation rather than claim a missing condition.
        return None
    specific = {
        'WACC 必须高于永续增长率': ('wacc_terminal_growth', '资本成本必须高于永续增长率，你希望调整哪一个？', '请确认两个比率的口径；不会自动改变任何数值。', ['wacc', 'terminal_growth']),
        '净利润为零或负数时，P/E 不可比；请选择 P/S、DCF 或补充规范化净利润': ('net_income_basis', '目前净利润为零或负数，请补充规范化利润，或选择其他估值方法。', '可以改用P/S或DCF；请说明调整利润的依据。', ['net_income', 'method']),
        '末期 FCFF 为负，永续增长终值不可直接使用；请补充可支撑正现金流的预测或选退出倍数': ('terminal_cash_flow', '末期现金流为负，请确认预测或改用退出倍数终值。', '不会自动把负现金流调整为正数。', ['forecasts', 'terminal_method']),
        '末年 EBITDA 为负，退出倍数终值不可比': ('terminal_ebitda', '末年EBITDA为负，请确认末年预测或终值方法。', '需要可比的利润口径。', ['forecasts', 'terminal_method']),
        'exit_date 必须晚于 entry_date': ('holding_period', '退出日期应晚于进入日期，请确认持有期间。', '请给出明确的进入和退出日期。', ['entry_date', 'exit_date']),
        '初始现金不能低于最低现金；请明确资金来源和最低现金要求': ('cash_funding', '初始现金低于最低现金要求，现金资金来源如何安排？', '请确认初始现金和最低现金；不会默认补资金。', ['initial_cash', 'minimum_cash']),
        '进入股权投入必须为正；请核对EV、债务、费用、初始现金和rollover': ('entry_funding', '目前计算的进入股权投入不为正，请确认进入资金结构。', '请核对企业价值、债务、费用、初始现金和卖方滚存金额。', ['entry_ev', 'entry_debt', 'entry_fees', 'initial_cash', 'seller_rollover']),
        '模型计算超出有限数值范围；请核对金额单位与假设尺度': ('assumption_scale', '这些假设导致数值超出计算范围，请确认金额单位和数值尺度。', '不会自动缩小金额或改变单位。', ['unit']),
    }
    if text.startswith('LBO预测年度数量与进入/退出日期不一致；') or text.startswith('LBO预测年份须连续并覆盖持有期间；') or text == 'LBO期间请使用连续的YYYY/FY2027E或Year1、Year2标签，不能混用或漏年':
        detail = ('forecast_horizon', '请让预测年度连续，并与进入、退出日期覆盖的持有期间一致。', '当前每行代表完整年度；不足一年的期间需要另行确认计算口径。', ['entry_date', 'exit_date', 'forecasts'])
    else:
        detail = specific.get(text)
    if detail is None:
        return None
    identifier, label, hint, missing = detail
    values = normalize_assumptions(resolved, assumptions)
    return needs_input('已保留已有假设，需要确认一个会影响计算的条件。',
                       [{'id': identifier, 'label': label, 'hint': hint}], purpose='valuation',
                       missing=missing, method=resolved, assumptions=_public_assumptions(resolved, values),
                       known_conditions=_known(values))


def task_clarification(purpose, body, *, requires_sources=False, min_sources=1,
                       has_project_context=False, requires_project=False):
    """Ordinary input preflight only; ID validity and scope are checked elsewhere."""
    if not isinstance(body, dict):
        raise ValueError('任务输入必须是对象')
    message = body.get('message', body.get('question', body.get('text', '')))
    if message is not None and not isinstance(message, str):
        raise ValueError('任务描述必须是文本')
    selected = body.get('document_ids', body.get('source_ids', []))
    if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
        raise ValueError('资料选择结构不正确')
    questions, missing = [], []
    if purpose in ('meeting', 'meetings'):
        transcript = body.get('transcript', body.get('text', ''))
        if not isinstance(transcript, str):
            raise ValueError('会议转写必须是文本')
        if not transcript.strip():
            questions.append({'id': 'transcript', 'label': '请提供这场会议的转写或笔记。',
                              'hint': '可以直接粘贴原文或先选择已有会议；不会编造会议内容。'})
            missing.append('transcript')
    elif not _text(message, 12000):
        questions.append({'id': 'message', 'label': '你希望我完成什么工作？',
                          'hint': '直接描述目标即可，例如整理会议、找出项目风险、修改备忘录或做估值。'})
        missing.append('message')
    if requires_project and not body.get('project_id'):
        questions.append({'id': 'project_id', 'label': '这项工作属于哪个项目？',
                          'hint': '选择已有项目后继续；项目材料与整理结果会保留在该项目内。'})
        missing.append('project_id')
    if requires_sources and len(set(selected)) < min_sources and not has_project_context:
        questions.append({'id': 'document_ids',
                          'label': ('请至少选择两份需要比较的材料。' if min_sources >= 2 else '这项工作需要使用哪些材料？'),
                          'hint': '可以选择项目内的资料或先导入文件，再继续当前任务。'})
        missing.append('document_ids')
    if not questions:
        return None
    return needs_input('请补充下面的条件，我会沿着原任务继续。', questions,
                       purpose=purpose, missing=missing)
