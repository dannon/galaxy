var tile = new ol.layer.Tile({source: new ol.source.OSM()});
var source = new ol.source.Vector({wrapX: false});
var vector = new ol.layer.Vector({source: source});
var scaleLineControl = new ol.control.ScaleLine();
var fullScreen = new ol.control.FullScreen();

var map = new ol.Map({
    controls: ol.control.defaults().extend([scaleLineControl, fullScreen]),
    layers: [tile, vector],
    target: 'map-view',
    view: new ol.View({
      center: [-11000000, 4600000],
      zoom: 4
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
}

typeSelect.onchange = () => {
    map.removeInteraction(draw);
    addInteraction();
};

addInteraction();

document.getElementById('export-png').addEventListener('click', e => {
    map.once('rendercomplete', event => {
        let canvas = event.context.canvas;
        if (navigator.msSaveBlob) {
            navigator.msSaveBlob(canvas.msToBlob(), 'map.png');
        } else {
            canvas.toBlob(blob => {
                saveAs(blob, 'map.png');
            });
        }
    });
    map.renderSync();
});
