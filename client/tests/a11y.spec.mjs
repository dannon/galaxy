import { test } from "@playwright/test";
import { injectAxe, checkA11y } from "axe-playwright";

test("basic test", async ({ page }) => {
    await page.goto("http://localhost:8080");
    await injectAxe(page);
    await page.waitForLoadState("networkidle");
    await checkA11y(page, null, {
        detailedReport: true,
        detailedReportOptions: {
            html: true,
        },
    });
});
