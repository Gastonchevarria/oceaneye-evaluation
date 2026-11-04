/*
 * OceanEye Sentinel 2.0 - Pitch Mode steps. DRAFT example for frontend/app/index.html; not wired in.
 *
 * Shape: same properties as tourSteps entries (stepNumber, title, text, view, highlightSelector,
 * action) plus durationMs. Extensions, all optional:
 *   requires      CSS selector of a planned feature (#1 SAM inspector, #2 arena, #5 browser verify).
 *                 Checked when the step is shown, not at start. If it matches nothing, the `fallback`
 *                 overrides are used.
 *   statsGate     { path, equals }: the step also needs stats.json[path] === equals (a metric object is
 *                 read through its `value`). Stats missing or different -> `fallback`.
 *   fallback      overrides for title / text / textNoStats / highlightSelector / action.
 *   textNoStats   caption without numbers, used when any {key} of `text` is missing in stats.json.
 * stepNumber / title / text / textNoStats are { en, es }; resolvePitchSteps() flattens them to
 * strings so the existing renderTourStep() can render the result unchanged.
 *
 * Numbers: never typed here. `text` holds {path.to.key|format} placeholders resolved from stats.json.
 * A path may end on a number or on a metric object { value, unit, display, n, ... }: the object is read
 * through its `value`. Paths follow specs/stats/stats.schema.json.
 * Formats: g1 (1 decimal, trailing .0 dropped, comma in es), s1 (milliseconds -> seconds, same as g1),
 * invpct (100 / ratio, whole number: a Wonder-over-Spark cost ratio of 4.162 gives 24), n0, runs.
 *
 * Behaviour (reference controller below):
 *   ?pitch=1[&lang=es|en][&step=N]  autostart after init() has loaded vessels and SAR detections
 *   Space   pause / resume          ArrowRight / ArrowLeft   next / previous step
 *   Esc     exit (does not set oceaneye_tour_completed). Keys are ignored while typing in a field and
 *           while Ctrl / Meta / Alt is held.
 *   prefers-reduced-motion: starts paused, scrollIntoView uses behavior 'auto', no auto-advance
 *   until Space is pressed.
 *
 * Event contract for planned features (CustomEvent on document, bubbles):
 *   pitch:sam    {phase:'select'}      #1 selects a chip and draws its mask
 *   pitch:arena  {phase:'accuracy'|'speed'}   #2 plays the replay (labelled REPLAY)
 *   pitch:verify {}                    #5 runs the in-browser Ed25519 / SHA-256 verification
 */

const PITCH_STATS_URL = (typeof location !== 'undefined' && /\/app\/(?:index\.html)?$/i.test(location.pathname))
    ? '../data/stats.json'
    : './data/stats.json';
const PITCH_TOTAL_MS = 90000;

const pitchReduceMotion = () =>
    typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
const pitchScroll = (sel) => {
    const el = document.querySelector(sel);
    if (el) el.scrollIntoView({ behavior: pitchReduceMotion() ? 'auto' : 'smooth', block: 'center' });
};
const pitchEmit = (name, detail = {}) =>
    document.dispatchEvent(new CustomEvent(name, { bubbles: true, detail }));
const pitchOpenSidebar = () => {
    const s = document.getElementById('sidebar');
    if (s && s.classList.contains('collapsed')) s.classList.remove('collapsed');
};
const pitchOpenRightPanel = () => {
    const p = document.getElementById('rightForensicPanel');
    if (p && p.classList.contains('collapsed')) p.classList.remove('collapsed');
};

