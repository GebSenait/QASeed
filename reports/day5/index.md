# QASeed — Day 5 reports

| File | What it shows |
|---|---|
| [day5_test_report_20261111-051707.html](day5_test_report_20261111-051707.html) | **Final Day 5 test run: 323 passed**, 0 failed, 0 skipped |
| [ui_e2e_payment_live.txt](ui_e2e_payment_live.txt) | **Live Gemini, through the Streamlit app:** payment scenario (8 succeeded / 4 failed) → plan (1 request, 6.3 s, 1,319 tokens) → 48 rows → 24/24 checks → CSV ZIP → 10/10 independent ZIP checks PASS |
| [ui_e2e_payment_offline.txt](ui_e2e_payment_offline.txt) | Same scenario replayed offline from the recorded live reply (no API call): PASS |
| [generator_S01_mock.txt](generator_S01_mock.txt) | Flagship scenario: plan → 200 linked rows → **25/25 data checks PASS** (requested vs generated vs verified, sample rows) |
| [datagen_mock.json](datagen_mock.json) | All 7 valid offline scenarios generated and verified — **120/120 checks passed** |
| [day5_test_report_20261111-011124.html](day5_test_report_20261111-011124.html) | Earlier run (generator + checks only): 262 passed |

Offline runs: plans come from scripted or recorded AI replies (`source=mock`); seed 42, as-of date 2026-11-11. `generator_S01_mock.txt` and `datagen_mock.json` were regenerated after the payment/invoice rule was added (previously 23/23 and 116/116; the two new checks raise the totals).

## Test runs (`python scripts/test_report.py --day 5`)

Three intermediate same-day runs (302, 311 and 323 passed, all PASS) were superseded by the final run below and removed before commit.

| Run (local time) | Result | Passed | Failed | Errors | Skipped | Commit | Report |
|---|---|---|---|---|---|---|---|
| 2026-11-11 11:00:03 | FAIL (exit 1) | 374 | 0 | 1 | 0 | `7b3ddec+uncommitted` | removed: one browser test timed out loading the local page (setup error, intermittent); its error log contained a local file path. Fixed with a reload-once retry; see the next run. |
| 2026-11-11 11:07:20 | PASS | 375 | 0 | 0 | 0 | `7b3ddec+uncommitted` | [day5_test_report_20261111-110720.html](day5_test_report_20261111-110720.html) |
