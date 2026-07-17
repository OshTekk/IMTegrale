import { BRAND } from "../brand";

export function BrandMark({ size = 36 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 40 40" aria-hidden="true">
      <rect className="brand-mark-background" width="40" height="40" rx="8" />
      <path className="brand-mark-integral" d="M28 8c-5.7 0-7.4 2.8-8 7.3l-1.2 9.4c-.6 4.5-2.3 7.3-8 7.3" />
      <circle className="brand-mark-start" cx="28" cy="8" r="2.6" />
      <circle className="brand-mark-end" cx="10.8" cy="32" r="2.6" />
    </svg>
  );
}

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <div className="brand" aria-label={BRAND.name}>
      <BrandMark />
      {!compact && <span className="brand-name">{BRAND.name}</span>}
    </div>
  );
}
