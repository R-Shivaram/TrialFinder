"""
Make the eligibility checklists that the website shows, ahead of time.

Uses the version 3 prompt with Claude Opus 5 (the owner's choice after steps 1-3),
through the Batch API (half price; answers take minutes instead of seconds).
Only this script spends money. The website just reads checklist-data/checklists.json,
so visitors can never cause any spending.

Usage (from the project folder):
  .venv/bin/python tools/generate_checklists.py plan
      Find the trials and estimate the cost. Free. Prints a plan ID.
  .venv/bin/python tools/generate_checklists.py generate --approve PLAN_ID
      Spend money, only for a plan the owner approved, and only if the worst
      possible cost fits under SPENDING_CAP_USD.
  .venv/bin/python tools/generate_checklists.py build
      Turn saved answers into the website's data file and a review sheet. Free.
"""

import argparse
import copy
import datetime
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evaluation"))
import step2  # noqa: E402  (shared helpers: quote check, prompt message)
from step1 import T2D_RE, grade, load_env_file, write_csv  # noqa: E402

DATA_DIR = ROOT / "tools" / "data"
PLAN_FILE = DATA_DIR / "plan.json"
RAW_FILE = DATA_DIR / "raw.json"
LEDGER_FILE = DATA_DIR / "spend_ledger.json"
REVIEW_FILE = DATA_DIR / "review.csv"
# The owner's decisions on "needs human review" flags. A flagged trial gets a checklist only
# when the owner has reviewed it, for example:
#   {"NCT01234567": {"show_checklist": true, "last_update": "2026-08-03", "note": "flag is about a lab value; OK to skip"}}
# last_update must match the trial's listing date, so a changed trial is hidden again until re-checked.
DECISIONS_FILE = DATA_DIR / "review_decisions.json"
SITE_FILE = ROOT / "checklist-data" / "checklists.json"

PROMPT = (ROOT / "prompts" / "eligibility_questions_v3.md").read_text()
SCHEMA = json.loads((ROOT / "prompts" / "eligibility_questions_v2.schema.json").read_text())  # v3 uses the v2 schema
MODEL = "claude-opus-5"

# ---- Spending rules. Only the owner may change these. ----
# Total the generator may ever spend, across all runs, counted at the worst possible cost
# until a run finishes and its real cost is known.
SPENDING_CAP_USD = 2.00
MAX_OUTPUT_TOKENS = 8000  # step 3's longest answer was 3,260 tokens
BATCH_PRICE_PER_MILLION = (2.50, 12.50)  # Opus 5 input/output at the Batch API's half price
TYPICAL_OUTPUT_TOKENS = 2500

# ---- Which trials get checklists ----
CENTER_ZIP = "02451"  # Waltham, MA (Boston area)
RADIUS_MILES = 50
CHILD_INSTRUCTION = (
    "\n\nThis trial includes children, so a parent or guardian may be answering for a child. "
    "For each question, also write `question_for_parent`: the same question asked to a parent or "
    "guardian about \"your child\" (for example, \"Does your child take metformin?\"). Keep the same "
    "meaning, numbers and plain-language explanations."
)


# ---------- Finding trials ----------

