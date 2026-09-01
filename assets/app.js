(() => {
  "use strict";

  const state = {
    all: [],
    filtered: [],
    page: 1,
    perPage: 15,
    stats: {},
    generatedAt: null,
  };

  const $ = (id) => document.getElementById(id);
  const els = {
    themeToggle: $("themeToggle"),
    updatedAt: $("updatedAt"),
    statTotal: $("statTotal"),
    statOA: $("statOA"),
    statOAPct: $("statOAPct"),
    statLatestYear: $("statLatestYear"),
    statTopics: $("statTopics"),
    resultCount: $("resultCount"),
    searchInput: $("searchInput"),
    yearFrom: $("yearFrom"),
    yearTo: $("yearTo"),
    topicFilter: $("topicFilter"),
    ensoFilter: $("ensoFilter"),
    sourceFilter: $("sourceFilter"),
    oaOnly: $("oaOnly"),
    sortSelect: $("sortSelect"),
    clearFilters: $("clearFilters"),
    activeFilterText: $("activeFilterText"),
    loadingState: $("loadingState"),
    emptyState: $("emptyState"),
    publicationList: $("publicationList"),
    pagination: $("pagination"),
    publicationTemplate: $("publicationTemplate"),
    yearChart: $("yearChart"),
    topicChart: $("topicChart"),
    sourceChart: $("sourceChart"),
  };

  function escapeHTML(value = "") {
    return String(value).replace(/[&<>'"]/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
    }[ch]));
  }

  function normalize(value = "") {
    return String(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  }

  function formatNumber(value) {
    return new Intl.NumberFormat("en-US").format(Number(value || 0));
  }

  function truncateAuthors(authors = []) {
    if (!authors.length) return "Authors not available";
    if (authors.length <= 5) return authors.join(", ");
    return `${authors.slice(0, 5).join(", ")} + ${authors.length - 5} more`;
  }

  function populateSelect(select, values) {
    const first = select.options[0];
    select.innerHTML = "";
    select.appendChild(first);
    [...new Set(values.filter(Boolean))]
      .sort((a, b) => a.localeCompare(b))
      .forEach(value => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      });
  }

  function initFilters() {
    populateSelect(els.topicFilter, state.all.flatMap(p => p.health_topics || []));
    populateSelect(els.ensoFilter, state.all.flatMap(p => p.enso_phases || []));
    populateSelect(els.sourceFilter, state.all.flatMap(p => p.source_databases || []));

    const years = state.all.map(p => Number(p.year)).filter(Boolean);
    if (years.length) {
      els.yearFrom.placeholder = String(Math.min(...years));
      els.yearTo.placeholder = String(Math.max(...years));
    }
  }

  function updateSummary() {
    const total = state.all.length;
    const oa = state.all.filter(p => p.is_oa).length;
    const years = state.all.map(p => Number(p.year)).filter(Boolean);
    const topics = new Set(state.all.flatMap(p => p.health_topics || []));

    els.statTotal.textContent = formatNumber(total);
    els.statOA.textContent = formatNumber(oa);
    els.statOAPct.textContent = total ? `${Math.round((oa / total) * 100)}% of records` : "availability identified";
    els.statLatestYear.textContent = years.length ? Math.max(...years) : "—";
    els.statTopics.textContent = topics.size || "—";

    if (state.generatedAt) {
      const date = new Date(state.generatedAt);
      const fmt = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short", timeZone: "America/Bahia" });
      els.updatedAt.textContent = `Last bibliographic update: ${fmt.format(date)} (Bahia time)`;
    }
  }

  function matchesPublication(pub) {
    const q = normalize(els.searchInput.value.trim());
    const from = Number(els.yearFrom.value) || null;
    const to = Number(els.yearTo.value) || null;
    const topic = els.topicFilter.value;
    const enso = els.ensoFilter.value;
    const source = els.sourceFilter.value;

    if (from && Number(pub.year) < from) return false;
    if (to && Number(pub.year) > to) return false;
    if (topic && !(pub.health_topics || []).includes(topic)) return false;
    if (enso && !(pub.enso_phases || []).includes(enso)) return false;
    if (source && !(pub.source_databases || []).includes(source)) return false;
    if (els.oaOnly.checked && !pub.is_oa) return false;

    if (q) {
      const haystack = normalize([
        pub.title,
        ...(pub.authors || []),
        pub.abstract,
        pub.journal,
        pub.doi,
        ...(pub.health_topics || []),
        ...(pub.enso_phases || []),
      ].join(" "));
      if (!haystack.includes(q)) return false;
    }
    return true;
  }

  function sortPublications(items) {
    const mode = els.sortSelect.value;
    return items.sort((a, b) => {
      if (mode === "citations") return Number(b.cited_by_count || 0) - Number(a.cited_by_count || 0);
      if (mode === "oldest") return Number(a.year || 9999) - Number(b.year || 9999);
      if (mode === "title") return String(a.title || "").localeCompare(String(b.title || ""));
      const dateA = a.publication_date || String(a.year || "");
      const dateB = b.publication_date || String(b.year || "");
      return dateB.localeCompare(dateA) || Number(b.cited_by_count || 0) - Number(a.cited_by_count || 0);
    });
  }

  function describeFilters() {
    const parts = [];
    const q = els.searchInput.value.trim();
    if (q) parts.push(`text “${q}”`);
    if (els.yearFrom.value || els.yearTo.value) parts.push(`years ${els.yearFrom.value || "…"}–${els.yearTo.value || "…"}`);
    if (els.topicFilter.value) parts.push(els.topicFilter.value);
    if (els.ensoFilter.value) parts.push(els.ensoFilter.value);
    if (els.sourceFilter.value) parts.push(els.sourceFilter.value);
    if (els.oaOnly.checked) parts.push("open access");
    els.activeFilterText.textContent = parts.length ? `Filtered by ${parts.join(" · ")}.` : "Showing the full catalogue.";
  }

  function applyFilters(resetPage = true) {
    if (resetPage) state.page = 1;
    state.filtered = sortPublications(state.all.filter(matchesPublication));
    els.resultCount.textContent = formatNumber(state.filtered.length);
    describeFilters();
    renderPublications();
  }

  function makeAction(label, href, primary = false) {
    const a = document.createElement("a");
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = label;
    if (primary) a.classList.add("primary-link");
    return a;
  }

  function renderCard(pub) {
    const node = els.publicationTemplate.content.firstElementChild.cloneNode(true);
    const tags = node.querySelector(".publication-tags");
    const topTopics = (pub.health_topics || []).slice(0, 3);
    topTopics.forEach(topic => {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = topic;
      tags.appendChild(tag);
    });
    (pub.enso_phases || []).slice(0, 2).forEach(phase => {
      const tag = document.createElement("span");
      tag.className = "tag secondary";
      tag.textContent = phase;
      tags.appendChild(tag);
    });

    node.querySelector(".publication-year").textContent = pub.year || "Year unavailable";
    node.querySelector(".publication-title").textContent = pub.title || "Untitled record";
    node.querySelector(".publication-authors").textContent = truncateAuthors(pub.authors || []);
    node.querySelector(".publication-journal").textContent = pub.journal || "Source title unavailable";

    const abstractEl = node.querySelector(".publication-abstract");
    const toggle = node.querySelector(".abstract-toggle");
    if (pub.abstract) {
      abstractEl.textContent = pub.abstract;
      if (pub.abstract.length > 380) {
        toggle.classList.remove("hidden");
        toggle.addEventListener("click", () => {
          const expanded = abstractEl.classList.toggle("expanded");
          toggle.textContent = expanded ? "Collapse abstract" : "Show full abstract";
        });
      }
    } else {
      abstractEl.textContent = "Abstract not available in the harvested metadata.";
    }

    const meta = node.querySelector(".publication-meta");
    const metaItems = [];
    if (pub.cited_by_count !== undefined && pub.cited_by_count !== null) metaItems.push(`${formatNumber(pub.cited_by_count)} citations (OpenAlex)`);
    if (pub.is_oa) metaItems.push("Open access");
    if (pub.doi) metaItems.push(`DOI ${pub.doi}`);
    if ((pub.source_databases || []).length) metaItems.push(`Sources: ${pub.source_databases.join(" + ")}`);
    metaItems.forEach(item => {
      const span = document.createElement("span");
      span.textContent = item;
      meta.appendChild(span);
    });

    const actions = node.querySelector(".publication-actions");
    const mainUrl = pub.oa_url || pub.landing_page_url || (pub.doi ? `https://doi.org/${encodeURIComponent(pub.doi)}` : "");
    if (mainUrl) actions.appendChild(makeAction(pub.oa_url ? "Open access ↗" : "View publication ↗", mainUrl, true));
    if (pub.doi) actions.appendChild(makeAction("DOI ↗", `https://doi.org/${encodeURIComponent(pub.doi)}`));
    if (pub.pmid) actions.appendChild(makeAction("PubMed ↗", `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(pub.pmid)}/`));
    if (pub.openalex_id) actions.appendChild(makeAction("OpenAlex ↗", `https://openalex.org/${encodeURIComponent(pub.openalex_id)}`));

    return node;
  }

  function renderPublications() {
    els.loadingState.classList.add("hidden");
    els.publicationList.innerHTML = "";
    els.pagination.innerHTML = "";

    if (!state.filtered.length) {
      els.emptyState.classList.remove("hidden");
      return;
    }
    els.emptyState.classList.add("hidden");

    const totalPages = Math.ceil(state.filtered.length / state.perPage);
    state.page = Math.min(state.page, totalPages);
    const start = (state.page - 1) * state.perPage;
    const pageItems = state.filtered.slice(start, start + state.perPage);
    const fragment = document.createDocumentFragment();
    pageItems.forEach(pub => fragment.appendChild(renderCard(pub)));
    els.publicationList.appendChild(fragment);
    renderPagination(totalPages);
  }

  function pageButton(text, page, disabled = false, active = false) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `page-button${active ? " active" : ""}`;
    b.textContent = text;
    b.disabled = disabled;
    b.addEventListener("click", () => {
      state.page = page;
      renderPublications();
      document.getElementById("publications").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    return b;
  }

  function renderPagination(totalPages) {
    if (totalPages <= 1) return;
    els.pagination.appendChild(pageButton("←", state.page - 1, state.page === 1));
    const pages = new Set([1, totalPages, state.page - 1, state.page, state.page + 1].filter(p => p >= 1 && p <= totalPages));
    let prev = 0;
    [...pages].sort((a, b) => a - b).forEach(page => {
      if (prev && page - prev > 1) {
        const span = document.createElement("span");
        span.textContent = "…";
        span.className = "page-button";
        span.style.display = "grid";
        span.style.placeItems = "center";
        els.pagination.appendChild(span);
      }
      els.pagination.appendChild(pageButton(String(page), page, false, page === state.page));
      prev = page;
    });
    els.pagination.appendChild(pageButton("→", state.page + 1, state.page === totalPages));
  }

  function countValues(field) {
    const counts = new Map();
    state.all.forEach(pub => {
      const values = Array.isArray(pub[field]) ? pub[field] : [pub[field]];
      values.filter(Boolean).forEach(value => counts.set(value, (counts.get(value) || 0) + 1));
    });
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }

  function renderBarList(container, entries, limit = 10) {
    container.innerHTML = "";
    const selected = entries.slice(0, limit);
    const max = selected[0]?.[1] || 1;
    selected.forEach(([label, value]) => {
      const row = document.createElement("div");
      row.className = "bar-item";
      row.innerHTML = `
        <span class="bar-label" title="${escapeHTML(label)}">${escapeHTML(label)}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${Math.max(2, (value / max) * 100)}%"></span></span>
        <span class="bar-value">${formatNumber(value)}</span>`;
      container.appendChild(row);
    });
    if (!selected.length) container.innerHTML = '<p class="muted">No data yet.</p>';
  }

  function renderYearChart() {
    els.yearChart.innerHTML = "";
    const counts = new Map();
    state.all.forEach(pub => {
      if (pub.year) counts.set(Number(pub.year), (counts.get(Number(pub.year)) || 0) + 1);
    });
    const years = [...counts.keys()].sort((a, b) => a - b);
    if (!years.length) {
      els.yearChart.innerHTML = '<p class="muted">No data yet.</p>';
      return;
    }
    const minYear = Math.min(...years);
    const maxYear = Math.max(...years);
    const maxCount = Math.max(...counts.values());
    for (let year = minYear; year <= maxYear; year += 1) {
      const value = counts.get(year) || 0;
      const wrap = document.createElement("div");
      wrap.className = "year-bar-wrap";
      wrap.dataset.label = `${year}: ${formatNumber(value)}`;
      const bar = document.createElement("div");
      bar.className = "year-bar";
      bar.style.height = `${Math.max(value ? 2 : 0, (value / maxCount) * 100)}%`;
      wrap.appendChild(bar);
      els.yearChart.appendChild(wrap);
    }
  }

  function renderCharts() {
    renderYearChart();
    renderBarList(els.topicChart, countValues("health_topics"), 10);
    renderBarList(els.sourceChart, countValues("source_databases"), 6);
  }

  function clearFilters() {
    els.searchInput.value = "";
    els.yearFrom.value = "";
    els.yearTo.value = "";
    els.topicFilter.value = "";
    els.ensoFilter.value = "";
    els.sourceFilter.value = "";
    els.oaOnly.checked = false;
    els.sortSelect.value = "newest";
    applyFilters(true);
  }

  function wireEvents() {
    let debounce;
    els.searchInput.addEventListener("input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => applyFilters(true), 160);
    });
    [els.yearFrom, els.yearTo].forEach(el => el.addEventListener("input", () => applyFilters(true)));
    [els.topicFilter, els.ensoFilter, els.sourceFilter, els.oaOnly, els.sortSelect].forEach(el => el.addEventListener("change", () => applyFilters(true)));
    els.clearFilters.addEventListener("click", clearFilters);

    els.themeToggle.addEventListener("click", () => {
      const dark = document.documentElement.dataset.theme === "dark";
      document.documentElement.dataset.theme = dark ? "light" : "dark";
      localStorage.setItem("enso-health-theme", dark ? "light" : "dark");
    });
  }

  async function loadData() {
    try {
      const response = await fetch(`data/publications.json?v=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      state.all = Array.isArray(payload) ? payload : (payload.publications || []);
      state.stats = payload.stats || {};
      state.generatedAt = payload.generated_at || null;
      initFilters();
      updateSummary();
      renderCharts();
      applyFilters(false);
    } catch (error) {
      console.error(error);
      els.loadingState.textContent = "Could not load data/publications.json. Check that the file exists and GitHub Pages is serving the repository root.";
      els.updatedAt.textContent = "Bibliographic dataset unavailable.";
    }
  }

  function initTheme() {
    const saved = localStorage.getItem("enso-health-theme");
    const preferred = saved || "light";
    document.documentElement.dataset.theme = preferred;
  }

  initTheme();
  wireEvents();
  loadData();
})();
