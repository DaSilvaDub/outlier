
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
5. **Active Roster & Offseason Movement Authority Invariant:**
   - **Zero-Tolerance for Hallucinated Team Affiliations:** Never infer NFL player-team affiliations from historical pre-training memory. Always query and anchor strictly to `outlier_nfl.roster.get_team_depth_chart(team)`, `outlier_nfl.roster.get_starting_qb(team)`, and the verified Outlier normalized feed `data/NFL/normalized/nfl_rosters_latest.json`.
   - **Mandatory Pre-Flight Text Validation:** Any assistant response, game script, betting report, or analysis discussing NFL personnel MUST be checked against `outlier_nfl.roster.validate_analysis_text_for_roster_errors(text)`. If any player is attributed to a former franchise (e.g. A.J. Brown on PHI, Hollywood Brown on KC, Walker on SEA, Rodgers on NYJ/GB, Jones on NYG, Richardson starting), execution must halt or correct immediately.
   - **Authoritative 2026 Core Anchors:**
     - **IND:** Starting QB Daniel Jones (Anthony Richardson is NOT starting); WR Keenan Allen, Josh Downs, Alec Pierce; TE Tyler Warren; RB Jonathan Taylor.
     - **KC:** Starting QB Patrick Mahomes; Lead RB Kenneth Walker III; WRs Rashee Rice, Xavier Worthy, Justin Watson; TE Travis Kelce.
     - **NE:** Starting QB Drake Maye; WRs A.J. Brown (WR1), Romeo Doubs, DeMario Douglas; TE Hunter Henry; RB Rhamondre Stevenson.
     - **PHI:** Starting QB Jalen Hurts; WRs DeVonta Smith, Hollywood Brown, Dontayvion Wicks; RB Saquon Barkley, Tank Bigsby; TE Dallas Goedert.
     - **PIT:** Starting QB Aaron Rodgers; WRs DK Metcalf, Michael Pittman Jr.; RBs Jaylen Warren, Rico Dowdle; TE Pat Freiermuth.
     - **TB:** Starting QB Baker Mayfield; RBs Bucky Irving, Kenny Gainwell; WRs Mike Evans, Chris Godwin Jr., Emeka Egbuka.
     - **MIN:** Starting QB Carson Wentz; RBs Aaron Jones Sr., Jordan Mason; WRs Justin Jefferson, Jordan Addison, Jauan Jennings.
     - **NO:** Starting QB Tyler Shough; RBs Travis Etienne Jr., Alvin Kamara; WR Chris Olave; TE Juwan Johnson, Noah Fant.
     - **TEN:** Starting QB Cam Ward; RBs Tony Pollard, Tyjae Spears; WRs Calvin Ridley, Carnell Tate, Wan'Dale Robinson.
     - **BUF:** Starting QB Josh Allen; RB James Cook III; WRs DJ Moore, Khalil Shakir; TE Dalton Kincaid.
     - **WAS:** Starting QB Jayden Daniels; WRs Terry McLaurin, Stefon Diggs; RB Brian Robinson Jr.
     - **DAL:** Starting QB Dak Prescott; RB Javonte Williams; WRs CeeDee Lamb, George Pickens.
     - **SEA:** Starting QB Sam Darnold; WRs Jaxon Smith-Njigba, Cooper Kupp; RB Zach Charbonnet; TE AJ Barner.
     - **CHI:** Starting QB Caleb Williams; RBs D'Andre Swift, Kyle Monangai; WRs Rome Odunze, Luther Burden III, Kalif Raymond; TE Colston Loveland.
     - **CLE:** Starting QB Deshaun Watson; RBs Quinshon Judkins, Jaleel McLaughlin; WRs KC Concepcion Jr., Jerry Jeudy; TE Harold Fannin Jr.
     - **ATL:** Starting QB Cooper Rush; RBs Bijan Robinson, Tyler Allgeier; WRs Drake London, Darnell Mooney, Jahan Dotson; TE Kyle Pitts Sr.
     - **GB:** Starting QB Jordan Love; RBs MarShawn Lloyd, Josh Jacobs; WRs Christian Watson, Jayden Reed; TEs Tucker Kraft, Jonnu Smith.
     - **MIA:** Starting QB Malik Willis (Tua Tagovailoa is NOT on Miami; signed with ATL); RBs De'Von Achane, Jaylen Wright, Raheem Mostert; WRs Tyreek Hill, Jaylen Waddle, Malik Washington; TE Julian Hill.

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

## Codebase Quirk: `outlier_nfl/external/` Stub Adapter Pattern
`outlier_nfl/external/__init__.py` imports adapters by name (currently `ngs`, `pbp`, and `schedule`). If a new adapter name is added to that import block but the corresponding `.py` file does not exist, the entire `outlier_nfl` package fails to import with a `ModuleNotFoundError`.

**Rule:** Every adapter referenced in `outlier_nfl/external/__init__.py` MUST have a corresponding module file in that directory. The minimal stub pattern is:

```python
def fetch(client, season: int, through_week: int = 22):
    """Stub — returns empty records until a real provider is wired up."""
    return {"records": []}
```

When adding a new adapter name to `__init__.py`, always create the stub file in the same commit. The pipeline treats missing external data gracefully (falls back to `[]`), so stubs are always safe to ship.


