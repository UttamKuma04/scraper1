import argparse
import os
import smtplib
import time
from datetime import datetime, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY")

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER")  
CANDIDATE_EMAIL = os.getenv("CANDIDATE_EMAIL")  
SMTP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# Supabase Storage Configuration
RESUME_BUCKET = "resume"
RESUME_FILE_KEY = "uttam_software.pdf"


def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_API_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_API_KEY must be set in .env")
    return create_client(SUPABASE_URL, SUPABASE_API_KEY)


def fetch_resume_from_supabase(supabase: Client, bucket: str = RESUME_BUCKET, file_key: str = RESUME_FILE_KEY) -> bytes:
    """Fetch PDF file as raw bytes directly from Supabase Storage."""
    try:
        pdf_bytes = supabase.storage.from_(bucket).download(file_key)
        if not pdf_bytes:
            raise ValueError("Downloaded PDF file is empty.")
        return pdf_bytes
    except Exception as e:
        raise RuntimeError(f"Failed to fetch resume '{file_key}' from Supabase bucket '{bucket}': {e}")


def fetch_jobs_to_process(supabase: Client, limit: int) -> list[dict]:
    """Fetch jobs from Supabase where status is 'new' AND is_relevant is True."""
    response = (
        supabase.table("job_posting")
        .select("id, company, role, contact_email, application_link, status, is_relevant")
        .eq("status", "new")
        .eq("is_relevant", True)  # Strictly pick relevant jobs only
        .limit(limit)
        .execute()
    )
    return response.data or []


def fetch_already_processed_ids(supabase: Client) -> set[int]:
    """Fetch job_posting_id entries from job_applications to prevent re-sending emails."""
    response = (
        supabase.table("job_applications")
        .select("job_posting_id")
        .execute()
    )
    return {row["job_posting_id"] for row in (response.data or []) if row.get("job_posting_id")}


def build_recruiter_email(
    company: str, role: str, receiver_email: str, resume_bytes: bytes, filename: str = RESUME_FILE_KEY
) -> MIMEMultipart:
    """Builds the formal job application email to the recruiter with attached resume from memory."""
    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_USER
    msg["To"] = receiver_email
    msg["Subject"] = f"Application for {role} - Uttam Kumar"

    html_body = f"""
    <html>
    <body>
    <p>Hi,</p>

    <p>
    I'm interested in the <strong>{role}</strong> opportunity at <strong>{company}</strong>.
    </p>

    <p>
    I have hands-on experience building scalable backend applications using Python, Django, Django REST Framework, PostgreSQL, Redis, Docker, and REST APIs. I've developed and deployed two live projects:
    </p>

    <ul>
        <li><a href="https://pingbeat.in" target="_blank"><strong>pingbeat.in</strong></a> – An uptime monitoring & APM platform with JWT authentication, Celery, Redis, real-time monitoring, and scalable REST APIs.</li>
        <li><a href="https://pharamcare.shop" target="_blank"><strong>PharmaCare.shop</strong></a> – An AI-powered medical assistant that leverages Django REST Framework to manage patient records, appointments, prescriptions, and AI-assisted healthcare workflows through secure APIs.</li>
    </ul>

    <p>
    During my backend internship, I also built scalable data pipelines, REST APIs, and CI/CD automation for production systems.
    </p>

    <p>
    I've attached my resume and would appreciate the opportunity to discuss how I can contribute to your team.
    </p>

    <p>Thank you for your time and consideration.</p>

    <p>
    Best regards,<br>
    Uttam Kumar<br>
    +91 9065880852<br>
    <a href="https://github.com/UttamKuma04">GitHub</a> | <a href="https://www.linkedin.com/in/uttam-kumar-ab5b78235/">LinkedIn</a>
    </p>

    </body>
    </html>
    """

    msg.attach(MIMEText(html_body, "html"))

    part = MIMEBase("application", "pdf")
    part.set_payload(resume_bytes)
    encoders.encode_base64(part)
    part.add_header(
        "Content-Disposition",
        f'attachment; filename="{filename}"',
    )
    msg.attach(part)

    return msg


