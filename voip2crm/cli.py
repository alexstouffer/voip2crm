#!/usr/bin/env python3
"""Google Voice -> WhisperX -> CRM pipeline.

Examples:
  python run.py --once                  # process new calls, then exit
  python run.py --once --dry-run        # same, but force the local CRM (no external pushes)
  python run.py --watch --interval 300  # poll every 5 minutes
  python run.py --once --limit 5        # cap how many messages to look at
  python run.py --once --reprocess      # ignore the processed-state db (re-do everything)
  python run.py --once --no-transcribe  # skip WhisperX, use Google's email transcript
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from voip2crm.config import Config
from voip2crm.pipeline import Pipeline


def main() -> int:
    p = argparse.ArgumentParser(description="Google Voice call -> CRM pipeline")
    p.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Process new calls once and exit")
    mode.add_argument("--watch", action="store_true", help="Poll continuously")
    p.add_argument("--interval", type=int, default=300, help="Seconds between polls in --watch")
    p.add_argument("--limit", type=int, default=None, help="Max messages to consider")
    p.add_argument("--dry-run", action="store_true", help="Force the local CRM adapter")
    p.add_argument("--reprocess", action="store_true", help="Ignore processed-state db")
    p.add_argument("--no-transcribe", action="store_true", help="Skip WhisperX")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = Config.load(args.config)
    pipe = Pipeline(cfg, dry_run=args.dry_run, skip_transcribe=args.no_transcribe)
    try:
        if args.once:
            pipe.run_once(limit=args.limit, reprocess=args.reprocess)
        else:
            logging.getLogger("voip2crm").info("Watching; Ctrl-C to stop.")
            while True:
                pipe.run_once(limit=args.limit, reprocess=args.reprocess)
                time.sleep(max(30, args.interval))
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        pipe.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
