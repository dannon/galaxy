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
    <div id="viz" />
    ${h.javascript_link( app_root + 'script.js' )}
    <script type="text/javascript">
      var raw_url = '${h.url_for( controller="/datasets", action="index" )}';
      var hda_id = '${trans.security.encode_id( hda.id )}';
      var url = raw_url + '/' + hda_id + '/display/';
      <!-- Test static dataset for just getting it working -->
      renderMeme(
          url + 'meme.json',
          url + 'full.fasta',
          url + 'base.fasta',
          url + 'pdb',
          'viz'
      );
    </script>
  </body>
</html>