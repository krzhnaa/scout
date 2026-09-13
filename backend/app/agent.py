"""Scout's core agent loop: plan -> research -> compare.

Each stage has one job and one model role (see README's model-role table).
The old version's failure mode was a bare RuntimeError when candidate
extraction came back empty. This version retries with broadened queries and
an Anakin agentic-search last resort before ever giving up, and when it
truly can't find evidence, it returns an honest partial result instead of
crashing.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from .anakin import AnakinClient
from .config import settings
from .entities import (
    deterministic_candidates_from_sources,
    get_action_eligibility,
    merge_candidates,
    normalize_candidate,
)
from .providers import ModelRouter
from .research import (
    ResearchBrief,
    build_candidate_deepdive_query,
    build_anakin_extraction_prompt,
    build_query_matrix,
    build_research_brief,
    calculate_evidence_coverage,
    deduplicate_sources,
    extract_candidates,
    filter_candidates_by_budget,
    infer_search_locale,
    parse_budget_ceiling,
    rank_sources,
    verify_purchase_info,
)
from .search import jina_fetch, run_parallel_searches, search_and_fetch

# How many candidates make it into the final "shortlist -> deep-dive -> top 5"
# stage. Kept separate from brief.candidate_count (which governs the wider
# discovery pass) so discovery can stay broad while verification stays
# cheap and focused.
SHORTLIST_SIZE = 8
FINAL_RESULTS = 5

RANK_SYSTEM = (
    "You are a decision agent comparing real, evidence-backed candidates against a goal. "
    "Score each candidate 1-10 using ONLY the evidence given — never invent facts. Weight "
    "candidates with more, higher-confidence evidence above those with thin evidence. If a hard "
    "budget ceiling is given in the goal/context, any candidate clearly over that ceiling must "
    "score no higher than 4, regardless of how good its other evidence is. "
    "Use this calibration so scores are consistent across runs: "
    "9-10 = multiple independent sources, price AND availability verified, within budget; "
    "7-8 = solid evidence from at least one reliable source, most criteria covered; "
    "5-6 = plausible candidate but with a real evidence gap (e.g. price not verified); "
    "3-4 = thin evidence, mostly identity/name confirmation only, or over budget; "
    "1-2 = barely more than a name. Do not default to the middle of the range out of caution — "
    "commit to a score that matches the evidence actually given. "
    'Respond with ONLY this JSON shape: {"options": [{"name": str, "score": number, '
    '"justification": "2-3 sentences citing the SPECIFIC evidence given \u2014 price, availability, '
    'review findings, or the gap that limited the score"}], "winner": str}'
)


class AgentSession:
    def __init__(self, goal: str, contact_email: str | None = None):
        self.id = str(uuid.uuid4())
        self.goal = goal
        self.contact_email = contact_email
        self.status = "started"
        self.iteration = 0
        self.reasoning_log: list[dict[str, Any]] = []
        self.timings: dict[str, float] = {}
        self.decision: dict[str, Any] | None = None
        self.action_result: dict[str, Any] | None = None
        self.report_path: str | None = None
        self.discord_sent = False
        self.error: str | None = None

        self.router = ModelRouter()
        self.anakin = AnakinClient()

        self.brief: ResearchBrief = ResearchBrief()
        self.all_snippets: list[dict[str, Any]] = []
        self.all_findings: list[dict[str, Any]] = []
        self.candidates: list[dict[str, Any]] = []
        self.shortlist: list[dict[str, Any]] = []
        self.evidence_gaps: list[str] = []
        self.budget_ceiling = None  # set in plan(); set() so /status can expose it
        self.search_locale = (settings.SEARCH_COUNTRY, settings.SEARCH_LANGUAGE)
        self.budget_dropped = 0

        # Set whenever a safety-net fallback (deterministic extraction,
        # deterministic scoring) had to stand in for a failed model call, so
        # the UI can say "unverified, try again" instead of presenting a
        # fallback result as if it were a normal confident answer.
        self.degraded = False
        self.degraded_reasons: list[str] = []

    def _mark_degraded(self, reason: str) -> None:
        self.degraded = True
        if reason not in self.degraded_reasons:
            self.degraded_reasons.append(reason)

    def _log_stage(self, stage: str, elapsed: float, extra: dict[str, Any] | None = None) -> None:
        entry = {"stage": stage, "elapsed_s": round(elapsed, 2)}
        if extra:
            entry.update(extra)
        self.reasoning_log.append(entry)
        self.timings[stage] = round(elapsed, 2)

    # ------------------------------------------------------------- plan ---
    async def plan(self) -> list[str]:
        self.brief = await build_research_brief(self.goal, self.router)
        self.budget_ceiling = parse_budget_ceiling(self.goal, self.brief.geography)
        self.search_locale = infer_search_locale(self.goal, self.brief.geography)
        return await build_query_matrix(self.goal, self.brief, self.router)

    # --------------------------------------------------------- research ---
    async def research_loop(self, queries: list[str]) -> None:
        tried: list[str] = []
        while self.iteration < settings.MAX_ITERATIONS:
            self.iteration += 1
            t0 = time.time()
            tried.extend(queries)

            sources = await self._gather_sources(queries)
            self.all_snippets.extend(sources)
            new_candidates = await self._extract_and_normalize(sources)
            self.candidates = merge_candidates(self.candidates, new_candidates)
            self._apply_budget_filter()
            self._record_findings(new_candidates)

            coverage = calculate_evidence_coverage(self.candidates, self.brief)
            self._log_stage(
                f"research_round_{self.iteration}",
                time.time() - t0,
                {
                    "queries": queries,
                    "sources": len(sources),
                    "candidates": len(self.candidates),
                    "coverage": coverage["coverage"],
                    "budget_dropped": self.budget_dropped,
                },
            )

            # Bug fix: this used to be `min(3, self.brief.candidate_count)`, which is
            # always 3 (candidate_count is clamped to >= 8), so the loop declared
            # victory after just 3 candidates and never actually pursued a top-10
            # list. Target the brief's real candidate_count instead.
            enough = (
                len(self.candidates) >= self.brief.candidate_count
                and coverage["coverage"] >= 0.5
            )
            if enough or self.iteration >= settings.MAX_ITERATIONS:
                break
            queries = await build_query_matrix(self.goal, self.brief, self.router, exclude=tried)

        if not self.candidates:
            await self._last_resort_search()

        self.evidence_gaps = calculate_evidence_coverage(self.candidates, self.brief)["missing"]

        # -------------------------------------------------------------
        # Purchase verification pass.
        #
        # Round-1 extraction only ever saw a 1.2k-char clip of each source,
        # so it correctly refuses to claim a verified price/availability from
        # that alone. This pass fetches the FULL page for the strongest
        # candidates and asks a focused, single-purpose question: does THIS
        # page state an exact price and confirm it's currently buyable? This
        # is what actually lights up "Buy Now" instead of "Purchase link not
        # verified".
        # -------------------------------------------------------------
        await self._verify_top_candidates()
        self._apply_budget_filter()

        # -------------------------------------------------------------
        # Shortlist -> per-candidate deep-dive.
        #
        # Everything above this point is discovery: broad queries, whatever
        # named things happen to show up across ~14 generic sources. That's
        # not the same as "find the best headphones under 3k, THEN go
        # confirm each finalist is real and buyable" — it's one flat pass.
        # This stage narrows to a real shortlist and, for each finalist that
        # still lacks a verified price/purchase link, fires a targeted
        # "<name> buy price" search of its own so the final top-5 aren't
        # hostage to whatever the original broad queries happened to return.
        # -------------------------------------------------------------
        self.shortlist = self._build_shortlist()
        await self._deepdive_shortlist()
        self._apply_budget_filter()

    def _final_pool(self) -> list[dict[str, Any]]:
        """Candidates the compare stage should actually score.

        Prefers the deep-dived shortlist (real names, individually
        verified); falls back to the raw candidate pool only if the
        shortlist stage never ran or produced nothing (e.g. zero candidates
        overall).
        """
        return self.shortlist or self.candidates

    def _build_shortlist(self, limit: int = SHORTLIST_SIZE) -> list[dict[str, Any]]:
        """Pick the strongest distinct candidates to carry into deep-dive.

        Ranked by how purchase-ready and evidence-backed they already are —
        candidates that already have a purchase URL or strong evidence get
        priority, since deep-dive is there to close remaining gaps, not
        re-litigate candidates that clearly aren't real.
        """
        by_id: dict[str, dict[str, Any]] = {}
        for c in self.candidates:
            eid = str(c.get("entity_id") or "")
            if eid and eid not in by_id:
                by_id[eid] = c
        ranked = sorted(
            by_id.values(),
            key=lambda c: (
                bool(c.get("price_verified")),
                bool(c.get("purchase_url")),
                float(c.get("evidence_confidence", 0) or 0),
                int(c.get("evidence_count", 0) or 0),
            ),
            reverse=True,
        )
        return ranked[:limit]

    async def _deepdive_shortlist(self) -> None:
        t0 = time.time()
        needs_deepdive = [
            c for c in self.shortlist
            if not (c.get("price_verified") and (c.get("purchase_url") or c.get("booking_url")))
        ]
        if not needs_deepdive:
            self._log_stage("deepdive_shortlist", time.time() - t0, {"deep_dived": 0})
            return

        country, language = self.search_locale

        async def _deepdive_one(candidate: dict[str, Any]) -> None:
            name = str(candidate.get("name") or "")
            if not name:
                return
            query = build_candidate_deepdive_query(name, self.brief.task_type)
            try:
                results = await search_and_fetch(query, country=country, language=language)
            except Exception:
                results = []
            if not results:
                return
            # Prefer a retailer/booking-looking host over a review/editorial
            # one — same intent as source_quality's RETAIL_HINTS bump, but
            # applied to a single candidate's own targeted results.
            best = results[0]
            for r in results:
                host_l = (r.get("url") or "").lower()
                if any(h in host_l for h in (
                    "amazon", "flipkart", "bestbuy", "walmart", "official",
                    "booking.com", "expedia", "makemytrip",
                )):
                    best = r
                    break
            page = best.get("content") or best.get("snippet") or ""
            try:
                verified = await verify_purchase_info([candidate], [page], self.router)
            except Exception:
                verified = {}
            row = verified.get(0)
            if not row:
                return
            if row.get("price_verified") and row.get("price") is not None:
                try:
                    price = float(row["price"])
                except (TypeError, ValueError):
                    price = None
                if price and price > 0:
                    candidate["price"] = round(price, 2)
                    candidate["currency"] = str(row.get("currency") or candidate.get("currency") or "").upper()
                    candidate["price_verified"] = True
                    candidate["price_source_url"] = best.get("url", "")
            purchase_url = row.get("purchase_url")
            if isinstance(purchase_url, str) and purchase_url.startswith("http"):
                candidate["purchase_url"] = purchase_url
            elif row.get("price_verified") and row.get("availability_verified") and not candidate.get("purchase_url"):
                candidate["purchase_url"] = best.get("url", "")
            if row.get("availability_verified"):
                candidate["availability_verified"] = True
            candidate["purchasable"] = bool(
                candidate.get("price_verified") and (candidate.get("purchase_url") or candidate.get("booking_url"))
            )
            candidate["action_eligibility"] = get_action_eligibility(candidate)
            candidate["source_urls"] = list(dict.fromkeys((candidate.get("source_urls") or []) + [best.get("url", "")]))[:20]

        await asyncio.gather(*[_deepdive_one(c) for c in needs_deepdive], return_exceptions=True)
        self._log_stage("deepdive_shortlist", time.time() - t0, {"deep_dived": len(needs_deepdive)})

    def _apply_budget_filter(self) -> None:
        """Hard-drop candidates that verifiably blow a parsed "under Xk" ceiling.

        Applied after every merge and again after purchase verification,
        since verification can newly attach a verified price that only then
        reveals a candidate is over budget.
        """
        if not self.budget_ceiling:
            return
        before = len(self.candidates)
        self.candidates = filter_candidates_by_budget(self.candidates, self.budget_ceiling)
        self.budget_dropped += before - len(self.candidates)

    async def _verify_top_candidates(self, limit: int = 12) -> None:
        t0 = time.time()
        # Prioritize candidates that already look closest to purchasable, plus
        # anything with decent evidence — cheapest path to real Buy Now results.
        ranked = sorted(
            self.candidates,
            key=lambda c: (
                bool(c.get("purchase_url")),
                float(c.get("evidence_confidence", 0) or 0),
                int(c.get("evidence_count", 0) or 0),
            ),
            reverse=True,
        )[:limit]
        targets = [c for c in ranked if c.get("source_url")]
        if not targets:
            return

        pages = await asyncio.gather(
            *[
                jina_fetch(self.router.client, c.get("purchase_url") or c.get("source_url"), fallback="")
                for c in targets
            ],
            return_exceptions=True,
        )
        pages = [p if isinstance(p, str) else "" for p in pages]

        try:
            verified = await verify_purchase_info(targets, pages, self.router)
        except Exception:
            verified = {}

        by_id = {c["entity_id"]: c for c in self.candidates if c.get("entity_id")}
        updated = 0
        for idx, candidate in enumerate(targets):
            row = verified.get(idx)
            if not row:
                continue
            entity_id = candidate.get("entity_id")
            target = by_id.get(entity_id)
            if not target:
                continue
            if row.get("price_verified") and row.get("price") is not None:
                try:
                    price = float(row["price"])
                except (TypeError, ValueError):
                    price = None
                if price and price > 0:
                    target["price"] = round(price, 2)
                    target["currency"] = str(row.get("currency") or target.get("currency") or "").upper()
                    target["price_verified"] = True
                    target["price_source_url"] = candidate.get("purchase_url") or candidate.get("source_url")
                    updated += 1
            purchase_url = row.get("purchase_url")
            if purchase_url and isinstance(purchase_url, str) and purchase_url.startswith("http"):
                target["purchase_url"] = purchase_url
            elif (
                row.get("price_verified")
                and row.get("availability_verified")
                and not target.get("purchase_url")
            ):
                # The page just verified is itself a live retailer/provider page.
                # Models often omit PAGE_URL in their JSON even though it is the
                # direct page they used to verify the price and availability.
                target["purchase_url"] = candidate.get("source_url")
            if row.get("availability_verified"):
                target["availability_verified"] = True
            target["purchasable"] = bool(
                target.get("price_verified") and (target.get("purchase_url") or target.get("booking_url"))
            )

        self._log_stage(
            "verify_purchase", time.time() - t0,
            {"checked": len(targets), "newly_price_verified": updated},
        )

    async def _gather_sources(self, queries: list[str]) -> list[dict[str, Any]]:
        country, language = getattr(self, "search_locale", (settings.SEARCH_COUNTRY, settings.SEARCH_LANGUAGE))
        results_by_query = await run_parallel_searches(queries, country=country, language=language)
        sources: list[dict[str, Any]] = [r for results in results_by_query.values() for r in results]

        if self.anakin.enabled and queries:
            try:
                result = await self.anakin.search(queries[0], limit=6)
                for r in (result.get("results") or [])[:6]:
                    sources.append({
                        "url": r.get("url", ""), "title": r.get("title", ""),
                        "content": r.get("snippet", ""), "snippet": r.get("snippet", ""),
                        "source": "anakin",
                    })
            except Exception:
                pass  # Anakin boosts recall here; it is never a hard dependency.

        return deduplicate_sources(sources)

    async def _extract_and_normalize(self, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Widened from 10 -> 14: reaching a full top-10 candidate list needs more
        # raw sources feeding the extraction call, since not every source yields
        # a distinct, valid candidate after normalize_candidate's filtering.
        # research.extract_candidates is kept in sync with this same [:14] cap.
        ranked = rank_sources(sources, minimum_quality=0.2)[:14] or rank_sources(sources)[:14]
        raw_candidates = await extract_candidates(self.goal, self.brief, ranked, self.router)
        out = []
        for raw in raw_candidates:
            candidate = normalize_candidate(raw, self.brief.task_type)
            if candidate is not None:
                out.append(candidate)

        if not out and ranked:
            anakin_candidates = await self._anakin_extract_fallback()
            for raw in anakin_candidates:
                candidate = normalize_candidate(raw, self.brief.task_type)
                if candidate is not None:
                    out.append(candidate)
            if out:
                print(f"[agent] Anakin agentic-search fallback produced {len(out)} candidate(s).")
                self._mark_degraded(
                    "Primary extraction models were unavailable; Anakin's agentic search "
                    "was used instead (still real research, but a different, slower path)."
                )

        if not out and ranked:
            # The LLM extraction call returned nothing usable — a bad/expired
            # key, a rate limit, a malformed-JSON retry that also failed, a
            # network blip, whatever. That used to mean zero candidates and
            # therefore nothing to compare, even though search itself worked
            # fine (this is almost certainly what you were hitting: sources
            # found, candidates == 0). Fall back to building real, named
            # candidates directly from the search results themselves so
            # there's always something to rank and compare.
            fallback = deterministic_candidates_from_sources(ranked, self.brief.task_type)
            if fallback:
                print(
                    f"[agent] LLM extraction returned 0 candidates from {len(ranked)} sources; "
                    f"using {len(fallback)} deterministic fallback candidate(s) instead."
                )
                self._mark_degraded(
                    "Candidate extraction models were unavailable "
                    f"({self.router.last_error or 'no detail captured'})."
                )
            out = fallback
        return out

    async def _anakin_extract_fallback(self) -> list[dict[str, Any]]:
        """Ask Anakin's agentic search to research and extract candidates
        itself, as a middle ground between the failed LLM extraction call
        and the zero-LLM regex fallback. Only attempted if Anakin is
        configured and this session hasn't already spent its agentic-search
        budget (see ANAKIN_MAX_AGENTIC_SEARCH_CALLS)."""
        if not self.anakin.enabled:
            return []
        try:
            prompt = build_anakin_extraction_prompt(self.goal, self.brief)
            result = await self.anakin.agentic_search(prompt)
        except Exception as exc:
            print(f"[agent] Anakin extraction fallback failed: {exc}")
            return []
        raw = result.get("generatedJson")
        if raw is None:
            raw = result.get("generated_json")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                return []
        candidates = raw.get("candidates") if isinstance(raw, dict) else None
        return candidates if isinstance(candidates, list) else []

    def _record_findings(self, candidates: list[dict[str, Any]]) -> None:
        for c in candidates:
            for e in c.get("evidence", [])[:5]:
                self.all_findings.append({
                    "candidate": c.get("name"), "criterion": e.get("criterion"),
                    "claim": e.get("claim"), "confidence": e.get("confidence"),
                    "source_url": e.get("source_url"),
                })

    async def _last_resort_search(self) -> None:
        """Exhaust real options — including Anakin's deeper agentic search —
        before ever surfacing an empty result to the ranking stage."""
        t0 = time.time()
        sources = await self._gather_sources([self.goal])

        if self.anakin.enabled:
            try:
                result = await self.anakin.agentic_search(self.goal)
                extra = result.get("results") or result.get("sources") or []
                if isinstance(extra, list):
                    for r in extra[:8]:
                        if isinstance(r, dict) and r.get("url"):
                            sources.append({
                                "url": r.get("url", ""), "title": r.get("title", ""),
                                "content": r.get("content") or r.get("snippet", ""),
                                "snippet": r.get("snippet", ""), "source": "anakin",
                            })
            except Exception:
                pass

        self.all_snippets.extend(sources)
        new_candidates = await self._extract_and_normalize(sources)
        self.candidates = merge_candidates(self.candidates, new_candidates)
        self._apply_budget_filter()
        self._record_findings(new_candidates)
        self._log_stage(
            "research_last_resort", time.time() - t0,
            {"sources": len(sources), "candidates": len(self.candidates)},
        )

    # ---------------------------------------------------------- compare ---
    async def compare_options(self) -> None:
        if not self.candidates:
            note = (
                "No evidence-backed candidates were found even after broadening the "
                "search and trying Anakin's agentic search. Try a more specific goal, "
                "or one with clearer named options to compare."
            )
            if self.budget_dropped:
                note += (
                    f" Note: {self.budget_dropped} candidate(s) were found but dropped "
                    f"for exceeding the {self.budget_ceiling.amount:.0f} {self.budget_ceiling.currency} "
                    "budget — try a higher budget if these were close matches."
                )
            self.decision = {
                "options": [], "winner": None, "note": note,
                "degraded": self.degraded,
                "degraded_reason": " ".join(self.degraded_reasons) if self.degraded_reasons else None,
            }
            return

        merged_scores = await self._ensemble_scores()
        options = self._score_candidates(merged_scores)
        if not options:
            self._mark_degraded(
                "Ranking models were unavailable "
                f"({self.router.last_error or 'no detail captured'})."
            )
            options = self._deterministic_fallback_scores()
        options.sort(key=lambda o: o["score"], reverse=True)
        self.decision = {
            "options": options[:FINAL_RESULTS],
            "winner": options[0]["name"] if options else None,
            "degraded": self.degraded,
            "degraded_reason": " ".join(self.degraded_reasons) if self.degraded_reasons else None,
        }

    async def _ensemble_scores(self) -> dict[str, dict[str, list]]:
        pack = self._decision_pack()
        try:
            score_a, score_b = await asyncio.gather(
                self.router.deep(pack, RANK_SYSTEM),
                self.router.ensemble_b(pack, RANK_SYSTEM),
                return_exceptions=True,
            )
        except Exception:
            return {}
        # Bug fix: this used to only keep `score` and threw away `justification`
        # entirely, even though RANK_SYSTEM already asks for a detailed,
        # evidence-citing justification per candidate. That's why every card
        # fell through to the generic "Evidence-backed candidate." fallback.
        merged: dict[str, dict[str, list]] = {}
        for result in (score_a, score_b):
            if isinstance(result, Exception) or not isinstance(result, dict):
                continue
            for opt in result.get("options", []):
                if not isinstance(opt, dict) or not opt.get("name"):
                    continue
                try:
                    score = float(opt["score"])
                except (TypeError, ValueError):
                    continue
                entry = merged.setdefault(str(opt["name"]), {"scores": [], "justifications": []})
                entry["scores"].append(score)
                justification = str(opt.get("justification") or "").strip()
                if justification:
                    entry["justifications"].append(justification)
        return merged

    def _decision_pack(self) -> str:
        candidates = []
        for c in self._final_pool()[:20]:
            candidates.append({
                "name": c.get("name"), "source_url": c.get("source_url"),
                "price": c.get("price"), "currency": c.get("currency"),
                "price_verified": c.get("price_verified"),
                "availability_verified": c.get("availability_verified"),
                "evidence": [
                    {"criterion": e.get("criterion"), "claim": str(e.get("claim"))[:200],
                     "confidence": e.get("confidence")}
                    for e in c.get("evidence", [])[:5]
                ],
            })
        payload = {"goal": self.goal[:400], "criteria": self.brief.criteria, "candidates": candidates}
        if self.budget_ceiling:
            payload["budget_ceiling"] = f"{self.budget_ceiling.amount:.0f} {self.budget_ceiling.currency}"
        return json.dumps(payload, ensure_ascii=False)[:6000]

    def _score_candidates(self, merged_scores: dict[str, dict[str, list]]) -> list[dict[str, Any]]:
        options = []
        for c in self._final_pool():
            entry = merged_scores.get(str(c.get("name", "")))
            scores = entry.get("scores") if entry else None
            if not scores:
                continue
            option = dict(c)
            option["score"] = round(sum(scores) / len(scores), 1)
            # Detailed analysis, in priority order: the ranking model's own
            # evidence-citing justification (now actually kept, see bug fix
            # above) -> the extractor's stated reason -> a synthesized
            # evidence-count summary -> only then the generic fallback.
            justifications = entry.get("justifications") if entry else []
            if justifications:
                option["justification"] = max(justifications, key=len)
            elif c.get("reason"):
                option["justification"] = c["reason"]
            elif c.get("review_summary"):
                option["justification"] = c["review_summary"]
            elif c.get("evidence_count"):
                option["justification"] = (
                    f"Backed by {c['evidence_count']} verified claim(s) "
                    f"(avg. confidence {c.get('evidence_confidence', 0):.2f})."
                )
            else:
                option["justification"] = "Evidence-backed candidate."
            option["action_eligibility"] = get_action_eligibility(option)
            options.append(option)
        return options

    def _deterministic_fallback_scores(self) -> list[dict[str, Any]]:
        """Only used if both ranking models fail — evidence volume stands in."""
        options = []
        for c in self._final_pool()[:15]:
            evidence_conf = float(c.get("evidence_confidence", 0) or 0)
            evidence_count = int(c.get("evidence_count", 0) or 0)
            score = round(min(9.5, max(2.0, 3 + evidence_conf * 4 + min(2.0, evidence_count * 0.3))), 1)
            option = dict(c)
            option["score"] = score
            option["justification"] = "Ranked by evidence volume — the ranking models were unavailable."
            option["action_eligibility"] = get_action_eligibility(option)
            options.append(option)
        return options

    async def close(self) -> None:
        await self.router.close()
        await self.anakin.close()
