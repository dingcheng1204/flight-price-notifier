import { Navigate, type RouteObject } from "react-router";

import { RootLayout, NotFound, RouteError } from "@/components/RootLayout";
import { RequireAuth } from "@/components/RequireAuth";
import Index from "@/pages/Index";
import AuthPage from "@/pages/Auth";
import ResetPassword from "@/pages/ResetPassword";
import AppPage from "@/pages/AppPage";

export const routes: RouteObject[] = [
  {
    element: <RootLayout />,
    errorElement: <RouteError />,
    children: [
      { path: "/", element: <Index /> },
      // `key` forces a fresh form when switching between /sign-in and /sign-up.
      { path: "/sign-in", element: <AuthPage key="signin" initialMode="signin" /> },
      { path: "/sign-up", element: <AuthPage key="signup" initialMode="signup" /> },
      // Old URL from the TanStack version — keep links/bookmarks working.
      { path: "/auth", element: <Navigate to="/sign-in" replace /> },
      { path: "/reset-password", element: <ResetPassword /> },
      {
        element: <RequireAuth />,
        children: [{ path: "/app", element: <AppPage /> }],
      },
      { path: "*", element: <NotFound /> },
    ],
  },
];
