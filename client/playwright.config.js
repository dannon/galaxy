const config = {
    // Each test is given 30 seconds
    use: {
        baseUrl: "http://localhost:8080",
        headless: true,
        viewport: { width: 1280, height: 720 },
    },
    timeout: 30000,
    testMatch: "**/*.spec.mjs",
};

module.exports = config;
