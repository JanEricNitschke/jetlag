# AGENTS.md — Jet Lag Maps project guidelines

## Tooling

Everything runs through `uv`:

```bash
uv run pytest                    # test suite (32 tests)
uv run ruff check src tests      # lint
uv run ruff format src tests     # format
uv run pyrefly check src         # type check
uv run pre-commit run --all-files  # all hooks
uv run jetlag-maps import --kml <file> --out data   # KML → JSON import
uv run jetlag-maps render --data data --out kml      # JSON → KML
uv run jetlag-maps export --data data --out export/export.xlsx  # JSON → xlsx (REQUIRED after data edits)
uv run jetlag-maps export --data data --out export/   # JSON → per-season CSVs ONLY
uv run jetlag-maps web --data data --out maps         # JSON → folium HTML
uv run jetlag-maps sync --data data                  # push to Google Sheet
uv run jetlag-maps pull --out data                   # pull from Google Sheet
```

Beware: `export --out export/` (directory) writes only the CSVs — it
never refreshes `export/export.xlsx`, and pytest compares the committed
xlsx against the data. After data edits run BOTH export forms, or the
xlsx test fails.

## Critical modeling rule: duplicate visits

The source KML shows **each location exactly once per journey**. It does NOT
model duplicate visits — every return to a station, airport, or hub is a
separate real-world event but share the same KML placemark. This means:

- **Return trips** (walk back from a flag/challenge to the station you came
  from) must be manually added as duplicated stops in the model and data.
- **Hub pass-throughs** (stations you ride through without getting off or
  doing anything relevant) do NOT get their own stop — record the route in
  the leg's note instead (e.g. `"train — Keihin-Tōhoku via Ueno"`).
- When building geometry, respect the actual route — a leg that says
  `"train — Keihin-Tōhoku via Ueno"` must route through Ueno even if there
  is no stop pin there.

## Resources

### Fan timeline docs (u/Doubl3Sid3d)

One Google Doc per season with detailed per-leg times, challenges, and
reasoning. Fetch as plain text:
`https://docs.google.com/document/d/<id>/export?format=txt`

Post listing via pullpush.io (Reddit blocks direct access):
`https://api.pullpush.io/reddit/search/submission/?author=Doubl3Sid3d&size=100`

Docs exist for: S0, S1–S15 (no S16), S13.5, S17, S18. Missing: S16
(H+S UK), S19 (Japanorama), S20 (H+S Italy).

### Official production schedule (user-provided, authoritative)

The "Prod Ended" column is the winner-determination moment. Supersedes
the doc's "Filmed" lines when they conflict.

