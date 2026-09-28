/**
 * Clinical Trials Finder - Frontend Application Logic
 * Connects to ClinicalTrials.gov API (v2) and renders patient-friendly study cards.
 */

// DOM Elements
const searchForm = document.getElementById('search-form');
const conditionInput = document.getElementById('condition-input');
const zipInput = document.getElementById('zip-input');
const distanceSelect = document.getElementById('distance-select');
const searchBtn = document.getElementById('search-btn');

const conditionError = document.getElementById('condition-error');
const zipError = document.getElementById('zip-error');

const welcomeState = document.getElementById('welcome-state');
const loadingState = document.getElementById('loading-state');
const loadingMessage = document.getElementById('loading-message');
const emptyState = document.getElementById('empty-state');
const emptyMessage = document.getElementById('empty-message');
const errorState = document.getElementById('error-state');
const errorMessage = document.getElementById('error-message');
const retryBtn = document.getElementById('retry-btn');

const resultsState = document.getElementById('results-state');
const resultsSummary = document.getElementById('results-summary');
const resultsSubtext = document.getElementById('results-subtext');
const trialsList = document.getElementById('trials-list');
const sortSelect = document.getElementById('sort-select');

const tagChips = document.querySelectorAll('.tag-chip');

// State tracking
let lastSearch = { condition: '', zip: '', distance: '50' };
let currentStudies = [];

// Initialize App
document.addEventListener('DOMContentLoaded', () => {
  setupEventListeners();
});

function setupEventListeners() {
  // Sort selection change
  if (sortSelect) {
    sortSelect.addEventListener('change', () => {
      applySortingAndRender();
    });
  }

  // Form submission
  searchForm.addEventListener('submit', (e) => {
    e.preventDefault();
    handleSearch();
  });

  // Example tag chip clicks
  tagChips.forEach(chip => {
    chip.addEventListener('click', () => {
      conditionInput.value = chip.dataset.condition;
      zipInput.value = chip.dataset.zip;
      clearErrors();
      handleSearch();
    });
  });

  // Retry button
  retryBtn.addEventListener('click', () => {
    if (lastSearch.condition && lastSearch.zip) {
      conditionInput.value = lastSearch.condition;
      zipInput.value = lastSearch.zip;
      distanceSelect.value = lastSearch.distance;
      handleSearch();
    }
  });

  // Real-time input cleanup
  conditionInput.addEventListener('input', () => {
    if (conditionInput.value.trim()) conditionError.textContent = '';
  });

  zipInput.addEventListener('input', () => {
    // Only allow numbers
    zipInput.value = zipInput.value.replace(/\D/g, '').slice(0, 5);
    if (zipInput.value.length === 5) zipError.textContent = '';
  });
}

/**
 * Validates inputs and initiates the search process
 */
async function handleSearch() {
  clearErrors();

  const condition = conditionInput.value.trim();
  const zip = zipInput.value.trim();
  const distance = distanceSelect.value;

  let isValid = true;

  if (!condition) {
    conditionError.textContent = 'Please enter a medical condition or disease name.';
    isValid = false;
  }

  if (!zip) {
    zipError.textContent = 'Please enter a 5-digit US zip code.';
    isValid = false;
  } else if (!/^\d{5}$/.test(zip)) {
    zipError.textContent = 'Zip code must be exactly 5 numbers (e.g. 90210).';
    isValid = false;
  }

  if (!isValid) return;

  // Save last search
  lastSearch = { condition, zip, distance };

  // Switch to Loading View
  showView('loading');
  loadingMessage.textContent = `Locating recruiting trials for "${condition}" near zip ${zip}...`;
  searchBtn.disabled = true;

  try {
    // Step 1: Attempt to convert zip code to GPS coordinates for radius search
    const geo = await lookupZipCoordinates(zip);

    // Step 2: Query ClinicalTrials.gov API v2
    const data = await fetchClinicalTrials(condition, zip, distance, geo);

    // Step 3: Render results with calculated distances
    renderResults(data, condition, zip, geo, distance);
  } catch (error) {
    console.error('Search error:', error);
    errorMessage.textContent = error.message || 'Unable to connect to ClinicalTrials.gov. Please try again in a few moments.';
    showView('error');
  } finally {
    searchBtn.disabled = false;
  }
}

/**
 * Free geocoder lookup for US zip code coordinates
 * Fallback gracefully if service is unavailable
 */
