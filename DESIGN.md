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
