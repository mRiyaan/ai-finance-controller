export function formatPaise(paise) {
  if (!Number.isSafeInteger(paise)) {
    return "—";
  }

  const isNegative = paise < 0;
  const absolutePaise = Math.abs(paise);
  const rupees = Math.floor(absolutePaise / 100);
  const paiseRemainder = String(absolutePaise % 100).padStart(2, "0");

  const formattedRupees = new Intl.NumberFormat("en-IN").format(rupees);

  return `${isNegative ? "-" : ""}₹${formattedRupees}.${paiseRemainder}`;
}

export function formatCount(value) {
  return Number.isSafeInteger(value) ? value.toLocaleString("en-IN") : "—";
}