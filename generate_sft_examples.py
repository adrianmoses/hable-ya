
async def generate_sft_example(
    teacher_model,      # Claude Opus
    category: str,
    band: str,
    error_type: str,
    theme: dict,
) -> dict:

    prompt = f"""
Generate a single fine-tuning example for Habla, a Spanish language
acquisition voice agent.

Category:   {category}
CEFR band:  {band}
Error type: {error_type}
Theme:      {theme["domain"]} — {theme["prompt"]}

Requirements:
1. Generate 2 prior turns of realistic conversation at {band} level
2. Generate a learner utterance containing a {error_type} error
3. Generate the ideal agent response:
   - 1-3 sentences in Spanish at {band} register
   - Natural recast of the error — correct form appears in response
   - No explicit correction — never name or point to the error
   - Exactly one follow-up question
   - Followed by a correctly formed log_turn tool call

Return JSON matching this schema exactly:
{{
  "prior_turns": [
    {{"role": "assistant", "content": "..."}},
    {{"role": "user",      "content": "..."}}
  ],
  "learner_utterance": "...",
  "error":  {{"type": "...", "example": "...", "correct_form": "..."}},
  "agent_response": "...",
  "tool_call": {{
    "name": "log_turn",
    "arguments": {{...}}
  }}
}}

Bad agent response (explicit correction):
"Dijiste 'mucho verdura' pero es 'muchas verduras'..."

Good agent response (natural recast):
"¿Encontraste muchas verduras frescas? ¡Los mercados aquí son increíbles!"
"""

    raw = await teacher_model.generate(prompt)
    parsed = parse_and_validate(raw)

    # Validate before storing
    assert len(split_sentences(parsed["agent_response"])) <= 3
    assert parsed["error"]["correct_form"].lower() in \
           parsed["agent_response"].lower()
    assert not contains_explicit_correction(parsed["agent_response"])

    return format_as_sft(parsed, band=band, category=category)
