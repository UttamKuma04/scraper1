import argparse
import json
import os
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
from groq import Groq
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# =====================================================================
# CANDIDATE PROFILE & SKILL MATRIX
# =====================================================================
PRIMARY_SKILLS = [
    "Python", "Django", "Django REST Framework", "Flask", "SQL", "PostgreSQL", 
    "MySQL", "Redis", "REST APIs", "Celery", "Docker", "AWS (S3, EC2)", "Linux", 
    "Git", "GitHub", "JWT Authentication", "WebSockets", "Pandas", "Async Processing", 
    "Nginx", "Gunicorn", "Kubernetes", "MongoDB", "SQLite", "CI/CD", "System Design"
]

TARGET_ROLES = [
    "Software Engineer", "Backend Developer", "Python Developer", "Django Developer", 
    "Backend Engineer", "AI Backend Engineer", "Data Engineer", "Platform Engineer", "SDE-1"
]

EXPERIENCE_LEVEL = "Final Year B.Tech Student / Entry Level / Internship / 0-2 Years (Batch 2026/2027)"

EXCLUSIONS = [
    "Non-technical roles", "Sales/Marketing", "Customer Support", "BPO", "HR", 
    "Business Development", "UI/UX-only roles", "Frontend-only roles (React/Angular/Vue without backend)",
    "Senior/Lead/Manager positions requiring 3+ years of experience"
]


def get_clients() -> tuple[Client, Groq]:
    if not SUPABASE_URL or not SUPABASE_API_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_API_KEY must be set in .env")
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY must be set in .env")

    supabase = create_client(SUPABASE_URL, SUPABASE_API_KEY)
    groq = Groq(api_key=GROQ_API_KEY)
    return supabase, groq


def fetch_unclassified_jobs(supabase: Client, limit: int) -> list[dict]:
    """Fetch jobs where is_relevant is NULL."""
    response = (
        supabase.table("job_posting")
        .select("id, company, role, full_description, tech_keywords")
        .is_("is_relevant", "null")
        .limit(limit)
        .execute()
    )
    return response.data or []


def classify_job_with_groq(groq: Groq, job: dict, model: str) -> dict:
    """Uses Groq to classify if a job posting is relevant, prioritizing skill alignment."""
    company = job.get("company") or "Unknown"
    role = job.get("role") or "Unknown"
    description = job.get("full_description") or "No description provided."
    skills = ", ".join(job.get("tech_keywords") or []) or "Not explicitly listed"

    prompt = f"""
You are an expert technical recruiter evaluating job relevance for a candidate.

CANDIDATE SKILLS & PROFILE:
- Core Tech Stack (HIGHEST PRIORITY): {', '.join(PRIMARY_SKILLS)}
- Target Roles: {', '.join(TARGET_ROLES)}
- Experience Level: {EXPERIENCE_LEVEL}
- Strictly Excluded: {', '.join(EXCLUSIONS)}

JOB POSTING DETAILS TO EVALUATE:
- Company: {company}
- Job Title/Role: {role}
- Tech Keywords/Skills Listed: {skills}
- Full Description: {description}

EVALUATION RULES (FOLLOW IN STRICT ORDER):
1. **PRIMARY RULE - SKILL MATCH (MOST IMPORTANT)**:
   - Does the job require or heavily feature candidate's core tech stack (Python, Django, PostgreSQL, Redis, REST APIs, Docker, AWS, Celery, SQL, Data/Backend engineering tools)?
   - If the job tech stack heavily overlaps with the candidate's core skills, mark `is_relevant: true`.
   - If the job relies on completely unrelated technologies (e.g., Java/Spring, .NET/C#, Swift/iOS, Salesforce, SAP, Pure Frontend React/Vue without backend), mark `is_relevant: false`.

2. **SECONDARY RULE - ROLE & EXPERIENCE FIT**:
   - Is it an entry-level / SDE-1 / Internship / 0-2 years role matching target roles?
   - Reject if it requires 3+ years of experience or is a Senior/Lead/Manager role.
   - Reject if it falls under excluded non-technical domains (Sales, HR, BPO, UI/UX only).

Output ONLY a JSON object:
{{
  "is_relevant": true or false,
  "reason": "1-2 sentence explanation focusing primarily on skill match/mismatch."
}}
"""

    response = groq.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a precise technical recruiter. Your primary evaluation criterion is candidate skill match.",
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )

    content = response.choices[0].message.content
    return json.loads(content)


def process_classifications(supabase: Client, groq: Groq, limit: int, delay: float, model: str) -> None:
    jobs = fetch_unclassified_jobs(supabase, limit)

    if not jobs:
        print("No unclassified jobs found in database.")
        return

    print(f"Found {len(jobs)} unclassified jobs. Starting Groq skill-first evaluation...\n")

    classified_count = 0
    for idx, job in enumerate(jobs, start=1):
        job_id = job["id"]
        company = job.get("company")
        role = job.get("role")

        print(f"[{idx}/{len(jobs)}] Evaluating ID {job_id}: {company} - {role}...")

        try:
            result = classify_job_with_groq(groq, job, model=model)
            is_relevant = result.get("is_relevant", False)
            reason = result.get("reason", "No reason provided.")

            # Update Supabase record
            supabase.table("job_posting").update({
                "is_relevant": is_relevant,
                "relevance_reason": reason,
                "classified_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", job_id).execute()

            status_label = "RELEVANT" if is_relevant else "NOT RELEVANT"
            print(f" -> Result: {status_label}")
            print(f" -> Reason: {reason}\n")
            classified_count += 1

        except Exception as e:
            print(f" -> Error classifying job ID {job_id}: {e}\n")

        time.sleep(delay)

    print(f"Classification finished! Successfully updated {classified_count} jobs.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classify unclassified jobs in Supabase using Groq API.")
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of jobs to classify in one run (default: 50)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay in seconds between Groq requests to respect rate limits (default: 0.5)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="openai/gpt-oss-120b",
        help="Groq model to use (default: openai/gpt-oss-120b)",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    supabase, groq = get_clients()
    process_classifications(supabase, groq, limit=args.limit, delay=args.delay, model=args.model)


if __name__ == "__main__":
    main()