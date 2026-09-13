import { useEffect, useRef, useState } from "react";

import StatusTimeline from "./components/StatusTimeline.jsx";
import ComparisonTable from "./components/ComparisonTable.jsx";
import ActionProof from "./components/ActionProof.jsx";
import PurchaseModal from "./components/PurchaseModal.jsx";
import StarfieldBackground from "./components/StarfieldBackground.jsx";
import CornerCompanion from "./components/CornerCompanion.jsx";

import {
  IconAlert,
  IconArrowUp,
  IconCheck,
  IconChevronRight,
  IconClock,
  IconCopy,
  IconGlobe,
  IconLink,
  IconSearch,
  IconSparkle,
  IconTarget,
  IconZap,
} from "./components/icons.jsx";


const API_BASE =
  import.meta.env.VITE_API_BASE ||
  "http://localhost:8000";


const EXAMPLES = [
  "Find the best wireless headphones under ₹3000 in India",
  "Compare the best 27-inch 4K monitors under $300",
  "Find the best budget laptop for programming under ₹60000",
];


const STATUS_LABELS = {
  started: "Queued",
  planning: "Planning",
  researching: "Researching",
  comparing: "Comparing",
  acting: "Processing",
  delivering: "Finalizing",
  done: "Complete",
  error: "Error",
};


function formatStage(stage) {
  if (!stage) return "Agent event";

  return stage
    .replaceAll("_", " ")
    .replace(/\b\w/g, (char) =>
      char.toUpperCase()
    );
}


function getLatestLog(log = []) {
  if (!log.length) return null;

  return log[log.length - 1];
}


