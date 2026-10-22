"""Validate declared engagement geometry, without inventing orientation or LOS."""
import math
from .supply import coord, objects
from .adjudicator import bearing_deg, distance_km


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def check(state, previous=None):
    issues = []
    units = [(team, u) for team in ('red', 'blue') for u in objects(state.get(team + '_units'))]
    old = {(team, u.get('name')): u for team in ('red', 'blue')
           for u in objects((previous or {}).get(team + '_units'))}
    for index, event in enumerate(objects(state.get('engagements'))):
        name = event.get('attacker')
        candidates = [(team, u) for team, u in units if u.get('name') == name
                      and (not event.get('attacker_team') or event['attacker_team'] == team)]
        def flag(code, message):
            issues.append({'code': code, 'event': index, 'attacker': name,
                           'message': f'交战{index + 1}（{name or "攻击者未记录"}）：{message}'})
        if len(candidates) != 1:
            flag('unknown_attacker', '攻击者缺失或重名，无法验证射界')
            continue
        team, unit = candidates[0]
        prior = old.get((team, name), {})
        start = coord(event.get('attacker_pos'))
        target = coord([event.get('lat'), event.get('lng')])
        if start is None or target is None:
            flag('invalid_position', '缺少有效射击起点或目标坐标')
            continue
        known = [coord([u.get('lat'), u.get('lng')]) for u in (unit, prior)]
        if not any(p is not None and distance_km(start, p) <= .05 for p in known):
            flag('unverified_origin', '射击起点不在本回合起止位置50米内；需明确可验证的射击位置')
        if number(unit.get('hp')) is not None and unit['hp'] <= 0 and not prior:
            flag('inactive_attacker', '无可用攻击单位')
        distance = distance_km(start, target) * 1000
        maximum = number(unit.get('attack_range'))
        minimum = number(unit.get('min_attack_range', 0))
        if maximum is not None and (maximum <= 0 or distance > maximum + 1):
            flag('range_exceeded', f'目标距离{distance:.1f}米，超出最大射程{maximum:g}米')
        if minimum is not None and distance + 1 < minimum:
            flag('inside_minimum_range', '目标处于最小射程以内')
        sector = unit.get('fire_sector')
        if sector is None:
            sector = prior.get('fire_sector')
        if not isinstance(sector, dict):
            continue  # No declared orientation means unknown, never infer one from enemies.
        bearing, arc = number(sector.get('bearing_deg')), number(sector.get('arc_deg'))
        if bearing is None or arc is None or not 0 < arc <= 360:
            flag('invalid_sector', '射界角度无效')
            continue
        prior_sector = prior.get('fire_sector')
        if prior_sector and sector != prior_sector:
            change = unit.get('fire_sector_change')
            if prior_sector.get('fixed') or not isinstance(change, dict) or not str(change.get('reason') or '').strip():
                flag('undeclared_reorientation', '射界改变须记录fire_sector_change.reason；固定射界不可自动旋转')
        direction = bearing_deg(start, target)
        delta = abs((direction - bearing + 180) % 360 - 180)
        if distance > 1 and delta > arc / 2 + .01:
            flag('outside_sector', f'实际射向{direction:.2f}°超出声明射界{bearing:g}°±{arc / 2:g}°；'
                 '请纠正交战事件，或在有依据且允许转向时显式更新射界并记录转向原因')
    return issues


def errors(candidate, previous=None):
    return [issue['message'] for issue in check(candidate, previous)]


def enforce(candidate, previous=None):
    failures = errors(candidate, previous)
    if failures:
        raise ValueError('射击几何校验失败，本回合未提交：' + '；'.join(failures))
