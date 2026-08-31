import argparse
import json
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "jobs.json"
OUTPUT_PATH = ROOT / "jobs.html"


def load_data():
    with DATA_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_data(data):
    DATA_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def check_url(url):
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 JuliaOKelleyJobBoard/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            status = response.getcode()
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


def enrich_verification(data, skip_verify):
    today = datetime.now(timezone.utc).date().isoformat()
    data["updated_at"] = today
    for job in data["jobs"]:
        job["last_checked"] = today
        if skip_verify:
            job.setdefault("status", "Needs manual career-site verification")
        else:
            job["status"] = check_url(job["career_url"])
    return data


def tag_markup(tags):
    return "\n".join(f"<span>{escape(tag)}</span>" for tag in tags)


def job_markup(job):
    tags = tag_markup(job.get("tags", []))
    return f"""
          <article class="job-card">
            <div class="job-card__main">
              <p class="job-company">{escape(job["company"])}</p>
              <h2>{escape(job["title"])}</h2>
              <p class="job-meta">{escape(job["location"])} &middot; {escape(job["posted"])} &middot; {escape(job["salary"])}</p>
              <p>{escape(job["fit"])}</p>
              <div class="job-tags" aria-label="Role tags">
                {tags}
              </div>
            </div>
            <div class="job-card__actions">
              <a class="button primary" href="{escape(job["career_url"])}" target="_blank" rel="noopener noreferrer">Career site</a>
              <a class="button secondary" href="{escape(job["job_board_url"])}" target="_blank" rel="noopener noreferrer">Job board</a>
              <p>{escape(job.get("verification_note", ""))}</p>
              <small>{escape(job.get("status", "Verify on career site"))} &middot; Checked {escape(job.get("last_checked", job.get("posted_date", "")))}</small>
            </div>
          </article>"""


def render(data):
    jobs = "\n".join(job_markup(job) for job in data["jobs"])
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
          Updated {updated_at}. Roles are selected from recent postings, prioritized for remote U.S. or western-U.S. relevance, and include a career-site link for verification before applying.
        </p>
      </section>

      <section class="job-board-layout" aria-label="Matched job listings">
        <aside class="job-board-note">
          <h2>Search Focus</h2>
          <p>{escape(data["candidate"]["target_profile"])}</p>
          <p><strong>Location:</strong> {escape(data["candidate"]["location_preference"])}</p>
          <p><strong>Sources checked:</strong> {escape(sources)}</p>
          <p>The automated daily run refreshes this page at 6 AM MST and re-checks career links where the source allows automated access. Always confirm availability on the company career site.</p>
        </aside>
        <div class="job-list">
{jobs}
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
    args = parser.parse_args()

    data = load_data()
    data = enrich_verification(data, args.skip_verify)
    save_data(data)
    OUTPUT_PATH.write_text(render(data), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
