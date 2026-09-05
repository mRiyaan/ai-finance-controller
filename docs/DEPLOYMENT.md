# Deployment

## Production endpoints

### Frontend

```text
https://ai-finance-controller-git-main-m-riyaan.vercel.app/
```

### Backend

```text
https://ai-finance-controller-826949991342.asia-south1.run.app
```

### Swagger

```text
https://ai-finance-controller-826949991342.asia-south1.run.app/docs
```

---

## 1. Repository layout

The project is a monorepo:

```text
ai-finance-controller/
├── backend/
├── frontend/
├── sample-data/
└── docs/
```

The backend contains its own Dockerfile. The frontend is deployed from `frontend/`.

---

## 2. Local backend

```bash
cd backend

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000
```

Verify:

```bash
curl http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok"}
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

---

## 3. Local frontend

```bash
cd frontend
npm install
npm run dev
```

Set:

```text
NEXT_PUBLIC_API_URL=http://localhost:8000
```

for local development.

---

## 4. Docker validation

The backend was containerized and exercised locally before deployment.

Build:

```bash
cd backend
docker build -t ai-finance-controller .
```

Run:

```bash
docker run --rm -p 8080:8080 ai-finance-controller
```

The same containerized `/reconcile` path was then tested with the adversarial CSV batch.

The point of this step was to verify the actual deployment artifact, not just the laptop Python environment.

---

## 5. Google Cloud project

The backend was deployed to the existing Google Cloud project:

```text
apac-agent-build
```

The project was linked to the active billing account and verified as billing-enabled before the required APIs were enabled.

Enabled services:

```text
run.googleapis.com
cloudbuild.googleapis.com
artifactregistry.googleapis.com
```

Secret Manager was also enabled for the Gemini credential.

---

## 6. Gemini secret

The Gemini key is not stored in source control.

A Secret Manager secret was created:

```text
gemini-api-key
```

The Cloud Run runtime service account was granted secret-access permission.

Cloud Run receives the secret as:

```text
GEMINI_API_KEY
```

---

## 7. Production model configuration

The intended production model chain is:

```text
gemini-3.1-flash-lite
gemini-3.5-flash-lite
```

The value is supplied through:

```text
GEMINI_MODEL_CHAIN
```

The chain is tried left-to-right under the Stage 3 retry/fallback logic.

---

## 8. Cloud Run deployment

From:

```bash
cd backend
```

the production deployment can be performed with a source deployment using the repository's Dockerfile and the Cloud Run service configuration.

The critical runtime settings are:

```text
GEMINI_MODEL_CHAIN
ALLOWED_ORIGINS
GEMINI_API_KEY
```

The API key is injected from Secret Manager rather than stored in the image.

The deployed service is:

```text
ai-finance-controller
```

in:

```text
asia-south1
```

---

## 9. Frontend deployment

The Vercel project uses:

```text
Root Directory: frontend/
Framework: Next.js
```

The production frontend receives the backend base URL through:

```text
NEXT_PUBLIC_API_URL
```

Production value:

```text
https://ai-finance-controller-826949991342.asia-south1.run.app
```

The frontend code appends API paths itself.

Do not set:

```text
NEXT_PUBLIC_API_URL=https://...run.app/reconcile
```

---

## 10. Production smoke test

### Health

```bash
curl https://ai-finance-controller-826949991342.asia-south1.run.app/health
```

Expected:

```json
{"status":"ok"}
```

### Reconciliation

From the repository root:

```bash
curl -X POST \
  "https://ai-finance-controller-826949991342.asia-south1.run.app/reconcile" \
  -H "accept: application/json" \
  -F "merchant_file=@sample-data/adversarial_merchant_ledger.csv;type=text/csv" \
  -F "razorpay_file=@sample-data/adversarial_razorpay_settlement_report.csv;type=text/csv" \
  -F "bank_file=@sample-data/adversarial_bank_statement.csv;type=text/csv"
```

The verified deployment returned:

```text
HTTP 200
```

with the complete reconciliation response.

---

## 11. Release checks

Before calling the deployment complete:

```text
[ ] main branch is current
[ ] backend tests pass
[ ] Docker image builds
[ ] /health passes
[ ] /reconcile passes with adversarial data
[ ] Cloud Run service is serving 100% traffic
[ ] Vercel site loads
[ ] Vercel points to Cloud Run
[ ] dead letters are visible
[ ] Stage 3 evidence is visible
[ ] sample data is in the root sample-data directory
```

---

## 12. Deployment lessons

The major deployment issue was not application code. The Google Cloud project was initially attached to a closed billing account. Cloud Run and Cloud Build could not be activated until the project was linked to an active billing account.

The other important deployment lesson was that local success was not treated as enough evidence. The final adversarial request was sent to the live Cloud Run service and returned HTTP 200 with the expected backend-owned summary.

That is the deployment milestone that matters:

```text
source
→ Docker
→ Cloud Run
→ real HTTP request
→ real JSON response
```