| # | Theme | Location | Prod Began | Prod Ended | Days | Premiere Nebula | Premiere YouTube | Eps | Int | Finale Nebula | Finale YouTube |
|---|-------|----------|------------|------------|------|----------------|-----------------|-----|-----|--------------|----------------|
| 0 | Crime Spree | US | 14-Aug-2021 03:00 UTC−06 | 16-Aug-2021 ~18:00 UTC−07 | 1 | 16-Feb-2022 | — | 14 | | 16-Mar-2022 | — |
| 1 | Connect Four Across America | Western US | 3-Mar-2022 04:30 UTC−06 | 5-Mar-2022 11:16 UTC−07 | 2 | 25-May-2022 | 25-May-2022 | 3 | 7 | 1-Jun-2022 | 8-Jun-2022 |
| 2 | Race to Circumnavigate the Globe in 100 Hours | Global | 12-May-2022 05:00 UTC−06 | 16-May-2022 21:37 UTC−06 | 4 | 29-Jun-2022 | 6-Jul-2022 | 5 | 7 | 27-Jul-2022 | 3-Aug-2022 |
| 3 | Tag Across Europe | Central Europe | 26-Jul-2022 06:00 UTC+02 | 28-Jul-2022 20:00 UTC+02 | 2 | 7-Sep-2022 | 14-Sep-2022 | 7 | 7 | 19-Oct-2022 | 26-Oct-2022 |
| 4 | Race To The Most States | US | 25-Oct-2022 08:00 UTC−04 | 28-Oct-2022 19:28 UTC−06 | 3 | 7-Dec-2022 | 14-Dec-2022 | 5 | 7 | 4-Jan-2023 | 11-Jan-2023 |
| 5 | Race to the End of the World | New Zealand | 22-Jan-2023 08:00 UTC+13 | 26-Jan-2023 ~14:00 UTC+13 | 4 | 1-Mar-2023 | 8-Mar-2023 | 8 | 7 | 19-Apr-2023 | 26-Apr-2023 |
| 6 | Capture the Flag | Japan | 16-Apr-2023 07:00 UTC+09 | 20-Apr-2023 ~08:34 UTC+09 | 4 | 31-May-2023 | 7-Jun-2023 | 7 | 7 | 12-Jul-2023 | 19-Jul-2023 |
| 7 | Tag Across Europe 2 | Central Europe | 8-Jul-2023 06:00 UTC+02 | 10-Jul-2023 ~17:40 UTC+02 | 2 | 6-Sep-2023 | 13-Sep-2023 | 6 | 7 | 11-Oct-2023 | 18-Oct-2023 |
| 8 | Arctic Escape | US | 23-Oct-2023 15:00 UTC−08 | 27-Oct-2023 ~17:30 UTC−04 | 4 | 13-Dec-2023 | 20-Dec-2023 | 6 | 7 | 17-Jan-2024 | 24-Jan-2024 |
| 9 | Hide + Seek | Switzerland | 11-Dec-2023 08:30 UTC+01* | 14-Dec-2023 17:06 UTC+01 | 3 | 28-Feb-2024 | 6-Mar-2024 | 5 | 7 | 27-Mar-2024 | 3-Apr-2024 |
| 10 | Au$tralia | Australia | 11-Mar-2024 07:30 UTC+11 | 14-Mar-2024 20:30 UTC+11 | 3 | 15-May-2024 | 22-May-2024 | 6 | 7 | 19-Jun-2024 | 26-Jun-2024 |
| 11 | Tag Across Europe 3 | Southern Europe | 29-May-2024 06:00 UTC+02 | 31-May-2024 19:01 UTC+02 | 2 | 21-Aug-2024 | 28-Aug-2024 | 6 | 7 | 25-Sep-2024 | 2-Oct-2024 |
| 12 | Hide + Seek 2 (Japan) | Japan | 16-Sep-2024 07:30 UTC+09* | 21-Sep-2024 17:46 UTC+09 | 5 | 4-Dec-2024 | 11-Dec-2024 | 7 | 7 | 15-Jan-2025 | 22-Jan-2025 |
| 13 | Schengen Showdown | Europe | 6-Jan-2025 06:30 UTC+00 | 10-Jan-2025 ~15:00 UTC+01* | 4 | 5-Mar-2025 | 12-Mar-2025 | 6 | 7 | 9-Apr-2025 | 16-Apr-2025 |
| 13.5 | Hide + Seek Across NYC | New York City | 17-Dec-2024 12:30 UTC−05* | 29-Jan-2025 12:56 UTC−05 | 2 | 23-Apr-2025 | 30-Apr-2025 | 2 | 7 | 30-Apr-2025 | 7-May-2025 |
| 14 | Snake (South Korea) | South Korea | 2-Apr-2025 07:00 UTC+09 | 5-Apr-2025 ~18:30 UTC+09 | 3 | 11-Jun-2025 | 18-Jun-2025 | 6 | 7 | 16-Jul-2025 | 23-Jul-2025 |
| 15 | Tag Across Europe: All Stars | Central Europe | 10-Jun-2025 06:00 UTC+02 | 11-Jun-2025 ~12:15 UTC+02 | 1 | 17-Sep-2025 | 24-Sep-2025 | 6 | 7 | 22-Oct-2025 | 29-Oct-2025 |
| 16 | Hide + Seek United Kingdom | UK | 8-Sep-2025 11:00 UTC+01* | 13-Sep-2025 16:27 UTC+01 | 5 | 17-Dec-2025 | 24-Dec-2025 | 7 | 7 | 28-Jan-2026 | 4-Feb-2026 |
| 17 | Taiwan: Rail Rush | Taiwan | 5-Dec-2025 07:30 UTC+08 | 9-Dec-2025 ~15:30 UTC+08 | 4 | 18-Mar-2026 | 25-Mar-2026 | 8 | 7 | 6-May-2026 | 13-May-2026 |
| 18 | Stateside Scramble | North America | 5-Mar-2026 08:00 UTC−05 | 9-Mar-2026 17:28 UTC−04 | 4 | 10-Jun-2026 | 17-Jun-2026 | 6 | 7 | 15-Jul-2026 | 22-Jul-2026 |
| 19 | Japanorama | Japan | 21-May-2026 07:00 UTC+09 | 27-May-2026 12:42 UTC+09 | 6 | 19-Aug-2026 | 26-Aug-2026 | 8 | 7 | 7-Oct-2026 | 14-Oct-2026 |
| 20 | Hide + Seek Italy | Italy | ≤18-Sep-2026 ≤15:00 UTC+02 | ≤21-Sep-2026 | TBA | TBA | TBA | TBA | TBA | TBA | TBA |