async function lookupZipCoordinates(zip) {
  // Primary geocoder: Zippopotam.us (fast, free, CORS-enabled)
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3500);

    const response = await fetch(`https://api.zippopotam.us/us/${zip}`, {
      signal: controller.signal
    });
    clearTimeout(timeoutId);

    if (response.ok) {
      const info = await response.json();
      if (info.places && info.places.length > 0) {
        const place = info.places[0];
        return {
          lat: parseFloat(place.latitude),
          lon: parseFloat(place.longitude),
          placeName: `${place['place name']}, ${place['state abbreviation']}`
        };
      }
    }
  } catch (e) {
    console.warn('Zip coordinate lookup via primary service skipped/failed, trying backup...', e);
  }

  // Backup geocoder: US Census Geocoding API
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3500);

    const response = await fetch(
      `https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?address=${zip}&benchmark=Public_AR_Current&format=json`,
      { signal: controller.signal }
    );
    clearTimeout(timeoutId);

    if (response.ok) {
      const data = await response.json();
      const match = data.result?.addressMatches?.[0];
      if (match?.coordinates) {
        return {
          lat: match.coordinates.y,
          lon: match.coordinates.x,
          placeName: match.matchedAddress || `Zip ${zip}`
        };
      }
    }
  } catch (e) {
    console.warn('Backup geocoder failed or timed out, using direct location query.', e);
  }

  return null;
}

/**
 * Queries the official ClinicalTrials.gov API v2
 */
async function fetchClinicalTrials(condition, zip, distance, geo) {
  const baseUrl = 'https://clinicaltrials.gov/api/v2/studies';
  const params = new URLSearchParams();

  // Search by user condition
  params.append('query.cond', condition);

  // Filter only actively recruiting trials
  params.append('filter.overallStatus', 'RECRUITING');

  // Proximity filter: use geo distance if coordinates available, otherwise query.locn
  if (geo && geo.lat && geo.lon) {
    params.append('filter.geo', `distance(${geo.lat},${geo.lon},${distance}mi)`);
  } else {
    params.append('query.locn', zip);
  }

  // Study limit and total count (fetch up to 100 to cover all nearby results)
  params.append('pageSize', '100');
  params.append('countTotal', 'true');

  const url = `${baseUrl}?${params.toString()}`;

  const response = await fetch(url, {
    method: 'GET',
    headers: {
      'Accept': 'application/json'
    }
  });

  if (!response.ok) {
    throw new Error(`The trials registry returned an error (status: ${response.status}). Please try again.`);
  }

  return await response.json();
}

/**
 * Formats and displays study results on the screen
 */
function renderResults(data, condition, zip, geo, distance) {
  const studies = data.studies || [];
  const totalCount = data.totalCount !== undefined ? data.totalCount : studies.length;

  if (studies.length === 0) {
    const locationLabel = geo?.placeName ? `${geo.placeName} (${zip})` : `zip code ${zip}`;
    emptyMessage.textContent = `We couldn't find any actively recruiting trials for "${condition}" near ${locationLabel}.`;
    showView('empty');
    return;
  }

  // Tag each study with its original ranking and calculate distance to its nearest site
  studies.forEach((study, index) => {
    study._originalRank = index;
    const locations = study.protocolSection?.contactsLocationsModule?.locations || [];
    study._locationInfo = analyzeStudySites(locations, geo, distance, zip);
  });

  currentStudies = studies;

  // Update header text
  const locationLabel = geo?.placeName ? `near ${geo.placeName} (${zip})` : `near ${zip}`;
  resultsSummary.textContent = `Found ${totalCount} Recruiting ${totalCount === 1 ? 'Trial' : 'Trials'}`;
  resultsSubtext.textContent = `Showing studies for "${condition}" ${locationLabel}`;

  // Apply chosen sort ("nearest" or "relevance") and render cards
  applySortingAndRender();

  showView('results');
}

/**
 * Sorts currentStudies based on user selection ("nearest" or "relevance") and renders cards
 */
function applySortingAndRender() {
  const sortMode = sortSelect ? sortSelect.value : 'nearest';

  if (sortMode === 'nearest') {
    currentStudies.sort((a, b) => {
      const distA = a._locationInfo.nearestDistance ?? Infinity;
      const distB = b._locationInfo.nearestDistance ?? Infinity;
      if (distA !== distB) return distA - distB;
      return a._originalRank - b._originalRank;
    });
  } else if (sortMode === 'relevance') {
    currentStudies.sort((a, b) => a._originalRank - b._originalRank);
  }

  // Clear previous cards
  trialsList.innerHTML = '';

  // Render all cards
  currentStudies.forEach((study, index) => {
    const card = createTrialCard(study, index, study._locationInfo);
    trialsList.appendChild(card);
  });
}

/**
 * Creates an individual HTML trial card
 */
