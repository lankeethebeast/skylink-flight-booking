// Airport IATA Code Autocomplete
// Loads airport data and provides autocomplete for from/to inputs

(function() {
  let airportData = [];
  let loaded = false;

  // Load airport data
  async function loadAirports() {
    if (loaded) return airportData;
    try {
      const res = await fetch('/static/iata_codes.json');
      airportData = await res.json();
      loaded = true;
    } catch (e) {
      console.error('Failed to load airport data:', e);
    }
    return airportData;
  }

  // Search airports by query (code, city, name, country)
  function searchAirports(query) {
    if (!query || query.length < 1) return [];
    const q = query.toLowerCase().trim();
    return airportData.filter(a =>
      a.code.toLowerCase().includes(q) ||
      a.city.toLowerCase().includes(q) ||
      a.name.toLowerCase().includes(q) ||
      a.country.toLowerCase().includes(q)
    ).slice(0, 8); // limit to 8 results
  }

  // Create autocomplete dropdown
  function createDropdown(input) {
    const wrapper = input.closest('label') || input.parentElement;
    wrapper.style.position = 'relative';
    
    const dropdown = document.createElement('div');
    dropdown.className = 'airport-autocomplete-dropdown';
    dropdown.style.cssText = `
      display: none;
      position: absolute;
      top: 100%;
      left: 0;
      right: 0;
      z-index: 9999;
      background: #fff;
      border: 1px solid rgba(0,97,255,0.12);
      border-radius: 8px;
      max-height: 280px;
      overflow-y: auto;
      box-shadow: 0 12px 40px rgba(0,0,0,0.12);
      margin-top: 4px;
    `;
    wrapper.appendChild(dropdown);
    return dropdown;
  }

  function renderResults(dropdown, results, input) {
    if (results.length === 0) {
      dropdown.style.display = 'none';
      return;
    }

    dropdown.innerHTML = results.map((a, i) => `
      <div class="airport-option" data-code="${a.code}" data-index="${i}" style="
        padding: 10px 14px;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 12px;
        border-bottom: 1px solid rgba(0,97,255,0.06);
        transition: background 0.15s;
      ">
        <div style="
          background: linear-gradient(135deg, #0061ff, #00d4ff);
          color: #fff;
          padding: 4px 8px;
          border-radius: 6px;
          font-weight: 700;
          font-size: 0.8rem;
          letter-spacing: 0.5px;
          min-width: 42px;
          text-align: center;
        ">${a.code}</div>
        <div style="flex: 1; min-width: 0;">
          <div style="color: #0a0f1f; font-weight: 600; font-size: 0.9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${a.city}</div>
          <div style="color: #6b7280; font-size: 0.75rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${a.name}</div>
        </div>
        <div style="color: #6b7280; font-size: 0.7rem; white-space: nowrap;">${a.country}</div>
      </div>
    `).join('');

    dropdown.style.display = 'block';

    // Add hover effects and click handlers
    dropdown.querySelectorAll('.airport-option').forEach(opt => {
      opt.addEventListener('mouseenter', () => {
        opt.style.background = 'rgba(0,97,255,0.06)';
      });
      opt.addEventListener('mouseleave', () => {
        opt.style.background = '#fff';
      });
      opt.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const code = opt.dataset.code;
        const airport = airportData.find(a => a.code === code);
        input.value = code;
        input.setAttribute('title', `${airport.city} - ${airport.name}`);
        dropdown.style.display = 'none';
        input.blur();
      });
    });
  }

  // Attach autocomplete to an input
  function attachAutocomplete(input) {
    const dropdown = createDropdown(input);
    let debounceTimer;

    input.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        const query = input.value.trim();
        if (query.length < 1) {
          dropdown.style.display = 'none';
          return;
        }
        const results = searchAirports(query);
        renderResults(dropdown, results, input);
      }, 150);
    });

    input.addEventListener('focus', () => {
      const query = input.value.trim();
      if (query.length >= 1) {
        const results = searchAirports(query);
        renderResults(dropdown, results, input);
      }
    });

    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
      if (!dropdown.contains(e.target) && e.target !== input) {
        dropdown.style.display = 'none';
      }
    });

    // Close on Escape
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        dropdown.style.display = 'none';
        input.blur();
      }
    });
  }

  // Initialize when DOM is ready
  document.addEventListener('DOMContentLoaded', async () => {
    await loadAirports();

    // Attach to main from/to inputs
    const fromInput = document.getElementById('fromCode');
    const toInput = document.getElementById('toCode');
    if (fromInput) attachAutocomplete(fromInput);
    if (toInput) attachAutocomplete(toInput);

    // Also attach to multicity inputs via MutationObserver
    const observer = new MutationObserver(() => {
      document.querySelectorAll('input[name="leg_from"], input[name="leg_to"]').forEach(input => {
        if (!input.dataset.autocompleteAttached) {
          attachAutocomplete(input);
          input.dataset.autocompleteAttached = 'true';
        }
      });
    });
    const legsContainer = document.getElementById('legsContainer');
    if (legsContainer) {
      observer.observe(legsContainer, { childList: true, subtree: true });
      // Attach to existing inputs
      legsContainer.querySelectorAll('input[name="leg_from"], input[name="leg_to"]').forEach(input => {
        attachAutocomplete(input);
        input.dataset.autocompleteAttached = 'true';
      });
    }
  });
})();