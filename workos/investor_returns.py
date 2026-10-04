"""Investor cash flows, distinct from company valuation; Excel is the live engine.

The Python calculation is an independent economic check, not an Excel substitute.
Amounts share one explicit currency/unit. Source workbooks are never modified.
"""
from datetime import date
import math
import re

FIELDS = ['entry_date', 'exit_date', 'holding_years', 'investment_amount',
          'entry_equity_value', 'entry_valuation_basis', 'entry_ownership',
          'exit_ownership', 'ipo_dilution', 'dilution_events', 'exit_equity_value',
          'exit_net_income', 'exit_pe_multiple', 'exit_proceeds', 'cash_flows',
          'interim_cash_flows', 'source_notes', 'assumption_sources', 'source_conflicts']
LABELS = {'currency': '币种', 'unit': '金额单位', 'entry_date': '交割日期',
          'exit_date': '退出日期或持有年数', 'investment_amount': '我们的投资金额',
          'entry_equity_value': '进入股权估值（投前/投后）或持股比例',
          'entry_valuation_basis': '进入估值是投前还是投后',
          'exit_net_income': '退出年净利润与P/E，或退出股权估值/回收金额'}


def normalize(a):
    import copy
    a=copy.deepcopy(a)
    aliases={'人民币':'CNY','rmb':'CNY','cny':'CNY','美元':'USD','美金':'USD','usd':'USD',
             '港元':'HKD','港币':'HKD','hkd':'HKD','欧元':'EUR','eur':'EUR','英镑':'GBP','gbp':'GBP',
             '日元':'JPY','jpy':'JPY','新加坡元':'SGD','sgd':'SGD'}
    if isinstance(a.get('currency'),str):a['currency']=aliases.get(a['currency'].strip().lower(),a['currency'].strip().upper())
    if isinstance(a.get('unit'),str):
        unit=a['unit'].strip().lower();found=[]
        for alias,currency in aliases.items():
            if alias in unit:found.append(currency);unit=unit.replace(alias,'').strip()
        if found and len(set(found))==1 and a.get('currency') in (None,'',found[0]):
            a['currency']=found[0]
        elif found:unit=a['unit']  # Retain contradictions for a single follow-up.
        a['unit']={'百万':'millions','万':'万元','亿':'亿元','千':'千元','million':'millions','billion':'billions','thousand':'thousands'}.get(unit,unit)
    for key in ('entry_date','exit_date'):
        value=a.get(key)
        if isinstance(value,str):
            match=re.fullmatch(r'\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*',value)
            if match:
                try:a[key]=date(*map(int,match.groups())).isoformat()
                except ValueError:pass
    for key in ('holding_years','investment_amount','entry_equity_value','entry_ownership','exit_ownership',
                'ipo_dilution','exit_equity_value','exit_net_income','exit_pe_multiple','exit_proceeds'):
        if a.get(key) is not None:
            try:a[key]=number(a[key],key)
            except ValueError:pass
    if isinstance(a.get('dilution_events'),list):
        for i,value in enumerate(a['dilution_events']):
            try:a['dilution_events'][i]=number(value,'稀释比例')
            except ValueError:pass
    for key in ('cash_flows','interim_cash_flows'):
        if isinstance(a.get(key),list):
            for row in a[key]:
                if isinstance(row,dict):
                    converted=normalize({'entry_date':row.get('date'),'investment_amount':row.get('amount')})
                    if row.get('date') is not None:row['date']=converted['entry_date']
                    if row.get('amount') is not None:row['amount']=converted['investment_amount']
    return a


