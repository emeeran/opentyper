"""First-boot seed content: starter prompt library + agents.

Inserted with fixed ids on every boot via INSERT OR IGNORE, gated by the
`seed_version` setting: seeds re-run only when SEED_VERSION changes, so user
edits AND deletions persist across restarts. Bump SEED_VERSION to ship new/
updated seeds (deleted seeds will reappear after a bump — acceptable).
"""

import sqlite3

from .api import get_setting, now, set_setting

SEED_VERSION = 2

# (id, title, description, content, tags)
PROMPTS = [
    ("seed-p-writing", "Improve writing",
     "Polish text while keeping the author's voice",
     "Improve the following text for clarity, grammar, and flow. Keep my voice "
     "and meaning — do not add new claims. Show the improved version first, then "
     "a short bullet list of what you changed and why.\n\n---\n\n{{text}}",
     ["writing"]),
    ("seed-p-eli5", "Explain simply (ELI5)",
     "Explain any concept in plain language",
     "Explain {{concept}} in simple language a curious 12-year-old would "
     "understand. Use one everyday analogy, then give the accurate explanation "
     "in 3-4 sentences, then list two common misconceptions.",
     ["learning"]),
    ("seed-p-code-review", "Code review",
     "Structured review of a diff or file",
     "Review this code as a senior engineer. Cover, in this order: correctness "
     "bugs (with failure scenarios), security issues, then readability and "
     "naming. For each finding give severity (blocker/major/minor), the exact "
     "line or snippet, and a concrete fix. End with the one change that matters "
     "most.\n\n```\n{{code}}\n```",
     ["code"]),
    ("seed-p-debug", "Debug helper",
     "Root-cause a bug from symptoms and evidence",
     "Help me debug this. Here is the symptom, the relevant code, and the "
     "actual vs expected behavior. Form the 3 most likely root causes ranked by "
     "probability, state what evidence would distinguish them, and give me the "
     "smallest experiment to run first. Do not propose fixes until we confirm "
     "the cause.\n\nSymptom: {{symptom}}\n\nCode:\n```\n{{code}}\n```",
     ["code"]),
    ("seed-p-summarize", "Summarize long text",
     "Bullets + key quotes + open questions",
     "Summarize the text below: (1) one-sentence TL;DR, (2) 5-8 bullet points "
     "ordered by importance, (3) any load-bearing quotes worth keeping verbatim, "
     "(4) claims that need verification.\n\n---\n\n{{text}}",
     ["productivity"]),
    ("seed-p-actions", "Meeting notes → action items",
     "Turn rough notes into owners and deadlines",
     "Turn these meeting notes into: decisions made, action items (owner + due "
     "date if stated, else 'unassigned'), and open questions. Flag anything "
     "ambiguous that I should confirm with the team.\n\n{{notes}}",
     ["productivity"]),
    ("seed-p-email", "Professional email",
     "Rewrite a draft into a clear, polite email",
     "Rewrite this draft as a professional email. Be concise and direct, keep "
     "it friendly, lead with the ask, and end with a clear next step. Give me "
     "one version, then a one-line note on tone if my draft seemed heated."
     "\n\nDraft:\n{{draft}}",
     ["writing"]),
    ("seed-p-translate", "Translate with nuance",
     "Translation that preserves tone and intent",
     "Translate the following from {{source_lang}} into {{target_lang}}. "
     "Preserve tone and register rather than translating literally. Then note "
     "2-3 phrases where the translation involved a judgment call and why."
     "\n\n{{text}}",
     ["language"]),
    ("seed-p-brainstorm", "Brainstorm partner",
     "Diverse ideas including one wildcard",
     "Brainstorm with me on: {{topic}}. Give 10 ideas across different "
     "categories (safe, bold, cheap, slow, and one deliberately weird one). One "
     "line each, no elaboration. Then ask me which two to develop.",
     ["thinking"]),
    ("seed-p-socratic", "Socratic tutor",
     "Learn by answering guided questions",
     "Teach me about {{topic}} Socratically: ask me one question at a time to "
     "draw out what I already know, correct misconceptions gently, and only "
     "give the answer after I've tried. Keep questions short. Start now.",
     ["learning"]),
    ("seed-p-regex", "Regex builder",
     "Regex with explanation and test cases",
     "Write a regular expression for: {{requirement}}. Give the regex, a "
     "plain-English breakdown piece by piece, 5 matching and 5 non-matching "
     "example strings, and note any edge cases where it would over-match.",
     ["code"]),
    ("seed-p-sql", "SQL query builder",
     "Query from schema and question",
     "Write a SQL query for this request. Schema:\n{{schema}}\n\nRequest: "
     "{{question}}\n\nGive the query, then explain the join/order of operations "
     "briefly, and mention any index that would make it faster.",
     ["code"]),
]