def build_candidate_manual_email(company: str, role: str, app_link: str) -> MIMEMultipart:
    """Builds a manual application alert email to the candidate containing the application link."""
    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_USER
    msg["To"] = CANDIDATE_EMAIL
    msg["Subject"] = f"[Action Required] Apply Manually: {role} at {company}"

    html_body = f"""
    <html>
    <body>
    <h2>Manual Application Alert</h2>
    <p>Hi Uttam,</p>
    <p>A relevant job posting was found that does not have a recruiter email address. You can apply manually via the official link below:</p>

    <ul>
        <li><strong>Company:</strong> {company}</li>
        <li><strong>Role:</strong> {role}</li>
        <li><strong>Application Link:</strong> <a href="{app_link}" target="_blank">{app_link}</a></li>
    </ul>

    <p>Happy applying!</p>
    </body>
    </html>
    """

    msg.attach(MIMEText(html_body, "html"))
    return msg


def send_emails(limit: int, delay: float) -> None:
    if not SMTP_PASSWORD:
        raise ValueError("GMAIL_APP_PASSWORD environment variable is not set.")

    supabase = get_supabase_client()

    already_processed_ids = fetch_already_processed_ids(supabase)
    jobs = fetch_jobs_to_process(supabase, limit)

    jobs_to_process = [j for j in jobs if j["id"] not in already_processed_ids]

    if not jobs_to_process:
        print("No new relevant jobs to process.")
        return

    has_recruiter_jobs = any((j.get("contact_email") or "").strip() for j in jobs_to_process)
    resume_bytes = None

    if has_recruiter_jobs:
        print("Fetching resume PDF from Supabase Storage...")
        resume_bytes = fetch_resume_from_supabase(supabase)

    print(f"Found {len(jobs_to_process)} relevant job(s) to process. Connecting to SMTP...")

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
    except Exception as e:
        print(f"Failed to connect/login to SMTP server: {e}")
        return

    processed_count = 0
    try:
        for idx, job in enumerate(jobs_to_process, start=1):
            job_id = job["id"]
            company = job.get("company") or "Unknown Company"
            role = job.get("role") or "Python Software Developer"
            contact_email = (job.get("contact_email") or "").strip()
            application_link = (job.get("application_link") or "").strip()

            now_utc = datetime.now(timezone.utc).isoformat()

            # --- CASE 1: Recruiter Direct Email ---
            if contact_email and "@" in contact_email:
                print(f"[{idx}/{len(jobs_to_process)}] [RECRUITER] Sending email to '{contact_email}' for '{role}' at {company}...")

                try:
                    msg = build_recruiter_email(company, role, contact_email, resume_bytes)
                    server.sendmail(SMTP_USER, contact_email, msg.as_string())

                    # Log application sent to recruiter
                    supabase.table("job_applications").insert({
                        "job_posting_id": job_id,
                        "email_sent": True,
                        "recipient_type": "recruiter",
                        "sent_at": now_utc,
                    }).execute()

                    # Update status to 'applied'
                    supabase.table("job_posting").update({
                        "status": "applied"
                    }).eq("id", job_id).execute()

                    print(" -> Sent to Recruiter | Status: 'applied' | Logged as 'recruiter'\n")
                    processed_count += 1

                except Exception as e:
                    print(f" -> Failed sending to recruiter ({contact_email}): {e}\n")

            # --- CASE 2: No Recruiter Email -> Send Link to Candidate for Manual Apply ---
            elif application_link:
                print(f"[{idx}/{len(jobs_to_process)}] [CANDIDATE] Sending manual link alert to '{CANDIDATE_EMAIL}' for '{role}' at {company}...")

                try:
                    msg = build_candidate_manual_email(company, role, application_link)
                    server.sendmail(SMTP_USER, CANDIDATE_EMAIL, msg.as_string())

                    # Log manual alert sent to candidate
                    supabase.table("job_applications").insert({
                        "job_posting_id": job_id,
                        "email_sent": True,
                        "recipient_type": "candidate",
                        "sent_at": now_utc,
                    }).execute()

                    # Update status to 'manual_apply'
                    supabase.table("job_posting").update({
                        "status": "manual_apply"
                    }).eq("id", job_id).execute()

                    print(" -> Sent to Candidate | Status: 'manual_apply' | Logged as 'candidate'\n")
                    processed_count += 1

                except Exception as e:
                    print(f" -> Failed sending manual link to candidate: {e}\n")

            else:
                print(f"[{idx}/{len(jobs_to_process)}] Skipping Job ID {job_id}: Neither contact_email nor application_link is present.\n")

            time.sleep(delay)

    finally:
        server.quit()

    print(f"Finished! Successfully processed {processed_count} relevant job application(s).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Process new relevant jobs: Send application email to recruiter or manual link alert to candidate."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximum jobs to process in one execution (default: 1000)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Delay in seconds between emails (default: 3.0)",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    send_emails(limit=args.limit, delay=args.delay)


if __name__ == "__main__":
    main()