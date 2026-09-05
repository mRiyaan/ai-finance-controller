import Image from "next/image";
import {
  BankIcon,
  MerchantIcon,
  RazorpayIcon,
  VerifiedIcon,
} from "@/components/FinanceIcons";

export default function HeroVisual() {
  return (
    <div className="hero-visual" aria-label="Reconciliation data flow">
      <div className="hero-visual-glow" aria-hidden="true" />

      <div className="hero-visual-top">
        <div className="hero-visual-brand">
          <span className="hero-brand-icon" aria-hidden="true">
            <RazorpayIcon size={19} />
          </span>

          <div>
            <span>Payment infrastructure</span>
            <strong>Razorpay settlement data</strong>
          </div>
        </div>

        <span className="hero-live-badge">
          <span aria-hidden="true" />
          Deterministic-first
        </span>
      </div>

      <div className="hero-visual-canvas">
        <div className="hero-orbit hero-orbit-one" aria-hidden="true" />
        <div className="hero-orbit hero-orbit-two" aria-hidden="true" />

        <Image
        alt="Illustration representing routed financial reconciliation data"
        className="hero-route-image"
        height={260}
        src="assets/route-transparent-ground.svg"
        width={260}
        />

        <div className="hero-source-card hero-source-merchant">
          <span className="source-icon" aria-hidden="true">
            <MerchantIcon size={17} />
          </span>

          <div>
            <strong>Merchant</strong>
            <small>Ledger CSV</small>
          </div>
        </div>

        <div className="hero-source-card hero-source-razorpay">
          <span className="source-icon source-icon-razorpay" aria-hidden="true">
            <RazorpayIcon size={17} />
          </span>

          <div>
            <strong>Razorpay</strong>
            <small>Settlement report</small>
          </div>
        </div>

        <div className="hero-source-card hero-source-bank">
          <span className="source-icon source-icon-bank" aria-hidden="true">
            <BankIcon size={17} />
          </span>

          <div>
            <strong>Bank</strong>
            <small>Statement CSV</small>
          </div>
        </div>

        <div className="hero-verified-card">
          <span className="hero-verified-icon" aria-hidden="true">
            <VerifiedIcon size={15} />
          </span>

          <div>
            <strong>Verified pipeline</strong>
            <small>Integer-paise precision</small>
          </div>
        </div>
      </div>

      <div className="hero-visual-footer">
        <span>Stage 1 exact checks</span>
        <span aria-hidden="true">→</span>
        <span>Stage 2 guarded fuzzy</span>
        <span aria-hidden="true">→</span>
        <span>Stage 3 review</span>
      </div>
    </div>
  );
}