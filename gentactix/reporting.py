"""Program-owned factual summaries and conservative public-narrative guards.

These checks cover known failure classes, not general natural-language truth.
Rejected prose stays in the audit record; the world and original model event do
not change. Supply is an index, committed is cumulative input, HP is not people.
"""
import re
from . import sim_clock
from .supply import objects

INSTRUCTION = """【战报事实约束】正文服从本回合结构化状态。current_time是唯一当前时刻；
personnel.current是现有人数，committed是累计投入而非核定编制，不据此称满编；HP不是人数。
supply是0—100补给指数，不是弹药发数、粮水库存或可开火次数；0不等于已证实弹尽。
engagements是本回合射击记录，不得同时称相关单位整轮停火。未建立完整事件检索时，
不得声称“今天第一次”“唯一变化”“历史最高”。包围采样比例不等于实际道路条数。
几何补给通断不等于实际运达或库存补充。复盘中旧战报是待核验叙述，当前结构化账本优先。
"""

_DIGITS = dict(zip('零一二三四五六七八九', range(10)), 两=2)


def _cn_number(text):
    if text.isdigit():
        return int(text)
    if '十' in text:
        a, b = text.split('十', 1)
        return (_DIGITS.get(a, 1) if a else 1) * 10 + _DIGITS.get(b, 0)
    return _DIGITS.get(text)


def _times(text):
    for match in re.finditer(r'(?<!\d)([0-2]?\d)[:：]([0-5]\d)(?!\d)', text):
        yield int(match[1]), int(match[2])
    # Bare “一点补给/三点建议” is not a clock. Require an explicit minute suffix.
    for match in re.finditer(r'([零一二两三四五六七八九十\d]{1,3})[点时](一刻|三刻|半|整|[零一二两三四五六七八九十\d]{1,3}分)', text):
        hour = _cn_number(match[1])
        suffix = match[2] or ''
        minute = {'一刻': 15, '三刻': 45, '半': 30, '整': 0, '': 0}.get(suffix)
        if minute is None:
            minute = _cn_number(suffix.rstrip('分'))
        if hour is not None and minute is not None and hour < 24 and minute < 60:
            yield hour, minute


