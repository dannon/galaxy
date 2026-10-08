import { describe, expect, it } from "vitest";

import { errorMessageAsString } from "@/utils/simple-error";

function axiosError(status: number, statusText: string | undefined, data: unknown = "<html><body>nginx</body></html>") {
    return { response: { status, statusText, data } };
}

describe("errorMessageAsString", () => {
    // Browsers leave statusText empty over HTTP/2, for XHR as well as fetch.
    it("names an axios error status without relying on the status text", () => {
        expect(errorMessageAsString(axiosError(413, ""))).toBe("The request was too large (413)");
    });

    it.each([[""], [undefined]])(
        "says the request failed when there is no wording and the status text is %j",
        (statusText) => {
            expect(errorMessageAsString(axiosError(418, statusText))).toBe("The request failed (418)");
        },
    );

    it("falls back to the status text for a status it has no wording for", () => {
        expect(errorMessageAsString(axiosError(418, "I'm a Teapot"))).toBe("I'm a Teapot (418)");
    });

    it("prefers the API's own error message", () => {
        expect(errorMessageAsString(axiosError(413, "", { err_msg: "Quota exceeded" }))).toBe("Quota exceeded");
    });
});
