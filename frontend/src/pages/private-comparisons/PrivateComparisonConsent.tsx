import { useId } from "react";
import type { PrivateComparisonConsentManifestResponse } from "../../generated/api/types.gen";

export interface PrivateComparisonConsentState {
  identity: boolean;
  academic: boolean;
  copyRisk: boolean;
}

export const emptyPrivateComparisonConsent: PrivateComparisonConsentState = {
  identity: false,
  academic: false,
  copyRisk: false,
};

export interface CompletePrivateComparisonConsentState extends PrivateComparisonConsentState {
  identity: true;
  academic: true;
  copyRisk: true;
}

export function privateComparisonConsentComplete(
  value: PrivateComparisonConsentState,
): value is CompletePrivateComparisonConsentState {
  return value.identity && value.academic && value.copyRisk;
}

export function PrivateComparisonConsent({
  manifest,
  value,
  onChange,
  legend = "Ton consentement",
}: {
  manifest: PrivateComparisonConsentManifestResponse;
  value: PrivateComparisonConsentState;
  onChange: (value: PrivateComparisonConsentState) => void;
  legend?: string;
}) {
  const prefix = useId();
  const items = [
    {
      key: "identity" as const,
      label: manifest.identity_disclosure.confirmation,
    },
    {
      key: "academic" as const,
      label: manifest.academic_scope_confirmation,
    },
    {
      key: "copyRisk" as const,
      label: manifest.copy_risk.confirmation,
    },
  ];

  return (
    <fieldset className="private-comparison-consent">
      <legend>{legend}</legend>
      {items.map((item) => {
        const id = `${prefix}-${item.key}`;
        return (
          <label key={item.key} htmlFor={id}>
            <input
              id={id}
              type="checkbox"
              checked={value[item.key]}
              onChange={(event) => onChange({ ...value, [item.key]: event.target.checked })}
            />
            <span>{item.label}</span>
          </label>
        );
      })}
    </fieldset>
  );
}
