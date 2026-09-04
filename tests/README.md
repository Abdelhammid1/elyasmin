# tests/

## Fast smoke — every page returns non-500

```
pytest tests/test_smoke_all_pages.py -v
```

Runs in seconds against the dev DB. Hits every GET route in
`app.url_map`, asserts no 500s, plus spot-checks the 20 top-traffic
pages return 200. Add a new `xxx_id` entry to `_sample_ids()` in
`test_smoke_all_pages.py` when a new blueprint lands.

Motivated by the `returns/purchases/1` 500 that shipped to production
because no one navigated to that URL in dev with real data.

## Full E2E walk (slow, produces screenshots)

```
# Terminal 1
flask run --port 5001
# Terminal 2
python tests/e2e.py
```

Playwright script that clicks through the app, screenshots every
page, and writes `tests/report.html` + `tests/screenshots/*.png`.
Good for release QA; wrong tool for CI-style smoke.