function createTrialCard(study, index, locationInfo) {
  const protocol = study.protocolSection || {};
  const idModule = protocol.identificationModule || {};
  const designModule = protocol.designModule || {};
  const descModule = protocol.descriptionModule || {};

  const nctId = idModule.nctId || 'Study';
  const title = idModule.briefTitle || 'Untitled Clinical Study';
  const summary = descModule.briefSummary || 'No summary is currently available for this study.';
  
  // Format Phase using studyType and phases
  const rawPhases = designModule.phases || [];
  const studyType = designModule.studyType || '';
  const phaseLabel = formatPhase(studyType, rawPhases);

  // Extract Conditions Studied
  const condModule = protocol.conditionsModule || {};
  const rawConditions = condModule.conditions || [];
  const conditionsText = rawConditions.length > 0 ? rawConditions.join(', ') : '';

  // Official Study URL
  const officialUrl = `https://clinicaltrials.gov/study/${nctId}`;

  // Location and distance details
  const { nearestSiteText, facilityText, radiusCountText } = locationInfo;

  // Build card DOM element
  const card = document.createElement('article');
  card.className = 'trial-card';

  // Truncate long summary for readability
  const isLongSummary = summary.length > 280;
  const shortSummary = isLongSummary ? summary.substring(0, 260) + '...' : summary;

  card.innerHTML = `
    <div class="card-badges">
      <span class="badge badge-recruiting">● Recruiting</span>
      <span class="badge badge-phase">${escapeHtml(phaseLabel)}</span>
      <span class="badge badge-nct">${escapeHtml(nctId)}</span>
    </div>

    <h4 class="trial-title">${escapeHtml(title)}</h4>

    ${conditionsText ? `
      <div class="trial-conditions">
        <span class="conditions-label">Conditions studied:</span>
        <span class="conditions-list">${escapeHtml(conditionsText)}</span>
      </div>
    ` : ''}

    <div class="trial-location">
      <span class="location-icon" aria-hidden="true">📍</span>
      <div class="location-details">
        <div class="location-main-line">
          <span class="location-text">${escapeHtml(nearestSiteText)}</span>
          ${radiusCountText ? `<span class="location-radius-count">${escapeHtml(radiusCountText)}</span>` : ''}
        </div>
        ${facilityText ? `<div class="location-facility">${escapeHtml(facilityText)}</div>` : ''}
      </div>
    </div>

    <div class="trial-description">
      <p id="summary-${index}">${escapeHtml(shortSummary)}</p>
      ${isLongSummary ? `<button type="button" class="toggle-read-more" data-index="${index}">Read more</button>` : ''}
    </div>

    <div class="card-footer">
      <span class="results-subtext">Official study listing verified by NIH</span>
      <a href="${officialUrl}" target="_blank" rel="noopener noreferrer" class="card-link-btn">
        <span>View Study Details</span>
        <span aria-hidden="true">↗</span>
      </a>
    </div>
  `;

  // Attach Read More toggle if applicable
  if (isLongSummary) {
    const toggleBtn = card.querySelector('.toggle-read-more');
    const summaryP = card.querySelector(`#summary-${index}`);
    let expanded = false;

    toggleBtn.addEventListener('click', () => {
      expanded = !expanded;
      if (expanded) {
        summaryP.textContent = summary;
        toggleBtn.textContent = 'Show less';
      } else {
        summaryP.textContent = shortSummary;
        toggleBtn.textContent = 'Read more';
      }
    });
  }

  return card;
}

/**
 * Converts studyType and phase names into patient-friendly labels.
 * - OBSERVATIONAL -> "Observational study (no treatment given)"
 * - INTERVENTIONAL with phase NA/empty -> "Interventional study · no phase"
 * - Otherwise -> "Phase 2" or "Phase 2/3"
 */
function formatPhase(studyType, phases) {
  const type = (studyType || '').trim().toUpperCase();
  const rawPhases = Array.isArray(phases) ? phases : [];

  // 1. If OBSERVATIONAL
  if (type === 'OBSERVATIONAL') {
    return 'Observational study (no treatment given)';
  }

  // Check if phase is missing, empty, or 'NA'
  const isPhaseNA = rawPhases.length === 0 || rawPhases.every(p => p === 'NA');

  // 2. If INTERVENTIONAL with phase NA
  if (type === 'INTERVENTIONAL' && isPhaseNA) {
    return 'Interventional study · no phase';
  }

  // 3. Otherwise format the phase(s), like "Phase 2" or "Phase 2/3"
  const validPhases = rawPhases.filter(p => p !== 'NA');
  if (validPhases.length > 0) {
    // If standard numbered phases like PHASE1, PHASE2, PHASE3, PHASE4
    const numbersOnly = validPhases.every(p => /^PHASE[1-4]$/.test(p));
    if (numbersOnly) {
      const numbers = validPhases.map(p => p.replace('PHASE', ''));
      return `Phase ${numbers.join('/')}`;
    }

    return validPhases.map(p => {
      if (p === 'EARLY_PHASE1') return 'Early Phase 1';
      return p.replace('PHASE', 'Phase ');
    }).join(' / ');
  }

  // Fallback if not specified
  if (type) {
    return `${type.charAt(0) + type.slice(1).toLowerCase()} study · no phase`;
  }

  return 'Study phase not specified';
}

