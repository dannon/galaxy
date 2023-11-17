import { serverPath } from "@/utils/serverPath";

/**
 * Extracts the root URL from the <link rel="index"> tag in the document head.
 * If not present, defaults to a specified root URL.
 *
 * We should shift to using the <base> tag standard for defining the root URL.
 *
 * @param {string} [defaultRoot="/"] - The default root URL to use if no index link is found.
 * @returns {string} The root URL.
 */
export function getRootFromIndexLink(defaultRoot = "/"): string {
    // Early return if not in a browser environment
    if (typeof document === "undefined") {
        return defaultRoot;
    }

    // Search for the <link rel="index"> tag
    const indexLink = Array.from(document.getElementsByTagName("link")).find((link) => link.rel === "index");

    // Return the href of the index link if it exists, otherwise default root
    return indexLink?.href ? serverPath(indexLink.href) : defaultRoot;
}
