"""Command line entry point.

    python -m regextract locate  --procedure UKA [--registries NJR LROI] [--year 2025]
    python -m regextract confirm [--all | REF ...]
    python -m regextract extract --procedure UKA [--xlsx] [--no-ocr]
    python -m regextract run     --procedure UKA [--auto-confirm] [--xlsx]
"""
from __future__ import annotations

import argparse
import sys

from . import pipeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="regextract", description="Registry annual-report table extraction")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--procedure", default="UKA", help="UKA (default) or any procedure named in the configs, e.g. TKA")
        p.add_argument("--registries", nargs="*", help="subset of registries, e.g. NJR AOANJRR (default: all)")
        p.add_argument("--year", type=int, help="report year (default: latest config per registry)")

    common(sub.add_parser("locate", help="find table pages and propose them in config/page_map.yaml"))
    c = sub.add_parser("confirm", help="mark proposed pages as confirmed (the checkpoint)")
    c.add_argument("refs", nargs="*", help="e.g. NJR_2025/uka_brand (default: all proposed)")
    c.add_argument("--all", action="store_true")
    for name in ("extract", "run"):
        p = sub.add_parser(name, help="extract confirmed tables" if name == "extract" else "locate + extract")
        common(p)
        p.add_argument("--xlsx", action="store_true", help="also write an Excel workbook")
        p.add_argument("--no-ocr", action="store_true", help="skip OCR (use manual/ copies only)")
        p.add_argument("--auto-confirm", action="store_true", help="use proposed pages without confirming")
        p.add_argument("--outputs", nargs="*", choices=["device", "casemix"],
                       help="table kinds to build (default: both)")

    a = ap.parse_args(argv)
    if a.cmd == "locate":
        pipeline.cmd_locate(a.procedure, a.registries, a.year)
        print("\nCheck the pages listed in config/page_map.yaml / outputs/locate_report.csv, then run "
              "`python -m regextract confirm`.")
    elif a.cmd == "confirm":
        pipeline.cmd_confirm(a.refs or None)
    else:
        if a.cmd == "run":
            pipeline.cmd_locate(a.procedure, a.registries, a.year)
        res = pipeline.cmd_extract(a.procedure, a.registries, a.year, auto_confirm=a.auto_confirm,
                                   xlsx=a.xlsx, run_ocr=not a.no_ocr, outputs=a.outputs)
        n_err = sum(i["severity"] == "error" for i in res["issues"])
        print(f"\n{len(res['long'])} device long rows ({len(res['devices'])} devices), "
              f"{len(res['casemix'])} case-mix long rows -> outputs/{a.procedure}/")
        print(f"validation: {n_err} errors (see outputs/{a.procedure}/validation_report.csv)")
        return 1 if n_err else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
