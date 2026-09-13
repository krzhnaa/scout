import { IconCheck, IconExternal, IconSparkle, IconTrophy } from "./icons.jsx";

const labels = { product: "Buy Now", ticket: "Get Ticket", flight: "Book Flight", hotel: "Book Hotel", restaurant: "Reserve", reservation: "Reserve", service: "Book Service" };
const validUrl = (value) => /^https?:\/\/.+/i.test(value || "");
const canCheckout = (option) => option.purchasable === true && option.price_verified === true && Number(option.price) > 0 && validUrl(option.purchase_url);

function formatPrice(option) {
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: option.currency || "INR", maximumFractionDigits: 2 }).format(Number(option.price)); }
  catch { return `${option.currency || ""} ${option.price}`; }
}

export default function ComparisonTable({ decision, onBuyNow }) {
  if (!decision?.options?.length) return null;
  const options = [...decision.options].sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
  const degraded = decision.degraded === true;

  return <section className="glass holo-frame overflow-hidden animate-fade-up">
    <header className="border-b border-white/5 px-5 py-5 sm:px-6"><div className="flex items-start justify-between gap-3"><div className="flex gap-3"><div className="flex h-9 w-9 items-center justify-center rounded-xl border border-violet-400/15 bg-violet-400/[.06]"><IconSparkle className="h-4 w-4 text-violet-300" /></div><div><div className="text-[9px] font-semibold uppercase tracking-[.18em] text-violet-300/60">Decision engine</div><h3 className="mt-1 text-base font-semibold text-white">Scout&apos;s top {options.length}</h3><p className="mt-1 text-[10px] text-slate-600">Evidence-backed options — shown only when actually found.</p></div></div>{degraded
      ? <div className="flex items-center gap-1.5 rounded-full border border-amber-400/20 bg-amber-400/[.06] px-2.5 py-1.5"><span className="text-[9px] text-amber-300/80">Unverified</span></div>
      : <div className="flex items-center gap-1.5 rounded-full border border-emerald-400/10 bg-emerald-400/[.035] px-2.5 py-1.5"><IconCheck className="h-3 w-3 text-emerald-300" /><span className="text-[9px] text-emerald-300/70">Validated</span></div>}</div>
    {degraded && <p className="mt-3 rounded-lg border border-amber-400/15 bg-amber-400/[.04] px-3 py-2 text-[10px] leading-4 text-amber-200/80">
      {decision.degraded_reason || "Some ranking models were unavailable, so this list is less reliable than usual."} Try running the search again in a moment.
    </p>}</header>
    <div className="divide-y divide-white/[.035]">{options.map((option, index) => {
      const winner = index === 0 && option.name === decision.winner;
      const actionable = canCheckout(option);
      const hasVerifiedPrice = option.price_verified && Number(option.price) > 0;
      // A verified price can always enter Scout's Razorpay test checkout.
      // The backend retains the source page only as transaction evidence.
      const canStartTestCheckout = actionable || (hasVerifiedPrice && validUrl(option.source_url));
      const actionLabel = "ACQUIRE";
      return <article key={`${option.name}-${index}`} className={`px-5 py-5 sm:px-6 ${winner ? "bg-gradient-to-r from-violet-500/[.08] via-violet-400/[.025] to-transparent" : "transition hover:bg-white/[.015]"}`}><div className="flex gap-4"><div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border text-[10px] font-bold ${winner ? "border-amber-300/25 bg-amber-300/[.08] text-amber-200" : "border-white/[.055] bg-white/[.02] text-slate-500"}`}>{String(index + 1).padStart(2, "0")}</div><div className="min-w-0 flex-1"><div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start"><div><div className="flex flex-wrap items-center gap-2"><h4 className="text-base font-semibold text-slate-200">{option.name}</h4>{winner && <span className="inline-flex items-center gap-1 rounded-full border border-amber-300/15 bg-amber-300/[.07] px-2 py-.5 text-[8px] font-bold uppercase tracking-[.13em] text-amber-200"><IconTrophy className="h-2.5 w-2.5" />Winner</span>}</div>{option.provider && <p className="mt-1 text-[10px] text-slate-500">Provider: {option.provider}</p>}</div><div className="flex shrink-0 items-center gap-3"><span className="text-lg font-semibold text-slate-300">{Math.min(10, Math.max(0, Number(option.score || 0))).toFixed(1)}<span className="ml-1 text-[9px] text-slate-600">/10</span></span>{canStartTestCheckout && <div className="flex items-center gap-2"><span className="text-sm font-semibold text-emerald-200">{formatPrice(option)}</span><button type="button" onClick={() => onBuyNow?.(option)} className="scout-button rounded-lg px-3 py-2 text-[10px] font-semibold">{actionLabel}</button></div>}</div></div>{!canStartTestCheckout && hasVerifiedPrice && <p className="mt-2 text-xs font-semibold text-emerald-200">{formatPrice(option)} <span className="ml-2 font-normal text-slate-500">Purchase link not verified</span></p>}{option.transaction_details?.length > 0 && <p className="mt-2 text-[10px] text-cyan-100/55">{option.transaction_details.map((item) => `${item.label}: ${item.value}`).join(" · ")}</p>}{option.justification && <p className="mt-2 max-w-3xl text-[10px] leading-5 text-slate-500"><span className="font-medium text-slate-400">Why Scout chose it: </span>{option.justification}</p>}<div className="mt-3 flex items-center gap-3">{validUrl(option.source_url) && <a href={option.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[9px] font-medium text-violet-300/65 hover:text-violet-200"><IconExternal className="h-3 w-3" />View source</a>}{canStartTestCheckout && <span className="text-[8px] text-slate-700">Razorpay test checkout</span>}</div></div></div></article>;
    })}</div>
  </section>;
}
