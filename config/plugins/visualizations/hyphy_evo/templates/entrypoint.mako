<%
  app_root = "/static/plugins/visualizations/hyphy_evo/static/"
%>

<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <title>HyPhy Site Evolution and Structural Viewer</title>
  </head>
  <body>
    <div id="hyphy-root" />
    ${h.stylesheet_link( app_root + 'main.css' )}
    ${h.javascript_link( app_root + 'script.js' )}
    <script type="text/javascript">
      var raw_url = '${h.url_for( controller="/datasets", action="index" )}';
      var hda_id = '${trans.security.encode_id( hda.id )}';
      var url = raw_url + '/' + hda_id + '/display?to_ext=json';
      <!-- Test static dataset for rapid iteration -->
      renderFubar(
        "/static/coronahack/public/S.fna.FUBAR.json",
        "/static/coronahack/public/S-full.fasta",
        "/static/coronahack/public/S.pdb",
        "/static/coronahack/public/S-map.csv",
        "hyphy-root");
    </script>
  </body>
</html>
