/** Where the sign-up confirmation email lands. Must be allowed in Supabase → Auth → URL Configuration. */
export const CONFIRM_PATH = "/auth/confirmed";
export const confirmRedirectUrl = () => window.location.origin + CONFIRM_PATH;
/** How long the confirm page waits for supabase-js to pick the session out of the URL. */
export const SESSION_WAIT_MS = 4000;
