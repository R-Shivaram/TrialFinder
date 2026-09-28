"""
Step 1 evaluation: compare Claude Opus 5 and Claude Sonnet 5 at turning
eligibility criteria into yes/no screening questions, on the same 10
recruiting US type 2 diabetes trials.

Usage (from the project folder):
  .venv/bin/python evaluation/step1.py select   # pick the 10 trials (free)
  .venv/bin/python evaluation/step1.py run      # ask both models (costs money)
  .venv/bin/python evaluation/step1.py report   # rebuild the CSVs from saved answers (free)

Outputs in evaluation/:
  step1_trials.json   the 10 trials and their full original criteria
  step1_raw.json      every model answer as received, so reports can be rebuilt for free
  step1.csv           one row per kept question, with empty review columns
  step1_dropped.csv   questions removed by the quote check, and why
  step1_runs.csv      time, tokens and cost for each model call
"""

import csv
import difflib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "evaluation"
TRIALS_FILE = EVAL_DIR / "step1_trials.json"
RAW_FILE = EVAL_DIR / "step1_raw.json"
PROMPT = (ROOT / "prompts" / "eligibility_questions.md").read_text()
SCHEMA = json.loads((ROOT / "prompts" / "eligibility_questions.schema.json").read_text())

MODELS = {"opus": "claude-opus-5", "sonnet": "claude-sonnet-5"}
PRICE_PER_MILLION = {  # (input, output) in US dollars
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
}
EFFORT = "medium"
BUDGET_USD = 3.00  # hard stop, leaves room in the $5 credit
NUM_TRIALS = 10

T2D_RE = re.compile(r"type\s*(2|ii)\s*diabet|\bt2d", re.I)


# ---------- Picking trials ----------

def fetch_candidate_trials():
    """All recruiting type 2 diabetes trials with at least one US site."""
    studies, token = [], None
    while True:
        params = {
            "query.cond": "type 2 diabetes",
            "query.locn": "United States",
            "filter.overallStatus": "RECRUITING",
            "fields": "NCTId,BriefTitle,Condition,EligibilityCriteria,LocationCountry",
            "pageSize": "100",
        }
        if token:
            params["pageToken"] = token
        url = "https://clinicaltrials.gov/api/v2/studies?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)
        studies += data.get("studies", [])
        token = data.get("nextPageToken")
        if not token:
            break

    trials = []
    for s in studies:
        p = s.get("protocolSection", {})
        conditions = p.get("conditionsModule", {}).get("conditions", [])
        criteria = p.get("eligibilityModule", {}).get("eligibilityCriteria", "")
        countries = {loc.get("country") for loc in p.get("contactsLocationsModule", {}).get("locations", [])}
        if criteria.strip() and "United States" in countries and any(T2D_RE.search(c) for c in conditions):
            trials.append({
                "nct_id": p["identificationModule"]["nctId"],
                "title": p["identificationModule"].get("briefTitle", ""),
                "conditions": conditions,
                "criteria": criteria,
            })
    return trials


def select_trials():
    trials = fetch_candidate_trials()
    # Spread picks from shortest to longest criteria so both easy and hard cases are tested.
    trials.sort(key=lambda t: (len(t["criteria"]), t["nct_id"]))
    n = len(trials)
    picked = [trials[int((i + 0.5) * n / NUM_TRIALS)] for i in range(NUM_TRIALS)]
    TRIALS_FILE.write_text(json.dumps(picked, indent=2, ensure_ascii=False))
    print(f"{n} eligible trials found. Picked {len(picked)}:")
    for t in picked:
        print(f"  {t['nct_id']}  {len(t['criteria']):>5} characters  {t['title'][:70]}")


# ---------- Asking the models ----------

