import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  getSession: vi.fn(),
  onAuthStateChange: vi.fn(),
  resend: vi.fn(),
}));
vi.mock("@/integrations/supabase/client", () => ({ supabase: { auth } }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import AuthConfirmed from "@/pages/AuthConfirmed";
import { CONFIRM_PATH, SESSION_WAIT_MS } from "@/lib/auth-redirect";

const user = { id: "u1", email: "ding@example.com", user_metadata: { display_name: "Ding" } };

function renderAt(urlSuffix: string) {
  // the component reads window.location (where supabase-js leaves the tokens / errors)
  window.history.replaceState(null, "", CONFIRM_PATH + urlSuffix);
  const router = createMemoryRouter(
    [
      { path: CONFIRM_PATH, element: <AuthConfirmed /> },
      { path: "/app", element: <p>APP PAGE</p> },
      { path: "/sign-in", element: <p>SIGN IN PAGE</p> },
    ],
    { initialEntries: [CONFIRM_PATH] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

beforeEach(() => {
  auth.getSession.mockReset();
  auth.resend.mockReset();
  auth.onAuthStateChange
    .mockReset()
    .mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
});
afterEach(() => vi.useRealTimers());

describe("AuthConfirmed", () => {
  it("shows success, strips the tokens from the URL, then goes to /app", async () => {
    vi.useFakeTimers();
    auth.getSession.mockResolvedValue({ data: { session: { user } } });
    const router = renderAt("#access_token=secret&refresh_token=r&type=signup");
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText(/Email 驗證完成/)).toBeInTheDocument();
    expect(screen.getByText(/歡迎，Ding！/)).toBeInTheDocument();
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe(CONFIRM_PATH);

    expect(screen.getByText("3 秒後自動前往…")).toBeInTheDocument();
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        vi.advanceTimersByTime(1000);
      });
    }
    expect(router.state.location.pathname).toBe("/app");
  });

  it("also confirms when the session arrives via onAuthStateChange", async () => {
    auth.getSession.mockResolvedValue({ data: { session: null } });
    let cb: (e: string, s: unknown) => void = () => {};
    auth.onAuthStateChange.mockImplementation((fn) => {
      cb = fn;
      return { data: { subscription: { unsubscribe: vi.fn() } } };
    });
    renderAt("#access_token=x");
    expect(screen.getByText(/驗證中/)).toBeInTheDocument();
    act(() => cb("SIGNED_IN", { user }));
    expect(await screen.findByText(/Email 驗證完成/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /前往我的追蹤/ }));
    expect(await screen.findByText("APP PAGE")).toBeInTheDocument();
  });

  it("explains an expired link and resends the confirmation email", async () => {
    auth.resend.mockResolvedValue({ error: null });
    renderAt(
      "#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired",
    );
    expect(screen.getByText("確認連結無法使用")).toBeInTheDocument();
    expect(screen.getByText(/已過期或已經使用過/)).toBeInTheDocument();
    expect(auth.getSession).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("註冊用的 Email"), {
      target: { value: "ding@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /重新寄送確認信/ }));
    await waitFor(() => expect(screen.getByText(/已重新寄出確認信/)).toBeInTheDocument());
    expect(auth.resend).toHaveBeenCalledWith({
      type: "signup",
      email: "ding@example.com",
      options: { emailRedirectTo: window.location.origin + CONFIRM_PATH },
    });
  });

  it("reads errors from the query string too", () => {
    renderAt("?error=access_denied&error_code=otp_expired");
    expect(screen.getByText("確認連結無法使用")).toBeInTheDocument();
  });

  it("asks to sign in when no session shows up", async () => {
    vi.useFakeTimers();
    auth.getSession.mockResolvedValue({ data: { session: null } });
    renderAt("");
    await act(async () => {
      vi.advanceTimersByTime(SESSION_WAIT_MS);
    });
    expect(screen.getByText("請登入以繼續")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /前往登入/ })).toHaveAttribute("href", "/sign-in");
  });
});
