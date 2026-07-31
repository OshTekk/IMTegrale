import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import { ToastProvider } from "./components/Toast";
import { EpochQueryClientHost } from "./lib/epochQueryClient";
import { initializeInvitationFragmentOwner } from "./lib/invitationFragmentOwner";
import { SessionAuthority, SessionAuthorityRoot } from "./lib/sessionAuthority";
import { initializeTheme } from "./lib/theme";
import "./styles/core.css";
import "./styles.css";

initializeTheme();

const invitationFragmentOwner = initializeInvitationFragmentOwner();
const sessionAuthority = new SessionAuthority();
sessionAuthority.registerPurge(() => invitationFragmentOwner.destroy());
sessionAuthority.subscribe(() => invitationFragmentOwner.observe(sessionAuthority.getSnapshot()));
sessionAuthority.start();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SessionAuthorityRoot authority={sessionAuthority}>
      <EpochQueryClientHost>
        <BrowserRouter>
          <ToastProvider>
            <AppErrorBoundary>
              <App />
            </AppErrorBoundary>
          </ToastProvider>
        </BrowserRouter>
      </EpochQueryClientHost>
    </SessionAuthorityRoot>
  </StrictMode>,
);
