# -*- coding: utf-8 -*-
"""🧱 单位名单与数值守恒守卫（纯标准库，不 import streamlit/folium）。

## 为什么需要它

`app.py` 里 `st.session_state.war_state = updated_data` 是**整体替换**，
不是合并。模型某一回合漏返回一个单位，下一回合的 prompt 里就没有这个名字 ——
它永远补不回来。实测（measure_turn_degradation.py）：2% 漏返回率跑 50 回合，
12 个单位只剩 3 个。这不是模型变笨，是状态被重写后无人对账。

## 两条设计原则

**1. 回填 ≠ 修正坐标。** 位移越界时裁决器只报告、不拉回坐标，因为硬拉会
制造一个新的错误状态（把部队拖进河里）。而回填取消的是一次**不该发生的删除**，
恢复的内容原封不动取自上一回合快照，不引入任何新信息。两者的区别是：
一个是在编造状态，一个是在阻止状态被悄悄抹掉。

**2. 歼灭必须有明确通道。** 如果只是「缺失就回填」，被歼灭的单位会每回合
被复活，变成不死之身 —— 那比丢失更糟。所以合法消失只有两条路：
  - 新状态顶层 `eliminated` 数组里点名（prompt 已强制要求模型这么做）；
  - 上一回合该单位 hp 已经 <= 0（防御性放行）。
其余一律视为漏返回，回填并报告。

数值则**只报告不修正**：hp 上涨可能是合法增援，也可能是模型漏减，
系统没有资格替用户判断哪一种。
"""

import copy

UNIT_TEAMS = ('red_units', 'blue_units')

# hp 上涨超过 1% 才提示，以下算四舍五入噪音（8000 → 8001 不值得打扰用户）
HP_GAIN_TOLERANCE = 0.01

_NAME_KEYS = ('name', '名称', '单位名称', '部队名称', 'unit_name')
_HP_KEYS = ('hp', 'HP', '兵力', 'health', '生命值', '血量')


def unit_name(unit):
    """取单位名（覆盖中文键），空名返回空串。空名单位不参与名单核对。"""
    if not isinstance(unit, dict):
        return ''
    for key in _NAME_KEYS:
        value = unit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def unit_hp(unit):
    """取 hp，非数值返回 None（None 表示「不知道」，不是 0）。"""
    if not isinstance(unit, dict):
        return None
    for key in _HP_KEYS:
        if key in unit:
            try:
                value = float(unit[key])
            except (TypeError, ValueError):
                return None
            return value if value == value else None  # 过滤 NaN
    return None


def _team_name(team):
    return '红' if team == 'red_units' else '蓝'


def _declared_eliminated(new_state):
    """读取顶层 eliminated 声明（容忍字符串列表或对象列表）。"""
    names = set()
    raw = new_state.get('eliminated')
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                names.add(item.strip())
            elif isinstance(item, dict):
                names.add(unit_name(item))
    names.discard('')
    return names


def reconcile_roster(prev_state, new_state):
    """核对单位名单，把「无理由消失」的单位从上一回合快照回填。

    原地修改 `new_state` 的 red_units / blue_units，并返回报告。
    幂等：对已经回填过的状态再跑一次，recovered 为空。
    """
    report = {
        'recovered': [],      # 被回填的单位
        'eliminated': [],     # 合法消失（有声明或 hp 已归零）
        'suspected': [],      # 消失且上一回合 hp 很低，提示人眼复核
        'unnamed': 0,         # 空名单位数（无法参与核对）
        'duplicate': [],      # 同一方重名
        'prev_total': 0,
        'new_total': 0,
        'structure_missing': [],  # 整条 red_units / blue_units 缺失
        'note': '',
    }
    if not isinstance(prev_state, dict) or not isinstance(new_state, dict):
        report['note'] = '状态不是字典，跳过名单核对'
        return new_state, report

    declared = _declared_eliminated(new_state)

    for team in UNIT_TEAMS:
        prev_units = prev_state.get(team)
        new_units = new_state.get(team)

        if not isinstance(prev_units, list):
            prev_units = []
        if not isinstance(new_units, list):
            # 模型把整条列表弄丢了：当成空列表继续，后面会全部回填
            if prev_units:
                report['structure_missing'].append(f'{_team_name(team)}方单位列表缺失')
            new_units = []
            new_state[team] = new_units

        present = {}
        for unit in new_units:
            name = unit_name(unit)
            if not name:
                report['unnamed'] += 1
                continue
            if name in present:
                report['duplicate'].append(f'{_team_name(team)}/{name}')
            present[name] = unit
            report['new_total'] += 1

        for prev_unit in prev_units:
            name = unit_name(prev_unit)
            if not name:
                report['unnamed'] += 1
                continue
            report['prev_total'] += 1
            if name in present:
                continue

            prev_hp = unit_hp(prev_unit)
            if name in declared:
                report['eliminated'].append(
                    {'team': team, 'name': name, 'why': 'eliminated 列表内声明'})
                continue
            if prev_hp is not None and prev_hp <= 0:
                report['eliminated'].append(
                    {'team': team, 'name': name, 'why': '上一回合 hp 已归零'})
                continue

            # 无理由消失 ⇒ 判定为漏返回，按上一回合原样回填
            new_units.append(copy.deepcopy(prev_unit))
            present[name] = new_units[-1]
            report['new_total'] += 1
            entry = {'team': team, 'name': name, 'hp': prev_hp}
            if prev_hp is not None and prev_hp <= 10:
                entry['why'] = '上一回合 hp 已极低（<=10），也可能确实被歼'
                report['suspected'].append(entry)
            report['recovered'].append(entry)

    return new_state, report


