import * as ol from 'ol';
import * as source from 'ol/source';
import * as format from 'ol/format';
import * as interaction from 'ol/interaction';
import * as style from 'ol/style';
import * as layer from 'ol/layer';
import * as control from 'ol/control';
import * as View from 'ol/View';
import * as Map from 'ol/Map';
import {fromLonLat} from 'ol/proj'
import * as Graticule from 'ol/Graticule';


import "../node_modules/ol/ol.css";


var MapViewer = (function(mv) {

    /** Create the map view */
    mv.setMap = (vSource, target) => {
        
        console.log(vSource);
        console.log(target);
        
        let tile = new layer.Tile({source: new source.OSM()});
        console.log(tile);
        
        // add scale to the map
        let scaleLineControl = new control.ScaleLine();
        console.log(scaleLineControl);

        // create vector with styles
        let vectorLayer = new layer.Vector({
            source: vSource
        });
        console.log(vectorLayer);
        
        let view = new View({
            center: fromLonLat([12.5, 41.9]),
            zoom: 6
        });
        console.log(view);
        
        // create map view
        let map = new Map({
            layers: [tile, vectorLayer],
            target: target,
            view: view
        });
        console.log(map);
    };
    
    
    
    /** Load the map GeoJson and Shapefiles*/
    mv.loadFile = (filePath, fileType, target) => {
        if (fileType === 'geojson') {
            let formatType = new format.GeoJSON();
            let sourceVec = new source.Vector({format: formatType, url: filePath});
            mv.setMap(sourceVec, target);
        }
        else if (fileType === 'shp') {
            loadshp({url: filePath, encoding: 'utf-8', EPSG: 4326}, geoJson => {
                let URL = window.URL || window.webkitURL || window.mozURL;
		let url = URL.createObjectURL(new Blob([JSON.stringify(geoJson)], {type: "application/json"}));
		mv.setMap(new source.Vector({format: new format.GeoJSON(), url: url}), target);
            });
        }
    };
    return mv;
}(MapViewer || {}));

_.extend(window.bundleEntries || {}, {
    load: function(options) {
        var self = this,
        chart    = options.chart,
        dataset  = options.dataset,
        settings = options.chart.settings;
        $.ajax({
            url     : dataset.download_url,
            success : function( content ) {
                //console.log(options);
                //console.log(content);
                MapViewer.loadFile(dataset.download_url, dataset.extension, options.targets[0]);
                chart.state( 'ok', 'Chart drawn.' );
                options.process.resolve();  
            },
            error: function() {
                chart.state( 'failed', 'Failed to access dataset.' );
                options.process.resolve();
            }
        });
    }
});
