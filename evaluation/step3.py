"""
Step 3 evaluation: version 3 of the question writer, Claude Opus 5 only, on 10 NEW
type 2 diabetes trials that were not used in steps 1 and 2 (a held-out test).

Version 3 adds three rules to the version 2 prompt (prompts/eligibility_questions_v3.md):
  - each question stays strictly within its quoted original text, and never
    combines two criteria;
  - "except" / "unless" lists must be included in the question;
  - every required part of a criterion is covered, in separate questions if needed.

Everything else (listing questions without AI, backslash removal, quote check,
10-question limit, output format) is the same as step 2.

Usage (from the project folder):
  .venv/bin/python evaluation/step3.py select   # pick 10 new trials (free)
  .venv/bin/python evaluation/step3.py run      # ask Opus (costs money)
  .venv/bin/python evaluation/step3.py report   # rebuild the CSVs from saved answers (free)
"""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import step1  # noqa: E402
import step2 as base  # noqa: E402

base.NAME = "step3"
base.TRIALS_FILE = base.EVAL_DIR / "step3_trials.json"
base.RAW_FILE = base.EVAL_DIR / "step3_raw.json"
base.PROMPT = (base.ROOT / "prompts" / "eligibility_questions_v3.md").read_text()
base.MODELS = {"opus": "claude-opus-5"}


def select_trials():
    """Pick 10 new trials the same way as step 1, skipping every trial used before."""
    used = {t["nct_id"] for t in json.loads(step1.TRIALS_FILE.read_text())}
    trials = [t for t in step1.fetch_candidate_trials() if t["nct_id"] not in used]
    trials.sort(key=lambda t: (len(t["criteria"]), t["nct_id"]))
    n = len(trials)
    picked = [trials[int((i + 0.5) * n / step1.NUM_TRIALS)] for i in range(step1.NUM_TRIALS)]

    ids = ",".join(t["nct_id"] for t in picked)
    url = f"https://clinicaltrials.gov/api/v2/studies?filter.ids={ids}&fields=NCTId,EligibilityModule&pageSize=20"
    with urllib.request.urlopen(url, timeout=30) as resp:
        studies = json.load(resp)["studies"]
    for s in studies:
        e = s["protocolSection"]["eligibilityModule"]
        nct_id = s["protocolSection"]["identificationModule"]["nctId"]
        next(t for t in picked if t["nct_id"] == nct_id)["listing"] = {
            "minimum_age": e.get("minimumAge"),
            "maximum_age": e.get("maximumAge"),
            "sex": e.get("sex", "ALL"),
            "healthy_volunteers": e.get("healthyVolunteers"),
        }

    base.TRIALS_FILE.write_text(json.dumps(picked, indent=2, ensure_ascii=False))
    print(f"{n} unused eligible trials found. Picked {len(picked)}:")
    for t in picked:
        print(f"  {t['nct_id']}  {len(t['criteria']):>5} characters  {t['title'][:70]}")


def load_trials():
    if not base.TRIALS_FILE.exists():
        sys.exit("Pick the trials first: .venv/bin/python evaluation/step3.py select")
    return json.loads(base.TRIALS_FILE.read_text())


base.load_trials = load_trials

if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "select":
        select_trials()
    elif command == "run":
        base.run_models()
        base.build_reports()
    elif command == "report":
        base.build_reports()
    else:
        sys.exit(__doc__)
