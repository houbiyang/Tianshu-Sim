"""Replay supply, firing geometry and public prose guards without altering evidence.

python tools/qa/replay_consistency.py --export <saved-export.json> --out <new-directory>
No model calls. Old invalid shots are classified as rejected, never auto-rotated.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gentactix import adjudicator, battlefield, fire_control, reporting


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', dest='source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    raw = args.source.read_bytes()
    payload = json.loads(raw)
    args.out.mkdir(parents=True, exist_ok=True)
    records, fire_counts, report_counts = [], Counter(), Counter()
    path_count = 0
    for trace in payload['trajectory']:
        state = trace['after']
        before = trace.get('before')
        turn = trace['turn']
        adj = adjudicator.adjudicate(state, trace['time_step'], turn, trace.get('params'))
        view = battlefield.build_battlefield_view(state, adj)
        kernel_paths = [p for row in adj['units'] for p in row['supply_paths']]
        if view['supply_lines'] != kernel_paths:
            raise AssertionError(f'T{turn}: renderer/kernel route mismatch')
        path_count += len(kernel_paths)
        fire = fire_control.check(state, before)
        text, audit = reporting.render(trace.get('report'), state, before,
                                       turn=turn, adjudication=adj)
        fire_counts.update(i['code'] for i in fire)
        report_counts.update(i['code'] for i in audit['issues'])
        entry = {'turn': turn, 'fire_issues': fire, 'report_audit': audit,
                 'report': text, 'supply_paths': kernel_paths}
        (args.out / f'turn-{turn:03d}.json').write_text(json.dumps(entry, ensure_ascii=False, indent=2))
        records.append({'turn': turn, 'fire_status': 'rejected' if fire else 'verified',
                        'report_status': audit['status'], 'report_codes': [i['code'] for i in audit['issues']]})
    assert args.source.read_bytes() == raw, 'Original evidence must remain unchanged'
    summary = {'source_sha256': hashlib.sha256(raw).hexdigest(), 'original_unchanged': True,
               'model_calls': False, 'adjudicator': adjudicator.ADJUDICATOR_VERSION,
               'snapshots': len(records), 'identical_map_kernel_paths': path_count,
               'fire_issues': dict(fire_counts), 'report_issues': dict(report_counts),
               'records': records}
    (args.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != 'records'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
