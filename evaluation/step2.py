"""
Step 2 evaluation: version 2 of the question writer, on the same 10 trials as step 1.

Changes from version 1:
  - ClinicalTrials.gov's hidden formatting backslashes are removed before the
    criteria are sent to Claude, checked, or shown.
  - Age, sex and healthy-volunteer questions come from the study's structured
    listing fields, written by code with no AI.
  - New prompt (prompts/eligibility_questions_v2.md): keep every qualifier, never
    widen a criterion, explain every medical term, phrase ranges the easy way.
  - Contradictory or unclear criteria go to a "needs human review" file.
  - At most 10 questions per trial in total, enforced by code.

Usage (from the project folder):
  .venv/bin/python evaluation/step2.py run      # ask both models (costs money)
  .venv/bin/python evaluation/step2.py report   # rebuild the CSVs from saved answers (free)

Outputs in evaluation/:
  step2_trials.json        the same 10 trials, plus their structured listing fields
  step2_raw.json           every model answer as received
  step2.csv                one row per kept question, same columns as step1.csv
  step2_needs_review.csv   criteria Claude flagged for a person to check
  step2_dropped.csv        questions removed, and why
  step2_runs.csv           time, tokens and cost for each model call
"""

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from step1 import (  # noqa: E402
    EVAL_DIR, MODELS, EFFORT, TRIALS_FILE as STEP1_TRIALS_FILE, T2D_RE,
    BULLET_RE, unescape, grade, write_csv, call_cost, load_env_file,
)

ROOT = EVAL_DIR.parent
NAME = "step2"  # prefix for every output file; step3.py changes it
TRIALS_FILE = EVAL_DIR / "step2_trials.json"
RAW_FILE = EVAL_DIR / "step2_raw.json"
PROMPT = (ROOT / "prompts" / "eligibility_questions_v2.md").read_text()
SCHEMA = json.loads((ROOT / "prompts" / "eligibility_questions_v2.schema.json").read_text())

MAX_QUESTIONS = 10
BUDGET_USD = 1.00  # the owner asked to be told first if a run would cost more than $1
LISTING = "study listing (no AI)"

OTHER_DIABETES_RE = re.compile(r"type\s*(1|i)\b|t1d|gestational|pre-?diabet", re.I)


# ---------- Trials and listing fields ----------

def load_trials():
    """Same trials and criteria text as step 1, plus structured fields fetched once."""
    if TRIALS_FILE.exists():
        return json.loads(TRIALS_FILE.read_text())
    trials = json.loads(STEP1_TRIALS_FILE.read_text())
    ids = ",".join(t["nct_id"] for t in trials)
    url = f"https://clinicaltrials.gov/api/v2/studies?filter.ids={ids}&fields=NCTId,EligibilityModule&pageSize=20"
    with urllib.request.urlopen(url, timeout=30) as resp:
        studies = json.load(resp)["studies"]
    fields = {}
    for s in studies:
        e = s["protocolSection"]["eligibilityModule"]
        fields[s["protocolSection"]["identificationModule"]["nctId"]] = {
            "minimum_age": e.get("minimumAge"),
            "maximum_age": e.get("maximumAge"),
            "sex": e.get("sex", "ALL"),
            "healthy_volunteers": e.get("healthyVolunteers"),
        }
    for t in trials:
        t["listing"] = fields[t["nct_id"]]
    TRIALS_FILE.write_text(json.dumps(trials, indent=2, ensure_ascii=False))
    return trials


def listing_questions(trial):
    """Age, sex and healthy-volunteer questions written by code from the listing fields."""
    f, questions = trial["listing"], []

    lo, hi = f["minimum_age"], f["maximum_age"]
    if lo or hi:
        lo_n, lo_u = lo.split() if lo else (None, None)
        hi_n, hi_u = hi.split() if hi else (None, None)
        if lo and hi and lo_u == hi_u:
            q = f"Are you between {lo_n} and {hi_n} {hi_u.lower()} old?"
        elif lo and hi:
            q = f"Are you at least {lo.lower()} old and {hi.lower()} old or younger?"
        elif lo:
            q = f"Are you {lo.lower()} old or older?"
        else:
            q = f"Are you {hi.lower()} old or younger?"
        source = " · ".join(filter(None, [lo and f"Minimum Age: {lo}", hi and f"Maximum Age: {hi}"]))
        questions.append({"question": q, "original_text": source, "criterion_type": "inclusion",
                          "counts_against": "no", "sensitive_topic": False})

    if f["sex"] in ("FEMALE", "MALE"):
        sex = f["sex"].lower()
        questions.append({"question": f"Is your sex {sex}?",
                          "original_text": f"Sexes Eligible for Study: {sex.capitalize()}",
                          "criterion_type": "inclusion", "counts_against": "no", "sensitive_topic": False})

    # A trial that doesn't accept healthy volunteers needs people who have its condition.
    # Only ask about type 2 diabetes when no other kind of diabetes or prediabetes is listed,
    # so nobody with a different listed condition is wrongly ruled out.
    conditions = trial["conditions"]
    other = [c for c in conditions if OTHER_DIABETES_RE.search(c) or ("diabet" in c.lower() and not T2D_RE.search(c))]
    if f["healthy_volunteers"] is False and any(T2D_RE.search(c) for c in conditions) and not other:
        questions.append({
            "question": "Do you have type 2 diabetes (a long-term condition where your body has trouble using insulin, so your blood sugar runs high)?",
            "original_text": "Accepts Healthy Volunteers: No · Conditions: " + ", ".join(conditions),
            "criterion_type": "inclusion", "counts_against": "no", "sensitive_topic": False,
        })
    return questions


