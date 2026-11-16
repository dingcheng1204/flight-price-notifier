import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BellRing, CreditCard, Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { useAccount } from "@/hooks/use-account";
import { cancelPlan, startCheckout, type Account } from "@/lib/flight-api";

const btn =
  "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-60";

/** The user's paid subscription: one per user, covers every route they watch. */
export function PlanCard({ email }: { email: string }) {
  const qc = useQueryClient();
  const account = useAccount(email);
  const [params, setParams] = useSearchParams();
  const purchase = params.get("purchase");
  const polls = useRef(0);

  // Back from the ECPay cashier: the S2S callback may land a few seconds after the browser.
  useEffect(() => {
    if (!purchase) return;
    if (purchase === "failed") {
      toast.error("付款未完成，可以再試一次。Payment was not completed.");
      setParams({}, { replace: true });
      return;
    }
    if (account.data?.served) {
      toast.success("訂閱已開通！確認信已寄到你的信箱。Subscription active.");
      setParams({}, { replace: true });
      return;
    }
    if (polls.current >= 15) {
      toast.message("付款處理中，稍後重新整理即可看到狀態。Payment is still processing.");
      setParams({}, { replace: true });
      return;
    }
    const t = setTimeout(() => {
      polls.current += 1;
      qc.invalidateQueries({ queryKey: ["account", email] });
    }, 2000);
    return () => clearTimeout(t);
  }, [purchase, account.data, account.dataUpdatedAt, email, qc, setParams]);

  const checkout = useMutation({
    mutationFn: startCheckout,
    onError: (e: Error) => toast.error(e.message),
  });
  const cancel = useMutation({
    mutationFn: cancelPlan,
    onSuccess: (a) => {
      qc.setQueryData(["account", email], a);
      toast.success(`已取消訂閱，${a.current_period_end_date ?? "本期結束"} 前仍會收到通知。`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  if (account.isLoading) {
    return (
      <div className="rounded-xl border bg-card p-5 text-sm text-muted-foreground">
        載入訂閱狀態… Loading…
      </div>
    );
  }
  if (account.isError || !account.data) {
    return (
      <div className="rounded-xl border border-destructive/40 bg-destructive/10 p-5 text-sm">
        無法載入訂閱狀態 Couldn't load your plan: {(account.error as Error | null)?.message}
      </div>
    );
  }

  const a = account.data;
  const price = `NT$${a.amount.toLocaleString()} / 月`;
  const waiting = Boolean(purchase) && purchase !== "failed" && !a.served;

  return (
    <section className="space-y-3 rounded-xl border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <BellRing className="h-5 w-5 text-primary-glow" /> 我的訂閱 My plan
        </h2>
        <StatusBadge a={a} waiting={waiting} />
      </div>
      <PlanText a={a} price={price} waiting={waiting} />
      <div className="flex flex-wrap items-center gap-3">
        {!a.served && (
          <button
            onClick={() => checkout.mutate()}
            disabled={checkout.isPending || waiting}
            className={`${btn} bg-primary text-primary-foreground hover:opacity-90`}
          >
            {checkout.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <CreditCard className="h-4 w-4" />
            )}
            {a.status === "pending_payment" ? "完成付款" : "訂閱"} {price}
          </button>
        )}
        {a.status === "active" && (
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <button disabled={cancel.isPending} className={`${btn} border hover:bg-accent`}>
                取消訂閱
              </button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>確定要取消訂閱嗎？</AlertDialogTitle>
                <AlertDialogDescription>
                  之後不會再扣款。已付費的這一期仍然有效，{a.current_period_end_date ?? "本期結束"}{" "}
                  之前你還是會收到所有航線的降價通知。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>先不要</AlertDialogCancel>
                <AlertDialogAction onClick={() => cancel.mutate()}>確定取消</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </div>
      {a.env === "stage" && !a.served && (
        <p className="text-xs text-muted-foreground">
          測試環境：請用測試卡 4311-9522-2222-2222，有效期限 12/30，CVV 222，簡訊驗證碼
          1234，不會真的扣款。
        </p>
      )}
    </section>
  );
}

function StatusBadge({ a, waiting }: { a: Account; waiting: boolean }) {
  const [label, cls] = waiting
    ? ["確認付款中", "bg-muted text-foreground"]
    : a.status === "active"
      ? ["訂閱中", "bg-primary text-primary-foreground"]
      : a.status === "cancelled" && a.served
        ? ["已取消（期限內仍有效）", "bg-muted text-foreground"]
        : a.status === "pending_payment"
          ? ["未完成付款", "bg-destructive/15 text-destructive"]
          : a.status === "expired" || a.status === "cancelled"
            ? ["已結束", "bg-destructive/15 text-destructive"]
            : ["未訂閱", "bg-muted text-foreground"];
  return <span className={`rounded-full px-3 py-1 text-xs font-medium ${cls}`}>{label}</span>;
}

function PlanText({ a, price, waiting }: { a: Account; price: string; waiting: boolean }) {
  const p = "text-sm text-muted-foreground";
  if (waiting) return <p className={p}>正在等綠界通知付款結果，通常幾秒鐘內完成…</p>;
  if (a.status === "active")
    return (
      <p className={p}>
        你所有航線的降價通知都會寄出。本期有效至 {a.current_period_end_date}，之後每月自動續訂{" "}
        {price}。
      </p>
    );
  if (a.status === "cancelled" && a.served)
    return (
      <p className={p}>
        已取消，不會再扣款。{a.current_period_end_date} 之前仍會寄出通知，到期後可以重新訂閱。
      </p>
    );
  return (
    <p className={p}>
      訂閱後，你設定的所有航線只要票價達到目標價，就會寄降價通知給你。每月 {price}
      ，以信用卡定期定額扣款，隨時可以取消。
      {a.status === "pending_payment" && " 你上次的付款還沒完成。"}
      {(a.status === "expired" || a.status === "cancelled") &&
        " 你的訂閱已結束，目前不會寄送通知。"}
    </p>
  );
}
