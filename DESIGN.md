---
version: alpha
name: CampusPulse Assignment Guardian
description: A quiet student workspace for source-grounded assignment review.
colors:
  primary: "#176b5e"
  background: "#f6f5f0"
  surface: "#fffefa"
  text: "#192d3a"
  muted: "#67716f"
  border: "#dedfd5"
  warning: "#885b20"
  danger: "#a04734"
typography:
  sans:
    fontFamily: 'Inter, "Segoe UI", Arial, sans-serif'
rounded:
  DEFAULT: "17px"
spacing:
  mobile-page-gutter: "16px"
components:
  button: {}
  configuration-notice: {}
  requirement: {}
---

# CampusPulse Design System

## Overview

A student review desk: assignment text, requirement checklist, and supporting
repository evidence. The GDG Astana brief establishes the audience and workflow.
The existing interface uses English; browser-local dates use Intl. This change
preserves that language policy and the existing visual identity.
The numbered upload/analyze/review journey communicates actual progress.
Runtime CSS in `campuspulse/static/styles.css` is the canonical token owner;
this file records existing values rather than generating a second token system.

## Colors

Paper and surface backgrounds, dark blue text, teal actions, amber uncertainty,
and red errors use the tokens above. Meaning is always also expressed in text.
The maintained theme is light only.

## Typography

Use the existing sans-serif stack. Text keeps browser font fallback support.
Requirement titles, quoted sources, and status labels retain their existing roles.

## Layout

Reuse the two-column workspace and its single-column mobile behavior. Existing
breakpoints are 1440, 1000, 760, and 510 px. Preserve source wrapping and scrollable
evidence content. Configuration changes reuse the notice and service badge.

## Elevation & Depth

Use existing subtle panel borders and shadow. Service status introduces no new
overlay, modal, or floating surface.

## Shapes

Panels retain the canonical 17 px radius. Existing badges and controls keep their
business-specific shapes; no new component geometry is introduced.

## Components

`client.mjs:configuration` owns configuration messages, analysis availability,
and Issue-write availability. `app.mjs:renderHealth` maps that result to the
existing badge/notice and disabled analysis button. `showMessage` owns operation
feedback; `setBusy` prevents repeated submissions. API responses and
`docs/CONTRACT.md` own authorization and persisted state.

Cloud configuration enables analysis without local provider keys. Configuration
does not prove IAM, quota, or runtime availability: the analysis request checks
these. Upload remains available before cloud configuration. Cloud errors retain
the assignment, show a recovery message, and never trigger a local fallback.
Tool steps returned by cloud analysis use the existing activity renderer.

Use native buttons, existing keyboard focus, text status, and existing live
regions. Motion respects the current reduced-motion CSS. No new selectors,
calendar controls, navigation, or forms are introduced.

## Do's and Don'ts

- Keep source citations and unverified findings visible.
- Distinguish configuration from successful service verification.
- Keep credentials on the server and cloud runtime.
- Preserve the upload session on analysis failure.
- Do not describe a dry run or an injected test result as live cloud verification.

## Implementation Notes

Backend selection is server-controlled through ANALYSIS_BACKEND. Health exposes
configuration booleans and the backend name, never credentials or resource IDs.
Client unit tests and HTTP integration tests cover the cloud configuration and
error states. Browser testing provides separate evidence from these offline tests.

## CampusGDG Streamlit surface

`campus_chat_app.py` is a separate student workspace from the light CampusPulse
FastAPI surface above. Its redesign follows the user's explicit dark-theme and
Russian-language brief; the existing CampusPulse tokens and English UI stay intact.

The reference is a quiet study desk on a small laptop: the nearest due date has
priority, today's schedule and open tasks follow, and chat is a focused workspace.
Avoid decorative gradients, glows, emoji navigation and repeated bordered shells.

Runtime token owner: `campus_chat_ui/styles.css`, loaded by `CUSTOM_CSS` in
`campus_chat_app.py`. Streamlit's native theme
adapter is `.streamlit/config.toml`: background `#10151d`, surface `#181f2a`, text
`#eef2f8`, primary accent `#8ab8fa`. Primary action fill is `#3667ad` with white text;
muted text `#a9b7c9`, raised surface `#202a38`, borders `#354257`.

Use the installed Segoe UI/sans-serif stack for Cyrillic, 32px page headings,
21px section headings, 16px body/input text and 14px secondary labels. Numerals
in calendar/time use tabular figures. Shared controls have a 10px radius and 42px
minimum button height; content groups use 12–14px radii. Spacing uses 8/12/16/24px.
No external font request is required. Material icons come from Streamlit.

The workspace max width is 1180px; gutters reduce at 900px and 640px. At phone
width columns stack in content order. Only chat history has a bounded viewport
height; the composer remains in normal document flow, including split mode.
Native Streamlit controls own focus, keyboard behavior and popups. Global visible
focus, wrapping, scrollbars, reduced motion and forced colors are CSS concerns.

Behavior and component ownership are recorded in `docs/CAMPUSGDG_UI.md`. Inline
feedback carries errors, pending requests and connection state; samples always have
text labels. Dates display in Russian, typed dates use DD.MM.YYYY, and current dates
use Asia/Qyzylorda (UTC+5). Agent settings and existing session data are preserved.

The CampusGDG sidebar follows the Gemini pattern: a subdued rounded new-dialog
action, flat navigation rows with Material icons, a quiet recent-chat list with
local title search, and settings at the bottom. Native Streamlit radio inputs keep
selection and keyboard semantics beneath the row treatment. The sidebar surface is
`#161d27`, its selected pill is `#273e5a`, and selected text is `#c6dfff`. Only the
sidebar uses these variants; the main workspace and connection behavior stay intact.

The «До сдачи» view reuses the Streamlit surface, controls and token owner above.
Its signature is the paired before/after study timeline: date rails in the existing
accent, tabular time labels in muted text, and wrapping work titles. The pairing
communicates an actual plan change; it contains no decorative progress percentages.
Native columns stack on narrow screens in before/after reading order. Evidence and
tool receipts remain accessible through native expanders. Runtime rules live in
`campus_chat_ui/styles.css` under `.deadline-*`; no second theme or token set is added.
