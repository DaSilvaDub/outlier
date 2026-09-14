# Investigation Report: Comprehensive 32-Team Normalization Strategy for `outlier_nfl/config.py`

**Milestone:** M1 Iteration 2  
**Agent:** Explorer 2 (`teamwork_preview_explorer_m1_r2_2`)  
**Target File:** `outlier_nfl/config.py`  
**Related Test Files:** `tests/test_nfl_stress.py`, `tests/test_nfl_normalizer.py`  
**Date:** 2026-09-12  

---

## 1. Executive Summary

In Milestone 1 adversarial testing, Challenger 1 (`teamwork_preview_challenger_m1_1`) identified a critical gap in `outlier_nfl/config.py`: `normalize_team()` returned `None` when provided with standard sports feed, wire service, and odds-screen naming formats that combine team codes/abbreviations with nicknames (e.g., `"KC Chiefs"`, `"SF 49ers"`, `"TB Bucs"`, `"NY Giants"`, `"NY Jets"`, `"PIT Steelers"`, `"BAL Ravens"`, `"GB Packers"`, `"PHI Eagles"`).

Downstream Milestone 2 extractors (`games.py` for game lines and team totals; `props.py` for player props) depend entirely on `normalize_team()` to match matchup events, attribute team props, and infer player opponents. If team normalization fails on these ubiquitous composite formats, odds rows are silently dropped or assigned `None`, creating severe data loss in the NFL pipeline.

This investigation conducted a comprehensive census of all 32 NFL franchises across all 8 divisions. We verified that:
1. Expanding `NFL_TEAM_ALIASES` with 155 new composite aliases resolves 100% of composite naming variations across all 32 franchises.
2. The expansion introduces **zero key collisions or ambiguity** across the 32 franchises.
3. The expansion produces **zero false positives** on non-NFL sports entities (e.g., `"Los Angeles Lakers"`, `"New York Yankees"`, `"Alabama Crimson Tide"` continue to return `None`).
4. Algorithmic prefix matching (e.g. `compact.startswith("LOSANGELES")`) was evaluated and **firmly rejected** because it causes false-positive misclassification of non-NFL teams. Pure dictionary expansion maintains deterministic $O(1)$ performance and strict validation integrity.

---

## 2. Root Cause Analysis

### 2.1 The Current Implementation
In `outlier_nfl/config.py`:
```python
def _compact_key(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).upper()
    return "".join(ch for ch in text if ch.isalnum())

def normalize_team(value: Any) -> str | None:
    compact = _compact_key(value)
    if not compact:
        return None
    return NFL_TEAM_ALIASES.get(compact)
```

In the current dictionary (lines 82–244 of `config.py`), `NFL_TEAM_ALIASES` contains 153 static entries. For each team, it typically maps:
- Canonical 2-or-3 letter code (e.g., `"KC"`, `"BAL"`, `"SF"`)
- Full city name (e.g., `"KANSASCITY"`, `"BALTIMORE"`, `"SANFRANCISCO"`)
- Full official name (e.g., `"KANSASCITYCHIEFS"`, `"BALTIMORERAVENS"`, `"SANFRANCISCO49ERS"`)
- Nickname alone (e.g., `"CHIEFS"`, `"RAVENS"`, `"49ERS"`)

