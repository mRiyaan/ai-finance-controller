import io
import os
import pandas as pd
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .schemas import FullReconciliationResult
from .stage1_exact_match import (
    _load_bank_df,
    _load_merchant_df,
    _load_razorpay_df,
    reconcile_full,
)
from .stage3_exception_reasoner import reconcile_exceptions


app = FastAPI(title="AI Finance Controller - Stages 1, 2 and 3")


# Production/development origins.

DEFAULT_ALLOWED_ORIGINS = (
    "https://ai-finance-controller-git-main-m-riyaan.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://ai-finance-controller-liard-xi.vercel.app/",
    "https://ai-finance-controller-k34l9q8ug-m-riyaan.vercel.app/",

)


def _get_allowed_origins() -> list[str]:
    configured_origins = os.getenv("ALLOWED_ORIGINS", "")
    if configured_origins.strip():
        return [
            origin.strip().rstrip("/")
            for origin in configured_origins.split(",")
            if origin.strip()
        ]

    return list(DEFAULT_ALLOWED_ORIGINS)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _read_csv_upload(upload: UploadFile) -> pd.DataFrame:
    content = upload.file.read()

    return pd.read_csv(
        io.BytesIO(content),
        dtype=str,
        keep_default_na=False,
    )


@app.post("/reconcile", response_model=FullReconciliationResult)
async def reconcile_endpoint(
    merchant_file: UploadFile = File(...),
    razorpay_file: UploadFile = File(...),
    bank_file: UploadFile = File(...),
) -> FullReconciliationResult:
    merchant_df = _read_csv_upload(merchant_file)
    razorpay_df = _read_csv_upload(razorpay_file)
    bank_df = _read_csv_upload(bank_file)

    result = reconcile_full(
        merchant_df=merchant_df,
        razorpay_df=razorpay_df,
        bank_df=bank_df,
    )

    if not result.stage3_handoffs:
        return result

    merchant_rows, _ = _load_merchant_df(merchant_df)
    razorpay_rows, _ = _load_razorpay_df(razorpay_df)
    bank_rows, _ = _load_bank_df(bank_df)

    internal_handoffs = [
        {
            "record": handoff.record,
            "status": handoff.status,
            "score": handoff.score,
            "amount_diff_paise": handoff.amount_diff_paise,
            "date_diff_days": handoff.date_diff_days,
            "error_code": handoff.error_code,
            "failed_gates": handoff.failed_gates,
            "reason": handoff.reason,
            "source_record_id": handoff.source_record_id,
            "candidate_record_id": handoff.candidate_record_id,
            "review_evidence": (
                handoff.review_evidence.model_dump()
                if handoff.review_evidence is not None
                else None
            ),
        }
        for handoff in result.stage3_handoffs
    ]

    result.stage3_results = reconcile_exceptions(
        stage3_handoffs=internal_handoffs,
        merchant_rows=merchant_rows,
        razorpay_rows=razorpay_rows,
        bank_rows=bank_rows,
    )

    return result


@app.get("/health")
async def health():
    return {"status": "ok"}