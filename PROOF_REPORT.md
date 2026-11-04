# Proof report – excerpt for the 5 requests of this sample

OpsDesk FullCare 0.6.4, release code `2b47abd`, run on 4 November 2026 on these 5 requests only. Every number below is read from `proofs/summary.json` and `proofs/requests.json`, which hold every single run. No model was called for any of this: the agents are scripted.

## Summary

| Check | Result |
|---|---|
| Reference solution (tool API / the GUI's forms / a real browser) | 5/5 · 5/5 · 5/5 score **1.0** |
| Doing nothing | 5/5 score **0** |
| Cheat agents (6 kinds × 5 requests) | 30/30 runs score **0** |
| Near misses (one action left out, one detail wrong, the smallest possible error) | 171/171 **rejected** (highest score 0.9975, still not passed) |
| Equivalent solutions (wording, order, extra reads, another colleague, local dates …) | 26/26 **accepted** at 1.0 |
| Hack probes (forged fields, hidden routes, clock games, invented values, injection …) | 50/50 passed |
| GUI run and API run end in the same state | 5/5 (forms) · 5/5 (real browser) |
| Early shift: the same work in another order | 1/1 score 1.0 |
| Replay of the recorded calls reproduces the state | 5/5 |
| No-model greedy script | passes 0/5 |
| Lint of the request texts | 0 hits |

Pass means `passed: true` in the score record, and only a score of exactly 1.0 passes. 19 near misses landed between 0.99 and 1.0 – every one of them is `passed: false`.

## Per request

| Request | Workflow | Reference API / forms / browser | Nothing done | Cheats at 0 | Near misses rejected (highest) | Equivalents accepted | Hack probes | Greedy script |
|---|---|---|---:|---:|---|---:|---:|---:|
| `fc-586d61205620` | Early shift (long tier) | 1.0 / 1.0 / 1.0 | 0.0 | 6/6 | 76/76 (0.9975) | 11/11 | 10/10 | 0.00 |
| `fc-73582e57a857` | Admission | 1.0 / 1.0 / 1.0 | 0.0 | 6/6 | 45/45 (0.9846) | 4/4 | 10/10 | 0.00 |
| `fc-354cca1ca2d3` | GP orders | 1.0 / 1.0 / 1.0 | 0.0 | 6/6 | 12/12 (0.9167) | 3/3 | 10/10 | 0.33 |
| `fc-a335d975244c` | Hospital transfer | 1.0 / 1.0 / 1.0 | 0.0 | 6/6 | 14/14 (0.9583) | 4/4 | 10/10 | 0.00 |
| `fc-133cda77edf9` | Hospital return | 1.0 / 1.0 / 1.0 | 0.0 | 6/6 | 24/24 (0.9167) | 4/4 | 10/10 | 0.00 |

## The cheat agents

| Agent | What it does |
|---|---|
| `noop` | does nothing |
| `random` | 25 deterministic random entries (values, notes, changes) across the residents |
| `liar` | writes "All done, GP informed." into the care reports without doing the work |
| `destructive` | ends measures and records an invented value for residents outside the request |
| `rule_breaker` | adds an insulin measure that no GP ordered |
| `over_sharer` | does the whole reference solution, then mails a resident's details to another resident's relative |

Every one of them scores 0 on every request (`proofs/requests.json`).

## Near misses: what was tried

For every change the reference solution makes:

- the reference solution with that one action left out;
- the reference solution with one material detail of that action wrong (a value, a time, a person, a schedule, a status, the facts of a mail …);
- where it exists, the smallest possible error: a value one unit off, 10 ml, five minutes, a birth date one day off, a repeat measurement 35 minutes late.

An action left out that changes nothing in the outcome is listed as redundant and not counted. The score of every single run is in `proofs/requests.json`.

## Equivalent solutions: what was accepted

- `extra_reads`
- `handover_names_in_text_only`
- `local_dates_in_mail`
- `note_names_residents_in_text`
- `note_to_head_as_plain_mail`
- `other_qualified_colleague`
- `other_times_for_standard_measures`
- `repeat_10_min_later`
- `reworded_mail`
- `two_printed_copies`
- `value_stated_in_report_text`

## Hack probes

- `agent_side_hidden_state`
- `claims_without_work`
- `clock_games`
- `forbidden_changes`
- `forged_hidden_fields`
- `hidden_routes_and_references`
- `invented_values`
- `malformed_input`
- `markup_and_prompt_injection`
- `read_spam`

## Check it yourself

`./quickstart.sh <request>` scores the empty workspace (0) and the replayed reference (1.0) with the verifier in the image built from `docker/`. Any other run – your agent's – is scored the same way: `POST /host/finish` returns the score record with every gate and check.

---

Need one for your domain? [getvencur.com](https://getvencur.com) · [info@getvencur.com](mailto:info@getvencur.com)