Note: Days = travel days between Prod Began and Prod Ended (inclusive,
excluding overnight rest). `*` = start time offset by DST transition or
multi-day gap (e.g. S13.5 filmed Dec + Jan pickup).

Known discrepancies with the timeline docs (resolved in favour of this
schedule): S2 finish (elapsed-time misread as 1:37 am → actually 21:37
16-May UTC−06), S5 weekday label (Mon-Fri → actually Sun-Thu), S6 entire
date range (doc said Aug, actually Apr), S6 prod end (schedule originally
said Apr 19 ~16:00; E7's title math — "a 96 hour game" from the Apr 16
07:00 start — puts the tiebreaker the following morning, winner
determined Apr 20 ~08:34), S12 7th day (doc said Sep 22, actually Sep 21),
S13.5 first-day date (doc said Dec 18, actually Dec 17), S14 date range
(doc said Apr 1-4, actually Apr 2-5).

### Fan wiki (jetlagthegame.fandom.com)

Season pages with episode lists, filming dates (often tweet-sourced),
gameplay rules, and challenge descriptions.

### Wikipedia (en.wikipedia.org/wiki/Jet_Lag:_The_Game)

Season summaries, winners, game descriptions. Less detailed than the fan
wiki but more reliable for air dates.

### Seed files (seed/)

- `seed/raw/` — raw extracted KML/discord/wiki dumps per season
- `seed/season_NN_*.md` — structured route reconstructions from clips
- `seed/work/` — per-season fill scripts and lookup data
- For S13+ these are **trailer-based speculation** — trust the reddit
  docs over the seeds for content and ordering

### Committed KML (All Known Jet Lag Locations.kml)

The fan-curated master KML. Contains one placemark per location with
"Members who visited" lists. Does NOT contain:
- Duplicate visits (see the modeling rule above)
- Travel legs (geometry is added by the `guess-legs` / manual fetch tools)
- Time data beyond occasional `datetime:` lines

## Season structure notes

- S6 (Capture the Flag): 3 rounds + a Tokyo tiebreaker, all starting at
  Tokyo Station. Round boundaries are marked in data notes with
  `"Start of Round N"` / `"Start of Tiebreaker"` — the HTML renderer
  creates one toggleable layer per round from these markers.
- S13+ models: the seed files are speculation from trailers/teasers.
  Trust the reddit docs for content, order, and times.
- **Round-boundary markers** (`"Start of Round N"` / `"Start of
  Tiebreaker"`) go on the round's **Tokyo Station** start stop — every
  round of the show begins at Tokyo Station (R1 at Marunouchi Square,
  the plaza outside Tokyo Station). The renderer splits rounds by
  *array position* (everything from one marker to the next belongs to
  that round), so a marker must sit at or before the round's first stop
  in the array; move the stop earlier in the array if its date is the
  earliest.
- Filming dates: the production schedule is authoritative. The doc's
  "Filmed X" lines have been wrong multiple times (S2, S5, S6, S12, S14).

## Data conventions

- Times are timezone-aware (`+HH:MM` offsets in JSON), attached from stop
  coordinates via `timezonefinder` (DST-correct for the visited date).
- `when.start` on a stop = the moment the player arrived there (or the
  notable event if it's an event stop). `end` = rest-period end.
- Travel legs (`to_next`) carry the geometry; mode + note describe the
  service. `quality` ranges from `unknown` (straight line) to `exact`
  (actual GPS/ADS-B track).
- Coordinates must be at most **15 significant digits** — more than that
  does not survive the Excel round-trip, and the xlsx test fails. Round
  user-supplied coordinates before adding them (precedent: commit
  b8c0044, S5; and the S6 Omiya bank stop).
- Artifacts in `kml/`, `export/`, and `maps/` are regenerated from
  `data/*.json` — always regenerate all three after data edits, then
  run `pytest` to verify byte-identical round-trips.
