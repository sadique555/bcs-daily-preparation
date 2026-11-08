# BCS Daily Preparation — Free Edition

Static reader app + daily editorial collection on GitHub Pages.

- `index.html` — the reader app (date-wise editorials, tap-a-word
  dictionary, vocabulary + bookmarks saved on the device)
- `collect.py` — stdlib-only collector: 11 newspapers via Google News
  RSS site feeds, dedupe by URL + headline, writes `data/articles.json`
- `.github/workflows/collect.yml` — runs daily 06:30 Asia/Dhaka
  (00:30 UTC) and commits new data

Items carry the paper's published excerpt + the original link.
Full text is not stored: Google News links cannot be resolved
programmatically, and no paywall is bypassed.
