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
    /* Dark, whatever the browser prefers. The shop is watched from a screen
       of its own beside this one, and two consoles in a demo that disagree
       about their ground look like two unrelated tools. */
    color-scheme: dark;
    /* The page's own ground, written down rather than left to the `Canvas`
       system colour. `Canvas` reads as transparent wherever a browser has not
       resolved it against a painted ancestor, and a sticky table heading that
       is transparent has rows scrolling straight through the words on it. */
    --ground: #0f0f11;
    --teal: #2a9d8f;
    --deep: #123a3d;
    --gold: #e9c46a;
    --bad:  #c0392b;
    --good: #27865a;
    --amber: #d08214;
    --line: rgba(42,157,143,.28);
  }
  body { font: 14px/1.55 ui-sans-serif, system-ui, sans-serif; margin: 0;
         padding: 0 24px 48px; max-width: 1100px; background: var(--ground); }

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
  /* A disabled button still matches :hover, so without this the one control
     that is barred lights up solid teal under the pointer - the strongest
     "press me" the page has - and reads as the only thing worth clicking. The
     cursor and the dimming say the same thing three ways, because this is the
     click that would restage an incident somebody is in the middle of. */
  button:disabled { opacity: .45; cursor: not-allowed; }
  button:disabled:hover { background: transparent; color: inherit; }

  input[type=url] { font: inherit; padding: 7px 9px; border-radius: 7px;
                    width: 24em; border: 1px solid rgba(128,128,128,.45);
                    background: transparent; color: inherit; }
  .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .pill { display: inline-block; padding: 3px 11px; border-radius: 999px;
          font-size: 12px; border: 1px solid currentColor; }
  /* Red where the flag currently sits in the position that breaks the shop.
     Not "this flag is on": one of these rests on and one rests off, so
     red-means-on was an accusation against whichever flag happened to be a
     feature flag, and drew the fallback in the all-clear colour at the very
     moment it was breaking the shop.

     One colour, not two. A flag that is breaking nothing gets no colour,
     because there is nothing to say about it - and green would be an all-clear
     the page is not entitled to give: a scenario with no flag in it at all
     would have shown a row of green badges over a shop that is on fire.
     Whether the *shop* is well is the metrics' answer, and it is right below.

     A decoy is therefore never red, in either position. That is the honest
     reading and it is the scenario's whole point: a flag really did move, and
     moving it back changes nothing. */
  .breaking { color: var(--bad); }
  .idle { opacity: .55; }
  /* Nor is anything claimed when the provider could not be read at all. */
  .unknown { opacity: .55; font-style: italic; }
  .note { opacity: .6; font-size: 12px; }

  /* Fixed layout, so the columns are placed by the header rather than by the
     widest cell under them. Auto layout re-measures on every render, and the
     minute a marker appears in the first cell every number in the table steps
     sideways - a whole panel moving to report one row's news. Fixed also means
     a marker longer than its column simply runs past it, changing nothing.

     Borders separate rather than collapsed, and that is not cosmetic: with
     collapsed borders the sticky heading's own background is painted with the
     table's border layer rather than above the cells, so rows scroll straight
     through the headings whatever stacking order the heading is given. Zero
     spacing keeps the collapsed look. */
  table { border-collapse: separate; border-spacing: 0; width: 100%;
          table-layout: fixed; font-variant-numeric: tabular-nums; }
  th, td { text-align: right; padding: 4px 10px;
           border-bottom: 1px solid rgba(128,128,128,.13); }
  /* Quieter by colour, never by opacity. `opacity` fades an element together
     with its background, so a heading dimmed that way is a heading the rows
     scroll visibly through - which no stacking order can fix, because the
     heading is genuinely translucent. */
  th { font-weight: 500; color: rgba(200,205,210,.62); font-size: 12px; }
  /* Wide enough for a timestamp and for the markers that sit under it; the
     four numeric columns divide what is left. */
  th:first-child, td:first-child { text-align: left; width: 38%;
                                   white-space: nowrap; }
  .bad { color: var(--bad); font-weight: 600; }

  /* While a scenario is live the staging controls recede and Apply is barred:
     the audience should be able to see at a glance that something is in
     progress, and nobody should end it by reaching for the next scenario.

     The *controls* recede - the scenario list and the two buttons, nothing
     else. Everything beside them in that row is a reading rather than a
     control: which flags are on, which scenario is running, whether the alert
     was accepted. Those are the things a watcher is there to read, and they are
     never more worth reading than while something is in progress. So is the
     legend, which says why the controls are unavailable.

     Named rather than excluded, because opacity composites: a dimmed row cannot
     have a child restored to full strength, so the only way to keep a badge
     legible is to never dim what contains it.

     The buttons are not named here at all - a button dims by being disabled,
     just above. Reset is not disabled while a scenario runs and must not look
     as though it were: stopping a run is what it is *for*, and the middle of
     one is when somebody most needs it. */
  fieldset.busy #scenarios { opacity: .45; }
  /* The receding is the announcement; this is the answer to a click on one of
     the things that receded. Without it a disabled radio still shows a pointer
     and reads as merely decorative. */
  fieldset.busy .scenario { cursor: not-allowed; }
  fieldset.busy legend::after { content: 'in progress'; margin-left: 10px;
                                padding: 3px 10px; border-radius: 999px;
                                background: var(--bad); color: #fff;
                                font-weight: 600; letter-spacing: .12em;
                                animation: throb 1.6s ease-in-out infinite; }
  /* Motion, because a still badge on a page that repaints every two seconds
     reads as part of the furniture. */
  @keyframes throb { 50% { opacity: .5; } }
  @media (prefers-reduced-motion: reduce) {
    fieldset.busy legend::after { animation: none; }
  }

  /* Two different facts, two different lines.

     The action is the minute the flag moved. It is deliberately not green:
     that minute is half broken and half fixed - it carries the errors from
     before the toggle - and a green line above a red number reads as a promise
     the row underneath it breaks.

     Its wording says which way the flag went, and the page is told rather than
     assuming: this shop stages incidents in both directions, and half its
     scenarios end by switching a flag *on*. Written into the row by the script
     for that reason, where the recovery marker - one fact, one wording - can
     stay in the stylesheet. */
  tr.acted td { border-top: 2px solid var(--teal); }
  /* One line per action, under the minute rather than beside it. A minute in
     which two flags moved - which is exactly the minute worth reading - ran its
     markers on into the error rate and covered the number they were about. */
  td:first-child .marker { display: block; font-size: 11px; }
  .marker.tried { color: var(--teal); }
  /* Amber, because putting a flag back is neither the fault nor the fix: it is
     an attempt withdrawn. Green would claim a recovery and red would blame the
     agent for the ordinary case of having been wrong once. */
  .marker.undone { color: var(--amber); }

  /* Recovery is the first whole minute the shop looked well again, which is
     the claim green is entitled to make. */
  tr.recovered td { border-top: 2px solid var(--good); }
  tr.recovered td:first-child::after { content: ' - recovered';
                                       color: var(--good); font-size: 11px; }
  .scroll { max-height: 380px; overflow: auto;
            border: 1px solid rgba(128,128,128,.22); border-radius: 10px; }
  /* The metrics header stays put while the window is scrolled back through -
     a column of numbers whose headings have scrolled away is unreadable.

     Opaque, and that is the whole requirement: a translucent heading lets
     every row scroll through it, and a heading with a timestamp printed across
     it is less readable than no heading at all. A written-down colour rather
     than `Canvas`, which is only as opaque as whatever a browser resolves it
     against - the page declares one theme, so it can state its own ground.

     `z-index` because a sticky heading is not automatically above what scrolls
     past it: without a stacking order the rows are painted over the top of it,
     which looks like a transparency bug and is not one. */
  thead th { position: sticky; top: 0; z-index: 3; background: var(--ground);
             box-shadow: 0 1px 0 var(--line); }
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
    <span id="flags"></span>
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
// Asks the shop's monitoring to fire, which is as far as a page gets to be
// involved: the webhook itself goes server to server, from the monitoring stack
// to whatever is watching. A browser posting it would be sending an alert from
// the one place a real alert never comes from.
const ALERT_ENDPOINT = '/monitoring/alert';
// Anything at or above this reads as an incident rather than as the noise a
// healthy service always makes. Presentation only - nothing decides anything
// on it.
const ELEVATED_ERROR_RATE = 0.05;

