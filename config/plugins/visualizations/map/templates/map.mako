<%
    app_root = h.url_for("/static/plugins/visualizations/map/static/")
%>

<!DOCTYPE html>
<html>
<head lang="en">
    <meta name="viewport" content="width=device-width,initial-scale=1,minimum-scale=1,maximum-scale=1"/>
    <title>Open layers</title>
    ${h.stylesheet_link(app_root + 'css/ol.css')}
    ${h.stylesheet_link(app_root + 'css/ol_css.css')}
    ${h.javascript_link( app_root + "js/ol.js")}
</head>
<body class="map-view">
    <div id="map" class="map"></div>
    ${h.javascript_link( app_root + "js/ol_script.js")}
</body>
</html>