def fetch_trials():
    with urllib.request.urlopen(f"https://api.zippopotam.us/us/{CENTER_ZIP}", timeout=30) as resp:
        place = json.load(resp)["places"][0]
    lat, lon = float(place["latitude"]), float(place["longitude"])

    studies, token = [], None
    while True:
        params = {
            "query.cond": "type 2 diabetes",
            "filter.overallStatus": "RECRUITING",
            "filter.geo": f"distance({lat},{lon},{RADIUS_MILES}mi)",
            "fields": "NCTId,BriefTitle,Condition,EligibilityModule,LastUpdatePostDate",
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
        p = s["protocolSection"]
        conditions = p.get("conditionsModule", {}).get("conditions", [])
        e = p.get("eligibilityModule", {})
        if not e.get("eligibilityCriteria", "").strip() or not any(T2D_RE.search(c) for c in conditions):
            continue
        trials.append({
            "nct_id": p["identificationModule"]["nctId"],
            "title": p["identificationModule"].get("briefTitle", ""),
            "conditions": conditions,
            "criteria": e["eligibilityCriteria"],
            "last_update": p.get("statusModule", {}).get("lastUpdatePostDateStruct", {}).get("date"),
            "listing": {
                "minimum_age": e.get("minimumAge"),
                "maximum_age": e.get("maximumAge"),
                "sex": e.get("sex", "ALL"),
                "healthy_volunteers": e.get("healthyVolunteers"),
            },
        })
    return sorted(trials, key=lambda t: t["nct_id"])


def includes_children(trial):
    lo = trial["listing"]["minimum_age"]
    if not lo:
        return True  # no minimum age listed
    number, unit = lo.split()
    return not unit.lower().startswith("year") or int(number) < 18


# ---------- Cost ----------

def estimate(trial):
    """(typical, worst) cost in dollars for one trial at Batch API prices."""
    message = step2.user_message(trial, step2.listing_questions(trial))
    input_tokens = (len(PROMPT) + len(message) + (len(CHILD_INSTRUCTION) if includes_children(trial) else 0)) / 3.0 + 200
    price_in, price_out = BATCH_PRICE_PER_MILLION
    typical = input_tokens * price_in / 1e6 + TYPICAL_OUTPUT_TOKENS * price_out / 1e6
    worst = input_tokens * 1.3 * price_in / 1e6 + MAX_OUTPUT_TOKENS * price_out / 1e6
    return typical, worst


def actual_cost(usage):
    price_in, price_out = BATCH_PRICE_PER_MILLION
    return usage["input_tokens"] * price_in / 1e6 + usage["output_tokens"] * price_out / 1e6


def ledger():
    return json.loads(LEDGER_FILE.read_text()) if LEDGER_FILE.exists() else []


def committed_spend():
    """Real cost of finished runs, plus the worst-case cost of any run still in progress."""
    return sum(e.get("actual_usd", e["worst_case_usd"]) for e in ledger())


# ---------- plan ----------

def plan():
    trials = fetch_trials()
    done = json.loads(RAW_FILE.read_text()) if RAW_FILE.exists() else {}
    todo = [t for t in trials if t["nct_id"] not in done or done[t["nct_id"]].get("last_update") != t["last_update"]]
    typical = sum(estimate(t)[0] for t in todo)
    worst = sum(estimate(t)[1] for t in todo)
    plan_id = hashlib.sha256(
        json.dumps([[t["nct_id"], t["last_update"]] for t in todo]).encode() + PROMPT.encode()
    ).hexdigest()[:8]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_FILE.write_text(json.dumps({
        "plan_id": plan_id, "created": datetime.date.today().isoformat(),
        "typical_usd": round(typical, 2), "worst_case_usd": round(worst, 2), "trials": todo,
    }, indent=2, ensure_ascii=False))

    print(f"Recruiting trials listing type 2 diabetes within {RADIUS_MILES} miles of {CENTER_ZIP}: {len(trials)}")
    print(f"Need checklists (new or changed since last run): {len(todo)}"
          f" ({sum(includes_children(t) for t in todo)} include children)")
    print(f"Estimated cost: about ${typical:.2f}; worst possible: ${worst:.2f}")
    print(f"Spent so far by this tool: ${committed_spend():.2f} of the ${SPENDING_CAP_USD:.2f} cap")
    print(f"Plan ID: {plan_id}")


# ---------- generate ----------

def generate(approved_plan_id):
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    if not PLAN_FILE.exists():
        sys.exit("No plan yet. Run: tools/generate_checklists.py plan")
    current = json.loads(PLAN_FILE.read_text())
    if approved_plan_id != current["plan_id"]:
        sys.exit(f"Plan ID {approved_plan_id} doesn't match the current plan ({current['plan_id']}). Nothing was spent.")
    if any(e["plan_id"] == current["plan_id"] for e in ledger()):
        sys.exit("This plan was already run. Run 'plan' again to make a new one. Nothing was spent.")
    if committed_spend() + current["worst_case_usd"] > SPENDING_CAP_USD:
        sys.exit(f"Refusing: the worst possible cost (${current['worst_case_usd']:.2f}) plus earlier spending "
                 f"(${committed_spend():.2f}) would go over the ${SPENDING_CAP_USD:.2f} cap. Nothing was spent. "
                 "Only the owner can raise SPENDING_CAP_USD.")
    trials = current["trials"]
    if not trials:
        sys.exit("Nothing to generate.")

    load_env_file()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("No API key found. Add ANTHROPIC_API_KEY to the .env file first.")
    client = anthropic.Anthropic()

    requests = []
    for t in trials:
        schema, message = SCHEMA, step2.user_message(t, step2.listing_questions(t))
        if includes_children(t):
            schema = copy.deepcopy(SCHEMA)
            item = schema["properties"]["questions"]["items"]
            item["properties"]["question_for_parent"] = {"type": "string"}
            item["required"].append("question_for_parent")
            message += CHILD_INSTRUCTION
        requests.append(Request(custom_id=t["nct_id"], params=MessageCreateParamsNonStreaming(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=PROMPT,
            output_config={"effort": step2.EFFORT, "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": message}],
        )))

    batch = client.messages.batches.create(requests=requests)
    entries = ledger()
    entries.append({"plan_id": current["plan_id"], "batch_id": batch.id, "date": datetime.date.today().isoformat(),
                    "trials": len(trials), "worst_case_usd": current["worst_case_usd"]})
    LEDGER_FILE.write_text(json.dumps(entries, indent=2))
    print(f"Batch {batch.id} submitted for {len(trials)} trials. Waiting for results...")
    collect(batch.id)


