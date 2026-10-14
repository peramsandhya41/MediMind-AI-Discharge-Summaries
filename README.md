# MediMind — Smart, Verified & Doctor-Approved Discharge Summaries

MediMind is a hackathon/demo prototype with separate Doctor, Staff and Patient experiences, synthetic sample data, patient assignment, appointment scheduling, file sharing, notification centre, AI-assisted draft generation, doctor approval and PDF export.

## Important demo safety note

All preloaded patients and clinicians are synthetic demo identities. Do not upload real patient records or use this as a production clinical system. Authentication is prototype-grade and requires a security review, stronger session/token handling, access-control enforcement, verified identities, encryption, backups and applicable healthcare/privacy compliance before real use.

## Preloaded demo accounts

All preloaded accounts use password `demo123`.

### Doctor
- Email: `doctor@medimind.demo`
- Mobile: `9876543210`
- Password: `demo123`

There are 12 preloaded doctor accounts total. Other demo doctors follow the naming format in the `doctors` API; their email and mobile values are available in the UI dropdown/API, and password is `demo123`.

### Patient
- Email: `rahul.patient@medimind.demo`
- Mobile: `9000000001` (use the value displayed by the API if the legacy patient record is selected)
- Password: `demo123`

Other preloaded patients use emails such as `patient1@medimind.demo`, `patient2@medimind.demo`, etc. and password `demo123`.

### Staff
Create a staff account from the Staff portal sign-up screen. Staff accounts are stored in the local SQLite database.

## Run on Windows / VS Code

### 1. Backend terminal

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
python -m pip install --default-timeout=120 --retries 10 -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Backend checks: `http://127.0.0.1:8000/api/health` and API docs `http://127.0.0.1:8000/docs`.

### 2. Frontend terminal

```powershell
cd frontend
npm install
npm run dev
```

Open the local URL printed by Vite (commonly `http://localhost:5173`). Keep both terminals running.

## OCR, AI draft generation, professional PDF, and automatic approval email

- Doctor portal supports multi-file upload for PDF, PNG/JPG/WEBP/TIFF images, TXT, and DOCX (15 MB per file). PDF text is extracted first; scanned PDFs and images use Tesseract OCR. Extracted text is stored with the document and included as source context when a doctor generates a discharge-summary draft. The UI shows extraction status and lets the doctor preview extracted text.
- Install Tesseract OCR separately on Windows and ensure `tesseract.exe` is on PATH. A common installer is the UB Mannheim Windows build; after installing, open a new terminal and check `tesseract --version`. If OCR reports `ocr-not-installed`, install Tesseract and the Python requirements, then restart the backend.
- Optional hosted AI draft generation uses `MEDIMIND_AI_API_KEY` (or `OPENAI_API_KEY`) and `MEDIMIND_AI_MODEL`; keys must stay in the backend environment, never in frontend code. Alternatively, configure local Ollama. Without a configured AI model, the app generates a clearly review-required fallback draft and displays extracted source text; it cannot claim that fallback is a model-generated clinical summary.
- The PDF includes structured patient/admission details, clinical summary, diagnosis, investigations, treatment, medication reconciliation, follow-up, patient instructions, source-document extraction status, approval status/timestamp, page numbers, and a clinical safety notice.
- When the doctor approves a summary, it is automatically made available in the patient's portal, an in-app notification is created, and an email is attempted. Real email requires the SMTP variables below. The email does not attach sensitive medical documents; it tells the patient to sign in to view the summary securely.

### OCR setup on Windows

1. Install Python dependencies: `python -m pip install -r requirements.txt`
2. Install Tesseract OCR separately and ensure `tesseract --version` works in a new terminal.
3. Restart FastAPI and upload a scanned test image or PDF in the Doctor portal. Check the extraction status and preview the text before generating a draft.

## Features

- **Doctor dashboard:** personalized assigned-patient caseload, upcoming appointments and appointment requests, upload records, generate/edit/verify discharge summaries, audit and notifications. Only the doctor review flow can approve a summary.
- **Staff dashboard:** patient registration with doctor assignment, appointment scheduling, record management, follow-up coordination and notifications. Staff can view summary status and send only doctor-approved summaries to the patient portal.
- **Patient dashboard:** patient-specific profile, shared files and approved summaries, own appointment list, and appointment request form.
- **Seed data:** 12 synthetic doctors and 15 synthetic patients, assigned across doctors.
- **Notifications:** in-app notifications are stored in SQLite for shares, assignments, appointment creation/requests and approved-summary sharing. Patient appointment requests appear as `Requested` for the care team.
- **Real email:** optional SMTP delivery for patient registration, assignments, scheduled/requested appointments, shared files and approved discharge-summary notifications. Set SMTP environment variables in the backend terminal; for Gmail use an App Password. Never put SMTP credentials in frontend code or commit them to Git.

### Configure SMTP for actual email notifications

Set the following environment variables in the same terminal before starting the backend (replace placeholders with your own provider's settings):

```powershell
$env:MEDIMIND_SMTP_HOST="smtp.gmail.com"
$env:MEDIMIND_SMTP_PORT="587"
$env:MEDIMIND_SMTP_FROM="your-sender@gmail.com"
$env:MEDIMIND_SMTP_PASSWORD="your-app-password"
uvicorn app.main:app --reload --port 8000
```

If SMTP is not configured, sharing still creates an in-app notification and the document appears in the patient's portal; the app clearly reports that an external email was not sent.

## Deployment

For access from other computers/internet, deploy frontend and backend separately, set `VITE_API_BASE_URL` to the public backend `/api` URL, set `MEDIMIND_CORS_ORIGINS` to the exact deployed frontend origin, and use a persistent database/file store. The current local SQLite/file upload setup is for demonstration only. HTTPS and secure authentication are required before handling real medical information.

## Staff workspace update

Doctor, Staff, and Patient have separate sign-in choices. Staff gets a dedicated operational dashboard with Patient Registration, Record Management, Appointments, Doctor Follow-ups, Notifications, and Security & Audit. Doctors retain their own dashboard and assigned-patient view. Patient accounts continue to use a separate patient portal. Registration, doctor assignment, and appointment endpoints send external email when SMTP environment variables are configured; otherwise, in-app notifications are created. The header branding is MediMind Workspace with the updated logo.

To run the frontend after extracting this project, open a terminal in `frontend` and run `npm install` followed by `npm run dev`. Start the FastAPI backend separately as described above. The frontend build could not be verified in this environment because package installation timed out; if your local build reports an error, share the terminal output to troubleshoot it.


## Latest doctor, staff and patient workflow

- The demo doctor account has a seeded synthetic caseload and four sample appointments to make the dashboard useful on first launch. The app seeds 12 doctors and 15 synthetic patients into a fresh SQLite database. Existing databases are preserved; the seed routine only adds missing demo rows and updates the first six synthetic records to the primary demo doctor.
- Patients can request an appointment from their own portal. The request is saved with `Requested` status and in-app alerts are created for the patient and doctor. SMTP sends confirmation/alert emails when configured. Staff can review the request in Appointments.
- Doctors generate, edit and approve discharge-summary drafts. Staff can view status and share an approved summary; patients see the shared summary under Doctor-Approved Discharge Summaries.
- The frontend passes role headers for workflow gating. This is a demo prototype, not production-grade authorization: do not use it with real patient data until proper server-side sessions/tokens, role authorization, identity verification, secure password hashing, access logging, encrypted storage and privacy/compliance review are implemented.
