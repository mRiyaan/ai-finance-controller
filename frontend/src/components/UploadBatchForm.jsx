"use client";

import { useState } from "react";
import { checkBackendHealth, reconcileFiles } from "@/lib/api";

const initialFiles = {
  merchantFile: null,
  razorpayFile: null,
  bankFile: null,
};

const uploadFields = [
  {
    id: "merchant-file",
    name: "merchantFile",
    label: "Merchant ledger CSV",
    description: "Orders, gateway order IDs, and gross amounts.",
  },
  {
    id: "razorpay-file",
    name: "razorpayFile",
    label: "Razorpay settlement report CSV",
    description: "Payment entities, settlement IDs, actual fees, and tax.",
  },
  {
    id: "bank-file",
    name: "bankFile",
    label: "Bank statement CSV",
    description: "Credits, transaction dates, and bank references or UTRs.",
  },
];

function getMissingFiles(files) {
  return uploadFields
    .filter((field) => !files[field.name])
    .map((field) => field.label);
}

export default function UploadBatchForm({ onReconciliationSuccess }) {
  const [files, setFiles] = useState(initialFiles);
  const [requestState, setRequestState] = useState("idle");
  const [message, setMessage] = useState("");
  const [backendState, setBackendState] = useState("unchecked");

  function handleFileChange(event) {
    const { name, files: selectedFiles } = event.target;
    const selectedFile = selectedFiles?.[0] ?? null;

    setFiles((currentFiles) => ({
      ...currentFiles,
      [name]: selectedFile,
    }));

    setRequestState("idle");
    setMessage("");
  }

  async function handleHealthCheck() {
    setBackendState("checking");
    setMessage("");

    try {
      await checkBackendHealth();
      setBackendState("available");
    } catch (error) {
      setBackendState("unavailable");
      setMessage(
        error instanceof Error
          ? error.message
          : "Could not reach the FastAPI backend."
      );
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();

    const missingFiles = getMissingFiles(files);

    if (missingFiles.length > 0) {
      setRequestState("error");
      setMessage(
        `Select all required CSV files before reconciling: ${missingFiles.join(
          ", "
        )}.`
      );
      return;
    }

    setRequestState("loading");
    setMessage("");

    try {
      const data = await reconcileFiles(files);

      setRequestState("success");
      setBackendState("available");
      setMessage(
        "Reconciliation completed successfully. Detailed results will appear in the next dashboard milestone."
      );

      onReconciliationSuccess(data);
    } catch (error) {
      setRequestState("error");
      setMessage(
        error instanceof Error
          ? error.message
          : "The reconciliation request could not be completed."
      );
    }
  }

  const isLoading = requestState === "loading";

  return (
    <section className="upload-section" aria-labelledby="upload-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Batch intake</p>
          <h2 id="upload-title">Upload reconciliation files</h2>
        </div>

        <button
          className="health-button"
          type="button"
          onClick={handleHealthCheck}
          disabled={backendState === "checking" || isLoading}
        >
          {backendState === "checking"
            ? "Checking backend..."
            : "Check backend status"}
        </button>
      </div>

      <form className="upload-form" onSubmit={handleSubmit}>
        <div className="upload-grid">
          {uploadFields.map((field) => {
            const selectedFile = files[field.name];

            return (
              <label className="file-card" htmlFor={field.id} key={field.id}>
                <span className="file-card-icon" aria-hidden="true">
                  ↑
                </span>

                <span className="file-card-content">
                  <span className="file-card-title">{field.label}</span>
                  <span className="file-card-description">
                    {field.description}
                  </span>

                  <span className="file-card-name">
                    {selectedFile ? selectedFile.name : "No file selected"}
                  </span>
                </span>

                <input
                  accept=".csv,text/csv"
                  className="file-input"
                  id={field.id}
                  name={field.name}
                  onChange={handleFileChange}
                  type="file"
                />
              </label>
            );
          })}
        </div>

        <div className="upload-actions">
          <div>
            <p className="upload-note">
              The request may take longer when Stage 3 retries Gemini or
              switches models for unresolved exceptions.
            </p>

            {backendState === "available" && (
              <p className="status-message status-message-success">
                Backend health check passed.
              </p>
            )}

            {backendState === "unavailable" && (
              <p className="status-message status-message-error">
                Backend is unavailable. Start FastAPI on port 8000 and try
                again.
              </p>
            )}
          </div>

          <button
            className="reconcile-button"
            disabled={isLoading}
            type="submit"
          >
            {isLoading ? "Reconciling batch..." : "Run reconciliation"}
          </button>
        </div>

        {message && (
          <p
            aria-live="polite"
            className={`request-message ${
              requestState === "error"
                ? "request-message-error"
                : "request-message-success"
            }`}
          >
            {message}
          </p>
        )}
      </form>
    </section>
  );
}