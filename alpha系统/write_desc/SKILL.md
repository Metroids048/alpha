---
name: write_desc
description: >-
  Generate and set a structured description for a BRAIN alpha using the
  Idea / Rationale for data used / Rationale for operators used template.
  Use when the user asks to 写描述、生成描述、write description、set description、
  fill description for an alpha, or invokes /write_desc with an alpha_id.
allowed-tools:
  - mcp__brain-mcp__get_alpha_details
  - mcp__brain-mcp__set_alpha_properties
  - mcp__brain-mcp__get_datafields
  - mcp__brain-mcp__get_operators
  - mcp__brain-mcp__authenticate
---

# Write Description Skill

Generate a structured description for a BRAIN alpha and set it via `set_alpha_properties`.

## Template

Every description MUST follow this three-section template:

```
Idea:
<one concise sentence describing the core investment logic or hypothesis>

Rationale for data used:
<explain why the chosen data fields are relevant to the idea, what they capture, and how they relate to the market phenomenon>

Rationale for operators used:
<explain why the chosen operators (functions/transformations) are appropriate, what statistical or logical role they serve, and how they implement the idea>
```

## Instructions

Given an `alpha_id` (passed as skill args), follow these steps:

### Step 1: Authenticate (if needed)

If not already authenticated, call `authenticate` first.

### Step 2: Fetch Alpha Details

Call `get_alpha_details` with the `alpha_id` to retrieve:
- The alpha expression (`regular` field)
- Simulation settings (region, universe, delay, neutralization, decay, etc.)
- Any existing name/description

### Step 3: Analyze the Expression

Parse the alpha expression to identify:

1. **Data fields**: Extract all data field names used in the expression (e.g., `close`, `open`, `volume`, `returns`, `vwap`, or dataset-prefixed fields like `analyst4_avg_est`).
2. **Operators**: Extract all function/operator calls (e.g., `rank`, `ts_rank`, `ts_delta`, `group_neutralize`, `ts_regression`, `decay_linear`, etc.).

To help with identification:
- Call `get_operators` to get the full list of valid BRAIN operators for cross-referencing.
- For ambiguous field names, optionally call `get_datafields` with a search term to confirm what a field represents.

### Step 4: Generate the Description

Write the description following the template. Guidelines for each section:

**Idea:**
- One clear sentence.
- Describe the market hypothesis or signal logic.
- Avoid jargon where plain language suffices.
- Example: "Stocks with rising analyst consensus estimates relative to their historical trend tend to outperform."

**Rationale for data used:**
- List each data field and explain its relevance.
- Explain what real-world information the field captures.
- Connect the data to the investment idea.
- Example: "analyst4_avg_est captures the mean analyst earnings estimate, reflecting market expectations; ts_delta of this field measures the change in consensus over time, capturing estimate momentum."

**Rationale for operators used:**
- List each operator and explain its role.
- Explain the statistical or logical purpose.
- Connect the operator choice to the idea implementation.
- Example: "ts_rank normalizes the signal over a time window, making it comparable across stocks; group_neutralize removes sector bias ensuring the signal is stock-specific rather than industry-driven."

### Step 5: Set the Description

Call `set_alpha_properties` with:
- `alpha_id`: the provided alpha ID
- `regular_desc`: the generated description text

If the alpha is a SUPER type (has `combo` and `selection` fields), also set:
- `combo_desc`: description for the combo component
- `selection_desc`: description for the selection component

### Step 6: Report

Report to the user:
- The alpha ID
- The generated description (full text)
- Confirmation that the description was set successfully

## Error Handling

- If `alpha_id` is not provided in args, ask the user to provide it.
- If `get_alpha_details` fails, report the error and suggest checking the alpha ID.
- If the expression is empty or None, report that the alpha has no expression to describe.
- If `set_alpha_properties` fails, report the error and provide the description text so the user can set it manually.

## Language

- Generate descriptions in **English** by default.
- If the user's request is in Chinese, generate descriptions in **English** still (BRAIN platform requires English descriptions for submission).
- The skill's communication with the user can be in the user's preferred language.
