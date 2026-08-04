import argparse
import json
import os
import time
from dotenv import load_dotenv
import requests
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY") 

BASE_URL = "https://api.getsig.in/api/v2/jobs/recent"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}


def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_API_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_API_KEY must be set in your .env file.")
    return create_client(SUPABASE_URL, SUPABASE_API_KEY)


def fetch_all_jobs(limit: int, delay: float, timeout: int) -> list[dict]:
    cursor = None
    all_jobs = []
    seen_ids = set()

    with requests.Session() as session:
        session.headers.update(DEFAULT_HEADERS)

        while True:
            params = {"limit": limit}
            if cursor:
                params["cursor"] = cursor

            response = session.get(BASE_URL, params=params, timeout=timeout)
            response.raise_for_status()
            data = response.json()

            jobs = data.get("items", [])
            if not jobs:
                print("No more jobs returned by API.")
                break

            new_jobs = 0
            for job in jobs:
                job_id = job.get("id")
                unique_key = job_id if job_id is not None else json.dumps(job, sort_keys=True)

                if unique_key not in seen_ids:
                    seen_ids.add(unique_key)
                    all_jobs.append(job)
                    new_jobs += 1

            print(f"Fetched {len(jobs)} | New: {new_jobs} | Total: {len(all_jobs)}")

            next_cursor = data.get("next_cursor")
            if not next_cursor or next_cursor == cursor:
                print("Pagination finished.")
                break

            cursor = next_cursor
            time.sleep(delay)

    return all_jobs


def transform_job_for_supabase(job: dict) -> dict:
    """Map API JSON fields to Supabase PostgreSQL table column names."""
    return {
        "company": job.get("company") or "Unknown Company",
        "role": job.get("role") or "Unknown Role",
        "full_description": job.get("fullDescription"),
        "tech_keywords": job.get("techKeywords") if isinstance(job.get("techKeywords"), list) else [],
        "application_link": job.get("applicationLink"),
        "contact_email": job.get("contactEmail"),
        "contact_phone": job.get("contactPhone"),
        "stipend": job.get("stipend"),
        "location": job.get("location"),
    }


def normalize_str(val: str) -> str:
    return (val or "").strip().lower()


def save_jobs_to_supabase(supabase: Client, jobs: list[dict], batch_size: int = 100) -> None:
    if not jobs:
        print("No jobs fetched from API.")
        return

    print("Fetching existing jobs from Supabase...")
    existing_response = (
        supabase.table("job_posting")
        .select("company, role, application_link")
        .execute()
    )

    existing_links = set()
    existing_comp_roles = set()

    for row in (existing_response.data or []):
        link = normalize_str(row.get("application_link"))
        comp = normalize_str(row.get("company"))
        role = normalize_str(row.get("role"))

        if link:
            existing_links.add(link)
        if comp and role:
            existing_comp_roles.add((comp, role))

    print(f"Found {len(existing_links)} existing links and {len(existing_comp_roles)} existing company-role pairs in DB.")

    new_records = []
    for job in jobs:
        record = transform_job_for_supabase(job)
        
        link = normalize_str(record.get("application_link"))
        comp = normalize_str(record.get("company"))
        role = normalize_str(record.get("role"))

        if link and link in existing_links:
            continue

        if (comp, role) in existing_comp_roles:
            continue

        if link:
            existing_links.add(link)
        if comp and role:
            existing_comp_roles.add((comp, role))

        new_records.append(record)

    if not new_records:
        print("No new jobs to insert. Database is completely up to date.")
        return

    print(f"Inserting {len(new_records)} brand new jobs into Supabase...")

    total_inserted = 0
    for i in range(0, len(new_records), batch_size):
        batch = new_records[i : i + batch_size]
        
        response = (
            supabase.table("job_posting")
            .upsert(batch, on_conflict="application_link", ignore_duplicates=True)
            .execute()
        )

        inserted_count = len(response.data) if response.data else 0
        total_inserted += inserted_count
        print(f"Processed batch {i // batch_size + 1} ({inserted_count} new records added).")

    print(f"Successfully finished! Total new jobs processed: {total_inserted}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch recent jobs from GetSig API and safely insert into Supabase with duplicate protection."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Records per API request (default: 50)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="Delay in seconds between paginated API requests (default: 0.2)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for saving rows to Supabase (default: 100)",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    supabase = get_supabase_client()
    jobs = fetch_all_jobs(limit=args.limit, delay=args.delay, timeout=args.timeout)
    save_jobs_to_supabase(supabase, jobs, batch_size=args.batch_size)


if __name__ == "__main__":
    main()