const pitchStepsSource = [
    {
        stepNumber: { en: 'STEP 1 / 8', es: 'PASO 1 / 8' },
        title: { en: 'Ships that go dark', es: 'Barcos que se apagan' },
        text: {
            en: "Fishing fleets at the edge of Argentina's waters can switch off their AIS transponders and vanish from every tracking map.",
            es: 'Las flotas pesqueras en el borde de las aguas argentinas pueden apagar el AIS y desaparecer de todos los mapas.',
        },
        view: 'live_map',
        highlightSelector: '#map-container',
        durationMs: 8000,
        action: () => {
            pitchOpenSidebar();
            if (typeof map !== 'undefined' && map) {
                map.setView([-45.5, -59.5], 6.5);
                setTimeout(() => map.invalidateSize(), 60);
            }
        },
    },
    {
        stepNumber: { en: 'STEP 2 / 8', es: 'PASO 2 / 8' },
        title: { en: 'Radar plus AIS', es: 'Radar más AIS' },
        text: {
            en: 'OceanEye Sentinel 2.0 is built for Sentinel-1 radar, which sees through cloud and darkness. It checks each detection against AIS and drafts a decision-support dossier for a human to review.',
            es: 'OceanEye Sentinel 2.0 está pensado para el radar Sentinel-1, que ve a través de nubes y oscuridad. Contrasta cada detección con el AIS y redacta un borrador de apoyo a la decisión para revisión humana.',
        },
        view: 'live_map',
        highlightSelector: '.panel-sar-chip-box',
        durationMs: 13000,
        action: () => {
            pitchOpenRightPanel();
            setTimeout(() => pitchScroll('.panel-sar-chip-box'), 100);
        },
    },
    {
        stepNumber: { en: 'STEP 3 / 8', es: 'PASO 3 / 8' },
        title: { en: 'A benchmark scene, not live telemetry', es: 'Una escena benchmark, no telemetría en vivo' },
        text: {
            en: "Today's scene is a synthetic benchmark: {scene.ground_truth_targets} injected targets, {scene.dark_targets} of them dark. Because we injected them, we know the answer.",
            es: 'La escena de hoy es un benchmark sintético: {scene.ground_truth_targets} blancos inyectados, {scene.dark_targets} de ellos oscuros. Como los inyectamos, sabemos la respuesta.',
        },
        textNoStats: {
            en: "Today's scene is a synthetic benchmark with injected targets. Because we injected them, we know the answer.",
            es: 'La escena de hoy es un benchmark sintético con blancos inyectados. Como los inyectamos, sabemos la respuesta.',
        },
        view: 'sar_lab',
        highlightSelector: '#sarLabCanvas',
        durationMs: 9000,
    },
    {
        stepNumber: { en: 'STEP 4 / 8', es: 'PASO 4 / 8' },
        title: { en: 'Four Meta models, four roles', es: 'Cuatro modelos de Meta, cuatro roles' },
        text: {
            en: 'Four Meta models, four roles. Muse Wonder writes the dossier narrative. SAM 3.1 outlines ships on radar chips, scored against ground truth. Muse Image only illustrates. Muse Spark is the alternative we benchmarked.',
            es: 'Cuatro modelos de Meta, cuatro roles. Muse Wonder redacta la narrativa del dossier. SAM 3.1 contornea barcos en recortes de radar, puntuado contra la verdad conocida. Muse Image solo ilustra. Muse Spark es la alternativa que comparamos.',
        },
        view: 'forensic_dossier',
        requires: '#samInspector',
        statsGate: { path: 'sam.real_sar_inspector.status', equals: 'completed' },
        highlightSelector: '#samInspector',
        durationMs: 14000,
        action: () => {
            pitchScroll('#samInspector');
            pitchEmit('pitch:sam', { phase: 'select' });
        },
        fallback: {
            text: {
                en: 'Four Meta models, four roles. Muse Wonder writes the dossier narrative. Muse Image illustrates, and SAM 3.1 segments those illustrations, not radar pixels. Muse Spark is the alternative we benchmarked.',
                es: 'Cuatro modelos de Meta, cuatro roles. Muse Wonder redacta la narrativa del dossier. Muse Image ilustra, y SAM 3.1 segmenta esas ilustraciones, no píxeles de radar. Muse Spark es la alternativa que comparamos.',
            },
            highlightSelector: '#metaApiPanel, #metaSam31Canvas',
            action: () => pitchScroll('#metaApiPanel, #metaSam31Canvas'),
        },
    },
    {
        stepNumber: { en: 'STEP 5 / 8', es: 'PASO 5 / 8' },
        title: { en: 'Measured, not claimed', es: 'Medido, no declarado' },
        text: {
            en: 'Then we measured. {comparison.scenarios} hand-written scenarios; a rule-based check flags any draft number that contradicts the given values. Raw output: Wonder passed {comparison.models.muse_wonder_1_0.raw_valid_count} of {comparison.scenarios}, Spark {comparison.models.muse_spark_1_3.raw_valid_count}. Mean adherence, {comparison.models.muse_wonder_1_0.raw_mean_adherence_percent|g1} versus {comparison.models.muse_spark_1_3.raw_mean_adherence_percent|g1} percent.',
            es: 'Después, medimos. {comparison.scenarios} escenarios escritos a mano; un control por reglas marca todo número del borrador que contradiga los valores dados. En crudo: Wonder pasó {comparison.models.muse_wonder_1_0.raw_valid_count} de {comparison.scenarios}; Spark, {comparison.models.muse_spark_1_3.raw_valid_count}. Adherencia media: {comparison.models.muse_wonder_1_0.raw_mean_adherence_percent|g1} contra {comparison.models.muse_spark_1_3.raw_mean_adherence_percent|g1} por ciento.',
        },
        textNoStats: {
            en: 'Then we measured: hand-written scenarios, raw model output checked against reference values, both models, one run.',
            es: 'Después, medimos: escenarios escritos a mano, salida cruda de los modelos contrastada con valores de referencia, una sola corrida.',
        },
        view: 'forensic_dossier',
        requires: '#replayArena',
        highlightSelector: '#replayArena',
        durationMs: 15000,
        action: () => {
            pitchScroll('#replayArena');
            pitchEmit('pitch:arena', { phase: 'accuracy' });
        },
        fallback: { highlightSelector: null, action: null },
    },
    {
        stepNumber: { en: 'STEP 6 / 8', es: 'PASO 6 / 8' },
        title: { en: 'Speed, cost and what it implies', es: 'Velocidad, costo y qué implica' },
        text: {
            en: "Spark answered in {comparison.models.muse_spark_1_3.latency_mean_ms|s1} seconds to Wonder's {comparison.models.muse_wonder_1_0.latency_mean_ms|s1}, for roughly {comparison.ratios.cost_wonder_over_spark|invpct} percent of the estimated cost. {comparison.runs|runs}, {comparison.scenarios} cases: indicative. That suggests Spark for triage, Wonder for dossiers.",
            es: 'Spark respondió en {comparison.models.muse_spark_1_3.latency_mean_ms|s1} segundos; Wonder, en {comparison.models.muse_wonder_1_0.latency_mean_ms|s1}. Costo estimado de Spark: cerca del {comparison.ratios.cost_wonder_over_spark|invpct} por ciento. {comparison.runs|runs}, {comparison.scenarios} casos: indicativo. Sugiere Spark para triage y Wonder para dossiers.',
        },
        textNoStats: {
            en: 'We also compared the speed and cost of both models. One run: indicative.',
            es: 'También comparamos la velocidad y el costo de ambos modelos. Una corrida: indicativo.',
        },
        view: 'forensic_dossier',
        requires: '#replayArena',
        highlightSelector: '#replayArena',
        durationMs: 11000,
        action: () => {
            pitchScroll('#replayArena');
            pitchEmit('pitch:arena', { phase: 'speed' });
        },
        fallback: { highlightSelector: null, action: null },
    },
    {
        stepNumber: { en: 'STEP 7 / 8', es: 'PASO 7 / 8' },
        title: { en: 'Hash and signature', es: 'Hash y firma' },
        text: {
            en: "Each dossier carries a SHA-256 hash and an Ed25519 signature you can verify in your browser. It's a demo key: it proves the mechanism, not identity.",
            es: 'Cada dossier lleva un hash SHA-256 y una firma Ed25519 que podés verificar en tu navegador. Es una clave de demo: prueba el mecanismo, no la identidad.',
        },
        view: 'forensic_dossier',
        requires: '#btnVerifyInBrowser',
        highlightSelector: '#dossierLayaSigBox',
        durationMs: 11000,
        action: () => {
            pitchScroll('#dossierLayaSigBox');
            setTimeout(() => pitchEmit('pitch:verify'), 2500);
        },
        fallback: {
            text: {
                en: 'Each dossier carries a SHA-256 hash and an Ed25519 signature. The key is a demo key, so this shows the mechanism, not identity.',
                es: 'Cada dossier lleva un hash SHA-256 y una firma Ed25519. La clave es de demo, así que esto muestra el mecanismo, no la identidad.',
            },
            action: () => pitchScroll('#dossierLayaSigBox'),
        },
    },
    {
        stepNumber: { en: 'STEP 8 / 8', es: 'PASO 8 / 8' },
        title: { en: 'Verify it yourself', es: 'Verificalo vos' },
        text: {
            en: "Don't trust this pitch. Every number in it comes from one file, stats.json, in our public repo. Open it, recount it, break it.",
            es: 'No nos creás: verificalo. Cada número de este pitch sale de un solo archivo, stats.json, en nuestro repo público. Abrilo, recontalo, rompelo.',
        },
        view: 'live_map',
        highlightSelector: '#command-header',
        durationMs: 9000,
    },
];

