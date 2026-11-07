import { useEffect, useState } from "react";
import { Outlet, useNavigate, useOutletContext } from "react-router";
import type { User } from "@supabase/supabase-js";
import { supabase } from "@/integrations/supabase/client";

type AuthContext = { user: User };

/** Guards child routes: redirects to /sign-in when there is no signed-in user. */
export function RequireAuth() {
  const navigate = useNavigate();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    let cancelled = false;
    supabase.auth.getUser().then(({ data }) => {
      if (cancelled) return;
      if (!data.user) navigate("/sign-in", { replace: true });
      else setUser(data.user);
    });
    const { data } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === "SIGNED_OUT") navigate("/sign-in", { replace: true });
      else if ((event === "SIGNED_IN" || event === "USER_UPDATED") && session?.user) setUser(session.user);
    });
    return () => {
      cancelled = true;
      data.subscription.unsubscribe();
    };
  }, [navigate]);

  if (!user) return null;
  return <Outlet context={{ user } satisfies AuthContext} />;
}

export function useAuthUser(): User {
  return useOutletContext<AuthContext>().user;
}
