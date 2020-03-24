const webpack = require("webpack");
const path = require("path");
const MiniCssExtractPlugin = require("mini-css-extract-plugin");

module.exports = {
    mode: 'production',
    entry: path.resolve(__dirname, "src/index.js"),
    output: {
        filename: "script.js",
        path: path.resolve(__dirname, "static")
    },
    plugins: [
        new MiniCssExtractPlugin(),
        new webpack.ProvidePlugin({
            $: "jquery",
            jQuery: "jquery",
            d3: "d3",
            _: "underscore"
        })
    ],
    module: {
        rules: [
            {
                test: /\.(woff|woff2)(\?v=\d+\.\d+\.\d+)?$/,
                loader: "url-loader",
                options: { limit: 10000, mimetype: "application/font-woff" }
            },
            {
                test: /\.ttf(\?v=\d+\.\d+\.\d+)?$/,
                loader: "url-loader",
                options: { limit: 10000, mimetype: "application/octet-stream" }
            },
            { test: /\.eot(\?v=\d+\.\d+\.\d+)?$/, loaders: "file-loader" },
            {
                test: /\.svg(\?v=\d+\.\d+\.\d+)?$/,
                loaders: "url-loader",
                options: { limit: 10000, mimetype: "image/svg+xml" }
            },
            {
                test: /\.(sa|sc|c)ss$/,
                use: [
                    {
                        loader: MiniCssExtractPlugin.loader,
                        options: {
                            hmr: process.env.NODE_ENV === "development"
                        }
                    },
                    {
                        loader: "css-loader",
                        options: { sourceMap: true }
                    },
                    /*
                    {
                        loader: "postcss-loader",
                        options: {
                            plugins: function() {
                                return [require("autoprefixer")];
                            }
                        }
                    },*/
                    {
                        loader: "sass-loader",
                    }
                ]
            },
            /*
            {
                test: require.resolve("jquery"),
                use: [
                    {
                        loader: "expose-loader",
                        query: "jQuery"
                    },
                    {
                        loader: "expose-loader",
                        query: "$"
                    }
                ]
            },
            {
                test: require.resolve("d3"),
                use: [
                    {
                        loader: "expose-loader",
                        query: "d3"
                    }
                ]
            },
            {
                test: require.resolve("underscore"),
                use: [
                    {
                        loader: "expose-loader",
                        query: "_"
                    }
                ]
            }
            */
        ]
    },
    resolve: {
        modules: ["node_modules"],
        alias: {
            "phylotree.css": __dirname + "/node_modules/phylotree/build/phylotree.css"
        }
    }
};