const PITCH_FORMATS = {
    g1: (v, lang) => {
        const s = String(Number(v.toFixed(1)));
        return lang === 'es' ? s.replace('.', ',') : s;
    },
    s1: (v, lang) => PITCH_FORMATS.g1(v / 1000, lang),
    invpct: (v) => (v > 0 ? String(Math.round(100 / v)) : null),
    n0: (v) => String(Math.round(v)),
    runs: (v, lang) =>
        v === 1 ? (lang === 'es' ? 'Una corrida' : 'One run') : `${v} ${lang === 'es' ? 'corridas' : 'runs'}`,
};

function pitchLookup(stats, path) {
    const node = path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), stats);
    return node !== null && typeof node === 'object' && 'value' in node ? node.value : node;
}

function renderPitchText(tpl, stats, lang) {
    const missing = [];
    const text = tpl.replace(/\{([\w.]+)(?:\|(\w+))?\}/g, (_, path, fmt) => {
        const v = stats ? pitchLookup(stats, path) : undefined;
        const f = fmt ? PITCH_FORMATS[fmt] : String;
        const out = typeof v === 'number' && Number.isFinite(v) && f ? f(v, lang) : null;
        if (out === null) {
            missing.push(fmt && !PITCH_FORMATS[fmt] ? `${path}|${fmt}` : path);
            return '';
        }
        return out;
    });
    return { text: missing.length ? null : text, missing };
}

