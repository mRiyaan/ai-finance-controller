import io
import pandas as pd
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from .stage1_exact_match import reconcile
from .schemas import ReconciliationResult

app = FastAPI(title="AI Finance Controller - Stage 1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _read_csv_upload(upload: UploadFile) -> pd.DataFrame:
    content = upload.file.read()
    return pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)


@app.post("/reconcile", response_model=ReconciliationResult)
async def reconcile_endpoint(
    merchant_file: UploadFile = File(...),
    razorpay_file: UploadFile = File(...),
    bank_file: UploadFile = File(...),
) -> ReconciliationResult:
    merchant_df = _read_csv_upload(merchant_file)
    razorpay_df = _read_csv_upload(razorpay_file)
    bank_df = _read_csv_upload(bank_file)

    result = reconcile(merchant_df, razorpay_df, bank_df)
    return result


@app.get("/health")
async def health():
    return {"status": "ok"}