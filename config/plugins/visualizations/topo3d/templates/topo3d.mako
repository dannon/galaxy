<html>
    <head>
        <!-- Load plotly.js into the DOM -->
        <script src='https://cdn.plot.ly/plotly-2.12.1.min.js'></script>
        <script src='https://cdnjs.cloudflare.com/ajax/libs/d3/3.5.17/d3.min.js'></script>

        <!-- copied from script block on codepen -->
        <script type="text/javascript">

/* Build source dataset url */
var hda_id = '${ trans.security.encode_id( hda.id ) }'
var ajax_url = "${h.url_for( controller='/datasets', action='index')}/" + hda_id + "/display"

d3.csv(ajax_url, function(err, rows){
function unpack(rows, key) {
  return rows.map(function(row) { return row[key]; });
}

var z_data=[ ];
for(i=0;i<24;i++)
{
  z_data.push(unpack(rows,i));
}

var data = [{
           z: z_data,
           type: 'surface'
        }];

var layout = {
  title: 'Mt Bruno Elevation',
  autosize: false,
  width: 500,
  height: 500,
  margin: {
    l: 65,
    r: 50,
    b: 65,
    t: 90,
  }
};
Plotly.newPlot('myDiv', data, layout);
});


        </script>
</head>

<body>
<div id='myDiv'>            <!-- Plotly chart will be drawn inside this DIV --></div>
        </body>
    </html>