export default function App() {
  const [goal, setGoal] = useState("");

  const name = "Scout Demo";

  const email = "scout-demo@example.com";

  const [session, setSession] =
    useState(null);

  const [running, setRunning] =
    useState(false);

  const [error, setError] =
    useState("");

  const [copied, setCopied] =
    useState(false);

  const [purchaseOption, setPurchaseOption] =
    useState(null);

  const pollRef =
    useRef(null);

  const abortRef =
    useRef(null);


  /* =======================================================
     CLEANUP
     ======================================================= */

  useEffect(() => {
    return () => {
      if (pollRef.current) {
        clearInterval(
          pollRef.current
        );
      }

      if (abortRef.current) {
        abortRef.current.abort();
      }
    };
  }, []);


  /* =======================================================
     STOP POLLING
     ======================================================= */

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(
        pollRef.current
      );

      pollRef.current = null;
    }
  };


  /* =======================================================
     FETCH SESSION
     ======================================================= */

  const fetchSession = async (
    sessionId
  ) => {
    const response = await fetch(
      `${API_BASE}/agent/${sessionId}`
    );

    if (!response.ok) {
      throw new Error(
        `Status request failed (${response.status})`
      );
    }

    return response.json();
  };


  /* =======================================================
     POLLING
     ======================================================= */

  const beginPolling = (
    sessionId
  ) => {
    stopPolling();

    const poll = async () => {
      try {
        const data =
          await fetchSession(
            sessionId
          );

        setSession(data);

        if (
          data.status === "done" ||
          data.status === "error"
        ) {
          stopPolling();
          setRunning(false);
        }
      } catch (err) {
        stopPolling();
        setRunning(false);

        setError(
          err?.message ||
            "Unable to read Scout status."
        );
      }
    };

    poll();

    pollRef.current =
      setInterval(
        poll,
        1200
      );
  };


  /* =======================================================
     START
     ======================================================= */

  const startRun = async () => {
    const cleanGoal =
      goal.trim();

    if (
      !cleanGoal ||
      running
    ) {
      return;
    }

    setError("");
    setSession(null);
    setPurchaseOption(null);
    setCopied(false);
    setRunning(true);

    try {
      if (abortRef.current) {
        abortRef.current.abort();
      }

      const controller =
        new AbortController();

      abortRef.current =
        controller;

      const response =
        await fetch(
          `${API_BASE}/agent/start`,
          {
            method: "POST",
            headers: {
              "Content-Type":
                "application/json",
            },
            body: JSON.stringify({
              goal: cleanGoal,
              name:
                name.trim() ||
                "Scout Demo",
              email:
                email.trim() ||
                "scout-demo@example.com",
              action_mode:
                "research",
              confirm_action:
                false,
            }),
            signal:
              controller.signal,
          }
        );

      if (!response.ok) {
        let detail =
          `Unable to start Scout (${response.status})`;

        try {
          const body =
            await response.json();

          if (
            body?.detail
          ) {
            detail =
              Array.isArray(
                body.detail
              )
                ? body.detail
                    .map(
                      (item) =>
                        item.msg
                    )
                    .join(", ")
                : String(
                    body.detail
                  );
          }
        } catch {
          // Keep the HTTP error.
        }

        throw new Error(
          detail
        );
      }

      const body =
        await response.json();

      if (
        !body?.session_id
      ) {
        throw new Error(
          "Backend did not return a session ID."
        );
      }

      beginPolling(
        body.session_id
      );
    } catch (err) {
      if (
        err?.name ===
        "AbortError"
      ) {
        return;
      }

      setRunning(false);

      setError(
        err?.message ||
          "Scout could not start."
      );
    }
  };


  /* =======================================================
     ENTER TO RUN
     ======================================================= */

  const handleGoalKeyDown = (
    event
  ) => {
    if (
      (event.ctrlKey ||
        event.metaKey) &&
      event.key === "Enter"
    ) {
      event.preventDefault();
      startRun();
    }
  };


  /* =======================================================
     COPY SESSION ID
     ======================================================= */

  const copySessionId = async () => {
    if (
      !session?.id
    ) {
      return;
    }

    try {
      await navigator.clipboard.writeText(
        session.id
      );

      setCopied(true);

      window.setTimeout(
        () => {
          setCopied(false);
        },
        1600
      );
    } catch {
      // Clipboard access can be blocked by the browser.
    }
  };


  /* =======================================================
     DERIVED UI STATE
     ======================================================= */

  const latestLog =
    getLatestLog(
      session?.reasoning_log
    );

  const statusLabel =
    STATUS_LABELS[
      session?.status
    ] ||
    "Ready";

  const anakinSources =
    session?.anakin?.sources ||
    0;

  const anakinEnabled =
    session?.anakin?.enabled ||
    false;

  const anakinSearchCalls =
    session?.anakin?.usage
      ?.search_calls ??
    0;


  return (
    <div className="relative min-h-screen overflow-hidden">

      <StarfieldBackground />
      <CornerCompanion />

      {/* ===================================================
          BACKGROUND
          =================================================== */}

      <div className="pointer-events-none fixed inset-0 z-0">
        <div className="scout-grid absolute inset-0" />

        <div className="scout-orb scout-orb-purple" />

        <div className="scout-orb scout-orb-cyan" />

        <div className="scout-noise" />

        <div className="scan-line top-20" />
      </div>


      {/* ===================================================
          CONTENT
          =================================================== */}

      <main className="relative z-10 mx-auto w-full max-w-6xl px-4 py-5 sm:px-6 sm:py-8 lg:px-8">

        {/* =================================================
            NAV
            ================================================= */}

        <nav className="mb-16 flex items-center justify-between">

          <div className="flex items-center gap-3">

            <div className="saber-mark relative flex h-9 w-9 items-center justify-center rounded-xl border border-white/10 bg-white/[0.045] shadow-lg">
              <div className="absolute inset-0 rounded-xl bg-sky-400/10 blur-xl" />

              <IconSparkle className="relative h-4 w-4 text-sky-200" />
            </div>

            <div>
              <div className="text-sm font-bold tracking-[0.22em] text-white">
                SCOUT
              </div>

              <div className="mt-0.5 text-[9px] uppercase tracking-[0.22em] text-slate-500">
                Autonomous intelligence
              </div>
            </div>

          </div>


          <div className="hidden items-center gap-2 rounded-full border border-emerald-400/10 bg-emerald-400/[0.035] px-3 py-1.5 sm:flex">

            <span className="status-dot" />

            <span className="text-[10px] font-medium uppercase tracking-[0.15em] text-emerald-300/80">
              System online
            </span>

          </div>

        </nav>


        {/* =================================================
            HERO
            ================================================= */}

        <section className="mx-auto max-w-4xl text-center">

          <div className="animate-fade-up">

            <div className="saber-badge mb-5 inline-flex items-center gap-2 rounded-full border border-sky-400/20 bg-sky-400/[0.045] px-3.5 py-1.5">

              <IconZap className="h-3.5 w-3.5 text-sky-200" />

              <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-sky-100/80">
                Reads · Reasons · Acts
              </span>

            </div>


            <h1 className="scout-text-glow text-balance text-5xl font-semibold tracking-[-0.045em] text-white sm:text-6xl lg:text-7xl">
              Research anything.
              <br />

              <span className="saber-title bg-gradient-to-r from-sky-200 via-blue-300 to-red-300 bg-clip-text text-transparent">
                Scout finds the answer.
              </span>
            </h1>


            <p className="mx-auto mt-6 max-w-2xl text-sm leading-7 text-slate-400 sm:text-base">
              An autonomous research agent that searches
              the live web, gathers evidence, compares
              real options, and explains its decision.
            </p>

          </div>


          {/* =================================================
              COMMAND CENTER
              ================================================= */}

          <div
            className="glass scout-glow relative mt-10 overflow-hidden rounded-3xl p-1 animate-scale"
            style={{
              animationDelay:
                "100ms",
            }}
          >

            <div className="relative rounded-[22px] bg-[#080b13]/90 p-5 text-left sm:p-7">

              {/* top accent */}

              <div className="absolute left-10 right-10 top-0 h-px bg-gradient-to-r from-transparent via-sky-300/70 to-red-400/50 to-transparent" />


              <div className="mb-5 flex items-center justify-between">

                <div className="flex items-center gap-2">

                  <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-white/8 bg-white/[0.04]">
                    <IconTarget className="h-3.5 w-3.5 text-violet-300" />
                  </div>

                  <div>
                    <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">
                      Mission
                    </div>

                    <div className="text-xs text-slate-600">
                      Describe the outcome you want
                    </div>
                  </div>

                </div>


                <div className="hidden items-center gap-1.5 text-[9px] uppercase tracking-wider text-slate-600 sm:flex">
                  <span>Ctrl</span>
                  <span>+</span>
                  <span>Enter</span>
                </div>

              </div>


              <div className="relative">

                <textarea
                  value={goal}
                  onChange={(event) =>
                    setGoal(
                      event.target.value
                    )
                  }
                  onKeyDown={
                    handleGoalKeyDown
                  }
                  disabled={running}
                  placeholder="e.g. Find the best wireless headphones under ₹3000 in India, prioritizing sound quality and battery life."
                  className="scout-input min-h-[150px] resize-none rounded-2xl px-4 py-4 text-sm leading-7 sm:min-h-[170px] sm:px-5 sm:py-5"
                />

                <div className="pointer-events-none absolute bottom-4 right-4 hidden rounded-md border border-white/5 bg-black/20 px-2 py-1 text-[9px] text-slate-600 sm:block">
                  {goal.length}/4000
                </div>

              </div>


              {/* Examples */}

              <div className="mt-4 flex flex-wrap gap-2">

                {EXAMPLES.map(
                  (example) => (
                    <button
                      key={example}
                      type="button"
                      disabled={running}
                      onClick={() =>
                        setGoal(
                          example
                        )
                      }
                      className="group inline-flex items-center gap-1.5 rounded-full border border-white/[0.065] bg-white/[0.025] px-3 py-1.5 text-[10px] text-slate-500 transition-all hover:border-violet-400/20 hover:bg-violet-400/[0.05] hover:text-slate-300 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <IconChevronRight className="h-3 w-3 text-slate-700 transition-transform group-hover:translate-x-0.5 group-hover:text-violet-300" />

                      {example}
                    </button>
                  )
                )}

              </div>

              {/* Run */}

              <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">

                <div className="flex items-center gap-2 text-[10px] text-slate-600">

                  <IconGlobe className="h-3.5 w-3.5" />

                  <span>
                    Live web research
                  </span>

                  <span className="text-slate-800">
                    •
                  </span>

                  <span>
                    Multi-model reasoning
                  </span>

                </div>


                <button
                  type="button"
                  onClick={
                    startRun
                  }
                  disabled={
                    running ||
                    !goal.trim()
                  }
                  className="scout-button inline-flex min-h-11 items-center justify-center gap-2.5 rounded-xl px-5 text-xs font-semibold"
                >

                  {running ? (
                    <>
                      <span className="animate-spin-scout h-3.5 w-3.5 rounded-full border-2 border-white/25 border-t-white" />

                      Scout is working
                    </>
                  ) : (
                    <>
                      <IconSparkle className="h-3.5 w-3.5" />

                      Run Scout

                      <IconArrowUp className="h-3.5 w-3.5 rotate-45 opacity-60" />
                    </>
                  )}

                </button>

              </div>

            </div>

          </div>

        </section>


        {/* =================================================
            ERROR
            ================================================= */}

        {(error ||
          session?.error) && (
          <section className="mx-auto mt-7 max-w-4xl animate-fade-up">

            <div className="flex gap-3 rounded-2xl border border-red-400/15 bg-red-400/[0.045] p-4">

              <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-red-400/10">
                <IconAlert className="h-4 w-4 text-red-300" />
              </div>

              <div>
                <div className="text-xs font-semibold text-red-200">
                  Scout encountered an error
                </div>

                <div className="mt-1 text-[11px] leading-5 text-red-200/60">
                  {error ||
                    session?.error}
                </div>
              </div>

            </div>

          </section>
        )}


        {/* =================================================
            LIVE RUN
            ================================================= */}

        {session && (
          <section className="mx-auto mt-10 max-w-5xl animate-fade-up">

            {/* Run header */}

            <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">

              <div>

                <div className="mb-2 flex items-center gap-2">

                  <span className="status-dot" />

                  <span className="text-[9px] font-semibold uppercase tracking-[0.2em] text-emerald-300/70">
                    Live agent run
                  </span>

                </div>

                <h2 className="text-xl font-semibold tracking-tight text-white">
                  Scout is{" "}
                  <span className="text-violet-300">
                    {statusLabel.toLowerCase()}
                  </span>
                </h2>

              </div>


              <div className="flex items-center gap-2">

                {session.id && (
                  <button
                    type="button"
                    onClick={
                      copySessionId
                    }
                    className="inline-flex items-center gap-1.5 rounded-lg border border-white/7 bg-white/[0.025] px-2.5 py-1.5 text-[9px] text-slate-600 transition hover:border-white/12 hover:text-slate-400"
                  >
                    <IconCopy className="h-3 w-3" />

                    {copied
                      ? "Copied"
                      : "Session ID"}
                  </button>
                )}

                <div className="rounded-lg border border-white/7 bg-white/[0.025] px-2.5 py-1.5 text-[9px] text-slate-600">
                  {session.id?.slice(
                    0,
                    8
                  )}
                </div>

              </div>

            </div>


            {/* Agent timeline */}

            <StatusTimeline
              status={
                session.status
              }
              reasoningLog={
                session.reasoning_log ||
                []
              }
              timings={
                session.timings ||
                {}
              }
            />


            {/* =================================================
                AGENT TELEMETRY
                ================================================= */}

            <div className="mt-4 grid gap-3 sm:grid-cols-3">

              <TelemetryCard
                icon={
                  <IconSearch className="h-3.5 w-3.5" />
                }
                label="Web evidence"
                value={
                  session.reasoning_log
                    ?.filter(
                      (entry) =>
                        entry.stage?.includes(
                          "research"
                        )
                    )
                    .length ||
                  0
                }
                suffix="events"
              />


              <TelemetryCard
                icon={
                  <IconZap className="h-3.5 w-3.5" />
                }
                label="Anakin"
                value={
                  anakinSources
                }
                suffix="live sources"
                accent={
                  anakinEnabled
                }
              />


              <TelemetryCard
                icon={
                  <IconClock className="h-3.5 w-3.5" />
                }
                label="Latest event"
                value={
                  latestLog
                    ? `${latestLog.elapsed_s}s`
                    : "—"
                }
                suffix={
                  latestLog
                    ? formatStage(
                        latestLog.stage
                      )
                    : "waiting"
                }
              />

            </div>


            {/* =================================================
                CURRENT AGENT EVENT
                ================================================= */}

            {latestLog && (
              <div className="mt-4 rounded-2xl border border-white/7 bg-white/[0.018] p-4">

                <div className="flex items-start gap-3">

                  <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-violet-400/10 bg-violet-400/[0.05]">
                    <IconZap className="h-3.5 w-3.5 text-violet-300" />
                  </div>

                  <div className="min-w-0">

                    <div className="flex flex-wrap items-center gap-2">

                      <span className="text-xs font-semibold text-slate-200">
                        {formatStage(
                          latestLog.stage
                        )}
                      </span>

                      <span className="rounded-full border border-white/6 bg-white/[0.025] px-2 py-0.5 text-[9px] text-slate-600">
                        {latestLog.elapsed_s}s
                      </span>

                    </div>

                    {latestLog.message && (
                      <p className="mt-1.5 text-[11px] leading-5 text-slate-500">
                        {
                          latestLog.message
                        }
                      </p>
                    )}

                    {latestLog.findings && (
                      <p className="mt-1.5 text-[11px] leading-5 text-slate-400">
                        {
                          latestLog.findings
                        }
                      </p>
                    )}

                  </div>

                </div>

              </div>
            )}


            {/* =================================================
                FINAL RESULTS
                ================================================= */}

            {session.status ===
              "done" && (
              <div className="mt-8 space-y-5">

                {/* Result heading */}

                <div className="flex items-center gap-3">

                  <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-400/10">
                    <IconCheck className="h-4 w-4 text-emerald-300" />
                  </div>

                  <div>
                    <div className="text-[9px] font-semibold uppercase tracking-[0.18em] text-emerald-300/70">
                      Mission complete
                    </div>

                    <div className="mt-0.5 text-lg font-semibold text-white">
                      Scout has reached a decision
                    </div>
                  </div>

                </div>


                {/* Comparison */}

                <ComparisonTable
                  decision={
                    session.decision
                  }
                  onBuyNow={setPurchaseOption}
                />


                {/* Action/report */}

                <ActionProof
                  actionResult={
                    session.action_result
                  }
                  reportUrl={
                    session.report_url
                  }
                  discordSent={
                    session.discord_sent
                  }
                />

              </div>
            )}

          </section>
        )}

        {purchaseOption && session && (
          <PurchaseModal
            option={purchaseOption}
            sessionId={session.id}
            defaultEmail={session.contact_email || email}
            apiBase={API_BASE}
            onClose={() => setPurchaseOption(null)}
          />
        )}


        {/* =================================================
            EMPTY STATE
            ================================================= */}

        {!session &&
          !running && (
            <section className="mx-auto mt-16 max-w-4xl pb-10">

              <div className="grid gap-3 sm:grid-cols-3">

                <FeatureCard
                  icon={
                    <IconGlobe className="h-4 w-4" />
                  }
                  title="Live web"
                  text="Scout gathers fresh evidence instead of relying only on model memory."
                />

                <FeatureCard
                  icon={
                    <IconSearch className="h-4 w-4" />
                  }
                  title="Multi-step research"
                  text="Queries are planned, searched, reranked and refined before comparison."
                />

                <FeatureCard
                  icon={
                    <IconTarget className="h-4 w-4" />
                  }
                  title="Evidence-based"
                  text="Multiple models independently evaluate the same collected evidence."
                />

              </div>

            </section>
          )}


        {/* =================================================
            FOOTER
            ================================================= */}

        <footer className="mt-16 flex flex-col items-center justify-between gap-3 border-t border-white/5 py-6 text-[9px] uppercase tracking-[0.14em] text-slate-700 sm:flex-row">

          <div>
            Scout · Autonomous research agent
          </div>

          <div className="flex items-center gap-3">
            <span>
              Anakin
            </span>

            <span className="text-slate-800">
              /
            </span>

            <span>
              Serper
            </span>

            <span className="text-slate-800">
              /
            </span>

            <span>
              Jina
            </span>

            <span className="text-slate-800">
              /
            </span>

            <span>
              Multi-model
            </span>
          </div>

        </footer>

      </main>
    </div>
  );
}