/**
 * Haversine formula to compute great-circle distance between two GPS coordinates in miles
 */
function calculateDistanceMiles(lat1, lon1, lat2, lon2) {
  const R = 3958.8; // Radius of Earth in miles
  const dLat = (lat2 - lat1) * (Math.PI / 180);
  const dLon = (lon2 - lon1) * (Math.PI / 180);
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(lat1 * (Math.PI / 180)) * Math.cos(lat2 * (Math.PI / 180)) *
    Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

/**
 * Calculates distance from user's zip coordinates to EVERY site in the study,
 * identifies the closest site, and counts how many sites are within the search radius.
 */
function analyzeStudySites(locations, userGeo, searchRadius, searchZip) {
  if (!locations || locations.length === 0) {
    return {
      nearestSiteText: 'Location details available on official listing',
      facilityText: '',
      radiusCountText: '',
      nearestDistance: Infinity
    };
  }

  const radiusNum = parseFloat(searchRadius) || 50;

  // If user GPS coordinates are available, calculate distance to EVERY site
  if (userGeo && typeof userGeo.lat === 'number' && typeof userGeo.lon === 'number') {
    let nearestSite = null;
    let minDistance = Infinity;
    let sitesWithinRadiusCount = 0;

    locations.forEach(loc => {
      // ClinicalTrials.gov API v2 provides coordinates in loc.geoPoint
      const lat = loc.geoPoint?.lat ?? (typeof loc.latitude === 'number' ? loc.latitude : null);
      const lon = loc.geoPoint?.lon ?? (typeof loc.longitude === 'number' ? loc.longitude : null);

      if (lat !== null && lon !== null && !isNaN(lat) && !isNaN(lon)) {
        const dist = calculateDistanceMiles(userGeo.lat, userGeo.lon, lat, lon);

        if (dist <= radiusNum) {
          sitesWithinRadiusCount++;
        }

        if (dist < minDistance) {
          minDistance = dist;
          nearestSite = { loc, dist };
        }
      }
    });

    if (nearestSite) {
      const loc = nearestSite.loc;
      const roundedDist = Math.round(nearestSite.dist);
      const distLabel = roundedDist === 1 ? '1 mile away' : `${roundedDist} miles away`;

      // City, State format (e.g. "Pomona, CA")
      const cityStateParts = [loc.city, loc.state].filter(Boolean);
      const cityState = cityStateParts.length > 0 ? cityStateParts.join(', ') : (loc.country || 'Nearby Site');

      // The requested format: "Pomona, CA · 6 miles away"
      const nearestSiteText = `${cityState} · ${distLabel}`;

      // The radius count: e.g. "(3 sites within 50 miles)"
      let radiusCountText = '';
      if (sitesWithinRadiusCount > 0) {
        const sitePlural = sitesWithinRadiusCount === 1 ? '1 site' : `${sitesWithinRadiusCount} sites`;
        radiusCountText = `(${sitePlural} within ${radiusNum} miles)`;
      } else {
        radiusCountText = `(Nearest site is ${distLabel})`;
      }

      return {
        nearestSiteText,
        facilityText: loc.facility || '',
        radiusCountText,
        nearestDistance: nearestSite.dist
      };
    }
  }

  // Fallback: If GPS coordinates are not available, try matching zip code
  const exactZipMatch = searchZip ? locations.find(loc => loc.zip && loc.zip.includes(searchZip)) : null;
  const targetLoc = exactZipMatch || locations.find(loc => loc.status === 'RECRUITING') || locations[0];

  const cityState = [targetLoc.city, targetLoc.state].filter(Boolean).join(', ');
  const fallbackText = cityState || targetLoc.country || 'Location on record';
  const totalExtra = locations.length > 1 ? `(${locations.length} sites total)` : '';

  return {
    nearestSiteText: fallbackText,
    facilityText: targetLoc.facility || '',
    radiusCountText: totalExtra,
    nearestDistance: Infinity
  };
}

/**
 * Controls which state container is visible
 */
function showView(viewName) {
  welcomeState.classList.add('hidden');
  loadingState.classList.add('hidden');
  emptyState.classList.add('hidden');
  errorState.classList.add('hidden');
  resultsState.classList.add('hidden');

  switch (viewName) {
    case 'welcome':
      welcomeState.classList.remove('hidden');
      break;
    case 'loading':
      loadingState.classList.remove('hidden');
      break;
    case 'empty':
      emptyState.classList.remove('hidden');
      break;
    case 'error':
      errorState.classList.remove('hidden');
      break;
    case 'results':
      resultsState.classList.remove('hidden');
      break;
  }
}

function clearErrors() {
  conditionError.textContent = '';
  zipError.textContent = '';
}

function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
