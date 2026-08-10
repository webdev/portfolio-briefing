// Minimal client-side JS — Plotly chart bootstrapper.
// Pages that render a chart include a div with class="chart-target"
// and a data-chart-url attribute. We fetch the JSON spec and render.

(function () {
    function renderChart(node) {
        const url = node.getAttribute("data-chart-url");
        if (!url) return;
        fetch(url)
            .then(r => r.json())
            .then(spec => {
                Plotly.newPlot(node, spec.data || [], spec.layout || {}, {
                    responsive: true,
                    displaylogo: false,
                    modeBarButtonsToRemove: [
                        "lasso2d", "select2d", "toggleSpikelines",
                        "hoverClosestCartesian", "hoverCompareCartesian"
                    ],
                });
            })
            .catch(err => {
                node.innerHTML = "<div class='empty'>Chart failed to load: " + err + "</div>";
            });
    }

    function bootCharts() {
        document.querySelectorAll(".chart-target[data-chart-url]").forEach(renderChart);
    }

    // Plotly is loaded with `defer`, so it might not be ready when DOMContentLoaded fires.
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", whenPlotlyReady);
    } else {
        whenPlotlyReady();
    }

    function whenPlotlyReady() {
        if (typeof Plotly !== "undefined") {
            bootCharts();
            return;
        }
        const id = setInterval(() => {
            if (typeof Plotly !== "undefined") {
                clearInterval(id);
                bootCharts();
            }
        }, 50);
    }
})();

// ─── RSI zone filter bars (George 2026-08-10) ─────────────────────────
// "I want to see what is within the CSP range and what is within the
// covered call range, so I can clearly see it at a glance."
//
// Each `.rsi-filter-bar` (macro _macros/rsi_filter.html) filters the cards
// matching its data-rsi-cards selector WITHIN its parent element, keyed off
// the data-rsi-zones attribute the backend stamps on every card (zones are
// non-exclusive tags — computed server-side in app/rsi_zones.py; this JS
// only consumes them, never re-derives bands). Multi-select toggling;
// "All" resets; selection persists in localStorage per data-rsi-key.
// Cards with zone 'unknown' (no RSI) are visible only when no filter is
// active. Fail-silent throughout.
(function () {
    function initRsiFilterBar(bar) {
        var scope = bar.parentElement || document;
        var sel = bar.getAttribute("data-rsi-cards") || "[data-rsi-zones]";
        var storageKey = "rsiFilter:" + (bar.getAttribute("data-rsi-key") || window.location.pathname);

        function cards() {
            return Array.prototype.slice.call(scope.querySelectorAll(sel));
        }
        function zonesOf(card) {
            return (card.getAttribute("data-rsi-zones") || "unknown").split(/\s+/);
        }

        var active = [];  // empty = All (no filtering)
        try {
            var saved = JSON.parse(localStorage.getItem(storageKey) || "[]");
            if (Array.isArray(saved)) active = saved.filter(function (z) { return typeof z === "string"; });
        } catch (e) { active = []; }

        function refreshCounts() {
            var all = cards();
            bar.querySelectorAll(".rsi-filter-chip").forEach(function (btn) {
                var zone = btn.getAttribute("data-zone");
                var n = zone === "all"
                    ? all.length
                    : all.filter(function (c) { return zonesOf(c).indexOf(zone) !== -1; }).length;
                var span = btn.querySelector(".rsi-count");
                if (span) span.textContent = String(n);
            });
        }

        function apply() {
            cards().forEach(function (c) {
                if (!active.length) { c.style.display = ""; return; }
                var zs = zonesOf(c);
                var show = active.some(function (z) { return zs.indexOf(z) !== -1; });
                c.style.display = show ? "" : "none";
            });
            // Report pages: hide sections/hero left empty by the filter
            // (mirrors the existing filterCards behavior on report_view).
            scope.querySelectorAll(".rep-section, .rep-actionable-hero").forEach(function (sec) {
                var visible = sec.querySelectorAll('.rep-card:not([style*="none"])').length;
                sec.style.display = visible === 0 ? "none" : "";
            });
            bar.querySelectorAll(".rsi-filter-chip").forEach(function (btn) {
                var zone = btn.getAttribute("data-zone");
                var on = zone === "all" ? active.length === 0 : active.indexOf(zone) !== -1;
                btn.classList.toggle("uc-sort-active", on);
                btn.setAttribute("aria-pressed", on ? "true" : "false");
            });
            try { localStorage.setItem(storageKey, JSON.stringify(active)); } catch (e) { /* private mode */ }
        }

        bar.addEventListener("click", function (ev) {
            var btn = ev.target && ev.target.closest ? ev.target.closest(".rsi-filter-chip") : null;
            if (!btn) return;
            var zone = btn.getAttribute("data-zone");
            if (zone === "all") {
                active = [];
            } else {
                var i = active.indexOf(zone);
                if (i === -1) active.push(zone); else active.splice(i, 1);
            }
            apply();
        });

        refreshCounts();
        apply();
    }

    function bootRsiFilters() {
        document.querySelectorAll(".rsi-filter-bar").forEach(function (bar) {
            try { initRsiFilterBar(bar); } catch (e) { /* fail silent */ }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bootRsiFilters);
    } else {
        bootRsiFilters();
    }
})();
