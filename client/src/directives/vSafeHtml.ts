/**
 * Renders an HTML string into an element after cleaning it with DOMPurify.
 * Use this instead of `v-html` wherever markup is genuinely needed; plain
 * text belongs in `{{ }}` interpolation.
 *
 * The argument picks the profile (see sanitizeHtml.ts):
 *   v-safe-html="html"          -> `default` profile
 *   v-safe-html:links="html"    -> `links` profile
 */

import type { DirectiveBinding, ObjectDirective } from "vue";

import { type SafeHtmlProfile, sanitizeHtml } from "./sanitizeHtml";

export type SafeHtmlBinding = string | null | undefined;

function toHtml(value: unknown): string {
    if (value === null || value === undefined) {
        return "";
    }
    if (typeof value === "string") {
        return value;
    }
    // Coercing would show "[object Object]" or a comma-joined array, so the
    // caller has to build the string it wants rendered.
    console.warn("v-safe-html expects a string, got:", value);
    return "";
}

function render(el: HTMLElement, binding: DirectiveBinding<SafeHtmlBinding>) {
    const profile = (binding.arg ?? "default") as SafeHtmlProfile;
    el.innerHTML = sanitizeHtml(toHtml(binding.value), profile);
}

export const vSafeHtml: ObjectDirective<HTMLElement, SafeHtmlBinding> = {
    bind(el, binding) {
        render(el, binding);
    },
    update(el, binding) {
        // Like v-html, only touch the DOM when the content changes, so code that
        // decorates the rendered nodes after mount is not undone on every re-render.
        if (binding.value !== binding.oldValue) {
            render(el, binding);
        }
    },
};

export default vSafeHtml;
