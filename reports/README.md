# Test & Evaluation Reports

Evidence for each challenge day. Each day folder has an `index.md` summary; the other files are the raw results it links to. Files are kept exactly as generated (never edited by hand) so the numbers can be checked and reproduced.

## Summary

| Day | Result | Summary |
|---|---|---|
| 3 | 136 tests passing; 18-scenario planner baseline | [day3/index.md](day3/index.md) |
| 4 | 227 tests passing; evaluation 21/30 → 30/30 (mock); live Gemini 22/22; 0 unsafe requests accepted | [day4/index.md](day4/index.md) |
| 5 | 375 tests passing (incl. 5 real-browser tests); live Gemini through the app: 48 rows, 24/24 checks, verified CSV ZIP | [day5/index.md](day5/index.md) |
| 6 | 404 tests passing (incl. 5 Playwright browser tests); security review, 2 P1 bugs fixed with regression tests; live Gemini through the app after the fixes: 1 request, 48 rows, 26/26 checks, verified CSV ZIP | [day6/index.md](day6/index.md) · [release readiness](../docs/day6_release_readiness.md) |

## Raw files

| Type | Files | Produced by |
|---|---|---|
| Test reports (self-contained HTML) | `dayN/dayN_test_report_<timestamp>.html` | `python scripts/test_report.py --day N` |
| Evaluation results | `day4/eval_*.json` (raw) with `eval_*.txt` (readable) | `python -m qaseed.evaluate` |
| Generator results | `day5/generator_S01_mock.txt`, `day5/datagen_mock.json` | `python -m qaseed.generator` |
| End-to-end UI runs | `day5/ui_e2e_payment_*.txt`, `day6/ui_e2e_payment_live.txt` | `python scripts/day5_ui_e2e.py` (add `--live --confirm` for Gemini) |

**Mock vs live:** "mock" results replay recorded or hand-written AI replies to test QASeed's own logic, with no API call. "Live" results called Google Gemini. The two are never mixed. A `SKIPPED` live run is not a pass.
