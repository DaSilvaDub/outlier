---
name: postgame-prediction-audit
description: >-
  Audits and evaluates sports predictions, game scripts, and betting models against
  actual final game results and box scores using an 8-step objective rubric that
  separates outcome luck from reasoning quality.
---

# Postgame Prediction Audit Skill

Use this skill whenever tasked with evaluating, reconciling, or auditing a pre-game sports analysis, betting card, game script, or model prediction against actual game outcomes.

Do not judge an analysis solely by whether the predicted winner or side won. Rigorously evaluate the quality of the underlying reasoning, assumptions, statistics, and predicted game dynamics.

---

## 8-Step Audit Workflow

### Step 1: Extract Original Claims
Identify the core predictions and assumptions in the pre-game analysis without inventing unstated claims:
- Predicted winner, point spread, and game total
- Expected player/team performances (yardage, strikeouts, usage)
- Tactical and strategic expectations (game pace, defensive schemes, run/pass balance)
- Key statistical arguments and edge justifications
- Explicit conditions, uncertainties, or risk factors cited

### Step 2: Compare Each Claim with Reality
Classify every meaningful prediction against official post-game box scores:
- **Correct**: Outcome matched prediction and expected dynamics.
- **Partially Correct**: Direction was right, but margin/volume diverged significantly.
- **Incorrect**: Outcome failed to materialize.
- **Cannot be Verified**: Incomplete data or metric not captured in official box score.

### Step 3: Separate Outcome Accuracy from Reasoning Quality
Evaluate results along two orthogonal axes:
- **Outcome Accuracy**: Did the event happen?
- **Reasoning Quality**: Were the logical and statistical arguments sound prior to kickoff/first pitch?
Categorize into:
1. *Right for the right reasons* (high skill, sound process)
2. *Right for the wrong reasons* (lucky variance masking bad logic)
3. *Wrong despite good reasoning* (unavoidable tail-risk variance)
4. *Wrong because of flawed reasoning* (analytical failure)

### Step 4: Identify Decisive Differences
Determine the true game catalysts and contrast with pre-game expectations:
- Unexpected player performance or usage spikes/collapses
- In-game injuries, early benchings, or minute/pitch restrictions
- Tactical adjustments and second-half game script shifts
- Extreme turnover, red zone, or shooting/finishing variance
- High-leverage sequencing (stranding runners, late penalties)

Distinguish between factors the model *should* have anticipated vs. genuine random variance.

### Step 5: Detect Analytical Mistakes
Screen for common prediction pitfalls:
- Overweighting recent small samples (e.g. L5 hit-rate chasing)
- Ignoring opponent defensive efficiency or matchup splits
- Narrative-driven confirmation bias
- Failure to account for active roster shifts or inactive lists
- Misinterpreting conditional vs. absolute win probabilities
- Chasing heavily juiced lines without genuine positive EV

### Step 6: Produce the Scorecard (0–10 Scale)
Score the analysis across six dimensions:
1. **Result Prediction Accuracy**
2. **Statistical Reasoning**
3. **Tactical / Game Understanding**
4. **Player / Team Assessment**
5. **Risk & Uncertainty Handling**
6. **Overall Analysis Quality**

Provide a concise, evidence-grounded rationale for each score.

### Step 7: Distill Key Actionable Lessons
Identify 3–5 concrete lessons to upgrade future analyses:
- What went right or wrong
- Why it mattered structurally
- How future pipeline runs or prompts must adapt (e.g. volume haircuts, side restrictions, liquidity thresholds)

### Step 8: Rewrite Counterfactual Pre-Game Conclusion
Re-author an optimal pre-game conclusion using **only** information available before game start. Never leak hindsight or in-game outcomes into the rewrite.

---

## Output Template

Deliver the final audit structured as follows:

```markdown
### Verdict
[2–4 sentence executive summary]

### Prediction Check
| Original Claim | Pre-Game Stance & Odds | Actual Game Result | Verdict | Explanation |
| :--- | :--- | :--- | :--- | :--- |
| ... | ... | ... | ... | ... |

### What the Analysis Got Right

### What the Analysis Got Wrong

### What Actually Decided the Game

### Reasoning vs. Outcome
- Right for the right reasons: ...
- Right for the wrong reasons: ...
- Wrong despite good reasoning: ...
- Wrong because of flawed reasoning: ...

### Scorecard
- Result Prediction Accuracy: X/10
- Statistical Reasoning: X/10
- Tactical/Game Understanding: X/10
- Player/Team Assessment: X/10
- Risk & Uncertainty Handling: X/10
- Overall Analysis Score: X/10

### Key Lessons

### Improved Pre-Game Analysis (Zero Hindsight)

### Confidence
- Level: Low / Medium / High
- Supporting Data & Gaps: ...
```
