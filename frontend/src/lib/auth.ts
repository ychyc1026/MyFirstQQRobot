const SESSION_KEY = "ych.admin.session";

export function getSessionToken(): string | null {
  return window.localStorage.getItem(SESSION_KEY);
}

export function setSessionToken(token: string): void {
  window.localStorage.setItem(SESSION_KEY, token);
}

export function clearSessionToken(): void {
  window.localStorage.removeItem(SESSION_KEY);
}