/* ===========================================================
   TELEMETRY CARD
   =========================================================== */

function TelemetryCard({
  icon,
  label,
  value,
  suffix,
  accent = false,
}) {
  return (
    <div className="glass-soft rounded-2xl px-4 py-3">

      <div className="flex items-center justify-between">

        <div className="flex items-center gap-2 text-slate-600">

          <span
            className={
              accent
                ? "text-cyan-300"
                : "text-violet-300/70"
            }
          >
            {icon}
          </span>

          <span className="text-[9px] font-semibold uppercase tracking-[0.15em]">
            {label}
          </span>

        </div>

        {accent && (
          <span className="h-1.5 w-1.5 rounded-full bg-cyan-300 shadow-[0_0_10px_rgba(103,232,249,.8)]" />
        )}

      </div>

      <div className="mt-2 flex items-baseline gap-2">

        <span className="text-lg font-semibold tracking-tight text-slate-200">
          {value}
        </span>

        <span className="text-[9px] text-slate-700">
          {suffix}
        </span>

      </div>

    </div>
  );
}


/* ===========================================================
   FEATURE CARD
   =========================================================== */

function FeatureCard({
  icon,
  title,
  text,
}) {
  return (
    <div className="glass-soft group rounded-2xl p-4 transition-all duration-300 hover:-translate-y-0.5 hover:border-white/10">

      <div className="mb-3 flex h-8 w-8 items-center justify-center rounded-lg border border-white/7 bg-white/[0.035] text-violet-300 transition group-hover:bg-violet-400/[0.08]">
        {icon}
      </div>

      <div className="text-xs font-semibold text-slate-300">
        {title}
      </div>

      <p className="mt-1.5 text-[10px] leading-5 text-slate-600">
        {text}
      </p>

    </div>
  );
}
