import { useState } from "react";

const labels = { ticket: "Get Ticket", flight: "Book Flight", hotel: "Book Hotel", booking: "Book Now", reservation: "Reserve", service: "Book Now" };
const price = (option) => { try { return new Intl.NumberFormat(undefined, { style: "currency", currency: option.currency || "INR" }).format(Number(option.price)); } catch { return `${option.currency} ${option.price}`; } };

async function errorText(response) { try { return (await response.json()).detail || "The request could not be completed."; } catch { return "The request could not be completed."; } }

export default function PurchaseModal({ option, sessionId, apiBase, onClose }) {
  const [stage, setStage] = useState("checkout");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(null);
  const [email, setEmail] = useState("");

  if (!option) return null;
  const action = labels[option.purchase_type] || "Buy Now";
  const startCheckout = async () => {
    setBusy(true); setError("");
    try {
      const response = await fetch(`${apiBase}/payments/create-order`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sessionId, option_name: option.name }) });
      if (!response.ok) throw new Error(await errorText(response));
      const order = await response.json();
      if (!window.Razorpay) throw new Error("Razorpay Checkout did not load. Please disable blockers and try again.");
      const checkout = new window.Razorpay({
        key: order.key_id, amount: order.amount, currency: order.currency, name: "Scout",
        description: `Demo Checkout — ${order.option_name}`, order_id: order.order_id, theme: { color: "#715cff" },
        // Explicitly request every payment method Razorpay supports for
        // this currency, with Net Banking surfaced first. Net Banking and
        // UPI are India-domestic rails — Razorpay only offers them on INR
        // orders, which is why the backend always settles in INR.
        method: { netbanking: true, card: true, upi: true, wallet: true, emi: false, paylater: false },
        config: {
          display: {
            blocks: {
              banks: { name: "Pay via Net Banking", instruments: [{ method: "netbanking" }] },
              other: { name: "Other payment methods", instruments: [{ method: "card" }, { method: "upi" }, { method: "wallet" }] },
            },
            sequence: ["block.banks", "block.other"],
            preferences: { show_default_blocks: false },
          },
        },
        modal: { ondismiss: () => setBusy(false) },
        handler: async (result) => {
        try {
          const verify = await fetch(`${apiBase}/payments/verify`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sessionId, option_name: option.name, razorpay_order_id: result.razorpay_order_id, razorpay_payment_id: result.razorpay_payment_id, razorpay_signature: result.razorpay_signature }) });
          if (!verify.ok) throw new Error(await errorText(verify));
          setSuccess(await verify.json()); setStage("success");
        } catch (err) { setError(err.message || "Payment was not verified by Scout."); } finally { setBusy(false); }
      } });
      checkout.on("payment.failed", (event) => { setBusy(false); setError(event?.error?.description || "Razorpay could not complete the test checkout."); });
      checkout.open();
    } catch (err) { setBusy(false); setError(err.message || "Could not start test checkout."); }
  };
  const sendInvoice = async () => {
    setBusy(true); setError("");
    try {
      const response = await fetch(`${apiBase}/payments/send-invoice`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sessionId, razorpay_order_id: success.order_id, email: email.trim() }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body?.detail || "Invoice could not be sent.");
      if (body.invoice_status === "failed") throw new Error(body.message);
      setSuccess(body); setStage("sent");
    } catch (err) { setError(err.message || "Invoice could not be sent. You can retry without being charged again."); } finally { setBusy(false); }
  };
  const details = success?.transaction_details || option.transaction_details || [];
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#02030a]/80 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="Scout demo checkout"><div className="glass w-full max-w-lg overflow-hidden rounded-2xl animate-scale"><div className="border-b border-white/5 px-6 py-5"><div className="text-[9px] font-semibold uppercase tracking-[.18em] text-violet-300/70">{stage === "checkout" ? "Demo Checkout — Razorpay Test Mode" : "Scout transaction confirmation"}</div><h2 className="mt-1 text-lg font-semibold text-white">{stage === "checkout" ? option.name : "✓ Payment Successful"}</h2></div><div className="space-y-4 px-6 py-5 text-sm text-slate-300">
    {stage === "checkout" && <><div className="flex items-center justify-between"><span className="text-slate-500">Verified researched price</span><strong className="text-lg text-emerald-200">{price(option)}</strong></div>{option.currency && option.currency.toUpperCase() !== "INR" && <p className="text-xs text-slate-500">Charged as the INR equivalent in Razorpay Test Mode (this account settles in INR only) — the exact converted amount is shown on the next screen.</p>}{option.provider && <p className="text-xs text-slate-500">Provider: {option.provider}</p>}<p className="rounded-lg border border-amber-300/15 bg-amber-300/[.05] p-3 text-xs leading-5 text-amber-100/80">This simulates the researched transaction. The external retailer, airline, hotel, ticket provider, or service provider is not charged.</p>{error && <p className="rounded-lg border border-red-400/20 bg-red-400/10 p-3 text-xs text-red-200">{error}</p>}<div className="flex justify-end gap-3"><button className="rounded-lg border border-white/10 px-3 py-2 text-xs" disabled={busy} onClick={onClose}>Cancel</button><button className="scout-button rounded-lg px-3 py-2 text-xs font-semibold" disabled={busy} onClick={startCheckout}>{busy ? "Starting checkout…" : action}</button></div></>}
    {stage !== "checkout" && <><div className="grid grid-cols-2 gap-3 rounded-xl border border-white/[.06] bg-white/[.02] p-4 text-xs"><div><span className="text-slate-600">Transaction type</span><br />{(success.purchase_type || option.purchase_type || "transaction").replace("_", " ")}</div><div><span className="text-slate-600">Amount charged</span><br />{price({ ...option, ...success })}</div>{success.researched_currency && success.researched_currency !== success.currency && <div className="col-span-2"><span className="text-slate-600">Researched price</span><br />{price({ price: success.researched_price, currency: success.researched_currency })}{success.fx_rate ? ` (1 ${success.researched_currency} ≈ ${success.fx_rate} INR)` : ""}</div>}{(success.provider || option.provider) && <div className="col-span-2"><span className="text-slate-600">Provider</span><br />{success.provider || option.provider}</div>}<div><span className="text-slate-600">Payment ID</span><br /><span className="mono text-[10px]">{success.payment_id}</span></div><div><span className="text-slate-600">Order ID</span><br /><span className="mono text-[10px]">{success.order_id}</span></div></div>{details.length > 0 && <div className="text-xs text-slate-400">{details.map((detail) => <p key={detail.label}><span className="text-slate-600">{detail.label}: </span>{detail.value}</p>)}</div>}<p className="rounded-lg border border-amber-300/15 bg-amber-300/[.05] p-3 text-xs leading-5 text-amber-100/80">Razorpay Test Mode verified this simulation only. Scout has not purchased, booked, or reserved anything with the external provider.</p>
      {stage === "success" && <div className="rounded-xl border border-violet-400/15 bg-violet-400/[.045] p-4"><p className="font-medium text-white">Would you like the invoice sent to your email?</p><p className="mt-1 text-xs text-slate-500">A professional Scout transaction summary will be sent only if you request it.</p><div className="mt-3 flex flex-wrap gap-2"><button className="scout-button rounded-lg px-3 py-2 text-xs font-semibold" onClick={() => setStage("invoice")}>Yes, Email My Invoice</button><button className="rounded-lg border border-white/10 px-3 py-2 text-xs" onClick={() => setStage("continue")}>No, Continue</button></div></div>}
      {stage === "invoice" && <div className="rounded-xl border border-violet-400/15 bg-violet-400/[.045] p-4"><label className="block text-xs text-slate-400">Email address<input className="scout-input mt-2 rounded-lg px-3 py-2 text-sm" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" /></label>{error && <p className="mt-2 text-xs text-red-200">{error}</p>}<div className="mt-3 flex justify-end gap-2"><button className="rounded-lg border border-white/10 px-3 py-2 text-xs" disabled={busy} onClick={() => setStage("success")}>Back</button><button className="scout-button rounded-lg px-3 py-2 text-xs font-semibold" disabled={busy} onClick={sendInvoice}>{busy ? "Sending…" : "Send Invoice"}</button></div></div>}
      {stage === "sent" && <p className="rounded-lg border border-emerald-400/20 bg-emerald-400/[.08] p-3 text-xs text-emerald-100">Invoice sent to {success.email}.</p>}{stage === "continue" && <p className="text-xs text-slate-500">Payment verified. No invoice email was requested.</p>}<div className="flex gap-3"><button className="rounded-lg border border-white/10 px-3 py-2 text-xs" onClick={onClose}>Close</button></div></>}
  </div></div></div>;
}