def discard_derived(a, text):
    """Keep drivers, not model-computed caches, as the next turn's inputs.

    Extractors sometimes return every mathematically derived field despite the
    extraction contract. A later profit/dilution edit must still reach Excel.
    A separately explicit final receipt/ownership remains a user input.
    """
    import copy
    a=copy.deepcopy(a)
    direct_receipt=re.search(r'(?:回收|收回|收到|回款|退出所得)(?:金额|现金|为|是|约|按|采用|共|计|\s)*[\d一二三四五六七八九十]',text)
    direct_equity=re.search(r'退出(?:股权)?(?:估值|价值)(?:为|是|约|按|采用|\s)*[\d一二三四五六七八九十]',text)
    if direct_receipt:
        for key in ('exit_net_income','exit_pe_multiple','exit_equity_value'):a.pop(key,None)
    elif direct_equity:
        for key in ('exit_net_income','exit_pe_multiple','exit_proceeds'):a.pop(key,None)
    elif a.get('exit_net_income') is not None and a.get('exit_pe_multiple') is not None:
        a.pop('exit_equity_value',None);a.pop('exit_proceeds',None)
    elif a.get('exit_equity_value') is not None and not direct_receipt:
        a.pop('exit_proceeds',None)
    explicit_entry=re.search(r'(?:进入|初始|投资后|交割后)(?:持股|股权比例)|买入.{0,8}%|买.{0,8}%.*股',text)
    explicit_exit=re.search(r'(?:最终|退出|稀释后)(?:持股|股权比例)',text)
    if explicit_entry and a.get('entry_ownership') is not None:
        a.pop('entry_equity_value',None);a.pop('entry_valuation_basis',None)
    if explicit_exit and a.get('exit_ownership') is not None:
        a.pop('ipo_dilution',None);a.pop('dilution_events',None)
    if a.get('entry_equity_value') is not None and a.get('investment_amount') is not None and a.get('entry_valuation_basis') in ('pre_money','post_money') and not explicit_entry:
        a.pop('entry_ownership',None)
    if a.get('exit_ownership') is not None and (a.get('ipo_dilution') is not None or a.get('dilution_events')) and not explicit_exit:
        a.pop('exit_ownership',None)
    explicit_exit_date=re.search(r'(?:退出|清算)(?:日|日期|时间|在|为|是|\s|：|:)*20\d{2}|20\d{2}(?:年[底末]|[-/年]\d{1,2}[-/月]\d{1,2}日?).{0,3}(?:退出|清算)',text)
    if a.get('holding_years') is not None and not explicit_exit_date:a.pop('exit_date',None)
    return a


def number(value, label, minimum=None, maximum=None):
    if isinstance(value, str):
        candidate = value.strip().replace('，', ',')
        if re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?', candidate):
            candidate = candidate.replace(',', '')
        if candidate.endswith(('%', '％')):
            if not re.search(r'持股|稀释|比例|ownership|dilution',label,re.I):raise ValueError(label+'是金额或倍数，不能使用百分比')
            value = float(candidate[:-1]) / 100
        else:
            candidate = re.sub(r'\s*(?:倍|[x×])$', '', candidate, flags=re.I)
            try: value = float(candidate)
            except ValueError: pass
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + '需要明确的数值')
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(label + '超出可计算范围')
    return float(value)


def dates(a):
    try: entry = date.fromisoformat(a.get('entry_date', ''))
    except (TypeError, ValueError): raise ValueError('请说明交割日期，例如2027年12月31日')
    if a.get('exit_date'):
        try: exit_ = date.fromisoformat(a['exit_date'])
        except (TypeError, ValueError): raise ValueError('请说明退出日期，或说交割后持有几年')
        if a.get('holding_years') is not None:
            expected=dates({**a,'exit_date':None})[1]
            if expected!=exit_:raise ValueError('退出日期与交割后持有年数不一致，请确认采用哪一个')
    else:
        years = number(a.get('holding_years'), '持有年数', .01, 100)
        if years != int(years):
            raise ValueError('非整数持有年限请说明退出日期，以便按实际日期计算IRR')
        try: exit_ = entry.replace(year=entry.year + int(years))
        except ValueError: exit_ = entry.replace(year=entry.year + int(years), day=28)
    if exit_ <= entry: raise ValueError('退出日期需要晚于交割日期')
    if (exit_ - entry).days > 36525: raise ValueError('持有期超过100年，请核对日期')
    return entry, exit_


def missing(a):
    keys = [k for k in ('currency', 'unit') if not a.get(k)]
    if a.get('cash_flows') is not None:
        return keys
    for key in ('entry_date', 'investment_amount'):
        if a.get(key) is None or a.get(key) == '': keys.append(key)
    if not a.get('exit_date') and a.get('holding_years') is None: keys.append('exit_date')
    if a.get('exit_proceeds') is None:
        if a.get('exit_equity_value') is None and (a.get('exit_net_income') is None or a.get('exit_pe_multiple') is None):
            keys.append('exit_net_income')
        if a.get('exit_ownership') is None and a.get('entry_ownership') is None:
            if a.get('entry_equity_value') is None: keys.append('entry_equity_value')
            elif a.get('entry_valuation_basis') not in ('pre_money', 'post_money'): keys.append('entry_valuation_basis')
    return keys


def clarification(a, error=None):
    from .clarifications import needs_input
    absent = missing(a)
    conflicts = a.get('source_conflicts')
    if not absent and not error and not conflicts:
        try: calculate(a)
        except ValueError as exc: error = str(exc)
    if not absent and not error and not conflicts: return None
    labels = list(dict.fromkeys(LABELS.get(key, key) for key in absent))
    message = str(error) if error else '；'.join(str(x)[:200] for x in conflicts[:2]) if isinstance(conflicts, list) and conflicts else '请补充：' + '、'.join(labels)
    return needs_input('还差一组关键条件，其他信息已保留。',
                      [{'id': 'investor_return_inputs', 'label': message,
                        'hint': '用自己的话一次补充即可；金额可写“亿美元/万美金”，日期可写“交割后5年”。'}],
                      purpose='valuation', method='investor_return', missing=absent,
                      assumptions=a, known_conditions=[])


