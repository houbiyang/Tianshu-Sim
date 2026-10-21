"""Audit a real campaign export and its native DSH evidence without model calls."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', dest='export_path', type=Path, required=True)
    parser.add_argument('--dsh-home', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--turns', type=int, default=20)
    args = parser.parse_args()
    os.environ['GENTACTIX_DSH_HOME'] = str(args.dsh_home.resolve())
    from backend.analysis_routes import verify_dsh_records
    from gentactix import integrity, causal
    value = json.loads(args.export_path.read_text())
    json.dumps(value, allow_nan=False)
    assert value['turn_count'] == args.turns
    records = value['trajectory']
    assert [r['turn'] for r in records] == list(range(args.turns + 1))
    rows = []
    native_tools = {}
    for index, record in enumerate(records):
        after = record['after']
        assert after['simulation_clock']['elapsed_seconds'] == index * 900
        for team in integrity.UNIT_TEAMS:
            names = [integrity.unit_name(unit) for unit in after[team]]
            assert all(names) and len(set(names)) == len(names), 'Unnamed or duplicate unit'
        if index:
            before = record['before']
            assert before == records[index - 1]['after'], 'Discontinuous world snapshot'
            assert causal.clock_check(before, after, record['time_step'])['status'] == 'verified'
            reconciled, roster = integrity.reconcile_roster(before, after)
            assert reconciled == after, 'Unaccounted unit or field loss in committed state'
        checks = verify_dsh_records(record, flush_timeout=0)
        assert checks and all(row['status'] == 'verified' for row in checks), 'Native evidence incomplete'
        for run in record['runs']:
            assert run['finish_reason'] == 'completed'
            for event in run['events']:
                if event.get('type') == 'tool/call':
                    name = event.get('name', 'unknown')
                    native_tools[name] = native_tools.get(name, 0) + 1
        guards = record.get('guards', {})
        rows.append({'turn': index, 'time': after['current_time'],
                     'roster_count': sum(len(after[t]) for t in integrity.UNIT_TEAMS),
                     'trace_runs': len(checks), 'guards': guards,
                     'personnel_records': sum(isinstance(u.get('personnel'), dict) for t in integrity.UNIT_TEAMS for u in after[t]),
                     'equipment_records': sum(bool(u.get('equipment')) for t in integrity.UNIT_TEAMS for u in after[t])})
    assert value['war_state'] == records[-1]['after']
    report = {'passed': True, 'turns': args.turns, 'elapsed_seconds': args.turns * 900,
              'time_before': records[0]['after']['current_time'], 'time_after': value['war_state']['current_time'],
              'native_tools': native_tools, 'rows': rows,
              'warning_turns': [r['turn'] for r in rows if r['guards'].get('numeric_flags') or r['guards'].get('movement_violations')]}
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
