import { ChevronDown, Info } from "lucide-react";
import { GradeBadge } from "../../components/GradeBadge";
import { formatNumber } from "../../lib/format";
import { SIMULATION_GRADES } from "../../lib/simulations";

export function GpaSimulationFormula({ version }: { version: string }) {
  return (
    <details className="gpa-formula">
      <summary>
        <span>
          <Info size={16} /> Barème et formule
        </span>
        <ChevronDown size={17} />
      </summary>
      <div>
        <p>
          <strong>GPA = somme des points GPA × ECTS ÷ somme des ECTS.</strong> Seules les UE avec un grade et des ECTS
          renseignés sont incluses. Le résultat est arrondi au centième.
        </p>
        <dl className="gpa-grade-scale">
          {SIMULATION_GRADES.map(({ grade, points }) => (
            <div key={grade}>
              <dt>
                <GradeBadge grade={grade} />
              </dt>
              <dd>{formatNumber(points)} points</dd>
            </div>
          ))}
        </dl>
        <small>Règle IMTégrale {version} · projection indicative, jamais publiée dans le classement.</small>
      </div>
    </details>
  );
}