def _flows(items, currency, unit):
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError('请提供1至100笔带日期的投资人现金流')
    result = []
    for item in items:
        if not isinstance(item, dict) or set(item) - {'date', 'amount', 'label', 'currency', 'unit'}:
            raise ValueError('现金流需要日期、金额和可选说明；投资为负，回收为正')
        if item.get('currency', currency) != currency or item.get('unit', unit) != unit:
            raise ValueError('现金流币种或单位不同，请先提供同一口径金额与明确汇率')
        try: day = date.fromisoformat(item.get('date', ''))
        except (TypeError, ValueError): raise ValueError('每笔追加投资或分红需要实际日期')
        amount = number(item.get('amount'), '现金流金额')
        result.append({'date': day.isoformat(), 'amount': amount, 'label': str(item.get('label') or '投资人现金流')[:160]})
    return result


def xirr(flows):
    # Conventional cash flows have a unique root; decline ambiguous sign patterns.
    grouped = {}
    for flow in flows: grouped[flow['date']] = grouped.get(flow['date'], 0) + flow['amount']
    rows = [(date.fromisoformat(day), amount) for day, amount in sorted(grouped.items()) if amount]
    if len(rows) < 2 or rows[0][1] >= 0 or not any(v > 0 for _, v in rows):
        raise ValueError('IRR需要先投入、后回收的不同日期现金流；全额损失可计算MOC，IRR无有限XIRR解')
    signs = [v > 0 for _, v in rows]
    if sum(signs[i] != signs[i - 1] for i in range(1, len(signs))) > 1:
        raise ValueError('现金流多次正负切换，可能有多个IRR；请确认现金流或另用明确收益率口径')
    origin = rows[0][0]
    times = [((day - origin).days / 365, amount) for day, amount in rows]
    def value(log_rate):
        # Scaled NPV prevents overflow near -100% without moving its root.
        exponents = [-t * log_rate for t, _ in times]
        scale = max(exponents)
        return sum(amount * math.exp(power - scale) for power, (_, amount) in zip(exponents, times))
    low, high = -36., 36.
    if value(low) * value(high) > 0: raise ValueError('IRR超出可验证范围，请核对现金流尺度')
    for _ in range(180):
        middle = (low + high) / 2
        if value(middle) > 0: low = middle
        else: high = middle
    return math.expm1((low + high) / 2)