### 2.2 Why Feeds Emit Composite Code + Nickname Strings
In sports betting feeds (Outlier, DraftKings, FanDuel, BetMGM, ESPN, Action Network, CBS Sports):
1. **Ticker & Mobile Constraints**: Display space limits frequently truncate city names to standard codes or abbreviations followed by the nickname (e.g., "KC Chiefs", "SF 49ers", "TB Bucs", "NE Pats", "GB Packers").
2. **Two-Team Market Disambiguation**: In two-team metropolitan areas (New York and Los Angeles), feeds almost never use "New York" alone. Instead, they use "NY Giants" and "NY Jets". In `_compact_key()`, "NY Giants" becomes `"NYGIANTS"`. Because the current dictionary only contains `"NEWYORKGIANTS"`, `"NYG"`, and `"GIANTS"`, `"NYGIANTS"` evaluates to `None`!
3. **Colloquial Nicknames**: Tampa Bay is universally referred to as "Bucs" as well as "Buccaneers". "TB Bucs" produces `"TBBUCS"`, and "Tampa Bay Bucs" produces `"TAMPABAYBUCS"`, both of which were absent. San Francisco is widely referred to as "Niners", yielding `"SFNINERS"`, which was absent.
4. **Alternative Source Codes**: Pro-Football-Reference (PFR) and certain sports feeds use alternative standard 3-letter codes:
   - Green Bay: `GNB` (`GNBPACKERS`)
   - Kansas City: `KAN` (`KANCHIEFS`)
   - New England: `NWE` (`NWEPATRIOTS`)
   - New Orleans: `NOR` (`NORSAINTS`)
   - Tampa Bay: `TAM` (`TAMBUCS`, `TAMBUCCANEERS`)
   - San Francisco: `SFO` (`SFO49ERS`, `SFONINERS`)
   - Las Vegas: `LVR` (`LVRRAIDERS`)
   - Washington: `WSH` (`WSHCOMMANDERS`)
   - Jacksonville: `JAC` (`JACJAGUARS`, `JACJAGS`)

### 2.3 Danger of Algorithmic Fallback (Prefix Matching)
An initial consideration was adding a prefix-matching heuristic to `normalize_team`:
```python
# HAZARDOUS IMPLEMENTATION - DO NOT USE
for code, info in NFL_TEAMS.items():
    if compact.startswith(_compact_key(info.city)):
        return code
```
This naive heuristic fails catastrophically against adversarial negative test cases:
- `"Los Angeles Lakers"` has prefix `"LOSANGELES"` -> would erroneously return `"LAR"` or `"LAC"` instead of `None`.
- `"New York Yankees"` has prefix `"NEWYORK"` -> would erroneously return `"NYG"` or `"NYJ"` instead of `None`.
- `"Boston Celtics"` -> would fail or false-match.
- `"Alabama Crimson Tide"` -> would fail or false-match.

`tests/test_nfl_stress.py` explicitly tests in `test_normalize_team_adversarial_and_invalid_inputs`:
```python
assert normalize_team("Alabama Crimson Tide") is None
assert normalize_team("Los Angeles Lakers") is None
assert normalize_team("New York Yankees") is None
assert normalize_team("Real Madrid") is None
```
A static, exhaustive dictionary expansion guarantees that valid NFL combinations are resolved in $O(1)$ time while all non-NFL entities strictly return `None`.

---

## 3. Comprehensive 32-Team Addition Matrix

Below is the complete census of all 32 NFL franchises and the exact 155 composite alias additions required:

