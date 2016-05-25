define([
    "mvc/base-mvc",
    "utils/localization",
    "mvc/history/history-model",
], function( baseMVC, _l, HISTORY_MODEL){
'use strict';

var logNamespace = 'user';
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
        require('libs/Chart.js');
        var tpl = _.template([
            "<h2>Galaxy Quota Usage</h2>",
            "<% _.each(histories, function(history) { %>",
                "<li>",
                    "<%- history %>",
                "</li>",
            "<% }); %>",
        ].join(''));
        this.$el.html(tpl({histories: this.model.models}));
        var ctx = $('<canvas/>');
        $(this.$el).append(ctx);
        var chartData = [];
        var colorspacing = 360 / this.model.models.length;
        var c = 0;
        _.each(this.model.models, function(history){
            c += colorspacing;
            var h = {name: history.attributes.name,
                           size: history.attributes.size,
                           color: Color('hsl('+c+',60%,50%)').hexString()};
            chartData.push(h);
        });
        new Chart(ctx, {
            type: 'pie',
            data: {
                labels: _.pluck(chartData, 'name'),
                datasets: [{
                    data: _.pluck(chartData, 'size'),
                    backgroundColor: _.pluck(chartData, 'color'),
                    hoverBackgroundColor: _.pluck(chartData, 'color'),
                    label: _.pluck(chartData, 'name'),
                }]
            },
            options: {hover: {mode: 'single'}}
        });
    }
});

//==============================================================================
return {
    UserQuotaGraph : UserQuotaGraph
};});