def inspect(text, state, previous=None, *, review=False):
    issues = []
    # Do not claim exhaustive semantic verification; deny unsupported quantities.
    patterns = (
        ('strength_claim', r'满编|全员健在|毫发无损|快没人|没有能走路的人', '编制/行动能力断言缺少对应账本；以现有人数为准'),
        ('history_superlative', r'(?:今天|今日|本日|有史以来).{0,8}(?:第一次|首次|最高)|唯一.{0,4}变化', '首次/唯一/最高断言未经完整历史检索'),
        ('inventory_claim', r'(?:弹药|粮食|粮弹|坑道储备|囤弹|库存).{0,16}(?:归零|降到个位|耗尽|已满|打光|最高|够一次)|弹尽粮绝|满供|无弹|开火权', '补给指数不能代替弹药、粮水、库存、运达量或开火权限'),
        ('geometry_claim', r'几何内核.{0,24}(?:给出|写死|证明)|(?:内核|系统)(?:已)?(?:计算|算出).{0,12}(?:弹药|运量|视线|命中|杀伤)', '模型对内核能力的转述未经验证；以程序几何摘要为准'),
        ('direction_count', r'[零一二两三四五六七八九十\d]+个方向|退路只剩[一二两三四五六七八九十\d]+条', '采样方向比例不能转换为实际道路条数'),
        ('fire_denial', r'停火|不打(?:[，。；、]|$)|不能打枪', '停火断言需事件级时间范围，不能与本轮射击记录混用'),
    )
    for code, pattern, message in patterns:
        if re.search(pattern, text) and (code != 'fire_denial' or objects(state.get('engagements'))):
            issues.append({'code': code, 'message': message})
    end = sim_clock.parse_timestamp(state.get('current_time'))
    start = sim_clock.parse_timestamp((previous or {}).get('current_time')) or end
    if end and not review:
        for match in re.finditer(r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?', text):
            stamp = sim_clock.parse_timestamp(match[0])
            if stamp is not None and not start <= stamp <= end:
                issues.append({'code': 'narrative_clock', 'message': '正文日期时刻不在本回合时间窗'})
        for hour, minute in _times(text):
            minute_of_day = hour * 60 + minute
            if end.date() == start.date():
                valid = start.hour * 60 + start.minute <= minute_of_day <= end.hour * 60 + end.minute
            else:
                valid = True  # Multi-day prose needs date-aware semantic review, not guessing a day.
            if not valid:
                issues.append({'code': 'narrative_clock', 'message': f'正文时刻{hour:02}:{minute:02}不在本回合时间窗'})
    return issues


def _value(value):
    return str(value) if value is not None else '未记录'


def facts(state, previous=None, adjudication=None):
    old = {(t, u.get('name')): u for t in ('red', 'blue') for u in objects((previous or {}).get(t + '_units'))}
    geometry = {(r.get('team'), r.get('name')): r for r in objects((adjudication or {}).get('units'))}
    lines = ['**状态核对摘要（程序生成）**', '']
    for team in ('red', 'blue'):
        for u in objects(state.get(team + '_units')):
            p = u.get('personnel') if isinstance(u.get('personnel'), dict) else {}
            prior = old.get((team, u.get('name')), {}).get('personnel') or {}
            current, prior_count = p.get('current'), prior.get('current')
            change = f'{current - prior_count:+g}' if all(isinstance(v, (int, float)) for v in (current, prior_count)) else '未记录'
            name = str(u.get('name') or '未命名').replace('|', '／').replace('\n', ' ')
            lines.append(f"- **{name}**：现有/累计投入 {_value(current)}/{_value(p.get('committed'))}人；"
                         f"累计战斗/非战斗减员 {_value(p.get('casualties'))}/{_value(p.get('noncombat_losses'))}人；"
                         f"本步人数变化 {change}。HP {_value(u.get('hp'))}，补给指数 {_value(u.get('supply'))}。")
    firing = sorted({str(e.get('attacker')) for e in objects(state.get('engagements')) if e.get('attacker')})
    lines += ['', f"本轮记录交战{len(objects(state.get('engagements')))}次；攻击单位：{'、'.join(firing) or '无记录'}。"]
    if geometry:
        lines.append('几何补给连通：' + '；'.join(f"{name} {r.get('supply', '未知')}"
                    for (_, name), r in geometry.items()) + '。')
    lines.append('人数按战局账本；累计投入不是核定编制。HP与补给为指数，不能换算人数或弹药库存；几何连通不表示实际运达。')
    return '\n'.join(lines)


def render(text, state, previous=None, *, turn=None, adjudication=None, review=False):
    raw = str(text or '')
    body = re.sub(r'^\s*【T\+\d+[^】]*】\s*', '', raw)
    issues = inspect(body, state, previous, review=review)
    label = re.match(r'^\s*【T\+(\d+)\s*\|\s*([^】]+)】', raw)
    if label and turn is not None and (int(label[1]) != turn or label[2].strip() != state.get('current_time')):
        issues.append({'code': 'report_header', 'message': '模型标题回合/时刻错误，已由程序时钟重建'})
    retained = body
    if issues:
        if review:
            # Keep recommendations, but remove whole conflicting sentences rather
            # than guess names/pronouns and replace numbers inside the model's prose.
            sentences = re.split(r'(?<=[。！？\n])', body)
            retained = ''.join(s for s in sentences if not inspect(s, state, previous, review=True)).strip()
        else:
            retained = ''
    heading = (f"【T+{turn} | {state.get('current_time', '时间未记录')}】" if turn is not None
               else f"【状态时点 | {state.get('current_time', '时间未记录')}】")
    parts = [heading]
    if issues:
        parts.append('原模型文字触发事实校验，冲突内容未作为战报事实展示；原文与校验原因已留在审计记录。')
    if retained:
        label = '教学规则结算（参数未史实标定）' if state.get('mission') and not review else '模型解读（仍需核验）'
        parts.append('**' + label + '**\n\n' + retained)
    parts.append(facts(state, previous, adjudication))
    return '\n\n'.join(parts), {'version': 1, 'status': 'fallback' if issues else 'screened',
                                'issues': issues, 'original_text': raw,
                                'scope': '已知错误模式筛查；非通用语义证明'}