// The phases during which the shop is mid-scenario. Apply is barred and the
// staging panel recedes; Reset stays live, because it is the way out of a
// scenario that nothing else is going to end.
const IN_PROGRESS = ['running', 'recovering'];
// The phase in which the window has stopped advancing. Its last bucket is the
// last one there will ever be, which is what makes it readable as a whole.
const COMPLETE = 'complete';

let chosen = null;
let actions = [];
let windowIsFrozen = false;
// The last catalog polled, kept so a click on a scenario can redraw its badges
// without waiting for the next one.
let lastCatalog = null;

async function json(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

// Badges for the flags the *selected* scenario puts in play, and nothing else.
//
// Before a selection there is nothing to say, so nothing is shown: the shop has
// two flags and most scenarios use one, and a badge for a flag that is never
// going to move invites an audience to watch the wrong thing.
//
// The position comes from the shop's live reading and the meaning from the
// scenario, which is the only way round that works: the same flag is the fault
// in one scenario and a bystander in the next, so "what does ON mean here" is a
// question no flag can answer about itself.
function renderScenarioFlags(catalog) {
  const selected = document.querySelector('input[name="scenario"]:checked');
  const scenario = selected
    ? catalog.scenarios.find(entry => entry.id === selected.value)
    : null;
  const position = {};
  catalog.flags.forEach(flag => { position[flag.name] = flag.is_on; });

  replaceIfChanged(document.getElementById('flags'), !scenario ? '' : scenario.flags
    .map(flag => flagBadge(flag, position[flag.name]))
    .join(' '));
}

// ON and OFF in capitals, because colour no longer carries them. It says
// whether the shop is broken *by this flag being where it is*, which leaves the
// position itself with nothing but the word to announce it - and the position
// is the thing a watcher checks at a glance while an agent is working.
function flagBadge(flag, isOn) {
  if (isOn === null || isOn === undefined) {
    return '<span class="pill unknown" title="the flag provider could not be ' +
           'read">' + flag.name + ' UNKNOWN</span>';
  }

  const breaking = flag.breaks_when_on !== null && isOn === flag.breaks_when_on;

  return '<span class="pill ' + (breaking ? 'breaking' : 'idle') + '" title="' +
         (breaking
           ? 'the shop is broken while this flag sits here'
           : flag.breaks_when_on === null
             ? 'in this scenario no position of this flag breaks the shop'
             : 'this flag is not where it breaks the shop') +
         '">' + flag.name + (isOn ? ' ON' : ' OFF') + '</span>';
}

function renderCatalog(catalog) {
  lastCatalog = catalog;
  renderScenarioFlags(catalog);

  const phase = catalog.phase;
  const busy = IN_PROGRESS.includes(phase);
  actions = catalog.actions;
  windowIsFrozen = phase === COMPLETE;

  document.getElementById('staging').classList.toggle('busy', busy);
  document.getElementById('apply').disabled = busy;

  setTextIfChanged(
    document.getElementById('active'),
    catalog.active_scenario ? catalog.active_scenario + ' - ' + phase : 'nothing staged'
  );

  const host = document.getElementById('scenarios');

  if (!host.dataset.rendered) {
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
    host.addEventListener('change', event => {
      chosen = event.target.value;
      showTheSelectedScenariosFlags();
    });
  }

  // The radios follow Apply rather than sitting live beside it. A control that
  // answers a click and then changes nothing is worse than one that refuses:
  // picking a different scenario mid-run looks like it staged something, and
  // the next thing the watcher reads is telemetry from the scenario they think
  // they just left. Re-applied on every refresh, not only at render, because
  // the panel is built once and the phase changes underneath it.
  for (const radio of host.querySelectorAll('input[name="scenario"]')) {
    radio.disabled = busy;
  }
}

// Selecting a scenario redraws its badges at once rather than at the next poll.
// Two seconds is short, but it is long enough for a click to feel unanswered,
// and this is the one control whose whole job is to say what it is about to do.
function showTheSelectedScenariosFlags() {
  if (lastCatalog !== null) renderScenarioFlags(lastCatalog);
}

// The first whole minute after the action in which the shop looked well again.
// Read off the numbers rather than asked of the service, because that is what
// it is - a judgement about what the metrics show, made from the same threshold
// that colours them. The minute of the action itself is never it: it carries
// the errors from before the toggle, and is the reason the two marks are
// separate at all.
//
// *Whole* minute is the operative word, and the last bucket is never one while
// the scenario is live - it covers only the seconds of the current minute that
// have happened, so seconds after a revert it reads near zero and would claim a
// recovery the shop has not yet demonstrated.
//
// Once the window freezes that objection is gone. The last bucket stops being
// the minute in progress and becomes the last minute there is: it lies wholly
// after the revert, and it is sampled at the same fixed number of requests as
// every row above it. Short in wall-clock time, but not short of evidence - and
// excluding it would mean a settling period of one minute, which is what the
// demo runs, never showing the recovery it exists to show. It also lines this
// mark up with the panel clearing, which waits on the same event.
// Measured from the *last* action, not the first. An agent working an
// ambiguous incident changes a flag, finds the shop still broken, puts it back
// and tries another - so a recovery counted from the first move would mark the
// minutes after a failed attempt as a recovery that had not happened.
function firstRecoveredMinute(buckets) {
  if (!actions.length) return null;
  const lastActionAt = actions[actions.length - 1].at;
  const completed = windowIsFrozen ? buckets : buckets.slice(0, -1);
  const found = completed.find(bucket => bucket.bucket_id > lastActionAt &&
                                         bucket.error_rate < ELEVATED_ERROR_RATE);
  return found ? found.bucket_id : null;
}

// Whether a change puts a flag back rather than tries something with it.
//
// Nobody tells this page why a flag moved - the reasoning is Argus's, and this
// is the shop's screen - but the reason is not needed to tell these two apart.
// A change that returns a flag to the value it held before this incident's
// earlier change to the same flag is an undo, and that is readable from the
// list of changes alone. It is what "it tried that, and it did not help" looks
// like from outside, and it is the half of the story a single wording hid.
function isPutBack(action, index) {
  const earlier = actions.slice(0, index).filter(other => other.flag === action.flag);
  return earlier.length > 0 && earlier[earlier.length - 1].enabled !== action.enabled;
}

// What was done in that minute, one line per change. Both directions are real
// here: a feature flag is put back by switching it off, a withdrawn fallback by
// switching it back on - and the flag is named, because when two of them moved,
// "a flag" is the one thing a reader cannot work out.
function actionMarkers(minute) {
  return actions
    .map((action, index) => ({action: action, putBack: isPutBack(action, index)}))
    .filter(entry => entry.action.at === minute)
    .map(entry =>
      '<span class="marker ' + (entry.putBack ? 'undone' : 'tried') + '">' +
      (entry.putBack ? 'put back' : 'action taken') + ' - ' + entry.action.flag +
      ' ' + (entry.action.enabled ? 'on' : 'off') + '</span>')
    .join('');
}

function renderMetrics(buckets) {
  // Every bucket, not a recent slice. The window is what an investigation
  // reads, so it is what an audience should be able to scroll back through -
  // the calm minutes before the incident are half of what makes the incident
  // legible.
  const recoveredAt = firstRecoveredMinute(buckets);

  replaceIfChanged(document.getElementById('metrics'), buckets
    .map(bucket => {
      const rate = (100 * bucket.error_rate).toFixed(1) + '%';
      const cell = bucket.error_rate >= ELEVATED_ERROR_RATE
        ? '<td class="bad">' + rate + '</td>' : '<td>' + rate + '</td>';
      const notes = actionMarkers(bucket.bucket_id);
      const marker =
        notes ? ' class="acted"' :
        bucket.bucket_id === recoveredAt ? ' class="recovered"' : '';
      return '<tr' + marker + '><td>' + bucket.bucket_id + notes + '</td>' + cell +
             '<td>' + bucket.p50_ms + '</td><td>' + bucket.p95_ms + '</td>' +
             '<td>' + bucket.request_volume + '</td></tr>';
    })
    .join(''));
}

// Writing markup that is already on screen costs a repaint and buys nothing.
// Everything here is polled every couple of seconds, and most of what comes
// back is identical - a finished scenario's window never changes again, and a
// live one changes in its last row - so the page would otherwise flicker
// steadily while standing still.
function replaceIfChanged(element, markup) {
  if (element.innerHTML === markup) return;
  element.innerHTML = markup;
}

function setTextIfChanged(element, text) {
  if (element.textContent === text) return;
  element.textContent = text;
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
      setTextIfChanged(
        document.getElementById('logs'),
        lines.join('\\n') || '(nothing staged - press Apply to stage one)'
      );
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
      annotations: {summary: 'Error rate above threshold for 5m'},
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
