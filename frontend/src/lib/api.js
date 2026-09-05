const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export async function checkBackendHealth() {
  const response = await fetch(`${API_BASE_URL}/health`, {
    method: "GET",
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(
      `Backend health check failed with status ${response.status}.`
    );
  }

  const data = await response.json();

  if (data?.status !== "ok") {
    throw new Error("Backend health endpoint returned an unexpected response.");
  }

  return data;
}

export async function reconcileFiles({
  merchantFile,
  razorpayFile,
  bankFile,
}) {
  const formData = new FormData();

  formData.append("merchant_file", merchantFile);
  formData.append("razorpay_file", razorpayFile);
  formData.append("bank_file", bankFile);

  const response = await fetch(`${API_BASE_URL}/reconcile`, {
    method: "POST",
    body: formData,
  });

  let responseBody = null;

  try {
    responseBody = await response.json();
  } catch {
    responseBody = null;
  }

  if (!response.ok) {
    const detail =
      typeof responseBody?.detail === "string"
        ? responseBody.detail
        : `Reconciliation request failed with status ${response.status}.`;

    throw new Error(detail);
  }

  if (!responseBody || typeof responseBody !== "object") {
    throw new Error("The backend returned an invalid reconciliation response.");
  }

  return responseBody;
}