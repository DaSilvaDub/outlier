
## Codebase Quirk: `normalizer.implied_probability()`
When working with `outlier_scrapers`, note that `normalizer.implied_probability(price)` returns a **percentage** (e.g., `52.5` for -110), NOT a decimal probability (e.g., `0.525`). If you are passing this value into a field that expects a standard `[0, 1]` probability (such as those checked by `feedback.py`), you must divide the result by 100.

## Codebase Quirk: Push Probability Adjustments
When working with probabilities in `pack.py`, `game_totals.py`, or similar projection modules, consensus probabilities derived from devigged line odds (e.g., `devig_decimal` or `p_over_headline`) are conditional on a definitive win/loss (i.e., assuming no push occurs). To determine the true absolute win probability for a given side, you must multiply the conditional probability by `(1.0 - push_prob)`. Ensure this adjustment is made before populating probability output columns (like `model_prob` or `market_consensus_prob`) or feeding them into final sizing/edge calculations, otherwise the sizing logic will dangerously overestimate the edge on push-capable lines.

## Codebase Quirk: Outlier API JSON Control Characters
When using `json.loads()` to parse raw responses from the Outlier API (e.g., in `api.py`), ALWAYS pass `strict=False`. The upstream API occasionally returns invalid unescaped control characters in string fields, which will crash the standard `json.loads()` parser if strict mode is enforced.

## Codebase Quirk: NFL Consensus Line Selection vs. Alternate Ladders
When extracting player props from `data/NFL/normalized/nfl_props_*.json`, the Outlier dataset often contains multiple alternate ladder lines (e.g., +750 to -900 odds) and exchange quotes for the same player and market. Never pick the consensus line solely by `sum(len(books))`, as alternate ladders can aggregate large book counts across non-standard lines. Always enforce a balanced two-way market check: both `OVER` and `UNDER` must exist within normal betting juice (`-220 <= odds <= +180`), minimizing the deviation from -110. For touchdown markets (`ANYTIME_TD`), select `line == 0.5` and `position == 'OVER'`.

## NFL Game Script Calibration Heuristics (Learned from BUF 41 - DET 31 & Slate Re-Basing)
1. **Deficit-Risk Discount on Road Underdog RB Rushing Lines:** If a team is a road underdog (+4.5 or greater) facing a high-scoring favorite (Team Total >= 28.0), apply a **15% downward volume haircut** to the running back's projected rushing attempts and yardage. When an underdog falls behind by two scores, run volume collapses; pivot exposure to **Anytime TD** or **Receiving Props**, which remain active in catch-up mode.
2. **Two-High Shell Target Divergence in Comeback Mode:** When a favorite establishes a multi-score lead, defenses play deep two-high Cover-2/Cover-4 shells. This neutralizes vertical perimeter deep threats (aDOT >= 14.0, e.g. Jameson Williams), while funneling increased target volume (+20%) to intermediate slot receivers (e.g. Amon-Ra St. Brown) and pass-catching tight ends (e.g. Sam LaPorta).
3. **Multi-Window Hit Rate Re-Basing & Multi-Book Liquidity:** Never select or recommend a prop based solely on an isolated L5 hit rate (avoiding small-sample noise and alternate-ladder bait). A valid selection requires:
   - **Hit Rate Convergence:** L5 >= 80% *and* L10 >= 70%–80%, anchored by stable season snap/usage baselines.
   - **Balanced Line Movement & Consensus:** Quoted across multi-book consensus (minimum 3–5 regulated books) within standard two-way juice (-145 to +115), confirming sharp market validation rather than synthetic bookmaker ladders.
4. **Compiled Situational Factors (Injuries, Matchups, Weather):** Every prediction must cross-reference:
   - **Vacated Usage:** Reallocated target/rush share when key personnel are inactive or on IR.
   - **Opponent Defensive Efficiency:** Matchups against specific coverage shells and defensive front weaknesses (e.g., zone run defense vs. man perimeter).
   - **Environmental Calibration:** Outdoor wind (>12–15 mph) or precipitation haircuts on vertical passing vs. dome/controlled venue pace upgrades.