def collect(batch_id):
    import anthropic

    load_env_file()
    client = anthropic.Anthropic()
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        print(f"  still working: {batch.request_counts.processing} left")
        time.sleep(30)

    trials = {t["nct_id"]: t for t in json.loads(PLAN_FILE.read_text())["trials"]}
    raw = json.loads(RAW_FILE.read_text()) if RAW_FILE.exists() else {}
    spent = 0.0
    for result in client.messages.batches.results(batch_id):
        trial = trials[result.custom_id]
        record = {"trial": trial, "last_update": trial["last_update"], "batch_id": batch_id}
        if result.result.type == "succeeded":
            msg = result.result.message
            usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens}
            spent += actual_cost(usage)
            record.update(usage, stop_reason=msg.stop_reason)
            if msg.stop_reason == "end_turn":
                record["answer"] = json.loads(next(b.text for b in msg.content if b.type == "text"))
        else:
            record["error"] = result.result.type
        raw[result.custom_id] = record
    RAW_FILE.write_text(json.dumps(raw, indent=2, ensure_ascii=False))

    entries = ledger()
    for e in entries:
        if e["batch_id"] == batch_id:
            e["actual_usd"] = round(spent, 4)
    LEDGER_FILE.write_text(json.dumps(entries, indent=2))
    print(f"Done. This run cost ${spent:.2f}. Total spent by this tool: ${committed_spend():.2f}")
    build()


# ---------- build ----------

PARENT_LISTING_WORDING = [
    ("Are you between", "Is your child between"),
    ("Are you at least", "Is your child at least"),
    ("Are you ", "Is your child "),
    ("Is your sex", "Is your child's sex"),
    ("Do you have type 2 diabetes", "Does your child have type 2 diabetes"),
]


def parent_version(question):
    for before, after in PARENT_LISTING_WORDING:
        if question.startswith(before):
            return after + question[len(before):]
    raise ValueError(f"No parent wording for listing question: {question}")


