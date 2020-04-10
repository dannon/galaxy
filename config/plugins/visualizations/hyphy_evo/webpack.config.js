const webpack = require("webpack");
const path = require("path");
const MiniCssExtractPlugin = require("mini-css-extract-plugin");

module.exports = {
    mode: "production",
    entry: path.resolve(__dirname, "src/index.js"),
    output: {
        filename: "script.js",
        path: path.resolve(__dirname, "static"),
    },
    plugins: [
        new webpack.ProvidePlugin({
            $: "jquery",
            jQuery: "jquery",
            d3: "d3",
            _: "underscore",
        }),
    ],
    module: {
        rules: [
            {
                test: /\.(woff|woff2)(\?v=\d+\.\d+\.\d+)?$/,
                loader: "url-loader",
                options: { limit: 10000, mimetype: "application/font-woff" },
            },
            {
                test: /\.ttf(\?v=\d+\.\d+\.\d+)?$/,
                loader: "url-loader",
                options: { limit: 10000, mimetype: "application/octet-stream" },
            },
            { test: /\.eot(\?v=\d+\.\d+\.\d+)?$/, loaders: "file-loader" },
            {
                test: /\.svg(\?v=\d+\.\d+\.\d+)?$/,
                loaders: "url-loader",
                options: { limit: 10000, mimetype: "image/svg+xml" },
            },
            {
                test: /\.(sa|sc|c)ss$/,
                use: [
                    { loader: "style-loader" },
                    {
                        loader: "css-loader",
                        options: { sourceMap: true },
                    },
                    {
                        loader: "sass-loader",
                    },
                ],
            },
        ],
    },
    resolve: {
        modules: ["node_modules"],
        /*
        alias: {
            "phylotree.css": __dirname + "/node_modules/phylotree/build/phylotree.css"
        }
        */
    },
};
