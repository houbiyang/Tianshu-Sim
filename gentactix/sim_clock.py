"""Program-owned simulation clock; pure standard library, no guessed historical dates."""
import copy
from datetime import datetime, timedelta
import re


class ClockViolation(ValueError):
    pass


def parse_timestamp(value):
    text = str(value or '').strip()
    # BCE and relative expressions cannot be converted by datetime reliably.
    if re.search(r'公元前|前\d+年|之前|之后|拂晓|黎明|某日|某年', text):
        return None
    match = re.fullmatch(r'(?:公元)?(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})日?\s*[T ]?\s*'
                         r'(凌晨|早晨|上午|中午|下午|傍晚|晚上|夜间)?\s*'
                         r'(\d{1,2})(?:[:：](\d{2})(?::(\d{2}))?|[时点](?:(\d{1,2})分?)?)', text)
    if not match:
        return None
    year, month, day, period, hour, minute, second, cn_minute = match.groups()
    hour = int(hour)
    if period in ('下午', '傍晚', '晚上', '中午') and hour < 12:
        hour += 12
    if period == '凌晨' and hour == 12:
        hour = 0
    try:
        return datetime(int(year), int(month), int(day), hour, int(minute or cn_minute or 0), int(second or 0))
    except ValueError:
        return None


def duration_seconds(label):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(分钟|分|小时|钟头|天|日|星期|周|个月|月)\s*', str(label))
    if not match:
        raise ClockViolation('回合跨度须为明确正数，例如15分钟、6小时、1天')
    scales = {'分钟': 60, '分': 60, '小时': 3600, '钟头': 3600, '天': 86400,
              '日': 86400, '星期': 604800, '周': 604800, '个月': 2592000, '月': 2592000}
    seconds = float(match[1]) * scales[match[2]]
    if not 0 < seconds <= 315360000 or not seconds.is_integer():
        raise ClockViolation('回合跨度须精确到秒且大于零、不超过十年；月按30天计算')
    return int(seconds)


def _clock(state):
    stored = (state or {}).get('simulation_clock')
    if isinstance(stored, dict) and stored.get('version') == 1:
        return copy.deepcopy(stored)
    label = str((state or {}).get('current_time') or '未定起点')
    stamp = parse_timestamp(label)
    return {'version': 1, 'origin_label': label,
            'origin_iso': stamp.isoformat() if stamp else None, 'elapsed_seconds': 0}


def _label(clock):
    if clock['origin_iso']:
        stamp = datetime.fromisoformat(clock['origin_iso']) + timedelta(seconds=clock['elapsed_seconds'])
        return stamp.strftime('%Y-%m-%d %H:%M:%S' if stamp.second else '%Y-%m-%d %H:%M')
    minutes = clock['elapsed_seconds'] / 60
    return f"{clock['origin_label']} · T+{minutes:g}分钟"


def initialize(candidate):
    # The model cannot supply or overwrite the authoritative elapsed counter.
    clock = _clock({'current_time': candidate.get('current_time')})
    candidate['simulation_clock'] = clock
    candidate['current_time'] = _label(clock)
    return clock


def target(before, time_step):
    clock = _clock(before)
    clock['elapsed_seconds'] += duration_seconds(time_step)
    return _label(clock), clock


def enforce_step(before, candidate, time_step):
    expected, clock = target(before, time_step)
    declared = str(candidate.get('current_time') or '')
    stamp = parse_timestamp(declared)
    expected_stamp = parse_timestamp(expected)
    if not (declared == expected or (stamp and expected_stamp and stamp == expected_stamp)):
        raise ClockViolation(f'时间推进冲突：本回合{time_step}，必须返回current_time={expected!r}，实际为{declared!r}；候选战局未提交')
    candidate['current_time'] = expected
    candidate['simulation_clock'] = clock
    return {'status': 'verified', 'source': 'program_clock',
            'expected_minutes': duration_seconds(time_step) / 60,
            'actual_minutes': duration_seconds(time_step) / 60}


def instruction(before, time_step):
    expected, _ = target(before, time_step)
    return (f'\n【程序时钟硬约束】本回合结束current_time必须逐字为{expected!r}。'
            f'所有行动、战报和损耗只允许覆盖本回合{time_step}；禁止扩展到更晚事件。'
            'simulation_clock由程序维护，不得自行修改。日期不明确时T+为从起点累计的推演分钟。\n')