# ---------- Asking the models ----------

def user_message(trial, fixed):
    f = trial["listing"]
    already = "\n".join(f"- {q['question']}" for q in fixed) or "- (none)"
    return (
        f"Trial {trial['nct_id']}: {trial['title']}\n\n"
        "Structured listing fields:\n"
        f"- Minimum age: {f['minimum_age'] or 'not listed'}\n"
        f"- Maximum age: {f['maximum_age'] or 'not listed'}\n"
        f"- Sex: {f['sex']}\n"
        f"- Accepts healthy volunteers: {'yes' if f['healthy_volunteers'] else 'no'}\n"
        f"- Conditions: {', '.join(trial['conditions'])}\n\n"
        f"The website already asks these questions, so don't repeat them:\n{already}\n\n"
        f"Write at most {MAX_QUESTIONS - len(fixed)} questions.\n\n"
        f"<eligibility_criteria>\n{unescape(trial['criteria'])}\n</eligibility_criteria>"
    )


def run_models():
    import anthropic

    load_env_file()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("No API key found. Add ANTHROPIC_API_KEY to the .env file first.")

    trials = load_trials()
    raw = json.loads(RAW_FILE.read_text()) if RAW_FILE.exists() else {}
    spent = sum(r.get("cost_usd", 0) for r in raw.values())
    client = anthropic.Anthropic()

    for trial in trials:
        fixed = listing_questions(trial)
        for model in MODELS.values():
            key = f"{trial['nct_id']}|{model}"
            if key in raw and "error" not in raw[key]:
                continue
            if spent >= BUDGET_USD - 0.10:
                print(f"Stopping: ${spent:.2f} spent, close to the ${BUDGET_USD:.2f} limit.")
                save_raw(raw)
                return

            started = time.time()
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=16000,
                    system=PROMPT,
                    output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
                    messages=[{"role": "user", "content": user_message(trial, fixed)}],
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
            record = {"nct_id": trial["nct_id"], "model": model, "seconds": round(seconds, 1),
                      **usage, "cost_usd": round(cost, 4), "stop_reason": response.stop_reason}
            if response.stop_reason == "end_turn":
                text = next(b.text for b in response.content if b.type == "text")
                record.update(json.loads(text))
            raw[key] = record
            save_raw(raw)
            print(f"{key}: {seconds:.0f}s, ${cost:.3f} (total ${spent:.2f}), stop={response.stop_reason}")

    save_raw(raw)
    print(f"Done. Total spent on {NAME}: ${spent:.2f}")


def save_raw(raw):
    RAW_FILE.write_text(json.dumps(raw, indent=2, ensure_ascii=False))


# ---------- Quote check and reports ----------

def normalize(text):
    """Ignore spacing, line breaks, bullet characters and ClinicalTrials.gov's hidden backslashes."""
    return re.sub(r"\s+", " ", BULLET_RE.sub("", unescape(text).strip())).strip()


def check_quote(quote, criteria):
    import difflib
    q, original = normalize(quote), normalize(criteria)
    if not q:
        return False, "no original text given"
    if q in original:
        return True, ""
    matcher = difflib.SequenceMatcher(None, q, original, autojunk=False)
    found = sum(block.size for block in matcher.get_matching_blocks()) / len(q)
    if found >= 0.9:
        return False, f"small wording or punctuation change ({found:.0%} of the quote matches)"
    if found >= 0.6:
        return False, f"reworded or stitched together ({found:.0%} of the quote matches)"
    return False, f"not found in the criteria ({found:.0%} of the quote matches)"


def section_of(quote, criteria):
    original = normalize(criteria).lower()
    pos = original.find(normalize(quote).lower())
    split = original.find("exclusion criteria")
    if pos < 0 or split < 0:
        return "unknown"
    return "exclusion" if pos > split else "inclusion"


def question_row(trial_id, model, number, q, section_check):
    return {
        "trial_id": trial_id,
        "model": model,
        "question_number": number,
        "original_text": q["original_text"],
        "ai_question": q["question"],
        "answer_that_counts_against": q["counts_against"],
        "criterion_type": q["criterion_type"],
        "section_check": section_check,
        "sensitive_topic": "yes" if q["sensitive_topic"] else "no",
        "grade_level_original": grade(q["original_text"]),
        "grade_level_question": grade(q["question"]),
        "accurate (Y/N)": "",
        "easy to understand (Y/N)": "",
        "correct answer counts against (Y/N)": "",
        "notes": "",
    }


