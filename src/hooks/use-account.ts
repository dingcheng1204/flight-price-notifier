import { useQuery } from "@tanstack/react-query";
import { getAccount } from "@/lib/flight-api";

/** The signed-in user's paid subscription (GET /account). */
export function useAccount(email: string) {
  return useQuery({ queryKey: ["account", email], queryFn: getAccount, enabled: Boolean(email) });
}
