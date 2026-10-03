/**
 * Eligibility checklist ("screener") shown on trial cards.
 *
 * Privacy rule (CLAUDE.md #3): the visitor's answers live only in the radio buttons
 * on this page. They are never saved (no cookies, localStorage or logs) and never
 * sent anywhere. The only network request in this file loads the ready-made
 * checklists file, which happens before any answers exist and contains no visitor data.
 */
const Screener = (() => {
  const DATA_URL = 'checklist-data/checklists.json';

  // Checklists were written for people with type 2 diabetes, so they are only
  // offered for searches about type 2 diabetes or diabetes in general.
  const TYPE2_SEARCH = /type\s*(2|ii)\s*diabet|\bt2dm?\b|\bdm2\b/i;
  const OTHER_DIABETES_SEARCH = /type\s*(1|i)\b|\bt1dm?\b|gestational|pre-?diabet/i;

  const ANSWERS = [
    { value: 'yes', label: 'Yes' },
    { value: 'no', label: 'No' },
    { value: 'unknown', label: "I don't know" },
    { value: 'prefer_not', label: 'Prefer not to say' },
  ];

  const NOT_AVAILABLE = 'Checklist not available yet; see the full requirements on ClinicalTrials.gov.';
  const CONTACT_TEAM =
    "A quick checklist isn't available for this trial because some of its requirements need a closer look by a person. " +
    'Please contact the study team to ask whether this trial could be right for you.';
  const DISCLAIMER =
    'This is not an eligibility decision. Only the study team can decide who can join. ' +
    "This checked some of the trial's requirements, not all of them.";

  let dataPromise = null;

  function loadData() {
    if (!dataPromise) {
      dataPromise = fetch(DATA_URL)
        .then(response => (response.ok ? response.json() : { trials: {} }))
        .catch(() => ({ trials: {} }));
    }
    return dataPromise;
  }

  function searchFits(condition) {
    if (TYPE2_SEARCH.test(condition)) return true;
    return /diabet/i.test(condition) && !OTHER_DIABETES_SEARCH.test(condition);
  }

  /** Small helper to build elements without innerHTML, so trial text is never treated as code. */
  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'text') node.textContent = value;
      else if (key === 'className') node.className = value;
      else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of [].concat(children)) {
      if (child) node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    }
    return node;
  }

  function requirementsLink(officialUrl) {
    return el('a', { href: officialUrl, target: '_blank', rel: 'noopener noreferrer', text: 'See the full requirements on ClinicalTrials.gov ↗' });
  }

  function note(slot, text, officialUrl) {
    slot.replaceChildren(el('div', { className: 'screener-note' }, [el('p', { text }), requirementsLink(officialUrl)]));
  }

  /**
   * Fill a trial card's screener slot. Called once per card.
   */
  async function attach(slot, study, searchedCondition) {
    const protocol = study.protocolSection || {};
    const nctId = protocol.identificationModule?.nctId;
    const lastUpdate = protocol.statusModule?.lastUpdatePostDateStruct?.date;
    const officialUrl = `https://clinicaltrials.gov/study/${nctId}`;
    if (!nctId) return;

    const data = await loadData();
    const entry = data.trials?.[nctId];

    // No checklist when the search isn't about type 2 diabetes, when none was made,
    // or when the trial's listing changed after the checklist was made.
    if (!searchFits(searchedCondition) || !entry || entry.last_update !== lastUpdate) {
      note(slot, NOT_AVAILABLE, officialUrl);
      return;
    }
    if (!entry.available) {
      note(slot, entry.reason === 'needs_review' ? CONTACT_TEAM : NOT_AVAILABLE, officialUrl);
      return;
    }
    slot.replaceChildren(buildScreener(nctId, entry, officialUrl));
  }

  function buildScreener(nctId, entry, officialUrl) {
    const panelId = `screener-${nctId}`;
    const questions = entry.questions;

    const toggle = el('button', {
      type: 'button', className: 'screener-toggle', 'aria-expanded': 'false', 'aria-controls': panelId,
      text: `Check if this trial might fit you · ${questions.length} quick questions`,
    });
    const panel = el('div', { className: 'screener-panel', id: panelId, hidden: true });
    toggle.addEventListener('click', () => {
      const open = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', String(!open));
      panel.hidden = open;
    });

    panel.appendChild(el('p', {
      className: 'screener-intro',
      text: "These questions cover some of this trial's main requirements. Your answers stay on this page: " +
            "they are never saved or sent anywhere, and they disappear when you leave or search again.",
    }));

    // Children's trials: ask who is answering first, then word the questions to match.
    let role = entry.includes_children ? null : 'patient';
    const list = el('ol', { className: 'screener-questions' });
    const questionText = [];

    if (entry.includes_children) {
      const roleBox = el('fieldset', { className: 'screener-role' }, [
        el('legend', { text: 'Are you the patient, or a parent/guardian answering for a child?' }),
      ]);
      for (const [value, label] of [['patient', "I'm the patient"], ['parent', "I'm a parent or guardian answering for a child"]]) {
        const input = el('input', { type: 'radio', name: `${panelId}-role`, value, autocomplete: 'off' });
        input.addEventListener('change', () => {
          role = value;
          questions.forEach((q, i) => {
            questionText[i].textContent = role === 'parent' && q.text_for_parent ? q.text_for_parent : q.text;
          });
          list.hidden = false;
          actions.hidden = false;
          clearResult();
        });
        roleBox.appendChild(el('label', { className: 'screener-option' }, [input, label]));
      }
      panel.appendChild(roleBox);
      list.hidden = true;
    }

    questions.forEach((q, i) => {
      const text = el('span', { className: 'screener-question-text', text: q.text });
      questionText.push(text);
      const fromAi = q.source === 'ai';
      const options = el('div', { className: 'screener-options' });
      for (const answer of ANSWERS) {
        const input = el('input', { type: 'radio', name: `${panelId}-q${i}`, value: answer.value, autocomplete: 'off' });
        input.addEventListener('change', clearResult);
        options.appendChild(el('label', { className: 'screener-option' }, [input, answer.label]));
      }
      list.appendChild(el('li', { className: 'screener-question' }, [
        el('fieldset', {}, [
          el('legend', {}, [text]),
          el('span', {
            className: `screener-source ${fromAi ? 'screener-source-ai' : 'screener-source-listing'}`,
            text: fromAi ? 'Question written by AI' : "From the trial's listing",
          }),
          el('div', { className: 'screener-original' }, [
            el('span', { className: 'screener-original-label', text: fromAi ? 'Original trial text (ClinicalTrials.gov):' : 'Trial listing (ClinicalTrials.gov):' }),
            el('q', { text: q.original_text }),
          ]),
          q.sensitive ? el('p', { className: 'screener-hint', text: 'You can always choose "Prefer not to say."' }) : null,
          options,
        ]),
      ]));
    });
    panel.appendChild(list);

    const result = el('div', { className: 'screener-result', role: 'status', 'aria-live': 'polite' });
    const seeResult = el('button', { type: 'button', className: 'primary-btn screener-btn', text: 'See result' });
    const clear = el('button', { type: 'button', className: 'secondary-btn screener-btn', text: 'Clear answers' });
    const actions = el('div', { className: 'screener-actions', hidden: entry.includes_children }, [seeResult, clear]);
    panel.append(actions, result, el('p', { className: 'screener-disclaimer' }, [DISCLAIMER + ' ', requirementsLink(officialUrl)]));

    function answerTo(i) {
      return panel.querySelector(`input[name="${panelId}-q${i}"]:checked`)?.value || null;
    }

    function clearResult() {
      result.replaceChildren();
      result.className = 'screener-result';
    }

    seeResult.addEventListener('click', () => showResult());
    clear.addEventListener('click', () => {
      panel.querySelectorAll(`input[name^="${panelId}-q"]`).forEach(input => { input.checked = false; });
      clearResult();
    });

    function showResult() {
      const answers = questions.map((_, i) => answerTo(i));
      const conflicts = questions.filter((q, i) => answers[i] === q.counts_against);
      const unsure = answers.filter(a => a === null || a === 'unknown' || a === 'prefer_not').length;
      const children = [];

      if (conflicts.length) {
        result.className = 'screener-result screener-result-no';
        children.push(el('h5', { text: 'Likely not a match because of:' }));
        children.push(el('ul', {}, conflicts.map(q => el('li', {}, [
          el('q', { text: q.original_text }),
          q.source === 'listing' ? " (from the trial's listing)" : ' (from the trial\'s requirements)',
        ]))));
        children.push(el('p', { text: 'If you think this doesn\'t apply to you, the study team can tell you for sure.' }));
      } else if (unsure) {
        result.className = 'screener-result screener-result-unclear';
        children.push(el('h5', { text: 'Unclear: ask the study team.' }));
        children.push(el('p', {
          text: `${unsure} ${unsure === 1 ? 'question was' : 'questions were'} answered "I don't know" or ` +
                '"Prefer not to say", or left blank.',
        }));
      } else {
        result.className = 'screener-result screener-result-possible';
        children.push(el('h5', { text: 'Possible match: talk to the study team.' }));
        children.push(el('p', { text: "The study team's contact details are on the trial's ClinicalTrials.gov page." }));
      }
      children.push(el('p', { className: 'screener-result-disclaimer', text: DISCLAIMER }));
      result.replaceChildren(...children);
    }

    return el('div', { className: 'screener' }, [toggle, panel]);
  }

  return { attach };
})();