def check_numeric_integrity(prev_state, new_state, hp_gain_tolerance=HP_GAIN_TOLERANCE):
    """数值守恒检查：只报告，不修正。

    三类问题：
    - `hp_gain`：hp 上涨。可能是增援/整补（合法），也可能是模型漏减。
    - `hp_zero_alive`：hp 归零但仍在列表里。地图照样画满编军标，视觉上错。
    - `hp_negative`：hp 为负，一定是错的。
    """
    flags = []
    if not isinstance(prev_state, dict) or not isinstance(new_state, dict):
        return flags

    for team in UNIT_TEAMS:
        prev_units = prev_state.get(team)
        new_units = new_state.get(team)
        if not isinstance(prev_units, list) or not isinstance(new_units, list):
            continue

        prev_index = {}
        for unit in prev_units:
            name = unit_name(unit)
            if name:
                prev_index[name] = unit

        for unit in new_units:
            name = unit_name(unit)
            if not name:
                continue
            new_hp = unit_hp(unit)
            if new_hp is None:
                continue

            if new_hp < 0:
                flags.append({
                    'kind': 'hp_negative', 'team': team, 'name': name,
                    'prev': unit_hp(prev_index.get(name)), 'new': new_hp,
                })
                continue

            if new_hp == 0:
                flags.append({
                    'kind': 'hp_zero_alive', 'team': team, 'name': name,
                    'prev': unit_hp(prev_index.get(name)), 'new': 0,
                })
                continue

            prev_unit = prev_index.get(name)
            if prev_unit is None:
                continue
            prev_hp = unit_hp(prev_unit)
            if prev_hp is None:
                continue

            delta = new_hp - prev_hp
            threshold = max(1.0, prev_hp * hp_gain_tolerance)
            if delta > threshold:
                flags.append({
                    'kind': 'hp_gain', 'team': team, 'name': name,
                    'prev': prev_hp, 'new': new_hp, 'delta': delta,
                    'ratio': (delta / prev_hp) if prev_hp else None,
                })

    return flags


def summarize(report, flags):
    """压成一行摘要，供界面/日志用。"""
    parts = []
    if report:
        if report['recovered']:
            parts.append(f"回填 {len(report['recovered'])} 个单位")
        if report['eliminated']:
            parts.append(f"确认歼灭 {len(report['eliminated'])} 个")
        if report['structure_missing']:
            parts.append(f"结构缺失 {len(report['structure_missing'])} 处")
        if report['duplicate']:
            parts.append(f"重名 {len(report['duplicate'])} 个")
    if flags:
        gains = [f for f in flags if f['kind'] == 'hp_gain']
        zeros = [f for f in flags if f['kind'] == 'hp_zero_alive']
        negs = [f for f in flags if f['kind'] == 'hp_negative']
        if gains:
            parts.append(f"hp 上涨 {len(gains)} 处")
        if zeros:
            parts.append(f"hp 归零未移除 {len(zeros)} 处")
        if negs:
            parts.append(f"hp 为负 {len(negs)} 处")
    return ' · '.join(parts) if parts else '名单与数值均无异常'