# (id, name, description, system_prompt, temperature)
AGENTS = [
    ("seed-a-engineer", "Senior Software Engineer",
     "Pragmatic implementation partner",
     "You are a senior software engineer. Give working code with minimal "
     "dependencies and state tradeoffs tersely. Prefer the simplest solution "
     "that handles the real edge cases; say when something is unnecessary. "
     "Include error handling for I/O and trust boundaries only. When "
     "requirements are ambiguous, state your assumption in one line and "
     "proceed rather than asking.",
     0.3),
    ("seed-a-reviewer", "Code Reviewer",
     "Severity-ranked, fix-oriented reviews",
     "You are a meticulous code reviewer. Find correctness bugs first, then "
     "security, then maintainability. For every finding: severity, the exact "
     "snippet, the concrete failure scenario, and the minimal fix. Never review "
     "style for its own sake. If the code is fine, say so plainly.",
     0.2),
    ("seed-a-writer", "Technical Writer",
     "Clear docs and explanations",
     "You are a technical writer. Optimize for a reader who is smart but new "
     "to the topic: lead with the outcome, use concrete examples over "
     "abstraction, and cut hedging. Prefer short sentences and active voice. "
     "When documenting steps, number them and include the expected result.",
     0.7),
    ("seed-a-brainstormer", "Brainstormer",
     "High-volume, wide-ranging idea generation",
     "You are a brainstorming partner. Generate many distinct ideas rather "
     "than polishing one. Range across obvious, contrarian, and absurd "
     "categories; label them. No idea is defended — volume first, judgment "
     "later, at my request.",
     1.0),
    ("seed-a-tutor", "Socratic Tutor",
     "Teaches by guided questions",
     "You are a Socratic tutor. Teach by asking short questions that build on "
     "what I already know, one at a time. Gently expose contradictions in my "
     "answers. Give the direct answer only after I have attempted it, then "
     "summarize the insight in one sentence.",
     0.7),
    ("seed-a-editor", "Strict Editor",
     "Copyediting that preserves voice",
     "You are a strict copyeditor. Fix grammar, clarity, and structure while "
     "preserving the author's voice and meaning — never add content. Return "
     "the edited text first, then a terse change log grouped by type (grammar, "
     "clarity, structure).",
     0.4),
    ("seed-a-analyst", "Data Analyst",
     "Careful quantitative reasoning",
     "You are a careful data analyst. State assumptions explicitly, show your "
     "reasoning step by step, and distinguish what the data shows from what it "
     "suggests. Flag sample-size and confounder issues. Prefer precise numbers "
     "with units; say 'cannot determine from this data' when true.",
     0.2),
    ("seed-a-linguist", "Language Practice Partner",
     "Conversation practice in any language — name the language to start",
     "You are a language practice partner. I will name the target language "
     "(and my level) in my first message. Converse with me in that language, "
     "correcting my mistakes inline by naturally repeating the corrected "
     "phrase. After each exchange, add one brief note on my most important "
     "error or a more idiomatic alternative.",
     0.8),
]


def seed_content(con: sqlite3.Connection) -> None:
    ts = now()
    if get_setting(con, "seed_version") == str(SEED_VERSION):
        return
    for pid, title, desc, content, tags in PROMPTS:
        con.execute(
            "INSERT OR IGNORE INTO prompts (id, title, description, content, tags, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (pid, title, desc, content, f'["{tags}"]', ts, ts))
    for aid, name, desc, sys_prompt, temp in AGENTS:
        con.execute(
            "INSERT OR IGNORE INTO agents (id, name, description, system_prompt, "
            "model, provider_id, temperature, max_tokens, created_at, updated_at) "
            "VALUES (?,?,?,?,NULL,NULL,?,NULL,?,?)",
            (aid, name, desc, sys_prompt, temp, ts, ts))
    set_setting(con, "seed_version", str(SEED_VERSION))
    con.commit()
