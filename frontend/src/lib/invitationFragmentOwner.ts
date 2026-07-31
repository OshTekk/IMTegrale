import { isPrimaryOwnerSession } from "./auth";
import type { SessionAuthoritySnapshot } from "./sessionAuthority";

const INVITATION_TOKEN_PATTERN = /^pcinv1_[A-Za-z0-9_-]{43}$/;

type FragmentBinding = { authEpoch: number; sessionScope: string };

function tokenFromInitialHash(hash: string): string | null {
  if (!hash.startsWith("#")) return null;
  const params = new URLSearchParams(hash.slice(1));
  const values = params.getAll("invite");
  return values.length === 1 && INVITATION_TOKEN_PATTERN.test(values[0]!) ? values[0]! : null;
}

function urlWithoutFragment(url?: string | URL | null): string | URL | null | undefined {
  if (url === undefined || url === null) return url;
  const parsed = new URL(String(url), window.location.href);
  parsed.hash = "";
  if (url instanceof URL) return parsed;
  return `${parsed.pathname}${parsed.search}`;
}

export class InvitationFragmentOwner {
  private token: string | null;
  private rejectedFragment: boolean;
  private binding: FragmentBinding | null = null;
  private destroyed = false;
  private readonly originalPushState: History["pushState"];
  private readonly originalReplaceState: History["replaceState"];
  private readonly removeListeners: Array<() => void> = [];

  constructor() {
    this.originalPushState = window.history.pushState.bind(window.history);
    this.originalReplaceState = window.history.replaceState.bind(window.history);
    const initialHash = window.location.hash;
    this.token = tokenFromInitialHash(initialHash);
    const initialParameters = initialHash.startsWith("#")
      ? new URLSearchParams(initialHash.slice(1))
      : new URLSearchParams();
    this.rejectedFragment = initialParameters.has("invite") && this.token === null;
    this.scrubCurrentUrl();
    if (window.location.hash !== "") this.destroy();
    this.installPermanentUrlGuard();
  }

  observe(snapshot: SessionAuthoritySnapshot): void {
    if (this.destroyed || !this.token) return;
    if (snapshot.securityState === "verifying" && !snapshot.session) return;
    const session = snapshot.session;
    const authorized =
      snapshot.securityState === "verified" &&
      snapshot.sessionScope !== null &&
      session?.authenticated === true &&
      session.private_comparisons?.available === true &&
      isPrimaryOwnerSession(session) &&
      navigator.onLine !== false;
    if (!authorized) {
      this.destroy();
      return;
    }
    const nextBinding = { authEpoch: snapshot.authEpoch, sessionScope: snapshot.sessionScope! };
    if (
      this.binding &&
      (this.binding.authEpoch !== nextBinding.authEpoch || this.binding.sessionScope !== nextBinding.sessionScope)
    ) {
      this.destroy();
      return;
    }
    this.binding = nextBinding;
  }

  consume(authEpoch: number, sessionScope: string): string | null {
    if (
      this.destroyed ||
      !this.token ||
      !this.binding ||
      this.binding.authEpoch !== authEpoch ||
      this.binding.sessionScope !== sessionScope ||
      navigator.onLine === false
    ) {
      this.destroy();
      return null;
    }
    const value = this.token;
    this.token = null;
    this.binding = null;
    return value;
  }

  consumeRejectedFragment(): boolean {
    const rejected = this.rejectedFragment;
    this.rejectedFragment = false;
    return rejected;
  }

  destroy(): void {
    this.token = null;
    this.binding = null;
    this.destroyed = true;
  }

  disposeForTests(): void {
    this.destroy();
    for (const remove of this.removeListeners.splice(0)) remove();
    window.history.pushState = this.originalPushState;
    window.history.replaceState = this.originalReplaceState;
  }

  private scrubCurrentUrl(): void {
    this.originalReplaceState(window.history.state, "", `${window.location.pathname}${window.location.search}`);
  }

  private rejectLaterFragment(): void {
    if (!window.location.hash) return;
    this.destroy();
    this.scrubCurrentUrl();
  }

  private installPermanentUrlGuard(): void {
    const reject = () => this.rejectLaterFragment();
    window.addEventListener("hashchange", reject);
    window.addEventListener("popstate", reject);
    this.removeListeners.push(
      () => window.removeEventListener("hashchange", reject),
      () => window.removeEventListener("popstate", reject),
    );

    window.history.pushState = ((data: unknown, unused: string, url?: string | URL | null) => {
      const hasFragment = url !== undefined && url !== null && new URL(String(url), window.location.href).hash !== "";
      if (hasFragment) this.destroy();
      this.originalPushState(data, unused, urlWithoutFragment(url));
    }) as History["pushState"];
    window.history.replaceState = ((data: unknown, unused: string, url?: string | URL | null) => {
      const hasFragment = url !== undefined && url !== null && new URL(String(url), window.location.href).hash !== "";
      if (hasFragment) this.destroy();
      this.originalReplaceState(data, unused, urlWithoutFragment(url));
    }) as History["replaceState"];
  }
}

let fragmentOwner: InvitationFragmentOwner | null = null;

export function initializeInvitationFragmentOwner(): InvitationFragmentOwner {
  fragmentOwner ??= new InvitationFragmentOwner();
  return fragmentOwner;
}

export function consumeInvitationFragment(authEpoch: number, sessionScope: string): string | null {
  return fragmentOwner?.consume(authEpoch, sessionScope) ?? null;
}

export function resetInvitationFragmentOwnerForTests(): void {
  fragmentOwner?.disposeForTests();
  fragmentOwner = null;
}
