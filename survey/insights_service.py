"""LLM-backed service that turns a flagged question (≥50% poor responders) into
a Symptoms / Likely causes / High-level action triad.

Uses the Anthropic Messages API directly via the ``anthropic`` Python SDK.
Insights are grounded in the NAARI evidence library (see
``insights_grounding``): the library's index.md is injected into the system
prompt and the model can grep/read the converted source documents through
tools before writing, citing each source it uses inline.
"""

from __future__ import annotations

import json
import time
from typing import Optional

import anthropic
from django.conf import settings

from . import insights_grounding


class InsightsService:
    """Generate narrative insights for under-performing survey questions."""

    SYSTEM_PROMPT = (
        "You are a senior gender- and livelihoods-focused development analyst "
        "Always respond in english unless the user explicitly requests another language."
        "working on women's empowerment, women in agriculture, livelihoods, "
        "mobility, resource control, and collective agency in India. Your audience "
        "is programme teams, district officers, government partners, NGOs, and "
        "community institutions who use these briefings to prioritize interventions.\n\n"
        "You will receive an indicator, a specific survey question, the share of "
        "women performing poorly on it, a structured DATA CONTEXT block (option-level "
        "distribution, numeric spread, and the geographic hotspots where the issue "
        "concentrates), plus a reference guidance snippet from the programme's "
        "playbook. Use the playbook as a directional prior, but build the briefing "
        "primarily from the DATA CONTEXT - that is what makes the output useful "
        "to a district officer who already knows the playbook.\n\n"
        "EVIDENCE AND REFERENCES:\n"
        "- When drawing on external evidence, prioritize recent sources from the "
        "last five years and India-relevant work by FAO, CGIAR/IFPRI, the Ministry "
        "of Women and Child Development, the National Commission for Women, the "
        "Ministry of Agriculture and Farmers Welfare, state agriculture departments, "
        "and credible NGOs or research organizations working on women's "
        "empowerment or women in agriculture.\n"
        "- Prefer recommendations or best practices that have evidence of working "
        "elsewhere in comparable Indian rural/agricultural contexts. If a practice "
        "worked in another district or state, cite that source briefly and explain "
        "why it is relevant to this indicator.\n"
        "- Cite references inline in the JSON values, using a compact form such as "
        "(FAO, 2023), (CGIAR/IFPRI, 2022), or (MWCD, 2021). Do not include a "
        "separate bibliography field.\n"
        "- Never invent studies, publication years, organizations, statistics, or "
        "place names. If you do not have enough evidence or understanding to support "
        "a cause or recommendation, return an empty string for that field.\n\n"
        "OUTPUT FORMAT: respond with STRICT JSON only - no markdown fences, no "
        "commentary before or after. Use exactly these five keys:\n"
        "  - `symptoms`: 2-4 full sentences describing what the poor performance "
        "looks like on the ground. Anchor at least one sentence in a SPECIFIC "
        "number from the DATA CONTEXT - e.g. \"63% of respondents picked 'Not "
        "at all' on this Likert\", or \"the issue is sharpest in <state> "
        "(<pct>% poor) and <district>\". Name the agency dimension affected "
        "(decision-making, resource control, mobility, collective action). Cite "
        "a relevant reference if you connect the pattern to wider evidence.\n"
        "  - `likely_causes`: 2-4 full sentences naming the structural, social, "
        "or household-level drivers most consistent with the observed pattern. "
        "Be concrete (e.g. \"joint accounts operated by the spouse\", \"lack of "
        "vernacular product literacy\", \"time poverty from unpaid care work\"). "
        "If the geographic hotspots are clustered (e.g. all in one state), point "
        "out what about that region likely drives the result, and cite a recent "
        "reference when using external evidence.\n"
        "  - The recommended response is split into THREE persona-specific actions. "
        "Each is 2-4 full sentences, written for that audience's actual levers and "
        "decisions, and each must be distinct (do not repeat the same action across "
        "personas). When hotspots exist, target the highest-pct region first; when an "
        "option dominates the distribution, tailor the action to that specific failure "
        "mode. Prioritize proven or promising best practices from comparable contexts "
        "and cite the source inline. The three keys are:\n"
        "    - `action_fpo`: what the FPO / FPO Team (the farmer producer "
        "organization and its on-ground staff) should do directly with members "
        "and women producers.\n"
        "    - `action_programme`: what the Programme Team / Implementing Team "
        "(NGO, field teams, local government partners) should do to design, "
        "deliver, or course-correct the intervention.\n"
        "    - `action_donor`: what the Donors / Funding Team should do - where to "
        "direct funding, what to prioritize, monitor, or de-risk at a strategic "
        "level. Leave any of these three empty only if the data and credible "
        "evidence genuinely cannot support an action for that persona.\n\n"
        "RULES:\n"
        "- Cite numbers and place names ONLY from the DATA CONTEXT and the "
        "headline poor-performance rate. Never invent statistics.\n"
        "- Do not overuse FPO/SHG language. The survey may include women in "
        "FPOs/SHGs, but insights should apply to women in the relevant community "
        "or agricultural context unless the evidence points to FPOs/SHGs.\n"
        "- Use plain, actionable language. No bullet lists, markdown, or "
        "headings inside the JSON values - full sentences only.\n"
        "- Do not repeat the question text verbatim. Do not regurgitate the "
        "playbook snippet - extend it with the data and cited evidence.\n"
        "- `symptoms`, `likely_causes`, and the action fields must not paraphrase "
        "each other. Each adds new information (what is happening / why / what each "
        "persona should do)."
    )

    def __init__(self):
        self.api_key = settings.ANTHROPIC_API_KEY
        self.model = settings.ANTHROPIC_INSIGHTS_MODEL
        self.language_map = dict(settings.LANGUAGES)
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.web_search_enabled = getattr(settings, "ANTHROPIC_WEB_SEARCH", True)

    def generate_insight(
        self,
        indicator_name: str,
        indicator_description: str,
        question_text: str,
        poor_pct: float,
        guidance_snippet: Optional[dict] = None,
        data_context: Optional[dict] = None,
        respondents_total: Optional[int] = None,
        respondents_poor: Optional[int] = None,
        language: Optional[str] = None,
    ) -> Optional[dict]:
        """Call the LLM and return a dict with `symptoms`, `likely_causes`, and the
        three persona-targeted actions (`action_fpo`, `action_programme`,
        `action_donor`), or None.

        When `language` is a non-English code present in `settings.LANGUAGES`, the
        LLM is instructed to write the JSON values directly in that language so
        the cached output matches the user's selected website language.
        """

        guidance_text = ""
        if guidance_snippet:
            guidance_text = (
                "Reference guidance from the NAARI playbook (treat as a prior, not a script):\n"
                f"- Symptom pattern: {guidance_snippet.get('symptoms', '')}\n"
                f"- Likely causes: {guidance_snippet.get('likely_causes', '')}\n"
                f"- Recommended action: {guidance_snippet.get('action', '')}\n"
            )

        sample_line = ""
        if respondents_total is not None and respondents_poor is not None:
            sample_line = (
                f"Sample: {respondents_poor} of {respondents_total} respondents "
                f"flagged as poor.\n"
            )

        data_block = self._format_data_context(data_context)

        language_directive = self._language_directive(language)

        user_prompt = (
            f"Indicator: {indicator_name}\n"
            f"Indicator description: {indicator_description or '(none provided)'}\n"
            f"Question: {question_text}\n"
            f"Share of respondents performing poorly: {poor_pct:.1f}%\n"
            f"{sample_line}\n"
            f"DATA CONTEXT:\n{data_block}\n\n"
            f"{guidance_text}\n"
            "Return JSON with keys symptoms, likely_causes, action_fpo, "
            "action_programme, action_donor. "
            "Cite at least one specific number or place from the DATA CONTEXT in "
            "the symptoms field. Include compact inline references when using "
            "external evidence, and leave any field as an empty string if the "
            "data and credible recent evidence are insufficient."
            f"{language_directive}"
        )

        return self._call_anthropic(
            user_prompt=user_prompt,
            max_tokens=3000,
            temperature=0.5,
            guidance_snippet=guidance_snippet,
        )

    def generate_indicator_overview(
        self,
        indicator_name: str,
        indicator_description: str,
        rows: list[dict] | None = None,
        empowerment_context: Optional[dict] = None,
        language: Optional[str] = None,
        guidance: Optional[dict] = None,
    ) -> Optional[dict]:
        """Call the LLM once for an indicator-level overview.

        **Headline data:** ``empowerment_context`` — the indicator's
        empowerment / disempowerment counts and geographic breakdown computed
        from ``IndicatorResult`` (same source as the dashboard card and map).
        The LLM must anchor its symptoms in these numbers.

        **Supplementary context:** ``rows`` carries per-question option
        distributions (response patterns) so the model can describe *why*
        respondents are disempowered, but these no longer drive the headline
        metric.

        ``guidance`` is an optional playbook snippet used as the fallback when
        the LLM call fails (rate limit, API cap, non-JSON) so the indicator
        still gets a populated card instead of a blank one.
        """

        empowerment_block = self._format_empowerment_context(empowerment_context)

        question_blocks = []
        guidance_blocks = []
        for idx, row in enumerate(rows or [], start=1):
            question_text = row.get("question_text")
            if not question_text and row.get("question") is not None:
                question_text = row["question"].text
            question_text = question_text or ""
            row_guidance = row.get("guidance")
            question_blocks.append(
                f"Question {idx}: {question_text}\n"
                f"Response patterns:\n{self._format_data_context(row.get('data_context'))}"
            )
            if row_guidance:
                # Remember the first available snippet so it can seed the
                # fallback if the caller didn't pass an explicit one.
                if guidance is None:
                    guidance = row_guidance
                guidance_blocks.append(
                    f"For question {idx}:\n"
                    f"- Symptom pattern: {row_guidance.get('symptoms', '')}\n"
                    f"- Likely causes: {row_guidance.get('likely_causes', '')}\n"
                    f"- Recommended action: {row_guidance.get('action', '')}"
                )

        question_context = "\n\n".join(question_blocks) if question_blocks else "(none available)"
        guidance_context = "\n\n".join(guidance_blocks) if guidance_blocks else "(none available)"
        language_directive = self._language_directive(language)
        user_prompt = (
            f"Indicator: {indicator_name}\n"
            f"Indicator description: {indicator_description or '(none provided)'}\n\n"
            "Synthesize ONE indicator-level overview. Lead with the empowerment "
            "summary — anchor at least one sentence in the empowerment percentage "
            "and, when present, the lowest-empowerment regions. Use the response "
            "patterns to explain WHY respondents are disempowered, but do not list "
            "every question separately.\n\n"
            f"EMPOWERMENT SUMMARY (from indicator scoring):\n{empowerment_block}\n\n"
            f"RESPONSE PATTERNS (per-question breakdown for context):\n{question_context}\n\n"
            "Reference guidance from the NAARI playbook (treat as priors, not scripts):\n"
            f"{guidance_context}\n\n"
            "Return JSON with keys symptoms, likely_causes, action_fpo, "
            "action_programme, action_donor. "
            "symptoms and likely_causes should be 3-5 full sentences each; each action "
            "field should be 2-4 sentences tailored to that persona. Cite specific "
            "numbers or places from the EMPOWERMENT SUMMARY and RESPONSE PATTERNS. "
            "Include compact inline references from recent India-relevant "
            "evidence when using external studies or best practices. If the data and "
            "credible evidence are insufficient for any field, return an empty string "
            "for that field rather than guessing."
            f"{language_directive}"
        )

        return self._call_anthropic(
            user_prompt=user_prompt,
            max_tokens=4000,
            temperature=0.45,
            guidance_snippet=guidance,
        )

    # ------------------------------------------------------------------
    # Core Anthropic API call with retry logic
    # ------------------------------------------------------------------

    def _call_anthropic(
        self,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        guidance_snippet: Optional[dict] = None,
    ) -> Optional[dict]:
        """Send a request to the Anthropic Messages API and return parsed JSON.

        Retries on rate-limit (429) and transient server errors (5xx) with
        exponential back-off, matching the previous OpenRouter retry behaviour.
        """
        max_attempts = 4
        backoff = 2.0

        # Build the tools list — optionally include Anthropic's native web search.
        tools = []
        if self.web_search_enabled:
            tools.append({
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
            })

        # NAARI evidence library grounding: expose the Claude Code-style
        # search/read/list_sections tools and inject the library's main index
        # (index.md) into the system prompt so the model knows which source
        # file answers which indicator. The index block is marked for prompt
        # caching — it is large (~25k tokens) but identical across calls.
        grounded = insights_grounding.grounding_enabled()
        system_payload = self.SYSTEM_PROMPT
        if grounded:
            grounding_block = insights_grounding.build_system_block()
            if grounding_block:
                tools.extend(insights_grounding.GROUNDING_TOOLS)
                system_payload = [
                    {"type": "text", "text": self.SYSTEM_PROMPT},
                    {
                        "type": "text",
                        "text": grounding_block,
                        "cache_control": {"type": "ephemeral"},
                    },
                ]

        # Newer Claude models (Opus 4.8 / Fable 5 / …) have deprecated the
        # `temperature` parameter and reject requests that include it, so we
        # default to omitting it. If a model ever complains we drop it and
        # retry (handled in the APIStatusError branch below).
        send_temperature = getattr(settings, "ANTHROPIC_SEND_TEMPERATURE", False)

        for attempt in range(1, max_attempts + 1):
            try:
                create_kwargs = dict(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system_payload,
                )
                if send_temperature:
                    create_kwargs["temperature"] = temperature
                if tools:
                    create_kwargs["tools"] = tools

                # Agentic loop: the model may call the evidence-library tools
                # (search/read/list_sections) several times before answering.
                # Web search is a server-side tool and never lands here, but
                # long searched turns can pause (`pause_turn`) and need to be
                # handed back as-is. Any API error inside the loop bubbles to
                # the except handlers below and retries the whole conversation.
                messages = [{"role": "user", "content": user_prompt}]
                response = self.client.messages.create(
                    **create_kwargs, messages=messages
                )
                rounds = 0
                while (
                    response.stop_reason in ("tool_use", "pause_turn")
                    and rounds < insights_grounding.MAX_TOOL_ROUNDS
                ):
                    rounds += 1
                    messages.append(
                        {"role": "assistant", "content": response.content}
                    )
                    if response.stop_reason == "tool_use":
                        tool_results = []
                        for block in response.content:
                            if block.type != "tool_use":
                                continue
                            print(
                                f"InsightsService tool call: {block.name}"
                                f"({json.dumps(block.input)[:200]})"
                            )
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": insights_grounding.execute_grounding_tool(
                                    block.name, block.input
                                ),
                            })
                        if not tool_results:
                            messages.pop()
                            break
                        messages.append({"role": "user", "content": tool_results})
                    response = self.client.messages.create(
                        **create_kwargs, messages=messages
                    )
                if response.stop_reason == "tool_use":
                    # Tool budget exhausted — answer the dangling calls and
                    # force a final text-only turn.
                    messages.append(
                        {"role": "assistant", "content": response.content}
                    )
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": (
                                    "Tool budget exhausted. Write the final "
                                    "JSON now using the evidence already "
                                    "gathered."
                                ),
                            }
                            for block in response.content
                            if block.type == "tool_use"
                        ],
                    })
                    response = self.client.messages.create(
                        **create_kwargs,
                        messages=messages,
                        tool_choice={"type": "none"},
                    )
            except anthropic.RateLimitError as exc:
                # Rate limit is per-minute; wait long enough for the window
                # to reset before retrying.
                rate_wait = max(backoff, 60.0)
                print(
                    f"InsightsService rate-limited (attempt {attempt}/{max_attempts}), "
                    f"waiting {rate_wait:.0f}s: {exc}"
                )
                if attempt == max_attempts:
                    return None
                time.sleep(rate_wait)
                backoff *= 2
                continue
            except anthropic.APIStatusError as exc:
                # If the model rejects the deprecated `temperature` param, drop
                # it and retry the same attempt without it.
                if (
                    exc.status_code == 400
                    and send_temperature
                    and "temperature" in str(getattr(exc, "message", exc)).lower()
                ):
                    send_temperature = False
                    print("InsightsService: model rejects `temperature`; retrying without it")
                    continue
                # 5xx server errors — retry; other status codes — bail.
                if exc.status_code >= 500 and attempt < max_attempts:
                    print(
                        f"InsightsService {exc.status_code} — retrying in {backoff:.1f}s "
                        f"(attempt {attempt}/{max_attempts})"
                    )
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                print(
                    f"InsightsService API error: {exc.status_code} {exc.message}"
                )
                return None
            except anthropic.APIConnectionError as exc:
                print(
                    f"InsightsService connection error (attempt {attempt}/{max_attempts}): {exc}"
                )
                if attempt == max_attempts:
                    return None
                time.sleep(backoff)
                backoff *= 2
                continue

            # ---- Success path: extract text from the response ----
            # When web search is active the response contains interleaved
            # web_search_tool_result and text blocks, and the model's JSON
            # answer is frequently SPLIT across several text blocks (it emits
            # part of the JSON, runs a search, then continues). Taking only the
            # last block yields a fragment (e.g. starting mid-string), so we
            # concatenate every text block — in order, unstripped — to
            # reconstruct the full answer, then let _parse_json extract the
            # JSON object (it skips any reasoning preamble around it).
            raw = "".join(
                block.text
                for block in response.content
                if block.type == "text"
            ).strip()

            if not raw:
                print(
                    f"InsightsService empty content from Anthropic. "
                    f"stop_reason={response.stop_reason}"
                )
                return None

            parsed = self._parse_json(raw)
            if not parsed:
                truncated = getattr(response, "stop_reason", None) == "max_tokens"
                print(
                    "InsightsService non-JSON content, falling back "
                    f"(stop_reason={getattr(response, 'stop_reason', None)}"
                    f"{', TRUNCATED — raise max_tokens' if truncated else ''}). "
                    f"head={raw[:300]!r}"
                )
                return None

            return {
                "symptoms": str(parsed.get("symptoms", "")).strip(),
                "likely_causes": str(parsed.get("likely_causes", "")).strip(),
                # Persona-targeted actions; high_level_action falls back to whatever
                # an older-style response returned so nothing is silently dropped.
                "action_fpo": str(parsed.get("action_fpo", "")).strip(),
                "action_programme": str(parsed.get("action_programme", "")).strip(),
                "action_donor": str(parsed.get("action_donor", "")).strip(),
                "high_level_action": str(parsed.get("high_level_action", "")).strip(),
            }

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _language_directive(self, language: Optional[str]) -> str:
        if not language:
            return ""
        code = str(language).strip().lower()
        if not code or code == "en":
            return ""
        name = self.language_map.get(code, code)
        return (
            f"\n\nIMPORTANT: Write the JSON values for `symptoms`, `likely_causes`, "
            f"`action_fpo`, `action_programme`, and `action_donor` in {name} ({code}). "
            "The JSON keys must stay in English. Do not translate place names, numbers, "
            "or proper nouns drawn from the DATA CONTEXT (state/district names stay as-is)."
        )

    @staticmethod
    def _format_data_context(ctx: Optional[dict]) -> str:
        """Render the data_context dict as a compact, readable block for the
        LLM. Skips empty sections so the prompt isn't padded with placeholders.
        """
        if not ctx:
            return "(no detailed breakdown available)"
        lines: list[str] = []

        dist = ctx.get("option_distribution") or []
        if dist:
            lines.append("Response distribution (top options chosen):")
            for row in dist:
                tag = " [POOR]" if row.get("is_poor") else ""
                lines.append(
                    f"  - {row['text']}: {row['pct']:.1f}% ({row['count']}){tag}"
                )

        stats = ctx.get("value_stats")
        if stats:
            lines.append("Numeric spread:")
            lines.append(
                f"  - min={stats.get('min')}  p25={stats.get('p25')}  "
                f"median={stats.get('median')}  p75={stats.get('p75')}  "
                f"max={stats.get('max')}"
            )

        states = ctx.get("top_states") or []
        if states:
            lines.append("Hotspot states (highest poor share, min 5 responders):")
            for row in states:
                lines.append(
                    f"  - {row['name']}: {row['pct']:.1f}% poor "
                    f"({row['poor']}/{row['total']})"
                )

        districts = ctx.get("top_districts") or []
        if districts:
            lines.append("Hotspot districts (highest poor share, min 5 responders):")
            for row in districts:
                lines.append(
                    f"  - {row['name']}: {row['pct']:.1f}% poor "
                    f"({row['poor']}/{row['total']})"
                )

        return "\n".join(lines) if lines else "(no detailed breakdown available)"

    @staticmethod
    def _format_empowerment_context(ctx: Optional[dict]) -> str:
        """Render the IndicatorResult-based empowerment summary + geo breakdown
        as a compact block for the LLM.

        This is the headline data — the same numbers that appear on the
        dashboard "Percentage of Empowered Women" card and the empowerment map.
        """
        if not ctx:
            return "(no empowerment data available)"
        lines: list[str] = []

        emp_pct = ctx.get("empowerment_pct")
        empowered = ctx.get("empowered_count", 0)
        disempowered = ctx.get("disempowered_count", 0)
        total = ctx.get("total_count", 0)
        if total:
            dis_pct = round(100.0 - (emp_pct or 0.0), 1)
            lines.append(
                f"Empowered: {empowered} of {total} respondents "
                f"({emp_pct:.1f}%). Disempowered: {disempowered} ({dis_pct}%)."
            )

        states = ctx.get("top_states") or []
        if states:
            lines.append("Lowest-empowerment states (min 5 respondents):")
            for row in states:
                lines.append(
                    f"  - {row['name']}: {row['empowered_pct']:.1f}% empowered "
                    f"({row['empowered']}/{row['total']})"
                )

        districts = ctx.get("top_districts") or []
        if districts:
            lines.append("Lowest-empowerment districts (min 5 respondents):")
            for row in districts:
                lines.append(
                    f"  - {row['name']}: {row['empowered_pct']:.1f}% empowered "
                    f"({row['empowered']}/{row['total']})"
                )

        return "\n".join(lines) if lines else "(no empowerment data available)"

    @staticmethod
    def _parse_json(raw: str) -> Optional[dict]:
        if not raw:
            return None

        # Strip a leading ```json / ``` fence and any trailing fence so the
        # model's preferred markdown wrapping doesn't defeat the parser.
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.lstrip().lower().startswith("json"):
                text = text.lstrip()[4:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Fall back to a balanced-brace scan from the first "{". Unlike a plain
        # rfind("}"), this ignores braces inside string values and any preamble
        # or trailing commentary the model added around the object.
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        return None
        return None

    @staticmethod
    def _fallback_from_guidance(guidance: Optional[dict]) -> Optional[dict]:
        if not guidance:
            return None
        # The playbook only carries a single recommended action; surface it under the
        # implementing-team persona and leave the others empty.
        return {
            "symptoms": guidance.get("symptoms", ""),
            "likely_causes": guidance.get("likely_causes", ""),
            "action_fpo": "",
            "action_programme": guidance.get("action", ""),
            "action_donor": "",
            "high_level_action": "",
        }
