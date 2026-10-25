"use client";

import { ReactNode, useEffect } from "react";
import { useRouter } from "next/navigation";
import { ErrorState, LoadingState } from "@/components/states";
import { useAuth } from "@/lib/auth";
import { NETWORK_MESSAGE } from "@/lib/errors";

export default function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading, unreachable, retry } = useAuth();
  const router = useRouter();

  useEffect(() => {
    // Send to the login page only when we know there is no valid login: not while the server
    // cannot be reached, which says nothing about whether the login is still good.
    if (!loading && !user && !unreachable) router.push("/login");
  }, [loading, user, unreachable, router]);

  if (loading) return <LoadingState label="Loading..." />;
  if (!user && unreachable) {
    return (
      <ErrorState
        failure={{ message: NETWORK_MESSAGE, sessionExpired: false, notFound: false }}
        onRetry={retry}
      />
    );
  }
  if (!user) return null;
  return <>{children}</>;
}
