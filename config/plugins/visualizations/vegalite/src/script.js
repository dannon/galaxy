//
// export default {
//   components: {
// BarChart: VueVega.mapVegaLiteSpec(BarChartSpec)
//   }
// }


import { createApp } from "vue";
import FancyComponent from "./FancyComponent.vue";
// import VueVega from "vue-vega";
// import BarChartSpec from "spec/vega-lite/bar.vl.json";

const slashCleanup = /(\/)+/g;

function prefixedDownloadUrl(root, path) {
    return `${root}/${path}`.replace(slashCleanup, "/");
}

// Create the Vue application and mount it to the element with id 'app'
window.bundleEntries = window.bundleEntries || {};
window.bundleEntries.load = function (options) {
    console.debug("LOADING CHART");
    const dataset = options.dataset;
    const settings = options.chart.settings;
    const explorer = settings.get("explorer");
    const url = window.location.origin + prefixedDownloadUrl(options.root, "/api/datasets/" + dataset.id + "/content");
    // add data-* elements to the target element
    const targetElement = document.getElementById(options.target);
    for (const key in options) {
      if (options.hasOwnProperty(key)) {
        targetElement.setAttribute(`data-${key}`, options[key]);
      }
    }
    const app = createApp(FancyComponent);
    app.mount(targetelement);
    options.process.resolve();
};