def build():
    raw = json.loads(RAW_FILE.read_text()) if RAW_FILE.exists() else {}
    if not DECISIONS_FILE.exists():
        DECISIONS_FILE.write_text("{}\n")
    decisions = json.loads(DECISIONS_FILE.read_text())
    site, review_rows = {}, []
    for nct_id, record in sorted(raw.items()):
        trial = record["trial"]
        child = includes_children(trial)
        entry = {"last_update": record["last_update"], "includes_children": child}
        answer = record.get("answer")
        if not answer:
            entry.update(available=False, reason="not_generated")
            site[nct_id] = entry
            continue

        questions = []
        for q in step2.listing_questions(trial):
            questions.append({
                "source": "listing", "text": q["question"],
                "text_for_parent": parent_version(q["question"]) if child else None,
                "original_text": q["original_text"], "counts_against": q["counts_against"],
            })
        seen = {q["text"].strip().lower() for q in questions}
        dropped = []
        for q in answer["questions"]:
            ok, reason = step2.check_quote(q["original_text"], trial["criteria"])
            if ok and q["question"].strip().lower() in seen:
                ok, reason = False, "duplicate question"
            if ok and len(questions) >= step2.MAX_QUESTIONS:
                ok, reason = False, f"over the {step2.MAX_QUESTIONS}-question limit"
            if not ok:
                dropped.append(reason)
                continue
            seen.add(q["question"].strip().lower())
            questions.append({
                "source": "ai", "text": q["question"],
                "text_for_parent": q.get("question_for_parent") if child else None,
                "original_text": step2.unescape(q["original_text"]).strip(),
                "counts_against": q["counts_against"], "sensitive": q["sensitive_topic"],
            })

        needs_review = answer.get("needs_human_review", [])
        decision = decisions.get(nct_id, {})
        resolved = decision.get("show_checklist") is True and decision.get("last_update") == record["last_update"]
        ai_count = sum(q["source"] == "ai" for q in questions)
        if needs_review and not resolved:
            entry.update(available=False, reason="needs_review")
        elif ai_count == 0:
            entry.update(available=False, reason="no_questions")
        else:
            entry.update(available=True, questions=questions)
        site[nct_id] = entry

        for i, q in enumerate(questions, 1):
            review_rows.append({
                "trial_id": nct_id, "checklist_shown": "yes" if entry["available"] else f"no ({entry['reason']})",
                "source": "AI" if q["source"] == "ai" else "trial listing", "question_number": i,
                "original_text": q["original_text"], "question": q["text"],
                "question_for_parent": q["text_for_parent"] or "",
                "answer_that_counts_against": q["counts_against"],
                "grade_level_question": grade(q["text"]),
                "accurate (Y/N)": "", "easy to understand (Y/N)": "",
                "correct answer counts against (Y/N)": "", "notes": "",
            })
        for item in needs_review:
            review_rows.append({
                "trial_id": nct_id, "checklist_shown": "no (needs_review)", "source": "NEEDS HUMAN REVIEW",
                "question_number": "", "original_text": item["original_text"], "question": item["problem"],
                "question_for_parent": "", "answer_that_counts_against": "", "grade_level_question": "",
                "accurate (Y/N)": "", "easy to understand (Y/N)": "",
                "correct answer counts against (Y/N)": "", "notes": "",
            })
        if dropped:
            print(f"  {nct_id}: dropped {len(dropped)} question(s): {'; '.join(dropped)}")

    SITE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SITE_FILE.write_text(json.dumps({
        "about": "Pre-made eligibility checklists for TrialFinder. Made by tools/generate_checklists.py.",
        "made_on": datetime.date.today().isoformat(),
        "trials": site,
    }, indent=1, ensure_ascii=False))
    write_csv(REVIEW_FILE, review_rows)
    shown = sum(e["available"] for e in site.values())
    print(f"Website data: {len(site)} trials, {shown} with a checklist, {len(site) - shown} without.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["plan", "generate", "collect", "build"])
    parser.add_argument("--approve", help="plan ID the owner approved (for generate)")
    parser.add_argument("--batch", help="batch ID to collect, if waiting was interrupted")
    args = parser.parse_args()
    if args.command == "plan":
        plan()
    elif args.command == "generate":
        if not args.approve:
            sys.exit("generate needs --approve PLAN_ID. Nothing was spent.")
        generate(args.approve)
    elif args.command == "collect":
        collect(args.batch or ledger()[-1]["batch_id"])
    else:
        build()