| Division | Code | Team Name | Standard Nickname & Variants | Missing Composite & Colloquial Aliases Added |
|---|---|---|---|---|
| **AFC East** | `BUF` | Buffalo Bills | Bills | `BUFBILLS`, `BILLSBUF` |
| | `MIA` | Miami Dolphins | Dolphins, Phins | `MIADOLPHINS`, `DOLPHINSMIA`, `MIAPHINS`, `MIAMIPHINS`, `PHINSMIA`, `PHINS` |
| | `NE` | New England Patriots | Patriots, Pats (Alt code: NWE) | `NEPATRIOTS`, `PATRIOTSNE`, `NWEPATRIOTS`, `PATRIOTSNWE`, `NEPATS`, `PATSNE`, `NWEPATS`, `PATSNWE`, `NEWENGLANDPATS`, `PATS` |
| | `NYJ` | New York Jets | Jets (Prefix: NY) | `NYJETS`, `JETSNY`, `NYJJETS`, `JETSNYJ` |
| **AFC North** | `BAL` | Baltimore Ravens | Ravens | `BALRAVENS`, `RAVENSBAL` |
| | `CIN` | Cincinnati Bengals | Bengals | `CINBENGALS`, `BENGALSCIN` |
| | `CLE` | Cleveland Browns | Browns | `CLEBROWNS`, `BROWNSCLE` |
| | `PIT` | Pittsburgh Steelers | Steelers | `PITSTEELERS`, `STEELERSPIT` |
| **AFC South** | `HOU` | Houston Texans | Texans | `HOUTEXANS`, `TEXANSHOU` |
| | `IND` | Indianapolis Colts | Colts (City: Indy) | `INDCOLTS`, `COLTSIND`, `INDY`, `INDYCOLTS`, `COLTSINDY` |
| | `JAX` | Jacksonville Jaguars | Jaguars, Jags (Alt code: JAC) | `JAXJAGUARS`, `JAGUARSJAX`, `JACJAGUARS`, `JAGUARSJAC`, `JAXJAGS`, `JAGSJAX`, `JACJAGS`, `JAGSJAC`, `JACKSONVILLEJAGS`, `JAGS` |
| | `TEN` | Tennessee Titans | Titans | `TENTITANS`, `TITANSTEN` |
| **AFC West** | `DEN` | Denver Broncos | Broncos | `DENBRONCOS`, `BRONCOSDEN` |
| | `KC` | Kansas City Chiefs | Chiefs (Alt code: KAN) | `KCCHIEFS`, `CHIEFSKC`, `KANCHIEFS`, `CHIEFSKAN` |
| | `LV` | Las Vegas Raiders | Raiders (Alt codes: LVR, LAS, OAK) | `LVRAIDERS`, `RAIDERSLV`, `LVRRAIDERS`, `RAIDERSLVR`, `LASRAIDERS`, `OAKRAIDERS` |
| | `LAC` | Los Angeles Chargers | Chargers, Bolts (Alt code: SD, LA) | `LACCHARGERS`, `CHARGERSLAC`, `CHARGERSLA`, `SDCHARGERS`, `CHARGERSSD`, `LABOLTS`, `LACBOLTS`, `BOLTS` |
| **NFC East** | `DAL` | Dallas Cowboys | Cowboys | `DALCOWBOYS`, `COWBOYSDAL` |
| | `NYG` | New York Giants | Giants, G-Men (Prefix: NY) | `NYGIANTS`, `GIANTSNY`, `NYGGIANTS`, `GIANTSNYG`, `GMEN`, `NYGMEN`, `NYGGMEN` |
| | `PHI` | Philadelphia Eagles | Eagles (City: Philly) | `PHIEAGLES`, `EAGLESPHI`, `PHILLY`, `PHILLYEAGLES`, `EAGLESPHILLY` |
| | `WAS` | Washington Commanders | Commanders, Football Team, Redskins (Alt code: WSH) | `WASCOMMANDERS`, `COMMANDERSWAS`, `WSHCOMMANDERS`, `COMMANDERSWSH`, `WFT`, `WASFOOTBALLTEAM`, `WSHFOOTBALLTEAM`, `WASREDSKINS`, `WSHREDSKINS` |
| **NFC North** | `CHI` | Chicago Bears | Bears | `CHIBEARS`, `BEARSCHI` |
| | `DET` | Detroit Lions | Lions | `DETLIONS`, `LIONSDET` |
| | `GB` | Green Bay Packers | Packers (Alt code: GNB) | `GBPACKERS`, `PACKERSGB`, `GNBPACKERS`, `PACKERSGNB` |
| | `MIN` | Minnesota Vikings | Vikings, Vikes | `MINVIKINGS`, `VIKINGSMIN`, `MINVIKES`, `VIKESMIN`, `MINNESOTAVIKES`, `VIKES` |
| **NFC South** | `ATL` | Atlanta Falcons | Falcons | `ATLFALCONS`, `FALCONSATL` |
| | `CAR` | Carolina Panthers | Panthers | `CARPANTHERS`, `PANTHERSCAR` |
| | `NO` | New Orleans Saints | Saints (Alt codes: NOR, NOLA) | `NOSAINTS`, `SAINTSNO`, `NORSAINTS`, `SAINTSNOR`, `NOLA`, `NOLASAINTS`, `SAINTSNOLA` |
| | `TB` | Tampa Bay Buccaneers | Buccaneers, Bucs (Alt codes: TAM, TAMPA) | `TBBUCS`, `BUCSTB`, `TBBUCCANEERS`, `BUCCANEERSTB`, `TAMBUCS`, `BUCSTAM`, `TAMBUCCANEERS`, `BUCCANEERSTAM`, `TAMPABAYBUCS`, `TAMPABUCS`, `TAMPA` |
| **NFC West** | `ARI` | Arizona Cardinals | Cardinals, Cards (Alt code: AZ) | `ARICARDINALS`, `CARDINALSARI`, `AZCARDINALS`, `CARDINALSAZ`, `ARICARDS`, `CARDSARI`, `AZCARDS`, `CARDSAZ`, `ARIZONACARDS`, `CARDS` |
| | `LAR` | Los Angeles Rams | Rams (Alt code: STL, LA) | `LARRAMS`, `RAMSLAR`, `RAMSLA`, `STLRAMS`, `RAMSSTL` |
| | `SF` | San Francisco 49ers | 49ers, Niners (Alt code: SFO) | `SF49ERS`, `49ERSSF`, `SFO49ERS`, `49ERSSFO`, `SFNINERS`, `NINERSSF`, `SFONINERS`, `NINERSSFO`, `SANFRANCISCONINERS` |
| | `SEA` | Seattle Seahawks | Seahawks | `SEASEAHAWKS`, `SEAHAWKSSEA`, `SEATTLEHAWKS` |

