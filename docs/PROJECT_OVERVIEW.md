# MediMind — Project Overview

## Title
**MediMind — Smart, Verified & Doctor-Approved Discharge Summaries**

## Problem
Doctors and hospital staff may need to consolidate admission notes, investigation reports, prescriptions and treatment information into a discharge summary. Manual preparation is repetitive and can lead to inconsistent documentation.

## Proposed solution
MediMind assists the discharge workflow by ingesting patient records, preparing structured information, generating an AI draft, mapping claims to evidence, flagging missing/unsupported information, and requiring doctor review before approval.

## Differentiators
- Evidence-linked summary claims
- AI safety/evidence coverage panel
- Missing-information detection
- Doctor-in-the-loop editing and approval
- Version/audit history
- Patient-friendly instructions
- Medication visualization-ready data model
- Final PDF generation
- Optional local Llama 3.1 through Ollama

## Architecture
React/Vite → FastAPI → record extraction/OCR adapter → structured patient data → evidence engine → optional Llama 3.1 → doctor review → audit → PDF.

## Prototype boundaries
All included patient data is synthetic. The system is a documentation prototype, not a diagnostic or treatment system. Production use would require clinical validation, privacy/security controls, identity management, EHR/FHIR integration, monitoring, consent/retention policies and regulatory review.
