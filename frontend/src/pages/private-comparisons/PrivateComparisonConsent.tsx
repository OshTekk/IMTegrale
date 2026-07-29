import { useId } from "react";

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

export function privateComparisonConsentComplete(value: PrivateComparisonConsentState): boolean {
  return value.identity && value.academic && value.copyRisk;
}

export function PrivateComparisonConsent({
  value,
  onChange,
  legend = "Ton consentement",
}: {
  value: PrivateComparisonConsentState;
  onChange: (value: PrivateComparisonConsentState) => void;
  legend?: string;
}) {
  const prefix = useId();
  const items = [
    {
      key: "identity" as const,
      label: "J’accepte que mon identité officielle soit visible par l’étudiant qui acceptera ce lien.",
    },
    {
      key: "academic" as const,
      label: "J’accepte de partager mon résumé académique et mes UE communes dans le périmètre indiqué.",
    },
    {
      key: "copyRisk" as const,
      label: "Je comprends que l’autre participant pourra recopier ou capturer ce qu’il voit.",
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