---

## 4. Exact Implementation Strategy for Worker 1

### 4.1 Changes to `outlier_nfl/config.py`

#### A. Harden `_compact_key` (Defensive Guard)
Ensure non-primitive data structures (dicts, lists) and booleans are not coerced into strange string values:
```python
def _compact_key(value: Any) -> str:
    """Normalize string by removing non-alphanumeric characters and uppercasing."""
    if value is None or isinstance(value, (bool, dict, list, set, tuple)):
        return ""
    text = str(value).upper()
    return "".join(ch for ch in text if ch.isalnum())
```

#### B. Expand `NFL_TEAM_ALIASES`
In `outlier_nfl/config.py`, replace lines 82–244 with the organized dictionary containing the existing entries plus the 155 validated additions.

```python
# Comprehensive alias dictionary mapping raw names, cities, acronyms, code+nickname composites, and Outlier teamIds
NFL_TEAM_ALIASES: dict[str, str] = {
    # -------------------------------------------------------------------------
    # AFC East
    # -------------------------------------------------------------------------
    "BUF": "BUF",
    "BUFFALO": "BUF",
    "BUFFALOBILLS": "BUF",
    "BILLS": "BUF",
    "BUFBILLS": "BUF",
    "BILLSBUF": "BUF",
    "MIA": "MIA",
    "MIAMI": "MIA",
    "MIAMIDOLPHINS": "MIA",
    "DOLPHINS": "MIA",
    "MIADOLPHINS": "MIA",
    "DOLPHINSMIA": "MIA",
    "MIAPHINS": "MIA",
    "MIAMIPHINS": "MIA",
    "PHINSMIA": "MIA",
    "PHINS": "MIA",
    "NE": "NE",
    "NWE": "NE",
    "NEWENGLAND": "NE",
    "NEWENGLANDPATRIOTS": "NE",
    "PATRIOTS": "NE",
    "NEPATRIOTS": "NE",
    "PATRIOTSNE": "NE",
    "NWEPATRIOTS": "NE",
    "PATRIOTSNWE": "NE",
    "NEPATS": "NE",
    "PATSNE": "NE",
    "NWEPATS": "NE",
    "PATSNWE": "NE",
    "NEWENGLANDPATS": "NE",
    "PATS": "NE",
    "NYJ": "NYJ",
    "NEWYORKJETS": "NYJ",
    "JETS": "NYJ",
    "NYJETS": "NYJ",
    "JETSNY": "NYJ",
    "NYJJETS": "NYJ",
    "JETSNYJ": "NYJ",

    # -------------------------------------------------------------------------
    # AFC North
    # -------------------------------------------------------------------------
    "BAL": "BAL",
    "BALTIMORE": "BAL",
    "BALTIMORERAVENS": "BAL",
    "RAVENS": "BAL",
    "BALRAVENS": "BAL",
    "RAVENSBAL": "BAL",
    "CIN": "CIN",
    "CINCINNATI": "CIN",
    "CINCINNATIBENGALS": "CIN",
    "BENGALS": "CIN",
    "CINBENGALS": "CIN",
    "BENGALSCIN": "CIN",
    "CLE": "CLE",
    "CLEVELAND": "CLE",
    "CLEVELANDBROWNS": "CLE",
    "BROWNS": "CLE",
    "CLEBROWNS": "CLE",
    "BROWNSCLE": "CLE",
    "PIT": "PIT",
    "PITTSBURGH": "PIT",
    "PITTSBURGHSTEELERS": "PIT",
    "STEELERS": "PIT",
    "PITSTEELERS": "PIT",
    "STEELERSPIT": "PIT",

    # -------------------------------------------------------------------------
    # AFC South
    # -------------------------------------------------------------------------
    "HOU": "HOU",
    "HOUSTON": "HOU",
    "HOUSTONTEXANS": "HOU",
    "TEXANS": "HOU",
    "HOUTEXANS": "HOU",
    "TEXANSHOU": "HOU",
    "IND": "IND",
    "INDIANAPOLIS": "IND",
    "INDIANAPOLISCOLTS": "IND",
    "COLTS": "IND",
    "INDCOLTS": "IND",
    "COLTSIND": "IND",
    "INDY": "IND",
    "INDYCOLTS": "IND",
    "COLTSINDY": "IND",
    "JAX": "JAX",
    "JAC": "JAX",
    "JACKSONVILLE": "JAX",
    "JACKSONVILLEJAGUARS": "JAX",
    "JAGUARS": "JAX",
    "JAXJAGUARS": "JAX",
    "JAGUARSJAX": "JAX",
    "JACJAGUARS": "JAX",
    "JAGUARSJAC": "JAX",
    "JAXJAGS": "JAX",
    "JAGSJAX": "JAX",
    "JACJAGS": "JAX",
    "JAGSJAC": "JAX",
    "JACKSONVILLEJAGS": "JAX",
    "JAGS": "JAX",
    "TEN": "TEN",
    "TENNESSEE": "TEN",
    "TENNESSEETITANS": "TEN",
    "TITANS": "TEN",
    "TENTITANS": "TEN",
    "TITANSTEN": "TEN",

    # -------------------------------------------------------------------------
    # AFC West
    # -------------------------------------------------------------------------
    "DEN": "DEN",
    "DENVER": "DEN",
    "DENVERBRONCOS": "DEN",
    "BRONCOS": "DEN",
    "DENBRONCOS": "DEN",
    "BRONCOSDEN": "DEN",
    "KC": "KC",
    "KAN": "KC",
    "KANSASCITY": "KC",
    "KANSASCITYCHIEFS": "KC",
    "CHIEFS": "KC",
    "KCCHIEFS": "KC",
    "CHIEFSKC": "KC",
    "KANCHIEFS": "KC",
    "CHIEFSKAN": "KC",
    "LV": "LV",
    "LVR": "LV",
    "LAS": "LV",
    "LASVEGAS": "LV",
    "LASVEGASRAIDERS": "LV",
    "RAIDERS": "LV",
    "OAK": "LV",
    "OAKLAND": "LV",
    "OAKLANDRAIDERS": "LV",
    "LVRAIDERS": "LV",
    "RAIDERSLV": "LV",
    "LVRRAIDERS": "LV",
    "RAIDERSLVR": "LV",
    "LASRAIDERS": "LV",
    "OAKRAIDERS": "LV",
    "LAC": "LAC",
    "LOSANGELESCHARGERS": "LAC",
    "LACHARGERS": "LAC",
    "CHARGERS": "LAC",
    "LACCHARGERS": "LAC",
    "CHARGERSLAC": "LAC",
    "CHARGERSLA": "LAC",
    "SD": "LAC",
    "SANDIEGO": "LAC",
    "SANDIEGOCHARGERS": "LAC",
    "SDCHARGERS": "LAC",
    "CHARGERSSD": "LAC",
    "LABOLTS": "LAC",
    "LACBOLTS": "LAC",
    "BOLTS": "LAC",

    # -------------------------------------------------------------------------
    # NFC East
    # -------------------------------------------------------------------------
    "DAL": "DAL",
    "DALLAS": "DAL",
    "DALLASCOWBOYS": "DAL",
    "COWBOYS": "DAL",
    "DALCOWBOYS": "DAL",
    "COWBOYSDAL": "DAL",
    "NYG": "NYG",
    "NEWYORKGIANTS": "NYG",
    "GIANTS": "NYG",
    "NYGIANTS": "NYG",
    "GIANTSNY": "NYG",
    "NYGGIANTS": "NYG",
    "GIANTSNYG": "NYG",
    "GMEN": "NYG",
    "NYGMEN": "NYG",
    "NYGGMEN": "NYG",
    "PHI": "PHI",
    "PHILADELPHIA": "PHI",
    "PHILADELPHIAEAGLES": "PHI",
    "EAGLES": "PHI",
    "PHIEAGLES": "PHI",
    "EAGLESPHI": "PHI",
    "PHILLY": "PHI",
    "PHILLYEAGLES": "PHI",
    "EAGLESPHILLY": "PHI",
    "WAS": "WAS",
    "WSH": "WAS",
    "WASHINGTON": "WAS",
    "WASHINGTONCOMMANDERS": "WAS",
    "COMMANDERS": "WAS",
    "FOOTBALLTEAM": "WAS",
    "WASHINGTONFOOTBALLTEAM": "WAS",
    "REDSKINS": "WAS",
    "WASHINGTONREDSKINS": "WAS",
    "WASCOMMANDERS": "WAS",
    "COMMANDERSWAS": "WAS",
    "WSHCOMMANDERS": "WAS",
    "COMMANDERSWSH": "WAS",
    "WFT": "WAS",
    "WASFOOTBALLTEAM": "WAS",
    "WSHFOOTBALLTEAM": "WAS",
    "WASREDSKINS": "WAS",
    "WSHREDSKINS": "WAS",

    # -------------------------------------------------------------------------
    # NFC North
    # -------------------------------------------------------------------------
    "CHI": "CHI",
    "CHICAGO": "CHI",
    "CHICAGOBEARS": "CHI",
    "BEARS": "CHI",
    "CHIBEARS": "CHI",
    "BEARSCHI": "CHI",
    "DET": "DET",
    "DETROIT": "DET",
    "DETROITLIONS": "DET",
    "LIONS": "DET",
    "DETLIONS": "DET",
    "LIONSDET": "DET",
    "GB": "GB",
    "GNB": "GB",
    "GREENBAY": "GB",
    "GREENBAYPACKERS": "GB",
    "PACKERS": "GB",
    "GBPACKERS": "GB",
    "PACKERSGB": "GB",
    "GNBPACKERS": "GB",
    "PACKERSGNB": "GB",
    "MIN": "MIN",
    "MINNESOTA": "MIN",
    "MINNESOTAVIKINGS": "MIN",
    "VIKINGS": "MIN",
    "MINVIKINGS": "MIN",
    "VIKINGSMIN": "MIN",
    "MINVIKES": "MIN",
    "VIKESMIN": "MIN",
    "MINNESOTAVIKES": "MIN",
    "VIKES": "MIN",

    # -------------------------------------------------------------------------
    # NFC South
    # -------------------------------------------------------------------------
    "ATL": "ATL",
    "ATLANTA": "ATL",
    "ATLANTAFALCONS": "ATL",
    "FALCONS": "ATL",
    "ATLFALCONS": "ATL",
    "FALCONSATL": "ATL",
    "CAR": "CAR",
    "CAROLINA": "CAR",
    "CAROLINAPANTHERS": "CAR",
    "PANTHERS": "CAR",
    "CARPANTHERS": "CAR",
    "PANTHERSCAR": "CAR",
    "NO": "NO",
    "NOR": "NO",
    "NEWORLEANS": "NO",
    "NEWORLEANSSAINTS": "NO",
    "SAINTS": "NO",
    "NOSAINTS": "NO",
    "SAINTSNO": "NO",
    "NORSAINTS": "NO",
    "SAINTSNOR": "NO",
    "NOLA": "NO",
    "NOLASAINTS": "NO",
    "SAINTSNOLA": "NO",
    "TB": "TB",
    "TAM": "TB",
    "TAMPABAY": "TB",
    "TAMPABAYBUCCANEERS": "TB",
    "BUCCANEERS": "TB",
    "BUCS": "TB",
    "TBBUCS": "TB",
    "BUCSTB": "TB",
    "TBBUCCANEERS": "TB",
    "BUCCANEERSTB": "TB",
    "TAMBUCS": "TB",
    "BUCSTAM": "TB",
    "TAMBUCCANEERS": "TB",
    "BUCCANEERSTAM": "TB",
    "TAMPABAYBUCS": "TB",
    "TAMPABUCS": "TB",
    "TAMPA": "TB",

    # -------------------------------------------------------------------------
    # NFC West
    # -------------------------------------------------------------------------
    "ARI": "ARI",
    "AZ": "ARI",
    "ARIZONA": "ARI",
    "ARIZONACARDINALS": "ARI",
    "CARDINALS": "ARI",
    "ARICARDINALS": "ARI",
    "CARDINALSARI": "ARI",
    "AZCARDINALS": "ARI",
    "CARDINALSAZ": "ARI",
    "ARICARDS": "ARI",
    "CARDSARI": "ARI",
    "AZCARDS": "ARI",
    "CARDSAZ": "ARI",
    "ARIZONACARDS": "ARI",
    "CARDS": "ARI",
    "LAR": "LAR",
    "LOSANGELESRAMS": "LAR",
    "LARAMS": "LAR",
    "RAMS": "LAR",
    "LA": "LAR",
    "LARRAMS": "LAR",
    "RAMSLAR": "LAR",
    "RAMSLA": "LAR",
    "STL": "LAR",
    "STLOUIS": "LAR",
    "STLOUISRAMS": "LAR",
    "STLRAMS": "LAR",
    "RAMSSTL": "LAR",
    "SF": "SF",
    "SFO": "SF",
    "SANFRANCISCO": "SF",
    "SANFRANCISCO49ERS": "SF",
    "49ERS": "SF",
    "NINERS": "SF",
    "SF49ERS": "SF",
    "49ERSSF": "SF",
    "SFO49ERS": "SF",
    "49ERSSFO": "SF",
    "SFNINERS": "SF",
    "NINERSSF": "SF",
    "SFONINERS": "SF",
    "NINERSSFO": "SF",
    "SANFRANCISCONINERS": "SF",
    "SEA": "SEA",
    "SEATTLE": "SEA",
    "SEATTLESEAHAWKS": "SEA",
    "SEAHAWKS": "SEA",
    "SEASEAHAWKS": "SEA",
    "SEAHAWKSSEA": "SEA",
    "SEATTLEHAWKS": "SEA",
}
```

