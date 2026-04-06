import importlib
import logging
import os
import time
import sys
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [roi-eval] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RUN_HOUR = int(os.environ.get("ROI_EVAL_HOUR", "23"))
RUN_MINUTE = int(os.environ.get("ROI_EVAL_MINUTE", "50"))


def next_run_time() -> datetime:
    now = datetime.now()
    target = now.replace(hour=RUN_HOUR, minute=RUN_MINUTE, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def run_eval() -> None:
    try:
        roi_eval = importlib.import_module("src.lpr.roi_eval")
        argv = [
            "roi_eval.py",
            "--detect-dir",
            os.environ.get("DETECT_DIR", "/Detect"),
            "--roi-dir",
            os.environ.get("ROI_DIR", "/ROI"),
            "--module-yml",
            os.environ.get("MODULE_YML", "/module/module.yml"),
            "--days",
            os.environ.get("ROI_DAYS", "3"),
        ]

        if os.environ.get("AUTO_APPLY_ROI", "false").lower() == "true":
            argv.append("--auto-apply")

            # Optional container restart if you wire it up via docker sdk.
            # This scheduler does not enforce restart; roi_eval handles it if provided.
        sys.argv = argv
        roi_eval.main()
    except Exception:
        log.exception("ROI evaluation failed")


def main() -> None:
    log.info("ROI eval scheduler started — will run daily at %02d:%02d", RUN_HOUR, RUN_MINUTE)

    # Run once immediately so you always have a baseline report.
    run_eval()

    while True:
        target = next_run_time()
        wait_sec = (target - datetime.now()).total_seconds()
        log.info("Next evaluation scheduled at %s (in %.2fh)", target.strftime("%Y-%m-%d %H:%M:%S"), wait_sec / 3600.0)
        time.sleep(max(wait_sec, 1))
        run_eval()


if __name__ == "__main__":
    main()

