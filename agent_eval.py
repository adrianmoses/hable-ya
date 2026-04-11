

SYNTHETIC_LEARNER_PROMPT = """
You are simulating a Spanish L2 learner at production_level {level:.2f} ({band}).

Your job is to respond to the agent's Spanish utterance as this learner would —
naturally, with the errors and hesitations characteristic of this level.

Learner profile:
- Known error patterns: {error_patterns}
- L1 reliance: {L1_reliance:.2f}  (0=never uses English, 1=always)
- Vocabulary strengths: {vocab_strengths}

Behavior rules by level:
  A1: Single words or very short phrases. Frequent English fallback.
      Present tense only. Long pauses (represented as "...").
  A2: Short sentences. Occasional English words when stuck.
      Preterite attempts with errors. Simple connectors.
  B1: Full sentences with grammar errors. Rare English fallback.
      Attempts subordinate clauses. Noticeable hesitation patterns.
  B2: Mostly fluent. Subtle errors (ser/estar, subjunctive avoidance).
      Almost no English. Occasional register slips.
  C1: Near-fluent. Only idiomatic errors or minor slips.
      No English. Confident production.

Inject these specific errors with probability {error_probability:.2f}:
{error_patterns}

Return ONLY the learner's utterance — no explanation, no metadata.
Do not produce perfect Spanish unless level > 0.85.
"""


SESSION_JUDGE_PROMPT = """
You are evaluating a Spanish language acquisition agent called Habla.
Below is a complete session transcript between Habla and a simulated learner.

Learner true level: {true_band} (production_level: {true_level})
Session theme: {theme}

Transcript:
{transcript}

Score each dimension from 0.0 to 1.0 and give a one-sentence justification:

1. pedagogical_flow
   Did the agent maintain conversational momentum while still teaching?
   Did it avoid over-correcting or under-correcting?

2. register_consistency
   Did the agent's language stay appropriate for this learner's level
   throughout the session, including after L1 utterances?

3. recast_naturalness
   When recasts occurred, did they feel like natural conversation
   or like thinly disguised corrections?

4. learner_production_space
   Did the agent leave enough space for the learner to produce?
   Did it avoid dominating the conversation?

5. session_coherence
   Did the session feel like a natural themed conversation,
   or like a series of disconnected exercises?

Return JSON only:
{{
  "pedagogical_flow":       {{"score": 0.0, "reason": "..."}},
  "register_consistency":   {{"score": 0.0, "reason": "..."}},
  "recast_naturalness":     {{"score": 0.0, "reason": "..."}},
  "learner_production_space": {{"score": 0.0, "reason": "..."}},
  "session_coherence":      {{"score": 0.0, "reason": "..."}},
  "overall":                {{"score": 0.0, "reason": "..."}}
}}
"""

async def run_agent_eval(
    habla_model,            # Gemma 4 checkpoint being evaluated
    opus_client,            # Anthropic client for simulator + judge
    n_sessions: int = 20,   # number of simulated sessions
):
    results = []

    for _ in range(n_sessions):
        # Sample a synthetic learner profile
        learner = sample_synthetic_learner()

        # Run a simulated session
        transcript, db_state = await run_simulated_session(
            agent=habla_model,
            simulator=opus_client,
            learner=learner,
            turns=10,
        )

        # Deterministic checks — no Opus needed
        deterministic = {
            "error_pattern_recall": compute_recall(
                injected=learner["error_patterns"],
                captured=db_state["error_patterns"],
            ),
            "learner_production_ratio": compute_production_ratio(transcript),
            "session_closed_cleanly":   db_state["session_closed"],
            "active_vocab_growth":      len(db_state["active_vocab_added"]),
            "level_estimate_error":     abs(
                db_state["final_production_level"] - learner["true_level"]
            ),
        }

        # Opus judge — session-level qualitative scores
        judgment = await opus_judge(
            client=opus_client,
            transcript=transcript,
            learner=learner,
        )

        results.append({
            "learner":       learner,
            "deterministic": deterministic,
            "judgment":      judgment,
        })

    return aggregate_agent_eval(results)
