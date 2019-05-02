
var MapViewer = (function(mv) {

    mv.setInteractions = function(map, source) {
    
        var typeSelect = document.getElementById('geometry-type');
        var draw;

        var addInteraction = () => {
            let value = typeSelect.value;
            if (value !== 'None') {
                draw = new ol.interaction.Draw({
                    source: source,
                    type: typeSelect.value,
                    freehand: true
                });
                map.addInteraction(draw);
            }
        };

        typeSelect.onchange = () => {
           map.removeInteraction(draw);
           addInteraction();
        };

        addInteraction();
    };
    
    mv.exportMap = function(map) {
        document.getElementById('export-png').addEventListener('click', e => {
            map.once('rendercomplete', event => {
                let canvas = event.context.canvas;
                let fileName = Math.random().toString(11).replace('0.', '');
                fileName += '.png';
                if (navigator.msSaveBlob) {
                    navigator.msSaveBlob(canvas.msToBlob(), fileName);
                } else {
                    canvas.toBlob(blob => {
                        saveAs(blob, fileName);
                    });
                }
            });
            map.renderSync();
        });
    };
    
    mv.loadFile = function(filePath) {

        var tile = new ol.layer.Tile({source: new ol.source.OSM()});
        var fullScreen = new ol.control.FullScreen();     
        
        var styles = {
            'Polygon': new ol.style.Style({
                stroke: new ol.style.Stroke({
                    color: 'red',
                    width: 1
                }),
                fill: new ol.style.Fill({
                    color: 'rgba(0, 0, 255, 0.1)'
                })
            }),
            'Circle': new ol.style.Style({
                stroke: new ol.style.Stroke({
                    color: 'red',
                    width: 1
                }),
                fill: new ol.style.Fill({
                    color: 'rgba(0, 0, 255, 0.1)'
                })
            }),
            'Point': new ol.style.Style({
                image: new ol.style.Circle({
                    radius: 5,
                    fill: null,
                    stroke: new ol.style.Stroke({color: 'red', width: 1})
                })
            }),
            'LineString': new ol.style.Style({
                stroke: new ol.style.Stroke({
                    color: 'red',
                    width: 1
                })
            }),
            'MultiLineString': new ol.style.Style({
                stroke: new ol.style.Stroke({
                    color: 'red',
                    width: 1
                })
            }),
            'MultiPoint': new ol.style.Style({
                image: new ol.style.Circle({
                    radius: 5,
                    fill: null,
                    stroke: new ol.style.Stroke({color: 'red', width: 1})
                })
            }),
            'MultiPolygon': new ol.style.Style({
                stroke: new ol.style.Stroke({
                    color: 'red',
                    width: 1
                }),
                fill: new ol.style.Fill({
                    color: 'rgba(0, 0, 255, 0.1)'
                })
            }),
            'GeometryCollection': new ol.style.Style({
                stroke: new ol.style.Stroke({
                    color: 'red',
                    width: 1
                }),
                fill: new ol.style.Fill({
                    color: 'red'
                }),
                image: new ol.style.Circle({
                    radius: 10,
                    fill: null,
                    stroke: new ol.style.Stroke({
                        color: 'red'
                    })
                })
            }),
        };
        
        var styleFunction = function(feature) {
            return styles[feature.getGeometry().getType()];
        };
        
        var vectorSource = new ol.source.Vector({
            format: new ol.format.GeoJSON(),
            url: filePath
        })
        
        var vectorLayer = new ol.layer.Vector({
            source: vectorSource,
            style: styleFunction
        });
        
        var scaleLineControl = new ol.control.ScaleLine();
    
        var map = new ol.Map({
            controls: ol.control.defaults().extend([scaleLineControl, fullScreen]),
            layers: [tile, vectorLayer],
            target: 'map-view',
            view: new ol.View({
                center: [0, 0],
                zoom: 2
            })
        });

        var graticule = new ol.Graticule({
            strokeStyle: new ol.style.Stroke({
                color: 'rgba(255,120,0,0.9)',
                width: 2,
                lineDash: [0.5, 4]
            }),
            showLabels: true
        });

        graticule.setMap(map);
        
        mv.setInteractions(map, vectorSource);
        mv.exportMap(map);
    };

    return mv;
}(MapViewer || {}));

console.log(ol);
