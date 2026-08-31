import argparse
import json
import re
import sys
from datetime import datetime, timezone
from html import escape, unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "jobs.json"
SOURCE_PATH = ROOT / "data" / "job_sources.json"
DISCOVERY_PATH = ROOT / "data" / "job_discovery_report.json"
OUTPUT_PATH = ROOT / "jobs.html"

INACTIVE_STATUSES = {"applied", "closed", "no_longer_available", "not_available"}
DISCOVERY_KEYWORDS = (
    "customer experience",
    "voice of customer",
    "customer insights",
    "brand insights",
    "brand strategy",
    "market research",
    "customer lifecycle",
    "change management",
)


def load_json(path, fallback):
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def fetch(url):
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 JuliaOKelleyJobBoard/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=20) as response:
        return response.getcode(), response.read().decode("utf-8", errors="ignore")


def check_url(url):
    try:
        status, _ = fetch(url)
        if 200 <= status < 400:
            return "Career link reachable"
        return f"Career link returned HTTP {status}"
    except HTTPError as error:
        if error.code in (401, 403):
            return f"Career link blocks automated checks (HTTP {error.code}); verify manually"
        if error.code == 404:
            return "Career link returned HTTP 404; verify before applying"
        return f"Career link returned HTTP {error.code}"
    except URLError as error:
        return f"Automated check failed: {error.reason}"
    except TimeoutError:
        return "Automated check timed out; verify manually"


def normalize_status(job):
    user_status = job.get("application_status", "active").strip().lower()
    check_status = job.get("status", "").lower()
    if user_status in INACTIVE_STATUSES:
        return user_status
    if "404" in check_status or "verify before applying" in check_status:
        return "no_longer_available"
    return "active"


def status_label(status):
    labels = {
        "active": "Active",
        "applied": "Applied",
        "closed": "Closed",
        "no_longer_available": "No longer available",
        "not_available": "No longer available",
    }
    return labels.get(status, status.replace("_", " ").title())


def score_job(job):
    if "match_percent" in job:
        return int(job["match_percent"])
    text = " ".join(
        [
            job.get("title", ""),
            job.get("fit", ""),
            " ".join(job.get("tags", [])),
            job.get("location", ""),
        ]
    ).lower()
    score = 52
    weights = {
        "voice of customer": 14,
        "customer experience": 12,
        "director": 10,
        "brand": 8,
        "market research": 8,
        "customer insights": 8,
        "analytics": 6,
        "change management": 6,
        "remote": 8,
        "strategy": 5,
        "executive": 4,
    }
    for keyword, weight in weights.items():
        if keyword in text:
            score += weight
    return max(45, min(score, 98))


def sort_jobs(jobs):
    def key(job):
        normalized = normalize_status(job)
        inactive_rank = 1 if normalized != "active" else 0
        return (inactive_rank, -score_job(job), job.get("posted_date", ""))

    return sorted(jobs, key=key)


def extract_links(source_name, source_url, html):
    leads = []
    compact_html = re.sub(r"\s+", " ", html)
    link_pattern = r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
    for match in re.finditer(link_pattern, compact_html, flags=re.I):
        href = urljoin(source_url, unescape(match.group(1)))
        label = re.sub(r"<[^>]+>", " ", match.group(2))
        label = re.sub(r"\s+", " ", unescape(label)).strip()
        searchable = f"{label} {href}".lower()
        if not label or not any(keyword in searchable for keyword in DISCOVERY_KEYWORDS):
            continue
        leads.append({"source": source_name, "title": label[:140], "url": href})
    return leads


def discover_new_leads(data, skip_discovery):
    today = datetime.now(timezone.utc).date().isoformat()
    if skip_discovery:
        report = {"updated_at": today, "leads": [], "notes": ["Discovery skipped for this run."]}
        save_json(DISCOVERY_PATH, report)
        return report

    known_urls = {
        job.get("career_url", "").rstrip("/")
        for job in data.get("jobs", [])
    } | {
        job.get("job_board_url", "").rstrip("/")
        for job in data.get("jobs", [])
    }
    sources = load_json(SOURCE_PATH, {"discovery_sources": []}).get("discovery_sources", [])
    leads = []
    notes = []
    seen = set()

    for source in sources:
        try:
            _, html = fetch(source["url"])
            source_leads = extract_links(source["name"], source["url"], html)
        except Exception as error:
            notes.append(f"{source['name']}: {error}")
            continue

        for lead in source_leads:
            normalized = lead["url"].rstrip("/")
            if normalized in known_urls or normalized in seen:
                continue
            seen.add(normalized)
            leads.append(lead)
            if len(leads) >= 20:
                break
        if len(leads) >= 20:
            break

    report = {"updated_at": today, "leads": leads, "notes": notes}
    save_json(DISCOVERY_PATH, report)
    return report


def enrich_verification(data, skip_verify):
    today = datetime.now(timezone.utc).date().isoformat()
    data["updated_at"] = today
    for job in data["jobs"]:
        job["last_checked"] = today
        job["match_percent"] = score_job(job)
        if skip_verify:
            job.setdefault("status", "Needs manual career-site verification")
        else:
            job["status"] = check_url(job["career_url"])
        job["display_status"] = status_label(normalize_status(job))
    data["jobs"] = sort_jobs(data["jobs"])
    return data


