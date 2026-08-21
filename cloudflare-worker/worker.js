// Cloudflare Worker: receives {page_id, task} from the public Notion embed page
// and forwards it as a repository_dispatch event to GitHub Actions.
//
// The GitHub token lives only in this Worker's encrypted secret storage
// (env.GITHUB_TOKEN) — it is never sent to or visible from the browser.
//
// Deploy: see ../CLOUDFLARE_SETUP.md

const REPO = "Kaate3/notion-routine";
const ALLOWED_TASKS = { month: "notion-month-setup", report: "notion-week-report" };

export default {
  async fetch(request, env) {
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: cors });
    }

    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405, headers: cors });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response("Invalid JSON", { status: 400, headers: cors });
    }

    const { page_id, task } = body || {};
    const eventType = ALLOWED_TASKS[task];
    if (!page_id || !eventType) {
      return new Response("Missing or invalid page_id/task", { status: 400, headers: cors });
    }

    const ghRes = await fetch(`https://api.github.com/repos/${REPO}/dispatches`, {
      method: "POST",
      headers: {
        "Accept": "application/vnd.github+json",
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "notion-routine-worker",
      },
      body: JSON.stringify({ event_type: eventType, client_payload: { page_id } }),
    });

    return new Response(null, { status: ghRes.status, headers: cors });
  },
};
