define([
    "mvc/base-mvc",
    "utils/localization",
    "mvc/history/history-model",
    "libs/d3",
    "libs/plotly",
], function( baseMVC, _l, HISTORY_MODEL, d3, Plotly){
'use strict';

var UserQuotaGraph = Backbone.View.extend({
    //initialize
    initialize: function() {
        var self = this;
        this.setElement('<div/>');
        this.model = new HISTORY_MODEL.HistoryCollection(null, {'includeDeleted': true});
        this.model.fetch({
            success: function(){
                self.render();
            },
            error: function(){
                console.error("Failed to fetch usage information.");
            }
        });
    },

    render: function(){
        // TODO other require style
        var tpl = _.template([
            "<h2>Galaxy Quota Usage</h2>",
			"<p>Really descriptive text about the display you'll see below!</p>",
			"<p>Might even include a summary of deleted vs purged, etc.</p>",
        ].join(''));
        this.$el.html(tpl({histories: this.model.models}));

        var ctx = $('<div/>').attr('id', 'historyTreeview');
        $(this.$el).append(ctx);

        var chartData = [];
        var color_cat = d3.scale.category20c();
		var gray_cat = d3.interpolateRgb("#808080", "#E0E0E0");
        _.each(this.model.models, function(history){
            if(history.attributes.size > 0){
                var h = { name: history.attributes.name,
                          value: history.attributes.size,
						  history: history
                        };
				if (history.attributes.deleted === true){
					h.color = gray_cat(Math.random());
				} else {
					h.color = color_cat(history.attributes.name);
				}
                chartData.push(h);
            }
        });

        var root = {name: "tree",
                    children: chartData};

		// We're not actually using this as a binding, we just wanted d3 to
		// figure the rectangles for us.
        var treemap = d3.layout.treemap()
                               .size([400, 400])
                               .nodes(root);

		var shapes = [];
		var annotations = [];
        var x_trace = [];
        var y_trace = [];
        var t_trace = [];

		_.each(treemap, function(h){
			if (h.history !== undefined){
				var x_c = h.x + h.dx/2;
				var y_c = h.y + h.dy/2;
				var shape = { type: 'rect',
							  x0:  h.x,
							  y0: h.y,
							  x1:  h.x + h.dx,
							  y1: h.y + h.dy,
							  line: { width:2 },
							  fillcolor: h.color };
				shapes.push(shape);
				var annotation = { x: x_c,
								   y: y_c,
								   text: h.history.attributes.name,
								   showarrow: false };
				annotations.push(annotation);
				x_trace.push(x_c);
				y_trace.push(y_c);
				t_trace.push(h.history.attributes.name);
			}
		});

		var trace0 = {
			x: x_trace,
			y: y_trace,
			text: t_trace,
			mode: 'text',
			type: 'scatter'
		};

		var axis_config = {
				autorange: true,
				showgrid: false,
				zeroline: false,
				showline: false,
				showticklabels: false
		};
		var layout = {
			height: 700,
			width: 700,
			shapes: shapes,
			hovermode: 'closest',
			annotations: annotations,
			xaxis: axis_config,
			yaxis: axis_config
		};

		Plotly.newPlot('historyTreeview', [trace0], layout,
					   { //staticPlot: true,
						 showLink: false,
						 displayModeBar: false,
						 showLegend: false});
    }
});

//==============================================================================
return {
    UserQuotaGraph : UserQuotaGraph
};});
