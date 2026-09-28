# TrialFinder — Project Rules

TrialFinder is a patient-friendly clinical trial search site (live at https://trialfinder-ct.netlify.app, code at https://github.com/R-Shivaram/TrialFinder). It is plain HTML/CSS/JavaScript with no build step: `index.html`, `style.css`, `app.js`.

The owner is a beginner with no coding experience.

## Rules (always follow these)

1. **Never tell a user they qualify for a trial.** No wording like "you qualify", "you're eligible", or "you're a match". Only a trial's study team can decide eligibility — point users to the official listing and their doctor.
2. **Always show the original ClinicalTrials.gov text next to any AI-generated text.** Any summary, simplification, or explanation made by AI must appear alongside the original registry text it came from, clearly labeled as AI-generated.
3. **Never store or send users' health answers anywhere.** No saving to databases, cookies, localStorage, logs, or analytics, and no sending to any server or third-party service. (Note: the current search sends the typed condition and zip code to ClinicalTrials.gov and zip lookup services in order to run the search. Ask the owner before adding any new place data is sent.)
4. **Explain every change to the owner in plain language.** Say what changed, why, and what they'll notice on the site — no jargon, or define it when unavoidable.