---

### 4.2 Updating `tests/test_nfl_stress.py`

**Crucial Warning for Worker 1**: Challenger 1 wrote `test_normalize_team_code_plus_nickname_adversarial` to reproduce the missing aliases bug by asserting that all 10 target aliases failed:
```python
# CURRENT CODE in tests/test_nfl_stress.py (lines 312)
assert len(unresolved) == len(missing_aliases), f"Expected all {len(missing_aliases)} to be missing, but unresolved={unresolved}"
```
Once `config.py` is updated, `len(unresolved)` becomes `0`. Running the test suite as-is will cause `test_normalize_team_code_plus_nickname_adversarial` to fail.

Worker 1 **must** update `test_normalize_team_code_plus_nickname_adversarial` in `tests/test_nfl_stress.py` to assert that all aliases resolve correctly:

```python
def test_normalize_team_code_plus_nickname_adversarial():
    """Verify standard sports feed format 'Code + Nickname' or 'Abbrev + Nickname' for all franchises."""
    target_aliases = {
        # Challenger 1's original 10 reproduced cases
        "KC Chiefs": "KC",
        "SF 49ers": "SF",
        "TB Bucs": "TB",
        "NE Patriots": "NE",
        "PIT Steelers": "PIT",
        "BAL Ravens": "BAL",
        "GB Packers": "GB",
        "NY Jets": "NYJ",
        "NY Giants": "NYG",
        "PHI Eagles": "PHI",
        # Additional coverage for multi-word & alternate code franchises
        "BUF Bills": "BUF",
        "MIA Dolphins": "MIA",
        "CIN Bengals": "CIN",
        "CLE Browns": "CLE",
        "HOU Texans": "HOU",
        "IND Colts": "IND",
        "JAX Jaguars": "JAX",
        "TEN Titans": "TEN",
        "DEN Broncos": "DEN",
        "LV Raiders": "LV",
        "LAC Chargers": "LAC",
        "DAL Cowboys": "DAL",
        "WAS Commanders": "WAS",
        "CHI Bears": "CHI",
        "DET Lions": "DET",
        "MIN Vikings": "MIN",
        "ATL Falcons": "ATL",
        "CAR Panthers": "CAR",
        "NO Saints": "NO",
        "ARI Cardinals": "ARI",
        "LAR Rams": "LAR",
        "SEA Seahawks": "SEA",
    }
    for raw_name, expected_code in target_aliases.items():
        assert normalize_team(raw_name) == expected_code, f"Failed to normalize {raw_name!r} to {expected_code}"
```

---

## 5. Verification & Validation Evidence

We tested the proposed dictionary expansion empirically against both positive and negative suites:
1. **Full 32-Team Coverage**: Every team's `<CODE><NICKNAME>` and reverse `<NICKNAME><CODE>` resolves to its canonical code.
2. **Zero In-Repo Collisions**: The expanded dictionary was cross-checked against all 32 franchises. Zero duplicate keys with conflicting targets exist.
3. **Zero False Positives**: All negative adversarial test inputs (`"Los Angeles Lakers"`, `"New York Yankees"`, `"Alabama Crimson Tide"`, `"Real Madrid"`, `"Boston Celtics"`, `"Dallas Mavericks"`, `""`, `None`) continue to return `None`.
4. **Fast O(1) Performance**: Pure dictionary lookup retains constant time execution without regex compilation or loop traversal overhead.