def load_env_file():
    """Read ANTHROPIC_API_KEY from the project's .env file (never uploaded to GitHub)."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def call_cost(model, usage):
    price_in, price_out = PRICE_PER_MILLION[model]
    return usage["input_tokens"] * price_in / 1e6 + usage["output_tokens"] * price_out / 1e6


def run_models():
    import anthropic

    load_env_file()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("No API key found. Add ANTHROPIC_API_KEY to the .env file first.")

    trials = json.loads(TRIALS_FILE.read_text())
    raw = json.loads(RAW_FILE.read_text()) if RAW_FILE.exists() else {}
    spent = sum(r.get("cost_usd", 0) for r in raw.values())
    client = anthropic.Anthropic()

    # Alternate models trial by trial so both get the same trials if the budget runs out.
    for trial in trials:
        for label, model in MODELS.items():
            key = f"{trial['nct_id']}|{model}"
            if key in raw and "error" not in raw[key]:
                continue  # already done; don't pay twice
            if spent >= BUDGET_USD - 0.30:
                print(f"Stopping: ${spent:.2f} spent, close to the ${BUDGET_USD:.2f} limit.")
                save_raw(raw)
                return

            user_message = (
                f"Trial {trial['nct_id']}: {trial['title']}\n\n"
                f"<eligibility_criteria>\n{trial['criteria']}\n</eligibility_criteria>"
            )
            started = time.time()
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=16000,
                    system=PROMPT,
                    output_config={
                        "effort": EFFORT,
                        "format": {"type": "json_schema", "schema": SCHEMA},
                    },
                    messages=[{"role": "user", "content": user_message}],
                )
            except anthropic.AuthenticationError:
                sys.exit("The API key was rejected. Check the key in the .env file.")
            except anthropic.RateLimitError as e:
                raw[key] = {"error": f"rate limited: {e.message}"}
                print(f"{key}: rate limited, will retry on next run")
                continue
            except anthropic.APIStatusError as e:
                raw[key] = {"error": f"API error {e.status_code}: {e.message}"}
                print(f"{key}: API error {e.status_code}")
                continue
            except anthropic.APIConnectionError:
                raw[key] = {"error": "connection error"}
                print(f"{key}: connection error")
                continue

            seconds = time.time() - started
            usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
            cost = call_cost(model, usage)
            spent += cost
            record = {
                "nct_id": trial["nct_id"], "model": model, "seconds": round(seconds, 1),
                **usage, "cost_usd": round(cost, 4), "stop_reason": response.stop_reason,
            }
            if response.stop_reason == "end_turn":
                text = next(b.text for b in response.content if b.type == "text")
                record["questions"] = json.loads(text)["questions"]
            raw[key] = record
            save_raw(raw)
            print(f"{key}: {seconds:.0f}s, ${cost:.3f} (total ${spent:.2f}), stop={response.stop_reason}")

    save_raw(raw)
    print(f"Done. Total spent: ${spent:.2f}")


def save_raw(raw):
    RAW_FILE.write_text(json.dumps(raw, indent=2, ensure_ascii=False))


# ---------- Quote check and reports ----------

# A bullet character at the start of a line: * • ◦ ▪ ‣ · ● ○ – -
BULLET_RE = re.compile(r"^[ \t]*[*•◦▪‣·●○–-][ \t]+", re.M)


def normalize(text):
    """Ignore differences in spacing, line breaks and bullet characters."""
    return re.sub(r"\s+", " ", BULLET_RE.sub("", text.strip())).strip()


def unescape(text):
    """Remove the backslashes ClinicalTrials.gov stores before symbols like < > ( ) *."""
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!<>|~])", r"\1", text)


def check_quote(quote, criteria):
    """Returns (ok, reason). Reason explains a failure in plain words."""
    q, original = normalize(quote), normalize(criteria)
    if not q:
        return False, "no original text given"
    if q in original:
        return True, ""
    # ClinicalTrials.gov stores "\<" and "1\)" but displays "<" and "1)".
    if q in unescape(original):
        return False, "only difference: left out ClinicalTrials.gov's hidden formatting backslashes"
    matcher = difflib.SequenceMatcher(None, q, original, autojunk=False)
    found = sum(block.size for block in matcher.get_matching_blocks()) / len(q)
    if found >= 0.9:
        return False, f"small wording or punctuation change ({found:.0%} of the quote matches)"
    if found >= 0.6:
        return False, f"reworded or stitched together ({found:.0%} of the quote matches)"
    return False, f"not found in the criteria ({found:.0%} of the quote matches)"


def section_of(quote, criteria):
    """Whether the quote sits under 'Inclusion' or 'Exclusion' in the original."""
    original = normalize(criteria).lower()
    pos = original.find(normalize(quote).lower())
    split = original.find("exclusion criteria")
    if pos < 0 or split < 0:
        return "unknown"
    return "exclusion" if pos > split else "inclusion"


def grade(text):
    import textstat
    return round(textstat.flesch_kincaid_grade(text), 1) if text.strip() else ""


def build_reports():
    trials = {t["nct_id"]: t for t in json.loads(TRIALS_FILE.read_text())}
    raw = json.loads(RAW_FILE.read_text())

    kept_rows, dropped_rows, run_rows = [], [], []
    for record in raw.values():
        if "error" in record:
            continue
        criteria = trials[record["nct_id"]]["criteria"]
        questions = record.get("questions", [])
        kept, seen = 0, set()
        for q in questions:
            ok, reason = check_quote(q["original_text"], criteria)
            if ok and (q["question"].strip().lower() in seen):
                ok, reason = False, "duplicate question"
            if not ok:
                dropped_rows.append({
                    "trial_id": record["nct_id"], "model": record["model"],
                    "ai_question": q["question"], "original_text_given": q["original_text"],
                    "reason_dropped": reason,
                })
                continue
            seen.add(q["question"].strip().lower())
            kept += 1
            section = section_of(q["original_text"], criteria)
            kept_rows.append({
                "trial_id": record["nct_id"],
                "model": record["model"],
                "question_number": kept,
                "original_text": q["original_text"],
                "ai_question": q["question"],
                "answer_that_counts_against": q["counts_against"],
                "criterion_type": q["criterion_type"],
                "section_check": "ok" if section in ("unknown", q["criterion_type"]) else f"MISMATCH: quote is under {section}",
                "sensitive_topic": "yes" if q["sensitive_topic"] else "no",
                "grade_level_original": grade(q["original_text"]),
                "grade_level_question": grade(q["question"]),
                "accurate (Y/N)": "",
                "easy to understand (Y/N)": "",
                "correct answer counts against (Y/N)": "",
                "notes": "",
            })
        run_rows.append({
            "trial_id": record["nct_id"], "model": record["model"],
            "criteria_characters": len(criteria), "seconds": record["seconds"],
            "input_tokens": record["input_tokens"], "output_tokens": record["output_tokens"],
            "cost_usd": record["cost_usd"], "stop_reason": record["stop_reason"],
            "questions_returned": len(questions), "questions_kept": kept,
            "questions_dropped": len(questions) - kept,
            "count_in_5_to_10_range": "yes" if 5 <= kept <= 10 else "no",
        })

    order = list(trials)
    sort_key = lambda r: (order.index(r["trial_id"]), r["model"])
    write_csv(EVAL_DIR / "step1.csv", sorted(kept_rows, key=sort_key))
    write_csv(EVAL_DIR / "step1_dropped.csv", sorted(dropped_rows, key=sort_key))
    write_csv(EVAL_DIR / "step1_runs.csv", sorted(run_rows, key=sort_key))
    summarize(kept_rows, dropped_rows, run_rows)


def write_csv(path, rows):
    if not rows:
        path.write_text("")
        return
    # utf-8-sig so Excel and Numbers show symbols like ≤ correctly
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize(kept_rows, dropped_rows, run_rows):
    for model in MODELS.values():
        runs = [r for r in run_rows if r["model"] == model]
        if not runs:
            continue
        kept = [r for r in kept_rows if r["model"] == model]
        dropped = [r for r in dropped_rows if r["model"] == model]
        seconds = sorted(r["seconds"] for r in runs)
        q_grades = [r["grade_level_question"] for r in kept if r["grade_level_question"] != ""]
        o_grades = [r["grade_level_original"] for r in kept if r["grade_level_original"] != ""]
        print(f"\n{model}: {len(runs)} trials")
        print(f"  cost: ${sum(r['cost_usd'] for r in runs):.2f} total, ${sum(r['cost_usd'] for r in runs) / len(runs):.3f} per trial")
        print(f"  time: {seconds[0]:.0f}s fastest, {seconds[len(seconds) // 2]:.0f}s middle, {seconds[-1]:.0f}s slowest")
        print(f"  questions kept: {len(kept)}, dropped: {len(dropped)}")
        for d in dropped:
            print(f"    dropped ({d['trial_id']}): {d['reason_dropped']}")
        if q_grades:
            print(f"  average grade level: original {sum(o_grades) / len(o_grades):.1f}, question {sum(q_grades) / len(q_grades):.1f}")
        mismatches = [r for r in kept if r["section_check"] != "ok"]
        if mismatches:
            print(f"  inclusion/exclusion label mismatches: {len(mismatches)}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "select":
        select_trials()
    elif command == "run":
        run_models()
        build_reports()
    elif command == "report":
        build_reports()
    else:
        sys.exit(__doc__)
