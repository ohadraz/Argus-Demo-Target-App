from __future__ import annotations

"""Io's operator console: one page, no build step, no framework.

What a Grafana or Kibana user would have in front of them - the shop's own
metrics and log lines - plus the scenario controls a demo needs. Argus's side of
the story is told on Argus's own screen; nothing here narrates what Argus is
doing, because this page is the shop's view and the shop does not know it is
being watched.

Applying a scenario also fires the alert, from the browser. That is both fewer
things to explain and a truer picture: a real shop's monitoring notices and
raises the alert with nobody pressing anything. The alert endpoint is a constant
in this page's script and appears nowhere in the shop's Python - a fixture
configured with the address of the tool observing it would not be much of a
fixture.
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Io - beauty & fragrance</title>
<link rel="icon" type="image/svg+xml" href="/assets/favicon.svg">
<style>
  :root {
    color-scheme: light dark;
    --teal: #2a9d8f;
    --deep: #123a3d;
    --gold: #e9c46a;
    --bad:  #c0392b;
    --good: #27865a;
    --line: rgba(42,157,143,.28);
  }
  body { font: 14px/1.55 ui-sans-serif, system-ui, sans-serif; margin: 0;
         padding: 0 24px 48px; max-width: 1100px; }

  header { display: flex; align-items: center; gap: 22px; padding: 18px 0 22px;
           border-bottom: 1px solid var(--line); margin-bottom: 24px; }
  header img { width: 120px; height: auto; flex: none; }
  h1 { font-size: 30px; margin: 0; letter-spacing: .16em; font-weight: 500;
       text-transform: uppercase; }
  h1 small { display: block; font-size: 11px; letter-spacing: .3em;
             opacity: .65; margin-top: 6px; font-weight: 400; }
  .sub { margin: 12px 0 34px; opacity: .7; max-width: 46em; }

  h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .12em;
       opacity: .55; margin: 30px 0 8px; }
  fieldset { border: 1px solid var(--line); border-radius: 10px;
             padding: 14px 18px; margin: 0 0 18px; }
  legend { padding: 0 8px; opacity: .6; font-size: 12px;
           text-transform: uppercase; letter-spacing: .1em; }

  label.scenario { display: block; padding: 10px 0;
                   border-bottom: 1px solid rgba(128,128,128,.14); }
  label.scenario:last-child { border-bottom: 0; }
  label.scenario .desc { display: block; margin-left: 24px; opacity: .72;
                         margin-top: 3px; }

  button { font: inherit; padding: 8px 16px; border-radius: 7px; cursor: pointer;
           border: 1px solid var(--teal); background: transparent; color: inherit; }
  button:hover { background: var(--teal); color: #fff; }
  button.ghost { border-color: rgba(128,128,128,.5); }
  button.ghost:hover { background: rgba(128,128,128,.18); color: inherit; }

  input[type=url] { font: inherit; padding: 7px 9px; border-radius: 7px;
                    width: 24em; border: 1px solid rgba(128,128,128,.45);
                    background: transparent; color: inherit; }
  .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .pill { display: inline-block; padding: 3px 11px; border-radius: 999px;
          font-size: 12px; border: 1px solid currentColor; }
  .on  { color: var(--bad); }
  .off { color: var(--good); }
  .note { opacity: .6; font-size: 12px; }

  table { border-collapse: collapse; width: 100%;
          font-variant-numeric: tabular-nums; }
  th, td { text-align: right; padding: 4px 10px;
           border-bottom: 1px solid rgba(128,128,128,.13); }
  th { font-weight: 500; opacity: .6; font-size: 12px; }
  th:first-child, td:first-child { text-align: left; }
  .bad { color: var(--bad); font-weight: 600; }

  /* While a scenario is live the staging controls recede and Apply is barred:
     the audience should be able to see at a glance that something is in
     progress, and nobody should end it by reaching for the next scenario. */
  fieldset.busy { opacity: .55; }
  fieldset.busy legend::after { content: ' - in progress';
                                color: var(--bad); opacity: .9; }

  /* The minute the flag went off. Everything below this line is the shop
     recovering. */
  tr.recovered td { border-top: 2px solid var(--good); }
  tr.recovered td:first-child::after { content: ' - flag off';
                                       color: var(--good); font-size: 11px; }
  .scroll { max-height: 380px; overflow: auto;
            border: 1px solid rgba(128,128,128,.22); border-radius: 10px; }
  /* The metrics header stays put while the window is scrolled back through -
     a column of numbers whose headings have scrolled away is unreadable. */
  thead th { position: sticky; top: 0; backdrop-filter: blur(6px);
             background: rgba(127,127,127,.10); }
  pre { margin: 0; padding: 11px 13px; font: 12px/1.65 ui-monospace, monospace;
        white-space: pre-wrap; }
</style>
</head>
<body>

<header>
  <img src="/assets/io.svg" alt="">
  <div>
    <h1>Io<small>beauty &amp; fragrance</small></h1>
  </div>
</header>

<p class="sub">
  An imaginary online store. It sells perfume and cosmetics, and it is the
  system Argus watches.
</p>

<fieldset id="staging">
  <legend>Stage an incident</legend>
  <div id="scenarios"></div>
  <div class="row" style="margin-top:14px">
    <button id="apply">Apply scenario</button>
    <button id="reset" class="ghost">Reset</button>
    <span id="flag" class="pill">…</span>
    <span id="active" class="note"></span>
    <span id="fired" class="note"></span>
  </div>
</fieldset>

<h2>Metrics <span class="note">- per minute, newest last</span></h2>
<div class="scroll" id="metrics-scroll">
  <table>
    <thead><tr><th>minute</th><th>error rate</th><th>p50 ms</th><th>p95 ms</th><th>requests</th></tr></thead>
    <tbody id="metrics"></tbody>
  </table>
</div>

<h2>Logs</h2>
<div class="scroll" id="logs-scroll"><pre id="logs"></pre></div>

<script>
const POLL_MS = 2000;
// Where the shop's monitoring sends its alerts. Held by the page rather than by
// the service, so the shop's own code carries no reference to the tool watching
// it - and posted from the browser for the same reason.
const ALERT_ENDPOINT = 'http://localhost:8000/webhooks/alerts';
// Anything at or above this reads as an incident rather than as the noise a
// healthy service always makes. Presentation only - nothing decides anything
// on it.
const ELEVATED_ERROR_RATE = 0.05;

// The phases during which the shop is mid-scenario. Apply is barred and the
// staging panel recedes; Reset stays live, because it is the way out of a
// scenario that nothing else is going to end.
const IN_PROGRESS = ['running', 'recovering'];

let chosen = null;
let recoveredAt = null;

async function json(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function renderCatalog(catalog) {
  const flag = document.getElementById('flag');
  flag.textContent = catalog.flag + (catalog.flag_is_on ? ' is ON' : ' is off');
  flag.className = 'pill ' + (catalog.flag_is_on ? 'on' : 'off');

  const phase = catalog.phase;
  const busy = IN_PROGRESS.includes(phase);
  recoveredAt = catalog.recovered_at;

  document.getElementById('staging').classList.toggle('busy', busy);
  document.getElementById('apply').disabled = busy;

  document.getElementById('active').textContent =
    catalog.active_scenario ? catalog.active_scenario + ' - ' + phase : 'nothing staged';

  const host = document.getElementById('scenarios');
  if (host.dataset.rendered) return;
  host.dataset.rendered = '1';

  for (const scenario of catalog.scenarios) {
    const label = document.createElement('label');
    label.className = 'scenario';
    label.innerHTML =
      '<input type="radio" name="scenario" value="' + scenario.id + '">' +
      '<strong>' + scenario.title + '</strong>' +
      '<span class="desc">' + scenario.description + '</span>';
    host.appendChild(label);
  }
  host.addEventListener('change', event => { chosen = event.target.value; });
}

function renderMetrics(buckets) {
  // Every bucket, not a recent slice. The window is what an investigation
  // reads, so it is what an audience should be able to scroll back through -
  // the calm minutes before the incident are half of what makes the incident
  // legible.
  document.getElementById('metrics').innerHTML = buckets
    .map(bucket => {
      const rate = (100 * bucket.error_rate).toFixed(1) + '%';
      const cell = bucket.error_rate >= ELEVATED_ERROR_RATE
        ? '<td class="bad">' + rate + '</td>' : '<td>' + rate + '</td>';
      const marker = bucket.bucket_id === recoveredAt ? ' class="recovered"' : '';
      return '<tr' + marker + '><td>' + bucket.bucket_id + '</td>' + cell +
             '<td>' + bucket.p50_ms + '</td><td>' + bucket.p95_ms + '</td>' +
             '<td>' + bucket.request_volume + '</td></tr>';
    })
    .join('');
}

// How close to the bottom counts as "following the newest minute". Anyone who
// has scrolled back to examine an earlier minute is left where they are - a
// panel that yanks itself to the bottom every two seconds cannot be read.
const FOLLOWING_TOLERANCE_PX = 40;

function isFollowing(element) {
  const distanceFromBottom =
    element.scrollHeight - element.scrollTop - element.clientHeight;
  return distanceFromBottom <= FOLLOWING_TOLERANCE_PX;
}

function keepFollowing(element, render) {
  const wasFollowing = isFollowing(element);
  render();
  if (wasFollowing) element.scrollTop = element.scrollHeight;
}

async function refresh() {
  try {
    renderCatalog(await json('/scenario/catalog'));
    const buckets = await json('/metrics');
    keepFollowing(document.getElementById('metrics-scroll'),
                  () => renderMetrics(buckets));
    const lines = await json('/logs');
    keepFollowing(document.getElementById('logs-scroll'), () => {
      document.getElementById('logs').textContent =
        lines.join('\\n') || '(nothing staged - press Apply to stage one)';
    });
  } catch (error) {
    document.getElementById('logs').textContent = 'refresh failed: ' + error.message;
  }
}

async function raiseAlert() {
  const note = document.getElementById('fired');
  // Shaped like a Grafana alertmanager webhook, because that is what Argus's
  // own parser expects - the demo does not get a friendlier payload than a real
  // alerting stack would send.
  const alert = {
    status: 'firing',
    alerts: [{
      status: 'firing',
      labels: {
        alertname: chosen === 'bad-deployment' ? 'HighLatency' : 'HighErrorRate',
        service: 'io-shop',
        severity: 'critical',
      },
      annotations: {summary: 'error rate above threshold for 5m'},
      startsAt: new Date().toISOString(),
    }],
  };
  try {
    const response = await fetch(ALERT_ENDPOINT, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(alert),
    });
    const body = await response.json();
    note.textContent = response.ok
      ? 'alert raised - incident ' + (body.incident_id || '?')
      : 'alert rejected: ' + response.status;
  } catch (error) {
    // The shop's incident does not depend on anyone watching it. Staging
    // succeeded; only the delivery failed, and saying so is more useful than
    // failing the whole action.
    note.textContent = 'staged, but Argus is not reachable';
  }
}

document.getElementById('apply').onclick = async () => {
  if (!chosen) { alert('Pick a scenario first.'); return; }
  document.getElementById('fired').textContent = '';
  await json('/scenario/seed', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({scenario_id: chosen}),
  });
  await refresh();
  await raiseAlert();
};

document.getElementById('reset').onclick = async () => {
  await json('/scenario/reset', {method: 'POST'});
  document.getElementById('fired').textContent = '';
  refresh();
};

refresh();
setInterval(refresh, POLL_MS);
</script>
</body>
</html>
"""