function resolvePitchStep(s, stats, lang, doc) {
    const d = doc || document;
    const gateOk = !s.statsGate || (stats != null && pitchLookup(stats, s.statsGate.path) === s.statsGate.equals);
    const useFallback = Boolean((s.requires && !d.querySelector(s.requires)) || !gateOk);
    const eff = useFallback ? { ...s, ...s.fallback } : s;
    const rendered = renderPitchText(eff.text[lang], stats, lang);
    if (rendered.missing.length) console.warn('pitch: missing stats keys', rendered.missing);
    return {
        stepNumber: eff.stepNumber[lang],
        title: eff.title[lang],
        text: rendered.text ?? (eff.textNoStats ? eff.textNoStats[lang] : ''),
        view: eff.view,
        highlightSelector: eff.highlightSelector,
        action: eff.action,
        durationMs: eff.durationMs,
        usedFallback: useFallback,
    };
}

function resolvePitchSteps(source, stats, lang, doc) {
    return source.map((s) => resolvePitchStep(s, stats, lang, doc));
}

function pitchTotalMs(steps) {
    return steps.reduce((t, s) => t + s.durationMs, 0);
}

function createPitchClock({ steps, onStep, onEnd, setT = setTimeout, clearT = clearTimeout, now = Date.now }) {
    let idx = -1;
    let timer = null;
    let remaining = 0;
    let startedAt = 0;
    let paused = true;
    let stopped = false;

    const arm = (ms) => {
        remaining = ms;
        startedAt = now();
        timer = setT(advance, ms);
    };
    const show = (i) => {
        clearT(timer);
        idx = i;
        onStep(i, steps[i]);
        if (paused) remaining = steps[i].durationMs;
        else arm(steps[i].durationMs);
    };
    function advance() {
        if (stopped) return;
        if (idx + 1 < steps.length) show(idx + 1);
        else {
            clearT(timer);
            paused = true;
            onEnd();
        }
    }
    const pause = () => {
        if (paused) return;
        paused = true;
        clearT(timer);
        remaining -= now() - startedAt;
    };
    const resume = () => {
        if (!paused || stopped) return;
        paused = false;
        arm(Math.max(0, remaining));
    };
    return {
        start: (i = 0, { startPaused = false } = {}) => {
            stopped = false;
            paused = startPaused;
            show(i);
        },
        stop: () => {
            stopped = true;
            paused = true;
            clearT(timer);
        },
        pause,
        resume,
        toggle: () => (paused ? resume() : pause()),
        next: advance,
        prev: () => !stopped && idx > 0 && show(idx - 1),
        isPaused: () => paused,
        index: () => idx,
    };
}

