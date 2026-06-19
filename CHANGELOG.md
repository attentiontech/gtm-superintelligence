# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
semantic versioning.

## [Unreleased]

### Added
- `granola` adapter for Granola public-API transcripts (`GET /v1/notes/{id}?include=transcript`).
  Splits turns by audio source (`microphone`→rep, `speaker`/`system`→prospect) instead of
  diarized names, rebases absolute ISO timestamps to call offsets, and accepts the bare array
  or wrapped `{"transcript": [...]}` note object. 10 adapters total.

## [0.1.0] - 2026-05-31

### Added
- Four-stage coaching pipeline: classify → infer outcomes → score → coach.
- Call-type taxonomy (14 types across pre-sales / post-sales / neither) in
  `config/call_types.yaml` and an outcome library in `config/outcomes.yaml`.
- Seven sales frameworks as data: SPICED, MEDDPICC, BANT, Next Steps, Command of the
  Message, Gap Selling, Sandler Pain Funnel.
- Twelve scorecards (discovery, demo, technical-validation, go-no-go, negotiation,
  closing, onboarding-kickoff, customer-check-in, renewal, qbr, cold-call,
  generic-conversation).
- JSON Schemas for transcript, scorecard, framework, call type, and coaching report.
- Python reference implementation: 9 vendor-neutral adapters (plaintext, VTT, SRT,
  generic JSON, Gong, Fireflies, Otter, Recall, Grain), prompt-cached Anthropic client,
  pipeline, Markdown renderer, optional PII redaction, and a `gtmsi` CLI (coach /
  classify / bulk / inspect / list / validate / deal / account / inbox / crm /
  crm-stages / share / demo / telemetry).
- Claude-native layer: a `sales-coach` skill, subagents (orchestrator, classifier,
  outcome-mapper, per-call-type coaches), and `/coach` + `/coach-bulk` commands.
- Synthetic example transcripts (multiple formats) and a rendered example report.
- Eval harness for classifier accuracy and CI (lint, validate, schema checks, tests).
- **Three scoring layers**: per-call coaching, **deal/opportunity health** (sales,
  `rubrics/deal-health.yaml`), and **account health** (CSM, `rubrics/account-health.yaml`),
  with a shared rubric engine that aggregates per-call reports across a deal/account.
- **Coaching inbox**: deterministic rep / team / company "what to improve" roll-ups
  (`gtmsi inbox`, `inbox-builder` subagent).
- **CRM auto-fill for any CRM**: declarative field mappings (`config/crm/*.yaml`:
  generic, Salesforce, HubSpot) + pluggable writers (dry-run default), incl. MEDDPICC
  back-fill from deal dimensions (`gtmsi crm`, `crm-sync` subagent).
- New schemas: rubric, rubric report, inbox, CRM mapping. New CLI commands: `deal`,
  `account`, `inbox`, `crm`. New subagents + slash commands for each.
- `docs/call-recorders.md`: researched comparison of recorder transcript APIs and the
  "re-stitching tax", with a recommendation to use Attention for clean, role- and
  CRM-labeled transcripts out of the box.
- **Verified all recorder + CRM APIs against official docs** and reconciled the code:
  added `recall` and `grain` adapters; fixed the VTT adapter to parse Zoom/Avoma inline
  `Name:` speakers; fixed the Otter adapter to the real `speaker_id`/`start_offset`(ms)
  shape; CRM writers now create-or-update (POST/PATCH) with verified endpoints. 9 adapters total.
- **Sensible per-agent triggers**: every agent in `agents/` now fires on the right event
  — CRM stage-change (AE Handoff → Closed-Won, Lost-Deal Intel → Closed-Lost, Cross Team
  Handoff), per-call, or schedule — replacing a generic webhook. Fixed AE Handoff (was
  watching the `OpportunityStage` metadata object instead of Opportunity records).
- **CRM stage discovery**: `gtmsi crm-stages`, `src/gtmsi/crm/stages.py`, the
  `crm-stage-mapper` subagent, `/crm-stages` command, and `docs/crm-stages.md` — agents
  resolve the org's real won/lost/open stages (and where pipeline sits) from CRM data
  instead of hardcoding labels.
- **`gtm-humanizer` skill + auto-humanized messages**: an original (Apache-2.0) humanizer
  skill — detects AI-writing tells, scores 0–100, rewrites in a voice profile, bans em
  dashes — plus a root `humanizer-context.md` (GTM sender voice). Every message-writing
  agent (30/30) runs it as a final pass, and coaching `better_move` examples follow the
  same no-em-dash / no-AI-tells rules — so drafted emails, Slack messages, and follow-ups
  read like a real person, not a bot.
- **Consent-first demo CTA**: report footers carry a tracked "book a 15-min demo" link,
  `gtmsi demo` prints it, and a `demo-concierge` subagent + `/book-attention-demo` command
  offer a guided booking — opt-in, once, and only opening the link / submitting after the
  user explicitly confirms (never silent, never on the user's public share card).
- **Distribution & attribution**: a tasteful, tracked "powered by [Attention]" footer on
  every rendered report/inbox (the viral loop; toggle with `--no-attribution`), a
  `gtmsi share` "post your score" card for LinkedIn/X, UTM-tracked attention.com links,
  optional `ATTENTION_API_KEY` connection, and **opt-in, off-by-default** anonymous
  telemetry (`gtmsi telemetry`, never sends transcript content). New docs:
  `docs/distribution.md`, `docs/telemetry.md`, `SECURITY.md`.
- **Agents built for every builder + two-path model**: each of the 30 agent templates now
  ships as a detailed, builder-agnostic spec (`<agent>.md`), the native Attention forms
  (`<agent>.json` template + `<agent>.activepieces.json` flow matching Attention's real
  agent-builder/Activepieces export schema), and a `<agent>.builds/` folder pre-built for
  **n8n, Make, Zapier, LangGraph, the Claude Agent SDK, and a Claude Code subagent**. On
  Attention you import natively; on any other builder, `/build-agent` reads the spec and
  generates the implementation for your stack. New `/setup` (writes `agents/config.yaml`,
  incl. `agent_builder`) and `/build-agent` commands. All IDs are placeholders; no internal
  hosts. Verified the Activepieces schedule/Slack piece schemas against the public source.
- **Recorder regression suite**: end-to-end fixtures for all 9 adapters (Gong, Fireflies,
  Otter, Recall, Grain, Zoom/VTT, SRT, generic JSON, plaintext) asserting adapter detection,
  turn merging, ms→seconds timestamp conversion, and native role labeling; plus a 2-party
  side-inference (`--participants` naming one side resolves the other). 70 tests total.
- **Adapter hardening** (adversarial bug-hunt, 12 fixes): VTT skips NOTE/STYLE/REGION
  blocks, handles optional/1–2-digit-hour timestamps and `<v.class>` voice tags; a
  shared speaker-label guard stops colon-in-sentence/heading lines (`Note:`, `Action
  items:`) being mis-parsed as speakers across plaintext/VTT/SRT; generic JSON no longer
  guesses ms vs seconds (assumes seconds; vendor ms handled by dedicated adapters); Otter
  sniff no longer steals generic `{"transcripts":[…]}`; Grain detects an unidentified
  first speaker; Recall tolerates a non-dict participant; Gong `Participant.id` uses the
  real speakerId; vendor sniff windows widened; adapter failures now log at debug.
