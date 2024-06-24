import React from "react";

// import { observer } from 'mobx-react'
// import { createRoot } from "react-dom/client";
import { MSAView, MSAModel } from 'react-msaview'

// import { ThemeProvider } from '@mui/material/styles'
// import { createJBrowseTheme } from '@jbrowse/core/ui/theme'

function App(props) {
  const theme = createJBrowseTheme()

//   const model = MSAModel.create({ id: `${Math.random()}`, type: 'MsaView' })

  // can pass msaFilehandle and treeFilehandle if you want to point at a URL of a MSA/tree
  //
  const model = MSAModel.create({
    id: `${Math.random()}`,
    type: "MsaView",
    msaFilehandle: {uri: props.dataset}
  });
  //
  // or pass a string of an msa/tree directly to the "data" field if not pointing to a URL
  //
  // const model = MSAModel.create({
  //   id: `${Math.random()}`,
  //   type: "MsaView",
  //   data: {msa:/*string of msa here */}
  // });

  // choose MSA width, calculate width of div/rendering area if needed beforehand
  model.setWidth(1800)

  return (`
    <ThemeProvider theme={theme}>
      <div style={{ border: '1px solid black', margin: 20 }}>
        <MSAView model={model} />
      </div>
    </ThemeProvider>`
  )
}

/* This will be part of the charts/viz standard lib in 23.1 */
const slashCleanup = /(\/)+/g;
function prefixedDownloadUrl(root, path) {
    return `${root}/${path}`.replace(slashCleanup, "/");
}

Object.assign(window.bundleEntries || {}, {
    load: function (options) {
        const chart = options.chart;
        const dataset = options.dataset;
        const settings = chart.settings;
        const msaViz = msa({
            el: $("#" + options.target),
            vis: {
                conserv: "true" == settings.get("conserv"),
                overviewbox: "true" == settings.get("overviewbox"),
            },
            menu: "small",
            bootstrapMenu: "true" == settings.get("menu"),
        });
        msaViz.u.file.importURL(prefixedDownloadUrl(options.root, dataset.download_url), () => {
            msaViz.render();
            chart.state("ok", "Chart drawn.");
            options.process.resolve();
        });
    },
});
