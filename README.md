# TrialFinder 🩺

> A patient-friendly search tool to find actively recruiting clinical trials nearby.

🌐 **Live Website:** [https://trialfinder-ct.netlify.app](https://trialfinder-ct.netlify.app)

---

## Why This Matters

Every year, thousands of clinical trials seek participants for new therapies, medications, and treatments. For patients and families navigating serious, chronic, or rare health conditions, clinical trials can offer life-changing options.

However, the official government databases (such as ClinicalTrials.gov) were designed by and for **researchers, scientists, and regulatory bodies**—not patients. 

### Common hurdles for patients:
- **Researcher Jargon:** Confusing terms and abbreviations like *"Phase 1/Phase 2 Interventional Study with NA phase"* or *"Observational Cohort"* make it hard to understand what a trial actually entails.
- **Buried Locations:** A multi-center nationwide study often lists dozens of facilities alphabetically or arbitrarily. Patients searching from California frequently see a site in Arizona or Ohio first, without knowing whether a local hospital just 5 miles away is participating.
- **Complex Interfaces:** Dense filters and overwhelming clinical forms discourage patients and caregivers from finding trials they qualify for.

**TrialFinder was built to bridge this gap.** It translates complex registry data into a clean, simple, and compassionate experience designed specifically for patients and caregivers.

---

## What TrialFinder Does

- **Simple Search:** Enter a condition (e.g., *Asthma*, *Diabetes*, *Migraine*) and a 5-digit US zip code.
- **Actively Recruiting Only:** Automatically filters out closed, suspended, or completed studies so patients only see trials currently seeking participants.
- **Nearest Site Distance:** Analyzes every facility for each trial and highlights the **physically closest site** to your zip code with exact mileage (e.g., `Pomona, CA · 6 miles away (3 sites within 50 miles)`).
- **Plain-English Phase Explanations:**
  - *"Observational study (no treatment given)"*
  - *"Interventional study · no phase"*
  - *"Phase 2"*, *"Phase 2/3"*, etc.
- **"Conditions Studied" Clarity:** Displays the exact conditions recorded in the registry so users immediately understand why a study matched their search.
- **Flexible Sorting:** Sort results by **Nearest first** (closest clinic to you) or **Best match** (relevance to your medical keywords).
- **100% Private & Free:** No sign-up, no login, no tracking, and no search data saved.

---

## How It Works

1. **User Input & Geocoding:**
   - The user inputs a health condition and a 5-digit US zip code.
   - The app uses an open US geocoding service (Zippopotam.us with a fallback to the US Census API) to convert the zip code into geographic coordinates (`latitude` and `longitude`).
2. **ClinicalTrials.gov API v2:**
   - The app queries the official, public [ClinicalTrials.gov REST API v2](https://clinicaltrials.gov/data-api/api).
   - Parameters include `query.cond` (medical condition), `filter.overallStatus=RECRUITING` (only open trials), and `filter.geo=distance(...)` (proximity radius).
3. **Client-Side Proximity Calculation:**
   - For every trial returned, the app evaluates all listed site locations in the trial's `contactsLocationsModule`.
   - Using the mathematical **Haversine formula**, it calculates the distance between the patient's coordinates and every hospital site.
   - It identifies the single nearest clinic, counts how many sites are within the patient's radius, and sorts the trials so the closest options appear first.
4. **Direct Government Verification:**
   - Each trial card includes a direct link to the official NIH record at `https://clinicaltrials.gov/study/{NCTId}` for full protocol and investigator details.

---

## Tech Stack

TrialFinder is intentionally built with zero heavy frameworks or build steps:
- **HTML5:** Semantic, accessible layout.
- **CSS3:** Modern, responsive healthcare theme with high-contrast accessibility.
- **Vanilla JavaScript (ES6+):** Async/await API fetching, Haversine formula calculation, and real-time DOM rendering.
- **Zero Server Costs:** Runs 100% in the browser and can be deployed directly to Netlify, GitHub Pages, or Vercel.

---

## Running Locally

To run the project on your computer:

1. Clone or download this repository:
   ```bash
   git clone https://github.com/YOUR_USERNAME/trialfinder.git
   cd trialfinder
   ```
2. Start any local web server. For example, using Python:
   ```bash
   python3 -m http.server 8000
   ```
3. Open your browser and navigate to:
   ```
   http://localhost:8000
   ```

---

## Medical Disclaimer

This tool retrieves publicly available data directly from the U.S. National Library of Medicine (ClinicalTrials.gov) API for educational and informational purposes only. It is not medical advice. Always consult your doctor or primary healthcare provider before making any medical decisions or enrolling in a clinical study.
