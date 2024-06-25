import * as _ from "underscore";
import igv from "igv";
//var Datasets = window.bundleEntries.chartUtilities.Datasets;

_.extend(window.bundleEntries || {}, {
    load: function (options) {
        const igvDiv = document.createElement("div");

        /*
         * This is a bit of a hack because I want most of the charts framework
         * but without most of the cruft (svg and other stuff) that it expects
         * -- building it out this way to figure out what hoops I have to jump
         *  through and deal with, and then we'll reimplement this as the
         *  re-imagining of the charts framework as vue apps
         *  */

        const viewerContainer = document.getElementById(options.targets[0]).parentNode;
        const viewer = viewerContainer.parentNode;
        while (viewer.firstChild) {
            viewer.removeChild(viewer.firstChild);
        }
        viewer.append(igvDiv);

        // Default genome is based on database of initial dataset, if it's in
        // the hosted list

        console.debug(options);

        const primaryTrack = {
            name: options.dataset.name,
            url: options.dataset.download_url,
            format: options.dataset.extension,
            indexURL: "http://localhost:8080/dataset/get_metadata_file?hda_id=3d2da704d582a2f1&metadata_name=bam_index",
        };

        /*
        if (options.dataset.extension == 'bam') {
            primaryTrack.indexURL = "http://localhost:8080/dataset/get_metadata_file?hda_id=3d2da704d582a2f1&metadata_name=bam_index",
        }
        */
        const IGVOptions = {
            showNavigation: true,
            showRuler: true,
            tracks: [primaryTrack],
        };

        if (options.chart.settings.attributes.reference) {
            // If a custom reference is supplied, always use it.
            console.debug("Using custom reference");
            IGVOptions.reference = {
                fastaURL: options.chart.settings.attributes.reference,
                id: "custom_reference",
                name: "Custom (TODO:DSNAME)",
                indexed: false, // TODO .fai?
            };
        } else {
            if (options.dataset.genome_build != "?") {
                console.debug("DATASET BUILD SET");
                // Dataset has a genome set.  Use it.
                IGVOptions.genome = options.dataset.genome_build;
            } else if (options.chart.settings.attributes.genome) {
                console.debug("CHART BUILD SET");
                // Otherwise defer to what's in the chart config
                IGVOptions.genome = options.chart.settings.attributes.genome;
            } else {
                console.debug("NOTHING SET DEFAULTING TO hg38");
                // No idea what to do here otherwise.
                IGVOptions.genome = "hg38";
            }
        }
        /* 
        sample
        tracks: [
            {
                url: "https://s3.amazonaws.com/igv.org.demo/GBM-TP.seg.gz",
                indexed: false,
                isLog: true,
                name: "Segmented CN",
            },
        ],
        */
        console.debug("CREATING BROWSER WITH OPTIONS: ", IGVOptions);
        igv.createBrowser(igvDiv, IGVOptions).then(function (browser) {
            console.log("Created IGV browser");
            options.process.resolve();
        });
    },
});