function pickPitchLang(query, navLang) {
    const q = query.get('lang');
    if (q === 'es' || q === 'en') return q;
    return String(navLang || '').toLowerCase().startsWith('es') ? 'es' : 'en';
}

const PITCH_UI = {
    en: { prev: '◀ Back', next: 'Next ▶', last: 'Finish ✓', skip: 'Exit ✕' },
    es: { prev: '◀ Anterior', next: 'Siguiente ▶', last: 'Finalizar ✓', skip: 'Salir ✕' },
};

async function startPitchMode({ lang = 'en', startAt = 0 } = {}) {
    if (typeof window !== 'undefined' && typeof window._activePitchExit === 'function') {
        window._activePitchExit();
    }
    let stats = null;
    try {
        let r = await fetch(PITCH_STATS_URL, { cache: 'no-store' }).catch(() => null);
        if (!r || !r.ok) r = await fetch('../data/stats.json', { cache: 'no-store' }).catch(() => null);
        if (!r || !r.ok) r = await fetch('./data/stats.json', { cache: 'no-store' }).catch(() => null);
        if (!r || !r.ok) throw new Error(`HTTP ${r ? r.status : 'offline'}`);
        stats = await r.json();
    } catch (e) {
        console.warn('pitch: stats.json unavailable, using captions without numbers', e);
    }
    if (pitchTotalMs(pitchStepsSource) !== PITCH_TOTAL_MS) console.warn('pitch: durations do not sum to 90 s');

    const saved = { steps: tourSteps.slice(), next: window.nextTourStep, prev: window.prevTourStep, close: window.closeTour };
    const slots = pitchStepsSource.map((s) => ({ durationMs: s.durationMs }));
    tourSteps.splice(0, tourSteps.length, ...slots);

    const modal = document.getElementById('oceaneyeTourModal');
    const backdrop = document.getElementById('oceaneyeTourBackdrop');
    const dots = document.getElementById('tourDotsContainer');
    const prevBtn = document.getElementById('tourPrevBtn');
    const nextBtn = document.getElementById('tourNextBtn');
    const skipBtn = modal.querySelector('.tour-footer > div:last-child > button:last-child');
    const dotsHtml = dots.innerHTML;
    const savedLabels = { prev: prevBtn.textContent, skip: skipBtn ? skipBtn.textContent : '' };
    const ui = PITCH_UI[lang] || PITCH_UI.en;
    dots.innerHTML = slots.map(() => '<span class="tour-dot"></span>').join('');
    backdrop.style.cssText = 'display:block;background:rgba(3,6,12,0.35);backdrop-filter:none;pointer-events:none';
    modal.style.display = 'flex';

    const paintBadge = () => {
        const badge = document.getElementById('tourStepBadge');
        if (badge) badge.textContent = tourSteps[clock.index()].stepNumber + (clock.isPaused() ? ' | PAUSED' : '');
    };
    const clock = createPitchClock({
        steps: slots,
        onStep: (i) => {
            tourSteps[i] = resolvePitchStep(pitchStepsSource[i], stats, lang);
            currentTourStep = i;
            renderTourStep(i);
            modal.classList.add('tour-modal-bottom');
            prevBtn.textContent = ui.prev;
            nextBtn.textContent = i === slots.length - 1 ? ui.last : ui.next;
            if (skipBtn) skipBtn.textContent = ui.skip;
            paintBadge();
        },
        onEnd: () => exitPitch(),
    });

    function exitPitch() {
        clock.stop();
        window.removeEventListener('keydown', onKey, true);
        if (typeof window !== 'undefined') window._activePitchExit = null;
        tourSteps.splice(0, tourSteps.length, ...saved.steps);
        window.nextTourStep = saved.next;
        window.prevTourStep = saved.prev;
        window.closeTour = saved.close;
        document.querySelectorAll('.tour-highlighted').forEach((el) => el.classList.remove('tour-highlighted'));
        modal.style.display = 'none';
        modal.classList.remove('tour-modal-bottom');
        backdrop.style.cssText = '';
        dots.innerHTML = dotsHtml;
        prevBtn.textContent = savedLabels.prev;
        if (skipBtn) skipBtn.textContent = savedLabels.skip;
    }
    if (typeof window !== 'undefined') window._activePitchExit = exitPitch;
    function onKey(e) {
        if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
        if (e.target && e.target.closest && e.target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) return;
        const act = { ' ': clock.toggle, ArrowRight: clock.next, ArrowLeft: clock.prev, Escape: exitPitch }[e.key];
        if (!act) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        act();
        if (e.key === ' ') paintBadge();
    }
    window.addEventListener('keydown', onKey, true);
    window.nextTourStep = clock.next;
    window.prevTourStep = clock.prev;
    window.closeTour = exitPitch;
    clock.start(startAt, { startPaused: pitchReduceMotion() });
}

function startPitch90s() {
    const q = new URLSearchParams(location.search);
    const step = Number.parseInt(q.get('step') || '1', 10);
    return startPitchMode({
        lang: pickPitchLang(q, navigator.language),
        startAt: Number.isFinite(step) ? Math.min(Math.max(step - 1, 0), pitchStepsSource.length - 1) : 0,
    });
}

function maybeAutoStartPitch() {
    const q = new URLSearchParams(location.search);
    if (q.get('pitch') !== '1') return false;
    startPitch90s();
    return true;
}

if (typeof window !== 'undefined') {
    window.startPitch90s = startPitch90s;
    window.startMetaPitch90s = startPitch90s;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        pitchStepsSource, PITCH_TOTAL_MS, PITCH_FORMATS, PITCH_UI,
        pitchLookup, renderPitchText, resolvePitchStep, resolvePitchSteps, pitchTotalMs, createPitchClock, pickPitchLang,
    };
}
