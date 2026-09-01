# ENSO & Health Research Monitor

A static GitHub Pages website that automatically builds a searchable bibliographic catalogue of scientific literature connecting **El Niño, La Niña and ENSO** with **human health**.

## What is included

```text
enso-health-monitor/
├── index.html
├── assets/
│   ├── app.js
│   └── styles.css
├── config/
│   └── search_config.json
├── data/
│   └── publications.json
├── scripts/
│   └── update_publications.py
├── .github/
│   └── workflows/
│       └── update_publications.yml
├── requirements.txt
└── README.md
```

## Data strategy

- **OpenAlex**: primary broad scholarly discovery. Version 1.1 uses one combined ENSO × health **title/abstract** query through OpenAlex OQL, then classifies results locally. This avoids repeatedly crawling overlapping health-category searches and reduces false positives caused by matches only in full text.
- **PubMed**: primary biomedical discovery. The script searches PubMed title/abstract fields through NCBI E-utilities and fetches article metadata in XML.
- **Crossref**: DOI-based metadata enrichment when journal, authors or landing-page fields are missing.
- **Deduplication**: DOI first, then PMID, then normalized title + publication year.
- **Classification**: transparent keyword rules in `config/search_config.json` assign health topics and ENSO labels.

This is a literature-discovery monitor, not a guarantee of exhaustive systematic-review coverage.

## 1. Create the GitHub repository

Create a new repository, for example:

```text
enso-health
```

Copy all files from this package to the repository root and push them to the `main` branch.

## 2. Configure GitHub Pages

In the repository:

1. Open **Settings → Pages**.
2. Under **Build and deployment**, choose **Deploy from a branch**.
3. Select branch **main** and folder **/(root)**.
4. Save.

The site will then be available at a URL similar to:

```text
https://YOUR-USERNAME.github.io/enso-health/
```

## 3. Configure repository secrets

Go to:

**Settings → Secrets and variables → Actions → New repository secret**

Recommended secrets:

### `CONTACT_EMAIL`

Your contact email. It is sent to APIs that support identifying polite automated clients.

### `OPENALEX_API_KEY`

Recommended. Obtain an OpenAlex API key from your OpenAlex account/settings and save it here.

The script can make basic OpenAlex requests without a key, but a key gives a larger free API budget.

### `NCBI_API_KEY`

Optional. An NCBI API key increases the rate available to E-utilities. The workflow also works without one and automatically uses a slower request interval.

**Never place API keys directly in `index.html`, `app.js`, or any committed file.**

## 4. Run the first update

Open:

**Actions → Update scientific publications → Run workflow**

The workflow will:

1. install Python dependencies;
2. query OpenAlex;
3. query PubMed;
4. merge duplicate publications;
5. enrich incomplete DOI metadata through Crossref;
6. regenerate `data/publications.json`;
7. commit the updated JSON to the repository.

After the commit, GitHub Pages will serve the updated catalogue.

## 5. Automatic daily updates

The included workflow runs daily at:

```text
06:17 UTC
03:17 America/Bahia (UTC-3)
```

You can change the cron expression in:

```text
.github/workflows/update_publications.yml
```

## 6. Change the scientific search strategy

Edit:

```text
config/search_config.json
```

The file has two main sections.

### ENSO vocabulary

```json
"enso_terms": [
  "El Niño",
  "El Nino",
  "La Niña",
  "La Nina",
  "ENSO",
  "El Niño-Southern Oscillation"
]
```

### Health topics

Each health topic has a list of terms. Example:

```json
"Vector-borne diseases": [
  "dengue",
  "malaria",
  "zika",
  "chikungunya",
  "vector-borne"
]
```

For OpenAlex, the updater combines all health terms into one Boolean title/abstract query and assigns the thematic groups locally after retrieval. PubMed continues to run database-specific title/abstract queries by health topic and merges the resulting PMID set before fetching metadata.

## 7. Local testing

Install Python dependencies:

```bash
python -m pip install -r requirements.txt
```

For a full OpenAlex harvest, set a free OpenAlex API key before running the updater. In PowerShell:

```powershell
$env:OPENALEX_API_KEY="YOUR_OPENALEX_API_KEY"
$env:CONTACT_EMAIL="your@email.example"
```

The key is strongly recommended for the initial harvest because anonymous OpenAlex usage has a much smaller daily API budget. Do not save the key in a committed file.

Run the bibliographic updater:

```bash
python scripts/update_publications.py
```

If OpenAlex reaches its API budget, version 1.1 keeps any OpenAlex records already downloaded instead of discarding the entire partial harvest.

Then serve the repository with a local HTTP server (do not open `index.html` with `file://`, because the page fetches JSON):

```bash
python -m http.server 8000
```

Open:

```text
http://localhost:8000
```

## JSON record structure

Each merged publication has fields similar to:

```json
{
  "id": "doi:10.xxxx/example",
  "title": "...",
  "year": 2025,
  "publication_date": "2025-07-01",
  "authors": ["Author A", "Author B"],
  "journal": "Journal name",
  "doi": "10.xxxx/example",
  "pmid": "12345678",
  "openalex_id": "W123456789",
  "abstract": "...",
  "cited_by_count": 25,
  "is_oa": true,
  "oa_url": "...",
  "landing_page_url": "...",
  "health_topics": ["Vector-borne diseases"],
  "enso_phases": ["El Niño", "ENSO"],
  "source_databases": ["OpenAlex", "PubMed"],
  "type": "article",
  "affiliation_countries": ["BR"]
}
```

## Important methodological note

The monitor is intentionally broad for **discovery and surveillance**. A formal systematic review should still define database-specific validated search strings, screening criteria, protocol registration where appropriate, manual review and a documented inclusion/exclusion process.

## Version 1.1 notes

- Fixes loss of already-downloaded OpenAlex records after an HTTP 429 response.
- Replaces repeated category-by-category OpenAlex crawls with one combined query.
- Restricts OpenAlex discovery to title/abstract relevance rather than generic full-text matching.
- Reports the OpenAlex total match count at the beginning of the crawl.

## API documentation

- OpenAlex API: https://help.openalex.org/api/
- OpenAlex search: https://help.openalex.org/api/searching/
- PubMed / NCBI E-utilities: https://www.ncbi.nlm.nih.gov/books/NBK25501/
- Crossref REST API: https://www.crossref.org/documentation/retrieve-metadata/rest-api/
