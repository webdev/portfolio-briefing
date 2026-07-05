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
