<%
    app_root = h.url_for("/static/plugins/visualizations/ol/static/")
%>

<!DOCTYPE html>
<html>
<head lang="en">
    <meta name="viewport" content="width=device-width,initial-scale=1,minimum-scale=1,maximum-scale=1"/>
    <title>Open layers</title>
    ${h.stylesheet_link(app_root + 'css/bootstrap.min.css')}
    ${h.stylesheet_link(app_root + 'css/font-awesome.min.css')}
    ${h.stylesheet_link(app_root + 'css/ol.css')}
    ${h.stylesheet_link(app_root + 'css/map_view.css')}
    ${h.javascript_link( app_root + "js/jquery.min.js")}
    ${h.javascript_link( app_root + "js/ol.js")}
    ${h.javascript_link( app_root + "js/FileSaver.min.js")}
    ${h.javascript_link( app_root + "js/shpgeojson.js")}
    
</head>
<body class="body-map-view">
    <div id="map-view" class="map"></div>
    <div class="map-options">
        <select id="geometry-type" class="form-control">
            <option value="None">Select geometry type</option>
            <option value="Point">Point</option>
            <option value="LineString">LineString</option>
            <option value="Polygon">Polygon</option>
            <option value="Circle">Circle</option>
        </select>
        <button id="export-png" type="button" class="btn btn-secondary btn-sm"><i class="fa fa-download"></i>Export</button>
    </div>
    ${h.javascript_link( app_root + "js/script.js")}
    <script>
        $(document).ready(function() {
            MapViewer.loadFile(`${h.url_for(controller='/datasets', action='index')}/${hda.id}/display`);
        });
    </script>
</body>
</html>

