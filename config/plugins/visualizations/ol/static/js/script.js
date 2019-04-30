var raster = new ol.layer.Tile({
    source: new ol.source.OSM()
});

var source = new ol.source.Vector({wrapX: false});

var vector = new ol.layer.Vector({
    source: source
});

var map = new ol.Map({
    layers: [raster, vector],
    target: 'map-view',
    view: new ol.View({
      center: [-11000000, 4600000],
      zoom: 4
    })
});

var typeSelect = document.getElementById('type');

var draw;

function addInteraction() {
    var value = typeSelect.value;
    if (value !== 'None') {
        draw = new ol.interaction.Draw({
            source: source,
            type: typeSelect.value,
            freehand: true
        });
        map.addInteraction(draw);
    }
}

typeSelect.onchange = function() {
    map.removeInteraction(draw);
    addInteraction();
};

addInteraction();

document.getElementById('export-png').addEventListener('click', function(e) {
    map.once('rendercomplete', function(event) {
        var canvas = event.context.canvas;
        if (navigator.msSaveBlob) {
            navigator.msSaveBlob(canvas.msToBlob(), 'map.png');
        } else {
            canvas.toBlob(function(blob) {
                saveAs(blob, 'map.png');
            });
        }
    });
    map.renderSync();
});

