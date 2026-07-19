import { AlertTriangle, RotateCcw } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { Logo } from "./Logo";

interface AppErrorBoundaryProps {
  children: ReactNode;
}

interface AppErrorBoundaryState {
  failed: boolean;
}

export class AppErrorBoundary extends Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Uncaught application error", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;

    return (
      <main className="fatal-error-page">
        <Logo />
        <section role="alert">
          <span>
            <AlertTriangle size={24} />
          </span>
          <div>
            <p>Erreur d'affichage</p>
            <h1>Cette page n'a pas pu être chargée.</h1>
            <p>Tes données ne sont pas affectées. Recharge l'application pour reprendre là où tu en étais.</p>
          </div>
          <button className="primary-button" type="button" onClick={() => window.location.reload()}>
            <RotateCcw size={17} /> Recharger
          </button>
        </section>
      </main>
    );
  }
}
