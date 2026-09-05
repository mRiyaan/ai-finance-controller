function IconFrame({ children, size = 20, title }) {
  return (
    <svg
      aria-hidden={title ? undefined : true}
      aria-label={title}
      fill="none"
      height={size}
      role={title ? "img" : undefined}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
      viewBox="0 0 24 24"
      width={size}
    >
      {title ? <title>{title}</title> : null}
      {children}
    </svg>
  );
}

export function ControllerIcon({ size = 26 }) {
  return (
    <IconFrame size={size} title="AI Finance Controller">
      <path d="M4 19V5" />
      <path d="M20 19V5" />
      <path d="M4 8h16" />
      <path d="M7 5v3" />
      <path d="M12 5v3" />
      <path d="M17 5v3" />
      <path d="M7 19v-5h10v5" />
      <path d="M10 14v5" />
      <path d="M14 14v5" />
      <circle cx="18" cy="16" r="3" />
      <path d="M18 14.7v2.6" />
      <path d="M16.8 16h2.4" />
    </IconFrame>
  );
}

export function MerchantIcon({ size = 20 }) {
  return (
    <IconFrame size={size} title="Merchant ledger">
      <path d="M4 10.5 6 5h12l2 5.5" />
      <path d="M5 10.5V19h14v-8.5" />
      <path d="M4 10.5h16" />
      <path d="M7 10.5a2 2 0 0 0 4 0" />
      <path d="M11 10.5a2 2 0 0 0 4 0" />
      <path d="M8 19v-4h8v4" />
    </IconFrame>
  );
}

export function RazorpayIcon({ size = 20 }) {
  return (
    <IconFrame size={size} title="Payment gateway">
      <rect height="14" rx="2" width="18" x="3" y="5" />
      <path d="M3 10h18" />
      <path d="M7 15h3" />
      <path d="M15.5 14.2 17.3 16l-1.8 1.8" />
    </IconFrame>
  );
}

export function BankIcon({ size = 20 }) {
  return (
    <IconFrame size={size} title="Bank statement">
      <path d="m3 9 9-5 9 5" />
      <path d="M4 10h16" />
      <path d="M6 10v7" />
      <path d="M10 10v7" />
      <path d="M14 10v7" />
      <path d="M18 10v7" />
      <path d="M3 20h18" />
      <path d="M5 17h14" />
    </IconFrame>
  );
}

export function VerifiedIcon({ size = 20 }) {
  return (
    <IconFrame size={size} title="Verified">
      <circle cx="12" cy="12" r="8.5" />
      <path d="m8.5 12 2.3 2.3 4.7-5" />
    </IconFrame>
  );
}