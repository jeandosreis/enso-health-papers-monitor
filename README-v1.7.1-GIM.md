# ENSO & Health Research Monitor — v1.7.1 GIM hotfix

This hotfix corrects a refactoring error in v1.7:

- `fetch_gim()` now builds `gim_categories` before using it, merging the canonical English health vocabulary with the Portuguese/Spanish GIM aliases.
- `fetch_pubmed()` no longer references GIM-only variables (`gim_cfg` / `gim_categories`).
- The GIM audit now reports the actual sensitive discovery scope rather than labeling the server-side search as title-only.

No methodological rule was loosened: GIM discovery remains sensitive, but final inclusion remains local and title-first.

Test first:

```powershell
python scripts\test_gim.py
```

Only after the GIM test works should you run:

```powershell
python scripts\update_publications.py
```
