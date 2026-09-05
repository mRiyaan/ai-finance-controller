import StatusBadge from "@/components/StatusBadge";
import ThemeToggle from "@/components/ThemeToggle";

function FinanceControllerMark() {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height="25"
      viewBox="0 0 24 24"
      width="25"
    >
      <path
        d="M6.5 3.75h8.25l3.75 3.75v11.25a1.5 1.5 0 0 1-1.5 1.5H6.5A1.5 1.5 0 0 1 5 18.75v-13.5a1.5 1.5 0 0 1 1.5-1.5Z"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
      <path
        d="M14.75 3.75V7.5h3.75"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
      <path
        d="m8.5 13 2.1 2.1 4.9-4.9"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.9"
      />
    </svg>
  );
}

export default function Header() {
  return (
    <header className="site-header">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true">
          <FinanceControllerMark />
        </div>

        <div>
          <p className="eyebrow">Razorpay AI Buildathon 2026 · Track 04</p>
          <h1>Recon AI - Finance Reconciler</h1>
        </div>
      </div>

      <div className="header-actions">
        <ThemeToggle />
        <StatusBadge />
      </div>
    </header>
  );
}