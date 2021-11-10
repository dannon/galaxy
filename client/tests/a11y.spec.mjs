import { test } from "@playwright/test";
import { injectAxe, checkA11y } from "axe-playwright";

test("Home route a11y test", async ({ page }) => {
    await page.goto("http://localhost:8080");
    await injectAxe(page);
    await page.waitForTimeout(1000); // Sadly required.
    await page.waitForLoadState("domcontentloaded"); // wait for the page to fire domcontentloaded
    await page.waitForLoadState("networkidle");
    await checkA11y(page, null, {
        includedImpacts: ["critical", "serious"],
        detailedReport: true,
        detailedReportOptions: {
            html: true,
        },
    });
});

test("Login a11y test", async ({ page }) => {
    await page.goto("http://localhost:8080/login");
    await injectAxe(page);
    await page.waitForTimeout(1000); // Sadly required.
    await page.waitForLoadState("domcontentloaded"); // wait for the page to fire domcontentloaded
    await page.waitForLoadState("networkidle");
    await checkA11y(page, null, {
        includedImpacts: ["critical", "serious"],
        detailedReport: true,
        detailedReportOptions: {
            html: true,
        },
    });
});
