import { BarChart3, GraduationCap } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

export function SimulationLayout() {
  return (
    <div className="simulation-hub">
      <nav className="simulation-mode-switch" aria-label="Type de simulation">
        <NavLink to="/simulations/gpa" className={({ isActive }) => (isActive ? "active" : undefined)}>
          <GraduationCap size={17} />
          <span>GPA</span>
          <small>Grades et ECTS</small>
        </NavLink>
        <NavLink to="/simulations/notes" className={({ isActive }) => (isActive ? "active" : undefined)}>
          <BarChart3 size={17} />
          <span>Notes</span>
          <small>Moyennes et coefficients</small>
        </NavLink>
      </nav>
      <Outlet />
    </div>
  );
}
