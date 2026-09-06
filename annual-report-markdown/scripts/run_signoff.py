#!/usr/bin/env python3
"""人工签核入口（对应设计方案 §12 证据 / §13.4 复核队列）。

在矢量轮廓数字（或其他待复核对象）的“多通道一致性判定 + 人工签核”机制中，
本工具负责记录人工决定。记录写入 `复核/signoffs.jsonl`（追加，保留历史，不覆盖）。

诚实边界：
  - 只有 decision=accepted 且 --eligible 时，下游才算开放计算资格。
  - 本工具不做数值识别，只记录人工决定与旧候选的替换关系；重跑不得抹掉人工签核。

用法:
  run_signoff.py record <研究包> --object-ref <ref> --decision accepted|rejected|deferred|pending
                     [--reviewer <人>] [--note <说明>] [--eligible] [--run-id <id>]
  run_signoff.py list <研究包>
  run_signoff.py summary <研究包>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import evidence_chain  # noqa: E402

_SIGN_OFF_REL = "复核/signoffs.jsonl"


def _path(pkg: Path) -> Path:
    return pkg / _SIGN_OFF_REL


def _cmd_record(pkg: Path, args) -> int:
    record = evidence_chain.new_signoff(
        args.object_ref,
        reviewer=args.reviewer,
        decision=args.decision,
        note=args.note,
        eligible_for_calculation=args.eligible,
        replace_sign_off_id=args.replace_sign_off_id,
        run_id=args.run_id,
    )
    evidence_chain.write_signoffs(_path(pkg), [record])
    print(f"已记录签核 {record['sign_off_id']}")
    print(f"  object_ref : {record['object_ref']}")
    print(f"  decision   : {record['decision']} · eligible={record['eligible_for_calculation']}")
    print(f"  recorded_at: {record['recorded_at']}")
    return 0


def _cmd_list(pkg: Path, args) -> int:
    records = evidence_chain.load_signoffs(_path(pkg))
    if not records:
        print("（尚无签核记录）")
        return 0
    for r in records:
        print(f"{r.get('sign_off_id')} · {r.get('decision')} · {r.get('object_ref')} · {r.get('reviewer') or '-'}")
    return 0


def _cmd_summary(pkg: Path, args) -> int:
    records = evidence_chain.load_signoffs(_path(pkg))
    agg = evidence_chain.aggregate_signoffs(records)
    print(f"签核总数     : {agg['total']}")
    print(f"按决定       : {agg['by_decision']}")
    print(f"可计算       : {agg['eligible_for_calculation']}")
    print(f"待签核       : {agg['pending']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="记录一条人工签核")
    rec.add_argument("package_dir")
    rec.add_argument("--object-ref", required=True, help="复核对象引用（如 复核/vector-outline-index.json#vector-statement-01）")
    rec.add_argument("--decision", required=True, choices=evidence_chain.SIGN_OFF_DECISIONS)
    rec.add_argument("--reviewer")
    rec.add_argument("--note")
    rec.add_argument("--eligible", action="store_true", help="仅 accepted 时开放计算资格")
    rec.add_argument("--replace-sign-off-id")
    rec.add_argument("--run-id")
    rec.set_defaults(func=_cmd_record)

    lst = sub.add_parser("list", help="列出已记录签核")
    lst.add_argument("package_dir")
    lst.set_defaults(func=_cmd_list)

    summary = sub.add_parser("summary", help="签核汇总")
    summary.add_argument("package_dir")
    summary.set_defaults(func=_cmd_summary)

    args = ap.parse_args(argv)
    pkg = Path(args.package_dir)
    if not pkg.is_dir():
        print("ERROR: 研究包目录不存在:", pkg)
        return 2
    return args.func(pkg, args)


if __name__ == "__main__":
    sys.exit(main())
