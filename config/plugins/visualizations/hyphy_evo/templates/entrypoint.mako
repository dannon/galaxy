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
      var url = raw_url + '/' + hda_id + '/display?to_ext=json';
      <!-- Test static dataset for just getting it working -->
      renderMeme(
          '/static/coronahack/S-032420.fna.MEME.json',
          '/static/coronahack/S-032420-full.fasta',
          '/static/coronahack/S-032420-AA.fasta',
          '/static/coronahack/S.pdb',
          'viz'
      );
    </script>
  </body>
</html>