5. **Active Roster & Offseason Movement Authority Invariant:** Never infer or hallucinate NFL player-team affiliations (especially starting quarterbacks and skill players who changed teams in free agency or trades, e.g. **Kenneth Walker III was signed by the Kansas City Chiefs (KC)** as their lead running back, **Daniel Jones is on the Indianapolis Colts (IND)** as starting QB (Anthony Richardson is NOT starting), **A.J. Brown is on the New England Patriots (NE)** (NOT on the Eagles), **Hollywood Brown is on the Philadelphia Eagles (PHI)** (NOT on the Chiefs), **Aaron Rodgers is on the Pittsburgh Steelers (PIT)**, **Geno Smith is on the New York Jets (NYJ)**, **Carson Wentz is on the Minnesota Vikings (MIN)**, **Cam Ward is on the Tennessee Titans (TEN)**, **DK Metcalf is on the Pittsburgh Steelers (PIT)**, **David Montgomery is on the Houston Texans (HOU)**, **Travis Etienne Jr. is on the New Orleans Saints (NO)**, **DJ Moore is on the Buffalo Bills (BUF)**, **Stefon Diggs is on the Washington Commanders (WAS)**, **Javonte Williams & George Pickens are on the Dallas Cowboys (DAL)**, **Cooper Kupp is on the Seattle Seahawks (SEA)**) from historical pre-training memory. Always query and anchor to `outlier_nfl.roster.get_team_depth_chart(team)`, `outlier_nfl.roster.get_starting_qb(team)`, and the normalized feed's verified `team`, `player_name`, and `matchup` in `nfl_rosters_latest.json` or `nfl_props_*.json`.

## Windows PowerShell Python Execution & UTF-8 Console Encoding
1. **Avoid Complex Quotes in Terminal One-Liners:** In Windows PowerShell, running inline Python scripts (`python -c "..."`) with nested quotes or `$()` frequently fails with parser errors. Write scratch scripts to `<appDataDir>\brain\<conversation-id>/scratch/` instead.
2. **Stdout Encoding Constraint:** Python on Windows defaults stdout to `cp1252`, which raises `UnicodeEncodeError` when printing Unicode characters like `↳` (`\u21b3`) or em-dashes `—`. Scripts that print formatted output must call `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` and favor standard ASCII text.

## Codebase Quirk: Feedback Database Path (`feedback.sqlite3`)
The authoritative SQLite database for Outlier decisions, snapshots, and settlements is `calibration/feedback.sqlite3` (NOT `feedback.db`). Always use `outlier_scrapers.feedback_db.DEFAULT_DB_PATH` or specify `calibration/feedback.sqlite3` when querying or running raw diagnostics.

## Codebase Quirk: Test Pack Fixtures & Directory Naming Invariant
Never create test or benchmark directories in `packs/` using pure date digits (e.g., `packs/2099-07-07`). `organize_today_run2.py` scans `packs/` for all directories matching `d.name.replace("-", "").isdigit()` and selects `subdirs[-1]` as the latest slate. Any test fixture directory in `packs/` MUST be prefixed with an underscore or text (e.g., `_test_fixture_2099-07-07`) so production export scripts ignore it.

## MLB Strikeout Modeling Heuristics: Opponent Handedness K-Rate Split
When projecting pitcher strikeouts or auditing candidate edges, always check the opposing lineup's specific strikeout rate against the pitcher's handedness (vs LHP or vs RHP), rather than their overall team K-rate. Teams with pronounced splits (e.g., strikeout-heavy righty-dominant lineups facing a soft-tossing southpaw) consistently outperform baseline projections by +0.5 to +1.0 Ks.

## Exchange Alpha vs. Thin Liquidity Filter Calibration
When an exchange book (Prophetx or Novig) offers a player prop line that is a full integer/rung lower than consensus retail sportsbooks (e.g., 4.5 on Prophetx vs. 5.5 on DraftKings/FanDuel), do not treat this solely as a disqualifying `thin_liquidity` trap. Cross-reference whether the consensus higher line also carries positive projection support; if so, the discounted exchange line represents genuine market alpha.