def build_reports():
    trials = load_trials()
    raw = json.loads(RAW_FILE.read_text())
    order = [t["nct_id"] for t in trials]

    kept_rows, review_rows, dropped_rows, run_rows = [], [], [], []
    for trial in trials:
        fixed = listing_questions(trial)
        for i, q in enumerate(fixed, 1):
            kept_rows.append(question_row(trial["nct_id"], LISTING, i, q, "ok"))

        for model in MODELS.values():
            record = raw.get(f"{trial['nct_id']}|{model}")
            if not record or "error" in record:
                continue
            criteria = trial["criteria"]
            questions = record.get("questions", [])
            limit = MAX_QUESTIONS - len(fixed)
            kept = 0
            seen = {q["question"].strip().lower() for q in fixed}

            for q in questions:
                ok, reason = check_quote(q["original_text"], criteria)
                if ok and q["question"].strip().lower() in seen:
                    ok, reason = False, "duplicate question"
                if ok and kept >= limit:
                    ok, reason = False, f"over the {MAX_QUESTIONS}-question limit"
                if not ok:
                    dropped_rows.append({"trial_id": trial["nct_id"], "model": model,
                                         "ai_question": q["question"], "original_text_given": q["original_text"],
                                         "reason_dropped": reason})
                    continue
                seen.add(q["question"].strip().lower())
                kept += 1
                section = section_of(q["original_text"], criteria)
                check = "ok" if section in ("unknown", q["criterion_type"]) else f"MISMATCH: quote is under {section}"
                kept_rows.append(question_row(trial["nct_id"], model, len(fixed) + kept, q, check))

            for item in record.get("needs_human_review", []):
                ok, reason = check_quote(item["original_text"], criteria)
                review_rows.append({"trial_id": trial["nct_id"], "model": model,
                                    "original_text": item["original_text"], "problem": item["problem"],
                                    "quote_check": "ok" if ok else reason,
                                    "agree it needs review (Y/N)": "", "notes": ""})

            run_rows.append({
                "trial_id": trial["nct_id"], "model": model, "criteria_characters": len(criteria),
                "seconds": record["seconds"], "input_tokens": record["input_tokens"],
                "output_tokens": record["output_tokens"], "cost_usd": record["cost_usd"],
                "stop_reason": record["stop_reason"], "listing_questions": len(fixed),
                "ai_questions_returned": len(questions), "ai_questions_kept": kept,
                "ai_questions_dropped": len(questions) - kept,
                "total_questions": len(fixed) + kept,
                "needs_review_items": len(record.get("needs_human_review", [])),
            })

    sort_key = lambda r: (order.index(r["trial_id"]), r["model"] != LISTING, r["model"])
    write_csv(EVAL_DIR / f"{NAME}.csv", sorted(kept_rows, key=sort_key))
    write_csv(EVAL_DIR / f"{NAME}_needs_review.csv", sorted(review_rows, key=sort_key))
    write_csv(EVAL_DIR / f"{NAME}_dropped.csv", sorted(dropped_rows, key=sort_key))
    write_csv(EVAL_DIR / f"{NAME}_runs.csv", sorted(run_rows, key=sort_key))
    summarize(kept_rows, review_rows, dropped_rows, run_rows)


def summarize(kept_rows, review_rows, dropped_rows, run_rows):
    listing = [r for r in kept_rows if r["model"] == LISTING]
    print(f"\nQuestions from the study listing (no AI): {len(listing)}")
    for model in MODELS.values():
        runs = [r for r in run_rows if r["model"] == model]
        if not runs:
            continue
        kept = [r for r in kept_rows if r["model"] == model]
        dropped = [r for r in dropped_rows if r["model"] == model]
        seconds = sorted(r["seconds"] for r in runs)
        cost = sum(r["cost_usd"] for r in runs)
        print(f"\n{model}: {len(runs)} trials")
        print(f"  cost: ${cost:.2f} total, ${cost / len(runs):.3f} per trial")
        print(f"  time: {seconds[0]:.0f}s fastest, {seconds[len(seconds) // 2]:.0f}s middle, {seconds[-1]:.0f}s slowest")
        print(f"  AI questions kept: {len(kept)}, dropped: {len(dropped)}")
        for d in dropped:
            print(f"    dropped ({d['trial_id']}): {d['reason_dropped']}")
        print(f"  total questions per trial: {[r['total_questions'] for r in runs]}")
        print(f"  needs human review: {sum(r['model'] == model for r in review_rows)}")
        grades = [r["grade_level_question"] for r in kept if r["grade_level_question"] != ""]
        if grades:
            print(f"  average question grade level: {sum(grades) / len(grades):.1f}")
        mismatches = [r for r in kept if r["section_check"] != "ok"]
        if mismatches:
            print(f"  inclusion/exclusion label mismatches: {len(mismatches)}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "run":
        run_models()
        build_reports()
    elif command == "report":
        build_reports()
    else:
        sys.exit(__doc__)