def tag_markup(tags):
    return "\n".join(f"<span>{escape(tag)}</span>" for tag in tags)


def job_markup(job):
    tags = tag_markup(job.get("tags", []))
    normalized_status = normalize_status(job)
    status_class = " is-inactive" if normalized_status != "active" else ""
    return f"""
          <article class="job-card{status_class}">
            <div class="job-card__main">
              <div class="job-card__topline">
                <p class="job-company">{escape(job["company"])}</p>
                <span class="match-score">{score_job(job)}% match</span>
              </div>
              <h2>{escape(job["title"])}</h2>
              <p class="job-meta">{escape(job["location"])} &middot; {escape(job["posted"])} &middot; {escape(job["salary"])}</p>
              <p>{escape(job["fit"])}</p>
              <div class="job-tags" aria-label="Role tags">
                {tags}
              </div>
            </div>
            <div class="job-card__actions">
              <span class="job-status">{escape(status_label(normalized_status))}</span>
              <a class="button primary" href="{escape(job["career_url"])}" target="_blank" rel="noopener noreferrer">Career site</a>
              <a class="button secondary" href="{escape(job["job_board_url"])}" target="_blank" rel="noopener noreferrer">Job board</a>
              <p>{escape(job.get("verification_note", ""))}</p>
              <small>{escape(job.get("status", "Verify on career site"))} &middot; Checked {escape(job.get("last_checked", job.get("posted_date", "")))}</small>
            </div>
          </article>"""


def discovery_markup(report):
    leads = report.get("leads", [])
    if not leads:
        return """
          <p>No new unincorporated leads were found in the latest automated source scan.</p>"""
    return "\n".join(
        f"""
          <article class="lead-card">
            <span>{escape(lead["source"])}</span>
            <a href="{escape(lead["url"])}" target="_blank" rel="noopener noreferrer">{escape(lead["title"])}</a>
          </article>"""
        for lead in leads
    )


def render(data, discovery_report):
    jobs = "\n".join(job_markup(job) for job in data["jobs"])
    discovery = discovery_markup(discovery_report)
    sources = ", ".join(data["sources_checked"])
    updated_at = escape(data["updated_at"])
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Julia O'Kelley | Job Board</title>
    <meta name="description" content="Remote and Montana-friendly job board for Julia O'Kelley, matched to customer experience, Voice of Customer, brand strategy, market research, analytics, and change management roles.">
    <link rel="stylesheet" href="styles.css?v=20260831-layout">
  </head>
  <body class="job-board-page">
    <header class="site-header is-scrolled" data-header>
      <a class="brand" href="index.html" aria-label="Julia O'Kelley home">
        <span class="brand-mark">JO</span>
        <span>Julia O'Kelley</span>
      </a>
      <button class="nav-toggle" type="button" data-nav-toggle aria-label="Open navigation" aria-expanded="false">
        <span></span>
        <span></span>
      </button>
      <nav class="site-nav" data-nav>
        <a href="index.html">Portfolio</a>
        <a href="index.html#work">Selected Work</a>
        <a href="index.html#contact">Contact</a>
      </nav>
    </header>

    <main id="top">
      <section class="job-hero">
        <p class="eyebrow">Remote &amp; Montana-Friendly Search</p>
        <h1>Job board matched to Julia's CX, VOC, brand, insights, and change leadership profile.</h1>
        <p>
          Updated {updated_at}. Active roles sort first by resume match. Roles marked applied, closed, or no longer available stay visible but move to the end.
        </p>
      </section>

      <section class="job-board-layout" aria-label="Matched job listings">
        <aside class="job-board-note">
          <h2>Search Focus</h2>
          <p>{escape(data["candidate"]["target_profile"])}</p>
          <p><strong>Location:</strong> {escape(data["candidate"]["location_preference"])}</p>
          <p><strong>Sources checked:</strong> {escape(sources)}</p>
          <p>To move a role down after applying or if it disappears, update <strong>application_status</strong> in <strong>data/jobs.json</strong> to <strong>applied</strong>, <strong>closed</strong>, or <strong>no_longer_available</strong>. The daily run will keep it at the end.</p>
        </aside>
        <div class="job-list">
{jobs}
        </div>
      </section>

      <section class="job-discovery" aria-label="New job leads to review">
        <div>
          <p class="eyebrow">New Leads To Review</p>
          <h2>Job-board results not yet incorporated.</h2>
          <p>Generated from configured source pages during the daily refresh. Review these manually before adding them to the curated board.</p>
        </div>
        <div class="lead-list">
{discovery}
        </div>
      </section>
    </main>

    <footer class="site-footer">
      <span>Julia O'Kelley</span>
      <span>Daily job board refresh scheduled for 6 AM MST</span>
    </footer>

    <script src="script.js"></script>
  </body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Refresh Julia O'Kelley's job board.")
    parser.add_argument("--skip-verify", action="store_true", help="Render without checking career URLs.")
    parser.add_argument("--skip-discovery", action="store_true", help="Render without scanning source pages.")
    args = parser.parse_args()

    data = load_json(DATA_PATH, {"jobs": []})
    data = enrich_verification(data, args.skip_verify)
    discovery_report = discover_new_leads(data, args.skip_discovery)
    save_json(DATA_PATH, data)
    OUTPUT_PATH.write_text(render(data, discovery_report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
