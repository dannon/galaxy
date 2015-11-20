WEBPACK_BIN = node ./node_modules/webpack/bin/webpack.js

setup:
		npm install
build:
		@$(WEBPACK_BIN)

develop:
		@$(WEBPACK_BIN) -d

production:
		@$(WEBPACK_BIN) -p

watch:
		@$(WEBPACK_BIN) --watch -d