def calculate(a):
    allowed={'currency','unit',*FIELDS,'as_of_date','scenario','notes'}
    if set(a)-allowed:raise ValueError('以下回报条件尚未对应到模型，请说明含义：'+'、'.join(sorted(set(a)-allowed)))
    absent = missing(a)
    if absent: raise ValueError('请补充：' + '、'.join(LABELS.get(k, k) for k in absent))
    currency, unit = a['currency'], a['unit']
    if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}',currency):
        raise ValueError('请明确金额币种，例如美元或人民币')
    if not isinstance(unit, str) or not re.fullmatch(r'元|千元|万元|百万元|亿元|万亿元|units?|thousands?|millions?|billions?', unit, re.I):
        raise ValueError('金额单位需要统一，例如百万美元；不会仅替换标签而不换算金额')
    equity = ownership = entry_ownership = proceeds = None
    if a.get('cash_flows') is not None:
        if a.get('interim_cash_flows') or any(a.get(k) is not None for k in ('investment_amount', 'exit_proceeds', 'exit_equity_value', 'exit_net_income')):
            raise ValueError('完整现金流表与进入/退出测算同时出现；请明确采用哪一组，避免重复计入')
        flows = _flows(a['cash_flows'], currency, unit)
        days = sorted(flow['date'] for flow in flows)
        entry, exit_ = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    else:
        entry, exit_ = dates(a)
        investment = number(a['investment_amount'], '投资金额', .000000001)
        if a.get('exit_proceeds') is not None:
            proceeds = number(a['exit_proceeds'], '投资人退出回收', 0)
        else:
            if a.get('exit_equity_value') is not None:
                equity = number(a['exit_equity_value'], '退出公司股权价值', 0)
            else:
                income = number(a['exit_net_income'], '退出净利润', 0)
                multiple = number(a['exit_pe_multiple'], '退出P/E', .000000001)
                equity = income * multiple
            if a.get('entry_ownership') is not None:
                entry_ownership = number(a['entry_ownership'], '进入持股', 0, 1)
            elif a.get('exit_ownership') is None:
                entry_equity = number(a['entry_equity_value'], '进入股权估值', .000000001)
                denominator = entry_equity + investment if a['entry_valuation_basis'] == 'pre_money' else entry_equity
                entry_ownership = investment / denominator
                if entry_ownership > 1: raise ValueError('投资金额超过投后股权估值，请核对金额单位或估值口径')
            events = a.get('dilution_events') or []
            if not isinstance(events, list) or len(events) > 20: raise ValueError('稀释事件最多20项，请提供比例列表')
            if a.get('ipo_dilution') is not None and events:
                raise ValueError('IPO稀释与稀释事件列表同时出现，请确认是否重复计入IPO')
            if a.get('exit_ownership') is not None:
                if events or a.get('ipo_dilution') not in (None, 0):
                    raise ValueError('已提供最终退出持股，同时又提供稀释，请确认最终持股已含哪些稀释')
                ownership = number(a['exit_ownership'], '退出持股', 0, 1)
            else:
                ownership = entry_ownership
                for value in events or [a.get('ipo_dilution', 0) or 0]:
                    ownership *= 1 - number(value, '稀释比例', 0, 1)
            proceeds = equity * ownership
        flows = [{'date': entry.isoformat(), 'amount': -investment, 'label': '初始投资'}]
        interim = _flows(a['interim_cash_flows'], currency, unit) if a.get('interim_cash_flows') else []
        if any(not entry.isoformat() <= row['date'] <= exit_.isoformat() for row in interim):
            raise ValueError('追加投资/分红日期需位于交割与退出之间')
        if any(row['amount'] < 0 for row in interim) and ownership is not None:
            raise ValueError('追加投资会改变持股，请提供含追加投资后的最终退出持股，或完整投资人现金流')
        flows += interim + [{'date': exit_.isoformat(), 'amount': proceeds, 'label': '退出回收'}]
    flows.sort(key=lambda row: row['date'])
    invested = -sum(row['amount'] for row in flows if row['amount'] < 0)
    received = sum(row['amount'] for row in flows if row['amount'] > 0)
    if not math.isfinite(invested) or not math.isfinite(received):raise ValueError('金额尺度超过可计算范围，请核对单位与倍数')
    if invested <= 0: raise ValueError('需要至少一笔投资支出')
    if received == 0:
        irr = None
    else: irr = xirr(flows)
    return {'method': 'investor_return', 'method_label': '投资回报 · MOC / IRR',
            'currency': currency, 'unit': unit, 'entry_date': entry.isoformat(), 'exit_date': exit_.isoformat(),
            'holding_days': (exit_ - entry).days, 'total_invested': invested, 'total_received': received,
            'moic': received / invested, 'moc': received / invested, 'irr': irr,
            'entry_ownership': entry_ownership, 'exit_ownership': ownership,
            'exit_equity_value': equity, 'exit_proceeds': proceeds, 'cash_flows': flows,
            'formula': 'MOC=投资人全部回收÷全部投入；IRR=带实际日期的投资人现金流XIRR。P/E对应公司股权价值，乘稀释后持股得到我们的回收。',
            'warning': '按已提供的现金流/稀释测算；未提供的分红、费用、税、优先权及汇率未计入。原文件公式缓存未重算。' + (' 全额损失，XIRR无有限解。' if irr is None else '')}


def summary(result, sources=()):
    r=result
    lines=['# 投资回报测算', '',
           f"MOC / MOIC：{r['moic']:.2f}×；IRR：{r['irr']:.1%}。" if r['irr'] is not None else f"MOC：{r['moic']:.2f}×；IRR无有限XIRR解。",
           f"累计投入 {r['total_invested']:,.2f}，累计回收 {r['total_received']:,.2f}（{r['currency']} / {r['unit']}）。",
           f"交割 {r['entry_date']}；退出 {r['exit_date']}。",'',
           '| 日期 | 投资人现金流 | 事项 |','| --- | ---: | --- |']
    lines.extend(f"| {f['date']} | {f['amount']:,.2f} | {f['label'].replace('|','/')} |" for f in r['cash_flows'])
    lines += ['',r['formula'],r['warning'],'','计算：'+str(r.get('calculation_engine') or '独立现金流校验')]
    if sources:
        lines+=['','## 本轮资料范围']
        lines.extend('['+s['id']+'] '+s['name']+'；SHA256 '+s['hash']+('（部分读取）' if s['truncated'] else '') for s in sources)
    return '\n'.join(lines)
