import { useState } from "react";

import {
  IconAlert,
  IconCheck,
  IconDiscord,
  IconDownload,
  IconLink,
} from "./icons.jsx";


const API_BASE =
  import.meta.env.VITE_API_BASE ||
  "http://localhost:8000";


function assetUrl(path) {
  if (!path) return null;

  if (
    path.startsWith(
      "http://"
    ) ||
    path.startsWith(
      "https://"
    )
  ) {
    return path;
  }

  return `${API_BASE}/${path
    .replace(/^\.?\//, "")}`;
}


export default function ActionProof({
  actionResult,
  reportUrl,
  discordSent,
}) {
  const [downloadingReport, setDownloadingReport] =
    useState(false);

  if (
    !actionResult &&
    !reportUrl &&
    !discordSent
  ) {
    return null;
  }


  const performed =
    Boolean(
      actionResult?.performed
    );

  const success =
    Boolean(
      actionResult?.success
    );

  const downloadReport = async () => {
    if (!reportUrl || downloadingReport) return;

    setDownloadingReport(true);

    try {
      const response = await fetch(
        assetUrl(reportUrl)
      );

      if (!response.ok) {
        throw new Error(
          "The report could not be downloaded."
        );
      }

      const reportBlob = await response.blob();
      const objectUrl = URL.createObjectURL(
        reportBlob
      );
      const link = document.createElement("a");

      link.href = objectUrl;
      link.download =
        reportUrl.split("/").pop() ||
        "scout-report.pdf";

      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(
        () => URL.revokeObjectURL(objectUrl),
        0
      );
    } catch (error) {
      console.error(
        "Unable to download Scout report:",
        error
      );
    } finally {
      setDownloadingReport(false);
    }
  };


  return (
    <section className="glass holo-frame overflow-hidden animate-fade-up">

      {/* =====================================================
          HEADER
          ===================================================== */}

      <div className="border-b border-white/5 px-5 py-5 sm:px-6">

        <div className="flex items-center gap-3">

          <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-white/7 bg-white/[0.025]">

            {performed &&
            success ? (
              <IconCheck className="h-4 w-4 text-emerald-300" />
            ) : (
              <IconLink className="h-4 w-4 text-violet-300" />
            )}

          </div>


          <div>

            <div className="text-[9px] font-semibold uppercase tracking-[0.18em] text-slate-600">
              Execution & delivery
            </div>

            <h3 className="mt-1 text-base font-semibold text-white">
              What happened next
            </h3>

          </div>

        </div>

      </div>


      {/* =====================================================
          ACTION STATE
          ===================================================== */}

      {discordSent && (
        <div className="px-5 py-5 sm:px-6">

          <div
            className="rounded-xl border border-emerald-400/10 bg-emerald-400/[0.035] p-4"
          >

            <div className="flex gap-3">

              <div
                className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-emerald-400/10"
              >

                <IconDiscord className="h-3.5 w-3.5 text-emerald-300" />

              </div>


              <div className="min-w-0">

                <div className="flex flex-wrap items-center gap-2">

                  <span className="text-xs font-semibold text-slate-300">
                    Sent to Discord
                  </span>


                  <span
                    className="rounded-full border border-emerald-400/10 bg-emerald-400/[0.04] px-2 py-0.5 text-[8px] font-semibold uppercase tracking-wider text-emerald-300/70"
                  >
                    Delivered
                  </span>

                </div>


              </div>

            </div>

          </div>

        </div>
      )}


      {/* =====================================================
          SCREENSHOTS
          ===================================================== */}

      {(actionResult?.before_screenshot ||
        actionResult?.after_screenshot) && (
        <div className="border-t border-white/5 px-5 py-5 sm:px-6">

          <div className="mb-3 text-[9px] font-semibold uppercase tracking-[0.18em] text-slate-700">
            Execution proof
          </div>


          <div className="grid gap-4 sm:grid-cols-2">

            {actionResult.before_screenshot && (
              <ProofImage
                label="Before"
                src={assetUrl(
                  actionResult.before_screenshot
                )}
              />
            )}


            {actionResult.after_screenshot && (
              <ProofImage
                label="After"
                src={assetUrl(
                  actionResult.after_screenshot
                )}
              />
            )}

          </div>

        </div>
      )}


      {/* =====================================================
          DELIVERY
          ===================================================== */}

      {(reportUrl ||
        discordSent) && (
        <div className="flex flex-col gap-3 border-t border-white/5 px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">

          <div className="flex flex-wrap items-center gap-3">

            {reportUrl && (
              <button
                type="button"
                onClick={downloadReport}
                disabled={downloadingReport}
                className="inline-flex items-center gap-1.5 rounded-lg border border-white/7 bg-white/[0.025] px-3 py-2 text-[9px] font-medium text-slate-500 transition hover:border-violet-400/15 hover:bg-violet-400/[0.035] hover:text-violet-200"
              >
                <IconDownload className="h-3 w-3" />

                {downloadingReport
                  ? "Downloading..."
                  : "PDF report"}

              </button>
            )}


          </div>


          <div className="text-[9px] text-slate-700">
            Evidence preserved in the run report
          </div>

        </div>
      )}

    </section>
  );
}


/* ===========================================================
   PROOF IMAGE
   =========================================================== */

function ProofImage({
  label,
  src,
}) {
  return (
    <div>

      <div className="mb-2 flex items-center gap-2">

        <span className="h-1.5 w-1.5 rounded-full bg-violet-300/60" />

        <span className="text-[9px] font-semibold uppercase tracking-wider text-slate-600">
          {label}
        </span>

      </div>


      <div className="overflow-hidden rounded-xl border border-white/[0.055] bg-black/20">

        <img
          src={src}
          alt={`${label} execution proof`}
          className="block max-h-80 w-full object-cover transition duration-500 hover:scale-[1.015]"
          loading="lazy"
        />

      </div>

    </div>
  );
}
