import {
  IconCheck,
  IconClock,
  IconSearch,
  IconSparkle,
  IconTarget,
  IconZap,
} from "./icons.jsx";


const STAGES = [
  {
    key: "started",
    label: "Queued",
    icon: IconClock,
  },
  {
    key: "planning",
    label: "Planning",
    icon: IconTarget,
  },
  {
    key: "researching",
    label: "Research",
    icon: IconSearch,
  },
  {
    key: "comparing",
    label: "Compare",
    icon: IconSparkle,
  },
  {
    key: "acting",
    label: "Act",
    icon: IconZap,
  },
  {
    key: "delivering",
    label: "Deliver",
    icon: IconCheck,
  },
  {
    key: "done",
    label: "Done",
    icon: IconCheck,
  },
];


function stageIndex(status) {
  const index =
    STAGES.findIndex(
      (stage) =>
        stage.key === status
    );

  return index < 0 ? 0 : index;
}


function prettyStage(stage) {
  if (!stage) {
    return "Agent event";
  }

  return stage
    .replaceAll("_", " ")
    .replace(/\b\w/g, (char) =>
      char.toUpperCase()
    );
}


export default function StatusTimeline({
  status,
  reasoningLog = [],
  timings = {},
}) {
  const currentIndex =
    stageIndex(status);

  const isError =
    status === "error";

  const progress =
    status === "done"
      ? 100
      : isError
      ? 100
      : (
          currentIndex /
          (STAGES.length - 1)
        ) * 100;


  return (
    <div className={`glass holo-frame overflow-hidden ${isError ? "empire-alert" : ""}`}>

      {/* =====================================================
          HEADER
          ===================================================== */}

      <div className="flex items-center justify-between border-b border-white/5 px-5 py-4">

        <div>
          <div className="text-[9px] font-semibold uppercase tracking-[0.18em] text-slate-600">
            Agent execution
          </div>

          <div className="mt-1 text-xs font-medium text-slate-300">
            Live reasoning pipeline
          </div>
        </div>


        <div
          className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${
            isError
              ? "border-red-400/10 bg-red-400/[0.04] text-red-300/70"
              : status === "done"
              ? "border-emerald-400/10 bg-emerald-400/[0.04] text-emerald-300/70"
              : "border-violet-400/10 bg-violet-400/[0.04] text-violet-300/70"
          }`}
        >

          <span
            className={`h-1.5 w-1.5 rounded-full ${
              isError
                ? "bg-red-300"
                : status === "done"
                ? "bg-emerald-300"
                : "bg-violet-300"
            }`}
          />

          <span className="text-[9px] font-medium uppercase tracking-wider">
            {isError
              ? "Failed"
              : status === "done"
              ? "Complete"
              : "Running"}
          </span>

        </div>

      </div>


      {/* =====================================================
          STAGES
          ===================================================== */}

      <div className="overflow-x-auto px-5 py-6">

        <div className="min-w-[650px]">

          <div className="relative">

            {/* background line */}

            <div className="absolute left-5 right-5 top-5 h-px bg-white/[0.055]" />


            {/* progress line */}

            <div
              className={`absolute left-5 top-5 h-px rounded-full transition-all duration-1000 ${
                isError
                  ? "bg-red-400/60"
                  : "bg-gradient-to-r from-violet-500 via-indigo-400 to-cyan-300"
              }`}
              style={{
                width: `calc(${progress}% - ${
                  progress > 0
                    ? 0
                    : 0
                }px)`,
                maxWidth:
                  "calc(100% - 40px)",
              }}
            />


            {/* stage nodes */}

            <div className="relative flex justify-between">

              {STAGES.map(
                (
                  stage,
                  index
                ) => {

                  const done =
                    status ===
                      "done" ||
                    index <
                      currentIndex;

                  const active =
                    index ===
                      currentIndex &&
                    status !==
                      "done" &&
                    !isError;

                  const StageIcon =
                    stage.icon;

                  return (
                    <div
                      key={
                        stage.key
                      }
                      className="flex w-14 flex-col items-center"
                    >

                      <div
                        className={`relative flex h-10 w-10 items-center justify-center rounded-xl border transition-all duration-500 ${
                          done
                            ? "border-violet-400/20 bg-violet-400/[0.09] text-violet-200"
                            : active
                            ? "border-violet-400/30 bg-violet-400/[0.12] text-violet-200 shadow-[0_0_25px_rgba(124,92,255,.18)]"
                            : "border-white/[0.065] bg-[#0a0d15] text-slate-700"
                        }`}
                      >

                        {active && (
                          <span className="absolute inset-0 rounded-xl border border-violet-300/20 animate-pulse" />
                        )}

                        {done ? (
                          <IconCheck className="relative h-3.5 w-3.5" />
                        ) : (
                          <StageIcon className="relative h-3.5 w-3.5" />
                        )}

                      </div>


                      <span
                        className={`mt-2.5 text-center text-[9px] font-medium ${
                          done
                            ? "text-slate-400"
                            : active
                            ? "text-violet-200"
                            : "text-slate-700"
                        }`}
                      >
                        {stage.label}
                      </span>

                    </div>
                  );
                }
              )}

            </div>

          </div>

        </div>

      </div>


      {/* =====================================================
          EVENT STREAM
          ===================================================== */}

      <div className="border-t border-white/5">

        <div className="flex items-center justify-between px-5 py-3">

          <span className="text-[9px] font-semibold uppercase tracking-[0.18em] text-slate-600">
            Event stream
          </span>

          <span className="mono text-[9px] text-slate-700">
            {reasoningLog.length} events
          </span>

        </div>


        <div className="max-h-72 overflow-y-auto border-t border-white/[0.035]">

          {reasoningLog.length ===
            0 ? (
            <div className="px-5 py-7 text-center text-[10px] text-slate-700">
              Waiting for the first agent event...
            </div>
          ) : (
            <div className="divide-y divide-white/[0.035]">

              {reasoningLog.map(
                (
                  entry,
                  index
                ) => (

                  <div
                    key={`${entry.stage}-${index}`}
                    className="group px-5 py-3.5 transition hover:bg-white/[0.018] animate-fade"
                    style={{
                      animationDelay: `${Math.min(
                        index * 40,
                        300
                      )}ms`,
                    }}
                  >

                    <div className="flex gap-3">

                      <div className="relative mt-1 flex shrink-0 flex-col items-center">

                        <div className="h-1.5 w-1.5 rounded-full bg-violet-400/70 shadow-[0_0_8px_rgba(124,92,255,.4)]" />

                        {index <
                          reasoningLog.length -
                            1 && (
                          <div className="mt-1 h-full w-px bg-white/[0.045]" />
                        )}

                      </div>


                      <div className="min-w-0 flex-1">

                        <div className="flex flex-wrap items-center gap-2">

                          <span className="text-[10px] font-semibold text-slate-300">
                            {prettyStage(
                              entry.stage
                            )}
                          </span>

                          <span className="rounded-full border border-white/[0.055] bg-white/[0.02] px-1.5 py-0.5 text-[8px] text-slate-700">
                            {entry.elapsed_s}s
                          </span>

                        </div>


                        {entry.message && (
                          <p className="mt-1 text-[10px] leading-5 text-slate-600">
                            {
                              entry.message
                            }
                          </p>
                        )}


                        {entry.queries?.length >
                          0 && (
                          <div className="mt-2 flex flex-wrap gap-1.5">

                            {entry.queries.map(
                              (
                                query
                              ) => (
                                <span
                                  key={
                                    query
                                  }
                                  className="max-w-full truncate rounded-md border border-white/[0.045] bg-white/[0.018] px-2 py-1 text-[8px] text-slate-600"
                                >
                                  {query}
                                </span>
                              )
                            )}

                          </div>
                        )}


                        {entry.findings && (
                          <p className="mt-1.5 line-clamp-3 text-[10px] leading-5 text-slate-500">
                            {
                              entry.findings
                            }
                          </p>
                        )}


                        {entry.winner && (
                          <div className="mt-1.5 text-[10px] text-slate-500">

                            Winner{" "}
                            <span className="font-semibold text-violet-300/80">
                              {
                                entry.winner
                              }
                            </span>

                          </div>
                        )}

                      </div>

                    </div>

                  </div>

                )
              )}

            </div>
          )}

        </div>

      </div>


      {/* =====================================================
          TOTAL
          ===================================================== */}

      {timings.total && (
        <div className="border-t border-white/5 px-5 py-3">

          <div className="flex items-center gap-1.5 text-[9px] text-slate-700">

            <IconClock className="h-3 w-3" />

            Total runtime

            <span className="font-semibold text-slate-500">
              {timings.total}s
            </span>

          </div>

        </div>
      )}

    </div>
  );
}
