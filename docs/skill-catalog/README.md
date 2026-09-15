# Outlier Skill Atlas

This catalog explains every unique skill discovered across the configured Codex,
agent, system, plugin, runtime, and Outlier-repository skill roots on 2026-09-06.

## Open the guide

Open `outlier-skill-atlas.html` in a browser. It is a self-contained, offline HTML
document with embedded styling, data, and interactions.

The guide provides:

- a plain-English description of every skill;
- an Outlier relevance rating for every skill;
- a concrete Outlier-pipeline use or an explicit “no direct fit” explanation;
- functional category and installation-source filters;
- keyword search, relevance/name/category sorting, and pagination;
- operational cautions and the exact `SKILL.md` source path;
- CSV export of the current filtered view.

## Coverage

- 982 physical `SKILL.md` packages discovered
- 979 unique callable skill names represented
- 3 duplicate physical copies collapsed under their shared callable name
- 0 discovered skill files omitted

The companion `skills-inventory.csv` contains the complete row-level inventory.
`inventory-metadata.json` records the generation timestamp, configured roots, and
category/relevance totals.

## Relevance scale

| Rating | Meaning for Outlier |
|---|---|
| Direct | Purpose-built for an Outlier workflow. |
| Strong | Closely supports sports data, validation, analysis, backtesting, or operator review. |
| Supporting | Useful for adjacent research, data, infrastructure, documents, or safety work. |
| Peripheral | Useful around operations or presentation, but outside pack decision truth. |
| No direct fit | No core-pipeline use; applicable only if that external service becomes part of operations. |

Ratings describe fit, not general skill quality. Current `SKILL.md` instructions,
canonical pack artifacts, `PackIndex`, feed-health gates, and repository rules remain
authoritative. Paid/live Outlier desk reasoning remains off unless the user explicitly
authorizes it in the current request.
