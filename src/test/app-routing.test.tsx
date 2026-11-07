import { matchRoutes } from "react-router";
import { describe, expect, it, vi } from "vitest";

// Route modules import the Supabase client; stub it so matching needs no env/network.
vi.mock("@/integrations/supabase/client", () => ({ supabase: {} }));

import { routes } from "@/routes";

const leafPath = (url: string) => matchRoutes(routes, url)?.at(-1)?.route.path;

describe("App routing", () => {
  it.each(["/", "/app", "/sign-in", "/sign-up", "/reset-password", "/auth"])(
    "matches a page for %s instead of the not-found route",
    (url) => {
      expect(leafPath(url)).toBe(url);
    },
  );

  it("falls back to the not-found route for unknown paths", () => {
    expect(leafPath("/does-not-exist")).toBe("*");
  });
});
